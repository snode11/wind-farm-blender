"""Blender adapter for saved Blade Recon geometry and optional v0.2 textures.

Each import creates an independent scene. Frame callbacks only request updates;
mesh/color changes are committed by a main-thread timer. Embedded JSON restores
playback on file load without trusting executable Text blocks or old file paths.
"""
import hashlib
import json
import math
from pathlib import Path
import uuid

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector
import numpy as np

from .blade_recon_data import ReconSequence, TexturePackage, default_recon_path

GREEN = (0.15, 0.75, 0.25, 1.0)
ORANGE = (1.0, 0.55, 0.10, 1.0)
SESSIONS = {}
HUD_HANDLE = None
INSPECTION_PROPERTIES = (
    'wfrl_recon_texture_mode', 'wfrl_recon_texture_gain', 'wfrl_recon_texture_threshold',
    'wfrl_recon_inspect_blade', 'wfrl_recon_inspect_radius', 'wfrl_recon_inspect_tau',
    'wfrl_recon_inspect_size', 'wfrl_recon_inspect_box',
)
RETURN_VIEWPORTS = 'blade_recon_return_viewports'
SPACE_VIEW_PROPERTIES = ('show_region_ui', 'use_local_camera', 'lens', 'clip_start', 'clip_end')
SHADING_VIEW_PROPERTIES = ('type', 'light', 'color_type', 'show_shadows',
                           'background_type', 'background_color')
OVERLAY_VIEW_PROPERTIES = ('show_floor', 'show_axis_x', 'show_axis_y')


def active(scene):
    return scene.get('wfrl_scene_kind') == 'blade_recon'


def session(scene):
    return SESSIONS.get(scene.as_pointer())


def _inspection_changed(scene, context):
    value = session(scene)
    if value is None:
        return
    from . import blade_recon_inspection
    blade_recon_inspection.update(scene, value['blades'], value['sequence'].rotor)
    if context and context.screen:
        for area in context.screen.areas:
            area.tag_redraw()


def inspect_view(context, areas=None, scene=None, index=None):
    scene = scene or context.scene
    value = session(scene)
    if value is None:
        raise ValueError('请先载入重建结果')
    from . import blade_recon_inspection
    result = blade_recon_inspection.focus(context, value['sequence'], value['blades'],
                                         areas=areas, scene=scene, index=index)
    apply(scene)
    return result


def set_texture_mode(scene, mode):
    value = session(scene)
    if value is None:
        raise ValueError('请先载入带纹理的重建结果')
    from . import blade_recon_inspection
    return blade_recon_inspection.set_texture_mode(scene, value['blades'],
                                                   value['sequence'].rotor, mode)


def toggle_texture_mode(scene, enhanced='FALSE_COLOR'):
    value = session(scene)
    if value is None:
        raise ValueError('请先载入带纹理的重建结果')
    from . import blade_recon_inspection
    return blade_recon_inspection.toggle_texture_mode(scene, value['blades'],
                                                      value['sequence'].rotor, enhanced)


def _view_areas(context, areas=None):
    return tuple(area for area in (areas if areas is not None else
        (context.screen.areas if context.screen else ())) if area.type == 'VIEW_3D')


def _view_value(value):
    return value if isinstance(value, (bool, int, float, str)) else list(value)


def snapshot_viewports(context):
    """Serializable pre-import view state, including saved-file restoration."""
    values = []
    if context.screen:
        for index, area in enumerate(context.screen.areas):
            if area.type != 'VIEW_3D':
                continue
            space = area.spaces.active
            regions = tuple(space.region_quadviews) or (space.region_3d,)
            values.append(dict(area_index=index,
                space={key: _view_value(getattr(space, key)) for key in SPACE_VIEW_PROPERTIES},
                camera=space.camera.name if space.camera else None,
                shading={key: _view_value(getattr(space.shading, key)) for key in SHADING_VIEW_PROPERTIES},
                overlay={key: _view_value(getattr(space.overlay, key)) for key in OVERLAY_VIEW_PROPERTIES},
                regions=[dict(view_perspective=region.view_perspective,
                              view_location=list(region.view_location),
                              view_rotation=list(region.view_rotation),
                              view_distance=region.view_distance) for region in regions if region]))
    return dict(version=1, screen=context.screen.name if context.screen else None, areas=values)


def restore_viewports(context, snapshot):
    """Restore the captured screen only; leave other workspaces' views alone."""
    if not context.screen or snapshot.get('version') != 1:
        return 0
    if snapshot.get('screen') != context.screen.name:
        return 0
    count = 0
    areas = tuple(context.screen.areas)
    for record in snapshot.get('areas', ()):
        index = record['area_index']
        if index >= len(areas) or areas[index].type != 'VIEW_3D':
            continue
        area, space = areas[index], areas[index].spaces.active
        for key, value in record['space'].items():
            setattr(space, key, value)
        space.camera = bpy.data.objects.get(record.get('camera')) if record.get('camera') else None
        for target, key in ((space.shading, 'shading'), (space.overlay, 'overlay')):
            for name, value in record[key].items():
                setattr(target, name, value)
        regions = tuple(space.region_quadviews) or (space.region_3d,)
        for region, saved in zip(regions, record['regions']):
            if region:
                for name, value in saved.items():
                    setattr(region, name, value)
        area.tag_redraw()
        count += 1
    return count


