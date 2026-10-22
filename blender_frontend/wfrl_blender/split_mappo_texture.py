"""Held 601-sample reconstruction and source-image atlas on the MAPPO clock.

The atlases are fixed offline fusions of the original two-camera RGB images.
They follow saved estimated geometry; no reconstruction runs during playback.
The independent synthetic inspection remains a separate scene adapter.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .split_reconstruction_timing import TimingSpec, timing_for_frame, step_sample_frame


PREFIX = 'SplitMappoTexture.'
CAMERA = PREFIX + 'Camera'
MARKER = 'split_mappo_texture_review'
INDEX = 'split_mappo_texture_index'
DETAIL = 'split_mappo_texture_detail'
TRACKED_VIEW = 'split_mappo_texture_last_auto_view'
OFFSET = (10000., 0., 0.)
REFERENCE_OFFSET = (0., 10000., 0.)
STRUCTURE = ('Tower', 'Nacelle', 'Hub')
SOURCE_HASH = 'd3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde'
_SESSIONS = {}
POINTS = {
    'SUPPORTED': ('中段观察点', 2, 47.225, .8216145833333334, 3.),
    'TIP': ('叶尖观察点', 3, 59.975, .77734375, 2.),
}


def package_path():
    return Path(__file__).resolve().parent / 'assets' / 'blade_recon_mappo_tex'


def active(scene):
    return bool(scene and scene.get(MARKER) and scene.get('split_texture_current_source') == 'MAPPO')


def blades(scene):
    """The three estimated meshes carrying the source-image atlases."""
    return [scene.objects.get(name) for name in scene.get('split_mappo_texture_blade_names',
            [PREFIX + f'B{b}' for b in (1, 2, 3)])]


def objects(scene):
    """Complete displayed turbine; the support structure is simulated."""
    return blades(scene) + [scene.objects.get(name) for name in
            scene.get('split_mappo_texture_structure_names', [])]


def camera(scene):
    return scene.objects.get(scene.get('split_mappo_texture_camera_name', CAMERA)) if active(scene) else None


def owned(scene):
    return [obj for obj in scene.objects if obj.name.startswith(PREFIX)]


def _display_matrix(scene, source):
    """Move canonical nacelle-local context to this adapter's display offset."""
    anchor = scene.objects.get('SplitRecon.B1')
    origin = tuple(anchor.matrix_world.translation) if anchor is not None else REFERENCE_OFFSET
    matrix = source.matrix_world.copy()
    matrix.translation = tuple(float(matrix.translation[i]) + OFFSET[i] - origin[i] for i in range(3))
    return matrix


def _ensure_structure(scene, *, cached=False):
    """Share the source T1 model, leaving only the blades as saved estimates."""
    from . import split_turbine_context
    bind = split_turbine_context.refresh if cached else split_turbine_context.ensure
    return bind(scene, prefix=PREFIX + 'Reference.',
        offset=OFFSET, names_key='split_mappo_texture_structure_names')


def _runtime_signature(scene):
    """Cheap saved identity and ID checks, without parsing embedded samples.

    Mesh and shape-key replacement must restore and validate all 601 keys,
    UV/evidence attributes and packed image hashes, even at the same sample.
    File load and undo/redo explicitly discard these RNA references.
    """
    import bpy
    objects = blades(scene)
    cam = scene.objects.get(scene.get('split_mappo_texture_camera_name', CAMERA))
    if cam is None or len(objects) != 3 or any(obj is None or obj.type != 'MESH' for obj in objects):
        raise ValueError('保存的同源纹理网格或相机缺失')
    texts = []
    for key in ('recon', 'provenance', 'texture', 'texture_source'):
        name = scene.get('split_mappo_texture_' + key + '_text')
        block = bpy.data.texts.get(name or '')
        texts.append((name, block.as_pointer() if block is not None else None,
                      scene.get('split_mappo_texture_' + key + '_sha256')))
    return (scene.get('wfrl_farm_manifest_sha256'), scene.get('wfrl_farm_flex_path'),
            scene.get('split_mappo_texture_model_sha256'),
            tuple(scene.get('split_mappo_texture_blade_names', ())),
            scene.get('split_mappo_texture_camera_name'), cam.as_pointer(),
            tuple((obj.as_pointer(), obj.data.as_pointer(),
                   obj.data.shape_keys.as_pointer() if obj.data.shape_keys is not None else None,
                   len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys is not None else 0,
                   len(obj.data.vertices))
                  for obj in objects), tuple(texts))


