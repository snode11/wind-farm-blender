"""Blender adapter for the unchanged blade_recon v0.1 forward model.

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

from .blade_recon_data import ReconSequence, default_recon_path

GREEN = (0.15, 0.75, 0.25, 1.0)
ORANGE = (1.0, 0.55, 0.10, 1.0)
SESSIONS = {}
HUD_HANDLE = None


def active(scene):
    return scene.get('wfrl_scene_kind') == 'blade_recon'


def session(scene):
    return SESSIONS.get(scene.as_pointer())


def _material(name, color, evidence=False):
    mat = bpy.data.materials.new(name)
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
        K = np.array(d['K'])
        data.sensor_fit = 'HORIZONTAL'
        data.sensor_width = 36
        data.lens = float(K[0,0]*36/d['W'])
        data.shift_x = float(((d['W']-1)/2-K[0,2])/d['W'])
        ratio = float(K[0,0]/K[1,1])
        data.shift_y = float((K[1,2]-(d['H']-1)/2)*ratio/d['W'])
        data.clip_start, data.clip_end, data.display_size = .05, 3000, 1.5
        obj = bpy.data.objects.new(data.name, data)
        T = np.eye(4)
        T[:3] = d['T_cv_from_world']
        obj.matrix_world = Matrix((np.linalg.inv(T) @ np.diag([1,-1,-1,1])).tolist())
        scene.collection.objects.link(obj)
        obj.hide_select = True
        obj['blade_recon_owned'] = True
        obj['blade_recon_camera_name'] = d['name']
        obj['K_json'] = json.dumps(d['K'])
        obj['T_json'] = json.dumps(d['T_cv_from_world'])
        obj['W'], obj['H'] = d['W'], d['H']
        imported.append(obj)
    return imported


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
    SESSIONS[scene.as_pointer()] = dict(sequence=sequence, blades=objects, ghosts=ghosts,
        cameras=[o for o in scene.objects if 'blade_recon_camera_name' in o], pending=True, index=None)
    scene['blade_recon_error'] = ''


def load(context, path=None, truth_path=None, cameras_path=None):
    """Validate before allocating anything; preserve the previous scene on failure."""
    path = Path(path or default_recon_path()).expanduser().resolve()
    sequence = ReconSequence(path, truth_path=truth_path, cameras_path=cameras_path)
    raw = path.read_text(encoding='utf-8')
    companions = {}
    for key, explicit in (('truth', truth_path), ('cameras', cameras_path)):
        candidate = Path(explicit).expanduser().resolve() if explicit else path.with_name(key+'.json')
        if candidate.is_file():
            companions[key] = candidate.read_text(encoding='utf-8')
    previous_scene = context.scene
    prefix = 'WFRL.BladeRecon.'+uuid.uuid4().hex[:8]
    scene = bpy.data.scenes.new('叶片三维重建')
    try:
        scene['wfrl_scene_kind'] = 'blade_recon'
        scene['blade_recon_return_scene'] = previous_scene.name
        scene['blade_recon_source_path'] = str(path)
        scene['blade_recon_source_label'] = sequence.source_label
        scene['blade_recon_sha256'] = hashlib.sha256(raw.encode()).hexdigest()
        scene['blade_recon_model_sha256'] = sequence.model_sha256
        scene['blade_recon_error'] = ''
        _embed(scene, prefix+'.recon', raw)
        for key, payload in companions.items():
            _embed(scene, prefix+'.'+key, payload)
            scene['blade_recon_'+key+'_sha256'] = hashlib.sha256(payload.encode()).hexdigest()
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
            ob = _mesh(scene, prefix+'.Recon.B'+str(b+1), vertices[b].reshape(-1,3), faces, mat)
            ob.data.color_attributes.new(name='evidence', type='FLOAT_COLOR', domain='POINT')
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
        apply(scene)
        set_view(context, 'FRONT')
        return scene
    except Exception:
        if context.window and context.window.scene == scene:
            context.window.scene = previous_scene
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
    for b, obj in enumerate(value['ghosts']):
        visible = scene.wfrl_recon_show_truth and truth is not None
        obj.hide_set(not visible, view_layer=scene.view_layers[0])
        if visible:
            obj.data.vertices.foreach_set('co', truth[0][b].reshape(-1).astype(np.float32))
            obj.data.update()
        obj['blade_recon_applied_frame'] = scene.frame_current
    for obj in value['cameras']:
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


def set_view(context, name):
    scene = context.scene
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
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
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
                space.shading.type = 'SOLID'
                space.shading.light = 'STUDIO'
                space.shading.color_type = 'VERTEX'
                space.shading.show_shadows = False
                space.shading.background_type = 'VIEWPORT'
                space.shading.background_color = (.045,.06,.085)
                area.tag_redraw()


def remove(scene):
    SESSIONS.pop(scene.as_pointer(), None)
    for obj in list(scene.objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data and data.users == 0:
            if isinstance(data,bpy.types.Mesh):
                bpy.data.meshes.remove(data)
            elif isinstance(data,bpy.types.Camera):
                bpy.data.cameras.remove(data)
    for key in ('recon','truth','cameras'):
        name = scene.get(key+'_text')
        block = bpy.data.texts.get(name) if name else None
        if block:
            bpy.data.texts.remove(block)
    bpy.data.scenes.remove(scene)


def _hud():
    scene = bpy.context.scene
    if not active(scene):
        return
    import blf
    value = session(scene)
    lines = ['BLADE RECON  /  v0.1']
    if value and value['index'] is not None and value['index'] == scene.frame_current-1:
        seq, i = value['sequence'], value['index']
        label = 'SYNTHETIC SAMPLE' if 'SYNTHETIC' in seq.source_label.upper() else 'EXTERNAL RECONSTRUCTION'
        lines += [f'{label}  |  {i+1}/{len(seq.states)}  |  t = {seq.times[i]:.2f} s',
                  'GREEN: silhouette constraint   ORANGE: model inference']
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
        'wfrl_recon_show_truth': bpy.props.BoolProperty(name='真值线框',default=False,update=_toggle),
        'wfrl_recon_show_cameras': bpy.props.BoolProperty(name='三摄位置',default=False,update=_toggle),
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
    for name in ('wfrl_recon_truth_path','wfrl_recon_cameras_path','wfrl_recon_show_truth','wfrl_recon_show_cameras'):
        if hasattr(bpy.types.Scene,name):
            delattr(bpy.types.Scene,name)