def return_to_original(context):
    scene = context.scene
    original = bpy.data.scenes.get(scene.get('blade_recon_return_scene', ''))
    if original is None:
        raise ValueError('原场景已不存在')
    if context.screen and context.screen.is_animation_playing:
        bpy.ops.screen.animation_cancel(restore_frame=False)
    raw = scene.get(RETURN_VIEWPORTS)
    snapshot = json.loads(raw) if raw else {}
    context.window.scene = original
    restored = restore_viewports(context, snapshot)
    return dict(scene=original.name, restored_views=restored)


def _material(name, color, evidence=False):
    mat = bpy.data.materials.new(name)
    mat['blade_recon_owned'] = True
    mat.diffuse_color = color
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value = color
    bsdf.inputs['Roughness'].default_value = .65
    if evidence:
        attr = mat.node_tree.nodes.new('ShaderNodeVertexColor')
        attr.layer_name = 'evidence'
        mat.node_tree.links.new(attr.outputs['Color'], bsdf.inputs['Base Color'])
    return mat


def _texture_material(name, png, atlas):
    mat = _material(name, GREEN, evidence=True)
    mat['blade_recon_owned'] = True
    nt = mat.node_tree
    bsdf = nt.nodes.get('Principled BSDF')
    vcol = next(n for n in nt.nodes if n.bl_idname == 'ShaderNodeVertexColor')
    image = bpy.data.images.load(str(png), check_existing=False)
    image['blade_recon_owned'] = True
    if tuple(image.size) != (atlas['W'], atlas['H']) or image.channels != 4:
        bpy.data.images.remove(image)
        raise ValueError('Blender could not decode RGBA atlas '+str(png))
    image.pack()
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.name = 'SurfaceTexture'
    tex.image, tex.interpolation, tex.extension = image, 'Linear', 'REPEAT'
    # REPEAT is needed for bilinear interpolation across the circumference seam.
    # V never reaches a wrapped texel: clamp it to root/tip pixel centers, rather
    # than 0/1 where REPEAT would mix the opposite ends of the blade.
    uv = nt.nodes.new('ShaderNodeUVMap')
    uv.uv_map = 'atlas'
    separate = nt.nodes.new('ShaderNodeSeparateXYZ')
    nt.links.new(uv.outputs['UV'], separate.inputs[0])
    periodic = nt.nodes.new('ShaderNodeMath')
    periodic.name, periodic.operation = 'CircumferenceWrap', 'FRACT'
    nt.links.new(separate.outputs['X'], periodic.inputs[0])
    root = nt.nodes.new('ShaderNodeMath')
    root.name, root.operation = 'SpanRootClamp', 'MINIMUM'
    root.inputs[1].default_value = 1 - .5 / atlas['H']
    nt.links.new(separate.outputs['Y'], root.inputs[0])
    tip = nt.nodes.new('ShaderNodeMath')
    tip.name, tip.operation = 'SpanTipClamp', 'MAXIMUM'
    tip.inputs[1].default_value = .5 / atlas['H']
    nt.links.new(root.outputs[0], tip.inputs[0])
    combine = nt.nodes.new('ShaderNodeCombineXYZ')
    nt.links.new(periodic.outputs[0], combine.inputs['X'])
    nt.links.new(tip.outputs[0], combine.inputs['Y'])
    nt.links.new(combine.outputs[0], tex.inputs['Vector'])
    mix = nt.nodes.new('ShaderNodeMixRGB')
    mix.name, mix.blend_type = 'TexMix', 'MIX'
    alpha = nt.nodes.new('ShaderNodeMath')
    alpha.name, alpha.operation = 'TextureVisibility', 'MULTIPLY'
    alpha.inputs[1].default_value = 1
    nt.links.new(tex.outputs['Alpha'], alpha.inputs[0])
    nt.links.new(alpha.outputs[0], mix.inputs[0])
    nt.links.new(vcol.outputs['Color'], mix.inputs[1])
    nt.links.new(tex.outputs['Color'], mix.inputs[2])
    nt.links.new(mix.outputs[0], bsdf.inputs['Base Color'])
    mat['blade_recon_span_boundary'] = 'clamp_to_pixel_centers'
    mat['blade_recon_circumference_boundary'] = 'periodic'
    return mat