def _runtime(scene):
    """Refresh changed IDs without activating display settings on every tick."""
    value = _SESSIONS.get(scene.as_pointer())
    if value is not None:
        try:
            current = _runtime_signature(scene)
        except ReferenceError:
            current = None
        if current != value.get('binding'):
            value = None
    if value is None:
        value = _restore(scene)
    _validate_source(scene, value['provenance'], check_files=False)
    return value


def refresh(scene):
    """Validate cached identities and commit the held sample during polling."""
    value = _runtime(scene)
    value['structure'] = _ensure_structure(scene, cached=True)
    # Validate mutable embedded source/atlas metadata without reparsing JSON.
    # The multi-megabyte reconstruction and packed pixels are checked by full
    # restore after a binding change or load/history boundary.
    for key in ('provenance', 'texture', 'texture_source'):
        _read(scene, key)
    # Check file metadata only on the .25-second poll, not every displayed
    # frame. Changed/missing source files must pass the original hash checks.
    if _source_signature(value['source_root']) != value['source_signature']:
        root = _validate_source(scene, value['provenance'])
        value['source_root'], value['source_signature'] = root, _source_signature(root)
    return apply(scene, value=value)


def _source_signature(root):
    result = []
    for name in ('manifest.json', 'geometry.npz', 'blade-reference.json'):
        try:
            stat = (root / name).stat()
            result.append((stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except OSError:
            result.append(None)
    return tuple(result)


def _create_camera(scene, review, target, ortho_scale):
    """Start with the canonical comparison camera when it is available."""
    source = scene.objects.get('SplitRecon.Camera')
    if source is None:
        cam = review._camera(scene, CAMERA, target+(300., 0., 0.), target)
        cam.data.type, cam.data.ortho_scale = 'ORTHO', ortho_scale
    else:
        cam = source.copy()
        cam.data = source.data.copy()
        cam.name = CAMERA
        cam.parent = None
        cam.animation_data_clear()
        cam.matrix_world = _display_matrix(scene, source)
        scene.collection.objects.link(cam)
    cam['split_texture_owned'] = True
    cam['split_mappo_texture_owned'] = True
    return cam


def _embed(scene, key, raw):
    import bpy
    text = bpy.data.texts.new(PREFIX + scene.name + '.' + key)
    text.write(raw)
    text.use_fake_user = True
    text['split_mappo_texture_owned'] = True
    scene['split_mappo_texture_' + key + '_text'] = text.name
    scene['split_mappo_texture_' + key + '_sha256'] = hashlib.sha256(raw.encode()).hexdigest()


def _read(scene, key):
    import bpy
    block = bpy.data.texts.get(scene.get('split_mappo_texture_' + key + '_text', ''))
    raw = block.as_string() if block else ''
    if not raw or hashlib.sha256(raw.encode()).hexdigest() != scene.get('split_mappo_texture_' + key + '_sha256'):
        raise ValueError('保存的同源纹理 ' + key + ' 数据校验失败')
    return raw


def _validate_clock(sequence):
    if (len(sequence.states) != 601 or sequence.fps != 10. or
            not np.allclose(sequence.times, np.arange(601)/10., rtol=0, atol=1e-9) or
            not np.array_equal(sequence.source_frames, np.arange(601)) or
            sequence.rotor.cfg.n_sections != 40 or sequence.rotor.cfg.n_ring != 32):
        raise ValueError('同源纹理必须使用601个10Hz实际保存样本及40×32前向模型')
    if len(sequence.frames) != 601:
        raise ValueError('同源纹理保存样本时间映射数量不匹配')
    for i, frame in enumerate(sequence.frames):
        if not isinstance(frame, dict):
            raise ValueError('同源纹理保存样本时间映射格式错误')
        sim_t, blender_frame = frame.get('sim_t'), frame.get('blender_frame')
        if (type(sim_t) not in (int, float) or not np.isfinite(sim_t) or abs(sim_t - (117. + i/10.)) > 1e-9 or
                type(blender_frame) is not int or blender_frame != 1 + 6*i):
            raise ValueError('同源纹理保存样本的 sim_t 或 blender_frame 时间映射缺失或不匹配')
    return TimingSpec()


def _validate_source(scene, provenance, *, check_files=True):
    from .blade_recon_data import MODEL_SHA256
    if scene.get('wfrl_blade_model', 'nrel5mw') != 'nrel5mw':
        raise ValueError('同源纹理对照需要 NREL 叶片外形，请先切回 NREL 5MW')
    source_files = provenance.get('source_package_hashes', {})
    expected = provenance.get('mappo_manifest_sha256', source_files.get('assets/mappo/manifest.json'))
    if expected != SOURCE_HASH or scene.get('wfrl_farm_manifest_sha256') != expected:
        raise ValueError('左侧 MAPPO 数据与同源纹理的保存来源不匹配，请载入随包 MAPPO 60秒结果')
    if provenance.get('source_classification') != 'SAME_SOURCE_SIMULATION':
        raise ValueError('同源纹理来源分类缺失或不匹配')
    model = provenance.get('model')
    if not isinstance(model, dict) or model.get('sha256') != MODEL_SHA256:
        raise ValueError('同源纹理来源前向模型身份缺失或不匹配')
    clock = provenance.get('clock', {})
    if not isinstance(clock, dict):
        raise ValueError('同源纹理来源时间映射缺失或不匹配')
    expected_clock = dict(samples=601, fps=10., source_hz=40., time_start_s=0., time_end_s=60., source_frame_start=0, source_frame_end=600,
        simulation_start_s=117., simulation_end_s=177., timeline_fps=60., stride=6, frame_start=1, frame_end=3601)
    integers = {'samples', 'source_frame_start', 'source_frame_end', 'stride', 'frame_start', 'frame_end'}
    if any(type(clock.get(name)) not in ((int,) if name in integers else (int, float)) or
           clock.get(name) != value for name, value in expected_clock.items()):
        raise ValueError('同源纹理来源时间映射缺失或不匹配')
    if check_files:
        from . import farm_flex
        root = farm_flex.saved_package(scene)
        if root is None:
            raise ValueError('左侧同源 MAPPO 包无法定位')
        for name in ('manifest.json', 'geometry.npz', 'blade-reference.json'):
            digest = source_files.get('assets/mappo/' + name)
            try:
                actual = hashlib.sha256((root/name).read_bytes()).hexdigest()
            except OSError as exc:
                raise ValueError('左侧 MAPPO 来源文件无法读取：' + name) from exc
            if not digest or actual != digest:
                raise ValueError('左侧 MAPPO 来源文件与原图采集不一致：' + name)
        return root


def _restore(scene):
    from .blade_recon_data import ReconSequence
    raw = _read(scene, 'recon')
    provenance = json.loads(_read(scene, 'provenance'))
    source_root = _validate_source(scene, provenance)
    if hashlib.sha256(raw.encode()).hexdigest() != provenance['files']['recon.json']['sha256']:
        raise ValueError('保存的同源重建与来源文件身份不一致')
    texture_source = _read(scene, 'texture_source')
    if hashlib.sha256(texture_source.encode()).hexdigest() != provenance['files']['texture/texture.json']['sha256']:
        raise ValueError('保存的同源图集元数据与来源文件身份不一致')
    sequence = ReconSequence.from_data(json.loads(raw), source_label=provenance['source_label'],
        source_sha256=scene['split_mappo_texture_recon_sha256'])
    _validate_clock(sequence)
    if sequence.model_sha256 != scene.get('split_mappo_texture_model_sha256'):
        raise ValueError('同源纹理前向模型与保存结果不一致')
    saved_blades = blades(scene)
    saved_camera = scene.objects.get(scene.get('split_mappo_texture_camera_name', CAMERA))
    if saved_camera is None or len(saved_blades) != 3 or any(obj is None or obj.type != 'MESH' for obj in saved_blades):
        raise ValueError('保存的同源纹理网格或相机缺失')
    manifest = json.loads(_read(scene, 'texture'))
    if not isinstance(manifest.get('atlas'), dict) or len(manifest.get('files', [])) != 3:
        raise ValueError('保存的同源纹理元数据不完整')
    if manifest['atlas'] != json.loads(texture_source)['atlas']:
        raise ValueError('保存的同源纹理图集合同不一致')
    for blade, record in zip(saved_blades, manifest['files']):
        if record['sha256'] != provenance['files']['texture/'+record['name']]['sha256']:
            raise ValueError('保存的同源图像与来源文件身份不一致')
        keys = blade.data.shape_keys
        if (len(blade.data.vertices) != 40*32 or keys is None or keys.use_relative or
                len(keys.key_blocks) != 601 or keys.animation_data is None):
            raise ValueError('保存的同源纹理601个绝对几何样本缺失')
        if blade.data.uv_layers.get('atlas') is None or blade.data.color_attributes.get('evidence') is None:
            raise ValueError('保存的同源纹理 UV 或观测约束缺失')
        material = blade.data.materials[0] if len(blade.data.materials) else None
        tex = material.node_tree.nodes.get('SurfaceTexture') if material and material.use_nodes else None
        image = tex.image if tex else None
        if image is None or image.packed_file is None:
            raise ValueError('保存的同源纹理图像没有打包')
        if tuple(image.size) != (manifest['atlas']['W'], manifest['atlas']['H']):
            raise ValueError('保存的同源纹理图像尺寸不匹配')
        if hashlib.sha256(image.packed_file.data).hexdigest() != record['sha256']:
            raise ValueError('保存的同源纹理图像校验失败')
    structure = _ensure_structure(scene)
    value = dict(sequence=sequence, blades=saved_blades, structure=structure, index=None, tracking={}, provenance=provenance,
                 source_root=source_root, source_signature=_source_signature(source_root))
    value['binding'] = _runtime_signature(scene)
    _SESSIONS[scene.as_pointer()] = value
    return value


def _display_defaults(provenance):
    point = provenance.get('inspection_default') or next(iter(provenance.get('inspection_points', [])), {})
    return dict(wfrl_recon_show_texture=True, wfrl_recon_texture_mode='ORIGINAL',
        wfrl_recon_texture_gain=6., wfrl_recon_texture_threshold=.06,
        wfrl_recon_inspect_blade=str(point.get('blade', 1)),
        wfrl_recon_inspect_radius=float(point.get('radius_m', 36.)),
        wfrl_recon_inspect_tau=float(point.get('tau', .6)),
        wfrl_recon_inspect_size=float(point.get('size_m', 8.)), wfrl_recon_inspect_box=True)


def _activate(scene, value, *, new=False):
    from . import split_texture_settings as settings
    def commit():
        import bpy
        _release_tracking(scene, value)
        apply(scene, value=value)
        if new and bpy.context.scene == scene:
            bpy.context.view_layer.update()
        return value
    return settings.activate(scene, 'MAPPO', _display_defaults(value['provenance']), commit, new=new)


def ensure(scene):
    """Validate the matching saved source, then restore or import it once."""
    import bpy
    from . import blade_recon_review as review
    from .blade_recon_data import ReconSequence, TexturePackage
    if scene.get(MARKER):
        value = _runtime(scene)
        value['structure'] = _ensure_structure(scene)
        return _activate(scene, value)
    root = package_path()
    provenance_raw = (root/'manifest.json').read_text(encoding='utf-8')
    provenance = json.loads(provenance_raw)
    source_root = _validate_source(scene, provenance)
    required = ('recon.json', 'texture/texture.json', 'texture/blade1_tex.png',
                'texture/blade2_tex.png', 'texture/blade3_tex.png')
    if any(name not in provenance.get('files', {}) for name in required):
        raise ValueError('同源纹理包缺少来源文件记录')
    for name, record in provenance.get('files', {}).items():
        if record.get('sha256') != hashlib.sha256((root/name).read_bytes()).hexdigest():
            raise ValueError('同源纹理来源文件校验失败：' + name)
    sequence = ReconSequence(root/'recon.json')
    spec = _validate_clock(sequence)
    textures = TexturePackage(root/'texture', sequence.rotor)
    raw = sequence.path.read_text(encoding='utf-8')
    faces = sequence.rotor.tpl.faces()
    allocated = []
    try:
        vertices, _ = sequence.geometry(0)
        for b in range(3):
            mat = review._texture_material(PREFIX+f'Material.B{b+1}', textures.images[b], textures.data['atlas'])
            obj = review._mesh(scene, PREFIX+f'B{b+1}', vertices[b].reshape(-1, 3), faces, mat)
            allocated.append(obj)
            obj.location = OFFSET
            obj['split_mappo_texture_owned'] = True
            obj['split_texture_owned'] = True
            obj['split_mappo_texture_blade'] = b
            obj.data.color_attributes.new(name='evidence', type='FLOAT_COLOR', domain='POINT')
            review._add_uv(obj, sequence.rotor, faces)
            obj.shape_key_add(name='Sample0000', from_mix=False)
            obj.data.shape_keys.use_relative = False
        low, high = np.full((3, 3), np.inf), np.full((3, 3), -np.inf)
        for i in range(601):
            sample, _ = sequence.geometry(i)
            xyz = sample.reshape(3, -1, 3)
            low, high = np.minimum(low, xyz.min(axis=1)), np.maximum(high, xyz.max(axis=1))
            for b, obj in enumerate(allocated):
                key = obj.data.shape_keys.key_blocks[0] if i == 0 else obj.shape_key_add(name=f'Sample{i:04d}', from_mix=False)
                key.data.foreach_set('co', sample[b].reshape(-1).astype(np.float32))
                key.interpolation = 'KEY_LINEAR'
                keys = obj.data.shape_keys
                keys.eval_time = key.frame
                keys.keyframe_insert(data_path='eval_time', frame=spec.frame_start + spec.stride*i)
        for b, obj in enumerate(allocated):
            keys = obj.data.shape_keys
            for layer in keys.animation_data.action.layers:
                for strip in layer.strips:
                    bag = strip.channelbag(keys.animation_data.action_slot)
                    if bag:
                        for curve in bag.fcurves:
                            curve.extrapolation = 'CONSTANT'
                            for point in curve.keyframe_points:
                                point.interpolation = 'CONSTANT'
            obj['split_texture_bounds'] = low[b].tolist() + high[b].tolist()
            obj['split_mappo_texture_native_samples'] = 601
        structure = _ensure_structure(scene)
        allocated.extend(structure)
        target = np.array(OFFSET) + sequence.rotor.hub
        cam = _create_camera(scene, review, target, sequence.rotor.cfg.tip_radius_m*2.35)
        allocated.append(cam)
        _embed(scene, 'recon', raw)
        _embed(scene, 'texture', json.dumps(textures.manifest, ensure_ascii=False, sort_keys=True))
        _embed(scene, 'texture_source', (textures.path/'texture.json').read_text(encoding='utf-8'))
        _embed(scene, 'provenance', provenance_raw)
        scene['split_mappo_texture_source_label'] = sequence.source_label
        scene['split_mappo_texture_blade_names'] = [obj.name for obj in allocated[:3]]
        scene['split_mappo_texture_camera_name'] = cam.name
        scene['split_mappo_texture_model_sha256'] = sequence.model_sha256
        scene['split_mappo_texture_source_path'] = str(sequence.path)
        scene['split_mappo_texture_texture_path'] = str(textures.path)
        scene['split_mappo_texture_clock_relation'] = 'Same saved MAPPO source, 117-177 s; reconstruction held at 10 Hz on fixed 60 Hz timeline'
        scene[DETAIL] = False
        scene[MARKER] = True
        for key, val in [('start_s',117.), ('timeline_fps',60.), ('stride',6), ('samples',601), ('frame_start',1)]:
            scene['split_reconstruction_'+key] = val
        value = dict(sequence=sequence, blades=allocated[:3], structure=structure, index=None, tracking={}, provenance=provenance,
                     source_root=source_root, source_signature=_source_signature(source_root))
        value['binding'] = _runtime_signature(scene)
        _SESSIONS[scene.as_pointer()] = value
        return _activate(scene, value, new=True)
    except Exception:
        from . import split_turbine_context
        split_turbine_context.release(scene, 'split_mappo_texture_structure_names')
        for obj in allocated:
            bpy.data.objects.remove(obj, do_unlink=True)
        scene.pop(MARKER, None)
        scene.pop('split_mappo_texture_structure_names', None)
        scene.pop('split_mappo_texture_structure_names_offset', None)
        scene.pop('split_mappo_texture_structure_names_notice', None)
        scene.pop('split_mappo_texture_structure_notice', None)
        _SESSIONS.pop(scene.as_pointer(), None)
        raise


def apply(scene, *, value=None):
    from . import blade_recon_review as review, blade_recon_inspection as inspection
    value = value or _SESSIONS.get(scene.as_pointer()) or _restore(scene)
    _validate_source(scene, value['provenance'], check_files=False)
    timing = timing_for_frame(scene.frame_current+scene.frame_subframe)
    sequence, index = value['sequence'], timing.index
    if value['index'] != index:
        for b, obj in enumerate(value['blades']):
            keys = obj.data.shape_keys
            expected = keys.key_blocks[index].frame
            if keys.eval_time != expected:
                keys.eval_time = expected
            colors = np.where(np.repeat(sequence.observed[index, b], sequence.rotor.cfg.n_ring)[:, None],
                              review.GREEN, review.ORANGE)
            obj.data.color_attributes['evidence'].data.foreach_set('color', colors.astype(np.float32).reshape(-1))
            obj.data.update()
        value['index'] = index
    if scene.get(INDEX) != index:
        scene[INDEX] = index
    inspection.update(scene, value['blades'], sequence.rotor)
    return value


def information(scene):
    value = _SESSIONS.get(scene.as_pointer())
    if value is None or value['index'] is None:
        return None
    timing = timing_for_frame(scene.frame_current+scene.frame_subframe)
    sequence, index = value['sequence'], value['index']
    return dict(index=index, samples=601, time_s=float(sequence.times[index]),
                sample_time_s=117.+float(sequence.times[index]),
                timeline_time_s=timing.timeline_time_s, age_ms=timing.age_ms,
                in_range=timing.in_range, fps=sequence.fps, source_label=sequence.source_label,
                source_frame=int(sequence.source_frames[index]), synchronized=True)


def step(scene, direction):
    ensure(scene)
    scene.frame_set(step_sample_frame(scene.frame_current+scene.frame_subframe, direction), subframe=0.)
    apply(scene)


def toggle(scene):
    from . import blade_recon_inspection as inspection
    value = ensure(scene)
    return inspection.toggle_texture_mode(scene, value['blades'], value['sequence'].rotor, enhanced='CONTRAST')


def _tracked_surface(scene, value):
    """Cache UV triangle barycentrics; each pose only transforms three vertices."""
    from . import blade_recon_inspection as inspection
    from mathutils import Vector
    sequence = value['sequence']
    b, radius, tau, _size = inspection._coordinates(scene, sequence.rotor)
    obj = value['blades'][b]
    identity = b, radius, tau
    if value['tracking'].get('identity') != identity:
        layer = obj.data.uv_layers['atlas']
        uv = np.array([[tuple(layer.data[i].uv) for i in p.loop_indices] for p in obj.data.polygons])
        targets = np.array([tau, 1.-(radius-sequence.rotor.tpl.r[0])/(sequence.rotor.tpl.r[-1]-sequence.rotor.tpl.r[0])])
        edges = np.stack((uv[:,1]-uv[:,0], uv[:,2]-uv[:,0]), axis=-1)
        valid = np.abs(np.linalg.det(edges)) > 1e-12
        candidates = np.flatnonzero(valid)
        chosen = None
        for shift in (0.,1.,-1.):
            pairs = np.linalg.solve(edges[valid], (targets+[shift,0.]-uv[valid,0])[...,None])[...,0]
            weights = np.column_stack((1.-pairs.sum(axis=1), pairs))
            matches = np.flatnonzero((weights.min(axis=1) >= -1e-6) & (weights.max(axis=1) <= 1.+1e-6))
            if len(matches):
                position = matches[0]
                chosen = candidates[position], weights[position]
                break
        if chosen is None:
            raise ValueError('所选 atlas UV 没有匹配到同源估计叶片表面')
        poly, weights = chosen
        value['tracking'] = dict(identity=identity, vertices=list(obj.data.polygons[poly].vertices), weights=weights)
    vertices, axes = sequence.geometry(value['index'])
    tracking = value['tracking']
    points = vertices[b].reshape(-1,3)[tracking['vertices']]
    point = tracking['weights'] @ points
    normal = np.cross(points[1]-points[0], points[2]-points[0])
    rotor = sequence.rotor
    section = min(max(int(np.searchsorted(rotor.tpl.r, radius))-1,0), len(rotor.tpl.r)-2)
    fraction = (radius-rotor.tpl.r[section])/(rotor.tpl.r[section+1]-rotor.tpl.r[section])
    axis = axes[b,section]*(1.-fraction)+axes[b,section+1]*fraction
    if np.dot(normal,point-axis)<0:
        normal = -normal
    matrix = obj.matrix_world
    point = matrix @ Vector(point)
    normal = matrix.to_3x3().inverted().transposed() @ Vector(normal)
    normal.normalize()
    up = matrix.to_3x3() @ Vector(axes[b,section+1]-axes[b,section])
    up -= normal*up.dot(normal)
    up.normalize()
    return point, normal, up


def _tracking_view(area):
    """Only explicit navigation fields; redraw matrices are Blender-owned."""
    rv = area.spaces.active.region_3d
    return dict(perspective=rv.view_perspective, location=tuple(rv.view_location),
                rotation=tuple(rv.view_rotation), distance=float(rv.view_distance))


def _same_tracking_view(left, right):
    return (left['perspective'] == right['perspective'] and
            all(np.allclose(left[key], right[key], rtol=0, atol=1e-5)
                for key in ('location', 'rotation', 'distance')))


def _release_tracking(scene, value=None):
    """Migrate older saved closeups to user-owned, stationary navigation."""
    scene[DETAIL] = False
    scene.pop(TRACKED_VIEW, None)
    if value is not None:
        value.setdefault('tracked_views', {}).clear()


def track(scene, area, *, value=None, force=False):
    """Compatibility entry: playback never changes the user's view."""
    value = value or _SESSIONS.get(scene.as_pointer())
    _release_tracking(scene, value)


def _focus_surface(scene, area, value):
    """Position once at a manually chosen atlas coordinate, without tracking."""
    from mathutils import Matrix
    from . import blade_recon_inspection as inspection
    point, normal, up = _tracked_surface(scene, value)
    right = up.cross(normal).normalized()
    up = normal.cross(right).normalized()
    rotation = Matrix(((right.x,up.x,normal.x),(right.y,up.y,normal.y),(right.z,up.z,normal.z))).to_quaternion()
    rv, space = area.spaces.active.region_3d, area.spaces.active
    rv.view_perspective, rv.view_location, rv.view_rotation = 'ORTHO', point, rotation
    rv.view_distance = inspection._coordinates(scene, value['sequence'].rotor)[3]*1.35
    space.lens, space.clip_start, space.clip_end = 50., .01, 3000.
    space.overlay.show_overlays = False
    area.tag_redraw()
    return dict(target=tuple(point), normal=tuple(normal), index=value['index'])


def focus(context, area, point=None):
    value = ensure(context.scene)
    if point:
        _label, blade, radius, tau, size = POINTS[point]
        context.scene.wfrl_recon_inspect_blade = str(blade)
        context.scene.wfrl_recon_inspect_radius = radius
        context.scene.wfrl_recon_inspect_tau = tau
        context.scene.wfrl_recon_inspect_size = size
    _release_tracking(context.scene, value)
    # Native overlapping regions animate their visibility. Reassigning this on
    # every frame interrupts that animation and can reopen the sidebar. Only
    # an explicit focus action owns the initial hide; playback preserves it.
    area.spaces.active.show_region_ui = False
    return _focus_surface(context.scene, area, value)


def frame_changed(scene, depsgraph=None):
    """Commit the same held sample on seeks and playback, before redraw."""
    if not active(scene):
        return
    try:
        value = _runtime(scene)
        apply(scene, value=value)
    except (ReferenceError, RuntimeError, ValueError, AttributeError, KeyError) as exc:
        scene['split_review_status'] = 'ERROR: ' + str(exc)


def clear_runtime():
    _SESSIONS.clear()