def _add_uv(obj, rotor, faces):
    """Template arc-length coordinates; no resolution-dependent atlas allocation."""
    tpl, N, S = rotor.tpl, rotor.cfg.n_ring, rotor.cfg.n_sections
    pts = tpl.foils * tpl.chord[:, None, None]
    closed = np.concatenate([pts, pts[:, :1]], axis=1)
    cumulative = np.concatenate([np.zeros((S, 1)),
        np.cumsum(np.linalg.norm(np.diff(closed, axis=1), axis=-1), axis=1)], axis=1)
    tau = cumulative[:, :N] / cumulative[:, -1, None]
    v = 1 - (tpl.r-tpl.r[0]) / (tpl.r[-1]-tpl.r[0])
    u = tau.reshape(-1)[faces]
    rows = np.repeat(v[:, None], N, axis=1).reshape(-1)[faces]
    j = faces % N
    side = (np.arange(len(faces)) < (S-1)*N*2)[:, None]
    seam = (j == N-1).any(axis=1, keepdims=True) & (j == 0)
    u = np.where(seam & side, 1., u)
    u = np.where(side, u, u[:, :1])
    layer = obj.data.uv_layers.new(name='atlas')
    layer.data.foreach_set('uv', np.stack([u, rows], axis=-1).astype(np.float32).reshape(-1))


def bake_portable(scene):
    """Bake native held absolute geometry samples for standalone .blend playback.

    A packed section-by-frame evidence image also animates natively. The addon
    continues updating the inspectable evidence attribute when it is available.
    """
    value = session(scene)
    if value is None:
        raise ValueError('No reconstruction session to bake')
    seq = value['sequence']
    for b, obj in enumerate(value['blades']):
        obj.driver_remove('hide_viewport')
        obj.hide_viewport = False
        obj.shape_key_add(name='Sample0001', from_mix=False)
        keys = obj.data.shape_keys
        keys.use_relative = False
        keys.key_blocks[0].interpolation = 'KEY_LINEAR'
        for i in range(1, len(seq.states)):
            key = obj.shape_key_add(name=f'Sample{i+1:04d}', from_mix=False)
            key.data.foreach_set('co', seq.geometry(i)[0][b].reshape(-1).astype(np.float32))
            key.interpolation = 'KEY_LINEAR'
        for i, key in enumerate(keys.key_blocks):
            keys.eval_time = key.frame
            keys.keyframe_insert(data_path='eval_time', frame=i+1)
        # Blender 5 uses layered actions; iterate the actual key's action slot.
        action = keys.animation_data.action
        for layer in action.layers:
            for strip in layer.strips:
                bag = strip.channelbag(keys.animation_data.action_slot)
                if bag:
                    for curve in bag.fcurves:
                        for point in curve.keyframe_points:
                            point.interpolation = 'CONSTANT'
        obj['blade_recon_native_samples'] = len(seq.states)
        # The point attribute contains the section's pixel-center coordinate;
        # linear interpolation reproduces the vertex evidence colors on faces.
        section = obj.data.attributes.new(name='evidence_section', type='FLOAT', domain='POINT')
        coordinates = np.repeat((np.arange(seq.rotor.cfg.n_sections)+.5)/seq.rotor.cfg.n_sections,
                                seq.rotor.cfg.n_ring).astype(np.float32)
        section.data.foreach_set('value', coordinates)
        pixels = np.where(seq.observed[:, b, :, None], GREEN, ORANGE).astype(np.float32)
        evidence_image = bpy.data.images.new(obj.name+'.EvidenceFrames',
            width=seq.rotor.cfg.n_sections, height=len(seq.states), alpha=True, float_buffer=True)
        evidence_image.colorspace_settings.name = 'Non-Color'
        evidence_image.pixels.foreach_set(pixels.reshape(-1))
        evidence_image['blade_recon_owned'] = True
        evidence_image.pack()
        # Geometry-only imports share a material; make the native evidence
        # animation independent for each blade before replacing its input link.
        mat = obj.data.materials[0].copy()
        mat['blade_recon_owned'] = True
        obj.data.materials[0] = mat
        nt = mat.node_tree
        attribute = nt.nodes.new('ShaderNodeAttribute')
        attribute.attribute_name = 'evidence_section'
        clock = nt.nodes.new('ShaderNodeValue')
        clock.name = 'EvidenceSampleTime'
        driver = clock.outputs[0].driver_add('default_value').driver
        driver.expression = f'(min(max(frame-1,0),{len(seq.states)-1})+0.5)/{len(seq.states)}'
        coordinate = nt.nodes.new('ShaderNodeCombineXYZ')
        nt.links.new(attribute.outputs['Fac'], coordinate.inputs['X'])
        nt.links.new(clock.outputs[0], coordinate.inputs['Y'])
        evidence_tex = nt.nodes.new('ShaderNodeTexImage')
        evidence_tex.name = 'PortableEvidence'
        evidence_tex.image, evidence_tex.extension, evidence_tex.interpolation = evidence_image, 'EXTEND', 'Linear'
        nt.links.new(coordinate.outputs[0], evidence_tex.inputs['Vector'])
        mix = nt.nodes.get('TexMix')
        nt.links.new(evidence_tex.outputs['Color'], mix.inputs[1] if mix else nt.nodes['Principled BSDF'].inputs['Base Color'])
    scene['blade_recon_portable_geometry'] = True
    scene['blade_recon_portable_evidence'] = True
    scene.frame_set(scene.frame_current)


def _mesh(scene, name, vertices, faces, material):
    data = bpy.data.meshes.new(name)
    data.from_pydata(np.asarray(vertices).tolist(), [], [tuple(face) for face in faces])
    data.materials.append(material)
    data.update()
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.hide_select = True
    obj['blade_recon_owned'] = True
    return obj


def _cylinder(scene, name, lower, upper, radius0, radius1, material):
    lower, upper = np.array(lower), np.array(upper)
    axis = upper - lower
    axis /= np.linalg.norm(axis)
    ref = np.array([0., 1., 0.]) if abs(axis[1]) < .9 else np.array([1., 0., 0.])
    u = np.cross(axis, ref)
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    n = 32
    rings = [p + radius * (math.cos(a) * u + math.sin(a) * v)
             for p, radius in ((lower, radius0), (upper, radius1))
             for a in np.linspace(0, 2 * math.pi, n, endpoint=False)]
    faces = [(i, (i+1) % n, (i+1) % n+n, i+n) for i in range(n)]
    faces += [tuple(reversed(range(n))), tuple(range(n, n*2))]
    return _mesh(scene, name, rings, faces, material)


def _box(scene, name, center, size, material):
    vertices = [np.array(center) + np.array(size)*np.array([x, y, z])*.5
                for x, y, z in ((-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),
                                (1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1))]
    return _mesh(scene, name, vertices,
                 [(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)], material)


def _context_geometry(scene, rotor, prefix):
    mat = _material(prefix+'.Context', (.28,.33,.40,1))
    hub = rotor.hub
    _cylinder(scene, prefix+'.Tower', (0,0,0), (0,0,hub[2]-2.4), 3., 1.8, mat)
    _box(scene, prefix+'.Nacelle', (0,0,hub[2]), (8,4,4), mat)
    _cylinder(scene, prefix+'.Hub', hub-rotor.n*1.5, hub+rotor.n*1.5,
              rotor.cfg.hub_radius_m, rotor.cfg.hub_radius_m, mat)


def _camera(scene, name, position, target):
    data = bpy.data.cameras.new(name)
    data.lens = 35
    data.clip_end = 3000
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = position
    obj.rotation_euler = (Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler()
    obj['blade_recon_view_target'] = list(target)
    obj.hide_select = True
    obj['blade_recon_owned'] = True
    return obj


def _import_cameras(scene, sequence, prefix):
    imported = []
    for d in sequence.cameras:
        data = bpy.data.cameras.new(prefix+'.'+d['name'])
        data.clip_start, data.clip_end, data.display_size = .05, 3000, 1.5
        obj = bpy.data.objects.new(data.name, data)
        _apply_camera(obj, d)
        scene.collection.objects.link(obj)
        obj.hide_select = True
        obj['blade_recon_owned'] = True
        obj['blade_recon_camera_name'] = d['name']
        obj['K_json'] = json.dumps(d['K'])
        obj['T_json'] = json.dumps(d['T_cv_from_world'])
        obj['W'], obj['H'] = d['W'], d['H']
        imported.append(obj)
    return imported


def _apply_camera(obj, camera):
    K = np.array(camera['K'])
    data = obj.data
    data.sensor_fit, data.sensor_width = 'HORIZONTAL', 36
    data.lens = float(K[0,0]*36/camera['W'])
    data.shift_x = float(((camera['W']-1)/2-K[0,2])/camera['W'])
    data.shift_y = float((K[1,2]-(camera['H']-1)/2)*(K[0,0]/K[1,1])/camera['W'])
    T = np.eye(4)
    T[:3] = camera['T_cv_from_world']
    obj.matrix_world = Matrix((np.linalg.inv(T) @ np.diag([1,-1,-1,1])).tolist())
    obj['K_json'], obj['T_json'] = json.dumps(camera['K']), json.dumps(camera['T_cv_from_world'])
    obj['W'], obj['H'] = camera['W'], camera['H']


def _hide_stale(obj):
    # Hide an old mesh during the short interval between a seek and timer commit.
    obj['blade_recon_applied_frame'] = -1
    driver = obj.driver_add('hide_viewport').driver
    variable = driver.variables.new()
    variable.name = 'applied'
    variable.targets[0].id = obj
    variable.targets[0].data_path = '["blade_recon_applied_frame"]'
    driver.expression = 'frame != applied'


def _embed(scene, name, payload):
    text = bpy.data.texts.new(name)
    text.write(payload)
    text.use_fake_user = True
    text['blade_recon_owned'] = True
    scene[name.rsplit('.',1)[-1]+'_text'] = text.name


def _read_embedded(scene, key):
    name = scene.get(key+'_text')
    if not name:
        return None
    block = bpy.data.texts.get(name)
    if block is None:
        raise ValueError('保存的 '+key+' 数据缺失')
    return block.as_string()


def _restore(scene):
    raw = _read_embedded(scene, 'recon')
    if raw is None or hashlib.sha256(raw.encode()).hexdigest() != scene.get('blade_recon_sha256'):
        raise ValueError('保存的重建数据校验失败')
    truth, cameras = _read_embedded(scene, 'truth'), _read_embedded(scene, 'cameras')
    for key, value in (('truth', truth), ('cameras', cameras)):
        if value is not None and hashlib.sha256(value.encode()).hexdigest() != scene.get('blade_recon_'+key+'_sha256'):
            raise ValueError('保存的 '+key+' 数据校验失败')
    sequence = ReconSequence.from_data(json.loads(raw),
        truth_data=json.loads(truth) if truth else None,
        cameras_data=json.loads(cameras) if cameras else None,
        source_label=scene.get('blade_recon_source_label', 'External reconstruction'),
        source_sha256=scene['blade_recon_sha256'])
    if sequence.model_sha256 != scene.get('blade_recon_model_sha256'):
        raise ValueError('前向模型版本与保存结果不一致')
    objects = sorted((o for o in scene.objects if o.get('blade_recon_role') == 'estimate'),
                     key=lambda o:o['blade_recon_blade'])
    ghosts = sorted((o for o in scene.objects if o.get('blade_recon_role') == 'truth'),
                    key=lambda o:o['blade_recon_blade'])
    if len(objects) != 3 or any(len(o.data.vertices) != sequence.rotor.cfg.n_sections*sequence.rotor.cfg.n_ring for o in objects):
        raise ValueError('保存的叶片网格不匹配')
    texture_raw = _read_embedded(scene, 'texture')
    if texture_raw is not None:
        if hashlib.sha256(texture_raw.encode()).hexdigest() != scene.get('blade_recon_texture_sha256'):
            raise ValueError('保存的纹理元数据校验失败')
        texture = json.loads(texture_raw)
        for b, obj in enumerate(objects):
            if obj.data.uv_layers.get('atlas') is None:
                raise ValueError('保存的纹理 UV 缺失')
            tex = obj.data.materials[0].node_tree.nodes.get('SurfaceTexture')
            image = tex.image if tex else None
            expected = texture['files'][b]
            if image is None or image.packed_file is None:
                raise ValueError('保存的叶片纹理没有打包')
            if tuple(image.size) != (texture['atlas']['W'], texture['atlas']['H']):
                raise ValueError('保存的纹理尺寸不匹配')
            if hashlib.sha256(image.packed_file.data).hexdigest() != expected['sha256']:
                raise ValueError('保存的纹理图像校验失败')
    SESSIONS[scene.as_pointer()] = dict(sequence=sequence, blades=objects, ghosts=ghosts,
        cameras=[o for o in scene.objects if 'blade_recon_camera_name' in o], pending=True, index=None)
    scene['blade_recon_error'] = ''


def load(context, path=None, truth_path=None, cameras_path=None, texture_path=None):
    """Validate before allocating anything; preserve the previous scene on failure."""
    path = Path(path or default_recon_path()).expanduser().resolve()
    sequence = ReconSequence(path, truth_path=truth_path, cameras_path=cameras_path)
    textures = TexturePackage(texture_path, sequence.rotor) if texture_path else None
    raw = path.read_text(encoding='utf-8')
    companions = {}
    for key, explicit in (('truth', truth_path), ('cameras', cameras_path)):
        candidate = Path(explicit).expanduser().resolve() if explicit else path.with_name(key+'.json')
        if candidate.is_file():
            companions[key] = candidate.read_text(encoding='utf-8')
    previous_scene = context.scene
    previous_views = snapshot_viewports(context)
    prefix = 'WFRL.BladeRecon.'+uuid.uuid4().hex[:8]
    scene = bpy.data.scenes.new('叶片三维重建')
    try:
        scene['wfrl_scene_kind'] = 'blade_recon'
        scene['blade_recon_return_scene'] = previous_scene.name
        scene[RETURN_VIEWPORTS] = json.dumps(previous_views, ensure_ascii=False)
        scene['blade_recon_source_path'] = str(path)
        scene['blade_recon_source_label'] = sequence.source_label
        scene['blade_recon_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        scene['blade_recon_model_sha256'] = sequence.model_sha256
        scene['blade_recon_error'] = ''
        _embed(scene, prefix+'.recon', raw)
        for key, payload in companions.items():
            _embed(scene, prefix+'.'+key, payload)
            scene['blade_recon_'+key+'_sha256'] = hashlib.sha256(payload.encode()).hexdigest()
        if textures:
            texture_raw = json.dumps(textures.manifest, ensure_ascii=False, sort_keys=True)
            _embed(scene, prefix+'.texture', texture_raw)
            scene['blade_recon_texture_sha256'] = hashlib.sha256(texture_raw.encode()).hexdigest()
            scene['blade_recon_texture_source_path'] = str(textures.path)
        scene.frame_start, scene.frame_end = 1, len(sequence.states)
        scene.render.fps = max(1, min(32767, round(sequence.fps)))
        scene.render.fps_base = scene.render.fps/sequence.fps
        scene.render.resolution_x, scene.render.resolution_y = 1600, 1000
        scene.render.resolution_percentage = 100
        scene.unit_settings.system = 'METRIC'
        _context_geometry(scene, sequence.rotor, prefix)
        mat = _material(prefix+'.Evidence', GREEN, evidence=True)
        ghost_mat = _material(prefix+'.Truth', (.60,.67,.75,1))
        vertices, _ = sequence.geometry(0)
        faces = sequence.rotor.tpl.faces()
        blades, ghosts = [], []
        for b in range(3):
            blade_mat = _texture_material(prefix+'.Texture.B'+str(b+1), textures.images[b],
                                          textures.data['atlas']) if textures else mat
            ob = _mesh(scene, prefix+'.Recon.B'+str(b+1), vertices[b].reshape(-1,3), faces, blade_mat)
            ob.data.color_attributes.new(name='evidence', type='FLOAT_COLOR', domain='POINT')
            if textures:
                _add_uv(ob, sequence.rotor, faces)
            ob['blade_recon_role'], ob['blade_recon_blade'] = 'estimate', b
            _hide_stale(ob)
            blades.append(ob)
            if sequence.truth_states is not None:
                ghost = _mesh(scene, prefix+'.Truth.B'+str(b+1), vertices[b].reshape(-1,3), faces, ghost_mat)
                ghost.display_type, ghost.show_in_front, ghost.hide_render = 'WIRE', True, True
                ghost['blade_recon_role'], ghost['blade_recon_blade'] = 'truth', b
                _hide_stale(ghost)
                ghosts.append(ghost)
        cameras = _import_cameras(scene, sequence, prefix)
        radius, height = sequence.rotor.cfg.tip_radius_m, sequence.rotor.hub[2]
        target = (sequence.rotor.hub[0], 0, height*.80)
        for name, position in (('FRONT',(radius*4.8,0,height*.85)),
                               ('SIDE',(0,-radius*4.8,height)),
                               ('OVERVIEW',(radius*4.8,-radius*3.0,height*1.4))):
            cam = _camera(scene, prefix+'.View.'+name, position, target)
            cam['blade_recon_view'] = name
        SESSIONS[scene.as_pointer()] = dict(sequence=sequence, blades=blades, ghosts=ghosts,
                                          cameras=cameras, pending=True, index=None)
        if context.window and context.window.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        if context.window:
            context.window.scene = scene
        scene.wfrl_recon_truth_path = str(Path(truth_path).resolve()) if truth_path else ''
        scene.wfrl_recon_cameras_path = str(Path(cameras_path).resolve()) if cameras_path else ''
        scene.wfrl_recon_texture_path = str(textures.path) if textures else ''
        apply(scene)
        set_view(context, 'FRONT')
        return scene
    except Exception:
        if context.window and context.window.scene == scene:
            context.window.scene = previous_scene
            restore_viewports(context, previous_views)
        remove(scene)
        raise


def apply(scene):
    value = session(scene)
    if value is None:
        return
    seq = value['sequence']
    index = min(max(scene.frame_current-1, 0), len(seq.states)-1)
    vertices, _ = seq.geometry(index)
    truth = seq.truth_geometry(index)
    for b, obj in enumerate(value['blades']):
        obj.data.vertices.foreach_set('co', vertices[b].reshape(-1).astype(np.float32))
        colors = np.where(np.repeat(seq.observed[index,b], seq.rotor.cfg.n_ring)[:,None], GREEN, ORANGE)
        obj.data.color_attributes['evidence'].data.foreach_set('color', colors.astype(np.float32).reshape(-1))
        obj.data.update()
        obj['blade_recon_applied_frame'] = scene.frame_current
        # A UI redraw may already have cached the object's hidden base while
        # frame_set evaluated the stale-frame driver. Updating a custom property
        # and mesh alone does not invalidate that viewport visibility cache.
        # Tag the object and animation dependencies after committing the frame;
        # the normal graph evaluation then restores visibility without recursive
        # frame_set calls or removing the stale-frame guard.
        obj.update_tag(refresh={'OBJECT', 'TIME'})
        visibility = obj.data.materials[0].node_tree.nodes.get('TextureVisibility')
        if visibility:
            visibility.inputs[1].default_value = float(scene.wfrl_recon_show_texture)
    _inspection_changed(scene, None)
    for b, obj in enumerate(value['ghosts']):
        visible = scene.wfrl_recon_show_truth and truth is not None
        obj.hide_set(not visible, view_layer=scene.view_layers[0])
        if visible:
            obj.data.vertices.foreach_set('co', truth[0][b].reshape(-1).astype(np.float32))
            obj.data.update()
        obj['blade_recon_applied_frame'] = scene.frame_current
        obj.update_tag(refresh={'OBJECT', 'TIME'})
    for obj in value['cameras']:
        if seq.camera_frames:
            current = next(c for c in seq.camera_frames[index] if c['name'] == obj['blade_recon_camera_name'])
            _apply_camera(obj, current)
        obj.hide_set(not scene.wfrl_recon_show_cameras, view_layer=scene.view_layers[0])
    value.update(index=index, pending=False)
    scene['blade_recon_error'] = ''
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                area.tag_redraw()


def _toggle(scene, context):
    value = session(scene)
    if value:
        value['pending'] = True


@persistent
def _frame(scene, *_):
    value = session(scene)
    if value:
        value['pending'] = True


@persistent
def _loaded(_):
    SESSIONS.clear()
    # Restoring data and meshes is deferred out of file-load callbacks.
    for scene in bpy.data.scenes:
        if active(scene):
            scene['blade_recon_error'] = '正在恢复重建回放…'
            scene.pop('blade_recon_restore_failed', None)


def _tick():
    for scene in tuple(bpy.data.scenes):
        if not active(scene):
            continue
        try:
            value = session(scene)
            if value is None:
                if scene.get('blade_recon_restore_failed'):
                    continue
                _restore(scene)
                value = session(scene)
            if value['pending'] or value['index'] != min(max(scene.frame_current-1,0), len(value['sequence'].states)-1):
                apply(scene)
        except Exception as exc:
            scene['blade_recon_error'] = str(exc)
            scene['blade_recon_restore_failed'] = True
            SESSIONS.pop(scene.as_pointer(), None)
            for obj in scene.objects:
                if obj.get('blade_recon_role') in {'estimate','truth'}:
                    obj.hide_set(True, view_layer=scene.view_layers[0])
    return .02


def set_view(context, name, areas=None, scene=None):
    scene = scene or context.scene
    camera = next((o for o in scene.objects if o.get('blade_recon_view') == name), None)
    if camera is None:
        return
    scene.camera = camera
    target = camera.get('blade_recon_view_target')
    if target is None:
        value = session(scene)
        if value is None:
            return
        hub = value['sequence'].rotor.hub
        target = (hub[0], 0, hub[2]*.80)
    target = Vector(target)
    for area in _view_areas(context, areas):
        space = area.spaces.active
        space.show_region_ui = True
        space.use_local_camera = False
        space.camera = camera
        space.region_3d.view_perspective = 'PERSP'
        space.region_3d.view_location = target
        space.region_3d.view_rotation = camera.rotation_euler.to_quaternion()
        space.region_3d.view_distance = (camera.location-target).length
        space.lens = camera.data.lens
        space.clip_end = 3000
        space.overlay.show_floor = False
        space.overlay.show_axis_x = space.overlay.show_axis_y = False
        space.shading.type = 'MATERIAL' if scene.get('texture_text') else 'SOLID'
        space.shading.light = 'STUDIO'
        space.shading.color_type = 'VERTEX'
        space.shading.show_shadows = False
        space.shading.background_type = 'VIEWPORT'
        space.shading.background_color = (.045,.06,.085)
        area.tag_redraw()


def remove(scene):
    SESSIONS.pop(scene.as_pointer(), None)
    for obj in list(scene.objects):
        if not obj.get('blade_recon_owned'):
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if isinstance(data,bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data,bpy.types.Camera):
                bpy.data.cameras.remove(data)
    for key in ('recon','truth','cameras','texture'):
        name = scene.get(key+'_text')
        block = bpy.data.texts.get(name) if name else None
        if block and block.get('blade_recon_owned'):
            bpy.data.texts.remove(block)
    bpy.data.scenes.remove(scene)
    # Only unused resources allocated by this feature are reclaimed. Objects in
    # another scene and unrelated materials/images are never touched.
    for mat in tuple(bpy.data.materials):
        if mat.get('blade_recon_owned') and mat.users == 0:
            bpy.data.materials.remove(mat)
    for image in tuple(bpy.data.images):
        if image.get('blade_recon_owned') and image.users == 0:
            bpy.data.images.remove(image)


def _hud():
    scene = bpy.context.scene
    if not active(scene):
        return
    import blf
    value = session(scene)
    lines = ['BLADE RECON  /  '+('v0.2 surface texture' if scene.get('texture_text') else 'geometry')]
    if value and value['index'] is not None and value['index'] == scene.frame_current-1:
        seq, i = value['sequence'], value['index']
        label = 'SYNTHETIC SAMPLE' if 'SYNTHETIC' in seq.source_label.upper() else 'EXTERNAL RECONSTRUCTION'
        lines += [f'{label}  |  {i+1}/{len(seq.states)}  |  t = {seq.times[i]:.2f} s',
                  'GREEN: silhouette constraint   ORANGE: model inference']
        if scene.get('texture_text') and scene.wfrl_recon_show_texture:
            mode = scene.wfrl_recon_texture_mode
            if mode != 'ORIGINAL':
                lines += ['CYAN: brighter   MAGENTA: darker   (appearance only)' if mode == 'FALSE_COLOR'
                          else 'Contrast enhanced display; original texture preserved']
        if scene.wfrl_recon_inspect_box:
            lines += ['Inspection box: manually selected region']
    else:
        lines += [scene.get('blade_recon_error') or 'Updating selected frame...']
    for i, text in enumerate(lines):
        # Keep the legend clear of Blender's headers, tool shelf and view labels.
        blf.position(0, 140, 130+(len(lines)-1-i)*25, 0)
        blf.size(0, 16 if i else 21)
        blf.color(0, .88,.93,.98,1)
        blf.draw(0,text)


def register():
    global HUD_HANDLE
    definitions = {
        'wfrl_recon_truth_path': bpy.props.StringProperty(name='真值 JSON（可选）',subtype='FILE_PATH'),
        'wfrl_recon_cameras_path': bpy.props.StringProperty(name='相机 JSON（可选）',subtype='FILE_PATH'),
        'wfrl_recon_texture_path': bpy.props.StringProperty(name='纹理目录（可选）',subtype='DIR_PATH'),
        'wfrl_recon_show_texture': bpy.props.BoolProperty(name='表面纹理',default=True,update=_toggle),
        'wfrl_recon_show_truth': bpy.props.BoolProperty(name='真值线框',default=False,update=_toggle),
        'wfrl_recon_show_cameras': bpy.props.BoolProperty(name='三摄位置',default=False,update=_toggle),
        'wfrl_recon_texture_mode': bpy.props.EnumProperty(name='显示方式',
            items=(('ORIGINAL','原始纹理','显示保存的原始纹理'),
                   ('CONTRAST','增强灰度','提高表面亮暗对比度'),
                   ('FALSE_COLOR','青／洋红增强','青色表示偏亮，洋红表示偏暗；不是缺陷分类')),
            default='ORIGINAL',update=_inspection_changed),
        'wfrl_recon_texture_gain': bpy.props.FloatProperty(name='增强强度',default=6.,min=1.,max=20.,update=_inspection_changed),
        'wfrl_recon_texture_threshold': bpy.props.FloatProperty(name='亮暗阈值',default=.06,min=.005,max=.3,precision=3,update=_inspection_changed),
        'wfrl_recon_inspect_blade': bpy.props.EnumProperty(name='检查叶片',
            items=(('1','叶片 1',''),('2','叶片 2',''),('3','叶片 3','')),default='1',update=_inspection_changed),
        'wfrl_recon_inspect_radius': bpy.props.FloatProperty(name='距轮毂 / m',default=59.3,min=0.,max=1000.,precision=2,update=_inspection_changed),
        'wfrl_recon_inspect_tau': bpy.props.FloatProperty(name='表面位置',description='沿截面周线选择检查点，0 与 1 为同一接缝',
            default=.60,min=0.,max=1.,precision=3,update=_inspection_changed),
        'wfrl_recon_inspect_size': bpy.props.FloatProperty(name='局部范围 / m',default=2.,min=.3,max=20.,update=_inspection_changed),
        'wfrl_recon_inspect_box': bpy.props.BoolProperty(name='检查框',description='标出手动选中的检查区域，不表示缺陷检测结果',
            default=False,update=_inspection_changed),
    }
    for name, prop in definitions.items():
        setattr(bpy.types.Scene,name,prop)
    for handlers, handler in ((bpy.app.handlers.frame_change_post,_frame),(bpy.app.handlers.load_post,_loaded)):
        if handler not in handlers:
            handlers.append(handler)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick,first_interval=.02,persistent=True)
    if not bpy.app.background and HUD_HANDLE is None:
        HUD_HANDLE = bpy.types.SpaceView3D.draw_handler_add(_hud,(),'WINDOW','POST_PIXEL')


def unregister():
    global HUD_HANDLE
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for handlers, handler in ((bpy.app.handlers.frame_change_post,_frame),(bpy.app.handlers.load_post,_loaded)):
        if handler in handlers:
            handlers.remove(handler)
    if HUD_HANDLE is not None:
        bpy.types.SpaceView3D.draw_handler_remove(HUD_HANDLE,'WINDOW')
        HUD_HANDLE = None
    SESSIONS.clear()
    for name in ('wfrl_recon_truth_path','wfrl_recon_cameras_path','wfrl_recon_texture_path',
                 'wfrl_recon_show_texture','wfrl_recon_show_truth','wfrl_recon_show_cameras') + INSPECTION_PROPERTIES:
        if hasattr(bpy.types.Scene,name):
            delattr(bpy.types.Scene,name)
