"""Independent synthetic surface inspection inside the existing MAPPO split.

The right sample cursor deliberately does not use the MAPPO scene frame.  The
120 synthetic samples and the MAPPO result have different origins and clocks.
JSON and the original RGBA images are embedded for saved-file restoration.
"""
from __future__ import annotations

import hashlib
import json


PREFIX = 'SplitTexture.'
CAMERA = PREFIX + 'Camera'
MARKER = 'split_texture_review'
INDEX = 'split_texture_index'
DETAIL = 'split_texture_detail'
OFFSET = (10000., 0., 0.)
_SESSIONS = {}
POINTS = {
    'BRIGHT': ('亮斑', 59.3, .60),
    'HORIZONTAL': ('横细线', 57.0, .65),
    'VERTICAL': ('纵细线', 51.3, .645),
}
DISPLAY_DEFAULTS = dict(
    wfrl_recon_show_texture=True, wfrl_recon_texture_mode='FALSE_COLOR',
    wfrl_recon_texture_gain=6., wfrl_recon_texture_threshold=.06,
    wfrl_recon_inspect_blade='1', wfrl_recon_inspect_radius=59.3,
    wfrl_recon_inspect_tau=.60, wfrl_recon_inspect_size=1.6, wfrl_recon_inspect_box=True,
)


def active(scene):
    return bool(scene and scene.get(MARKER) and scene.get('split_texture_current_source', 'SYNTHETIC') == 'SYNTHETIC')


def objects(scene):
    names = scene.get('split_texture_blade_names', [PREFIX + f'B{i}' for i in (1, 2, 3)])
    return [scene.objects.get(name) for name in names]


def camera(scene):
    return scene.objects.get(scene.get('split_texture_camera_name', CAMERA)) if active(scene) else None


def owned(scene):
    return [obj for obj in scene.objects if obj.name.startswith(PREFIX)]


def _embed(scene, key, raw):
    import bpy
    text = bpy.data.texts.new(PREFIX + scene.name + '.' + key)
    text.write(raw)
    text.use_fake_user = True
    text['split_texture_owned'] = True
    scene['split_texture_' + key + '_text'] = text.name
    scene['split_texture_' + key + '_sha256'] = hashlib.sha256(raw.encode()).hexdigest()


def _read(scene, key):
    import bpy
    text = bpy.data.texts.get(scene.get('split_texture_' + key + '_text', ''))
    raw = text.as_string() if text else ''
    if not raw or hashlib.sha256(raw.encode()).hexdigest() != scene.get('split_texture_' + key + '_sha256'):
        raise ValueError('保存的表面纹理 ' + key + ' 数据校验失败')
    return raw


def _restore(scene):
    from .blade_recon_data import ReconSequence
    raw = _read(scene, 'recon')
    sequence = ReconSequence.from_data(json.loads(raw),
        source_label=scene.get('split_texture_source_label', ''),
        source_sha256=scene['split_texture_recon_sha256'])
    if sequence.model_sha256 != scene.get('split_texture_model_sha256'):
        raise ValueError('表面纹理前向模型与保存结果不一致')
    blades = objects(scene)
    saved_camera = scene.objects.get(scene.get('split_texture_camera_name', CAMERA))
    if saved_camera is None or len(blades) != 3 or any(obj is None or obj.type != 'MESH' for obj in blades):
        raise ValueError('保存的表面纹理网格或相机缺失')
    manifest = json.loads(_read(scene, 'texture'))
    if (not isinstance(manifest, dict) or not isinstance(manifest.get('atlas'), dict) or
            not isinstance(manifest.get('files'), list) or len(manifest['files']) != 3 or
            not all(isinstance(record, dict) and record.get('sha256') for record in manifest['files']) or
            not all(isinstance(manifest['atlas'].get(key), int) and manifest['atlas'][key] > 0
                    for key in ('W', 'H'))):
        raise ValueError('保存的表面纹理元数据不完整')
    for blade, record in zip(blades, manifest['files']):
        if len(blade.data.vertices) != sequence.rotor.cfg.n_sections * sequence.rotor.cfg.n_ring:
            raise ValueError('保存的表面纹理网格采样不匹配')
        if blade.data.uv_layers.get('atlas') is None:
            raise ValueError('保存的表面纹理 UV 缺失')
        evidence = blade.data.color_attributes.get('evidence')
        if evidence is None or len(evidence.data) != len(blade.data.vertices):
            raise ValueError('保存的表面纹理观测证据缺失')
        material = blade.data.materials[0] if len(blade.data.materials) else None
        if material is None or not material.use_nodes or material.node_tree is None:
            raise ValueError('保存的表面纹理材质缺失')
        tex = material.node_tree.nodes.get('SurfaceTexture')
        image = tex.image if tex else None
        if image is None or image.packed_file is None:
            raise ValueError('保存的表面纹理没有打包')
        if tuple(image.size) != (manifest['atlas']['W'], manifest['atlas']['H']):
            raise ValueError('保存的表面纹理尺寸不匹配')
        if hashlib.sha256(image.packed_file.data).hexdigest() != record['sha256']:
            raise ValueError('保存的表面纹理图像校验失败')
    value = dict(sequence=sequence, blades=blades, index=None)
    _SESSIONS[scene.as_pointer()] = value
    return value


def _activate(scene, value, *, new=False):
    from . import split_texture_settings as settings
    def commit():
        import bpy
        apply(scene, value=value)
        if new and bpy.context.scene == scene:
            bpy.context.view_layer.update()
        return value
    return settings.activate(scene, 'SYNTHETIC', DISPLAY_DEFAULTS, commit, new=new)


def ensure(scene):
    """Load the designated, matching package once; never replace saved inputs."""
    import bpy
    import numpy as np
    from . import blade_recon_review as review
    from .blade_recon_data import ReconSequence, TexturePackage, detail_recon_path, detail_texture_path
    if scene.get(MARKER):
        value = _SESSIONS.get(scene.as_pointer()) or _restore(scene)
        return _activate(scene, value)
    sequence = ReconSequence(detail_recon_path())
    textures = TexturePackage(detail_texture_path(), sequence.rotor)
    raw = sequence.path.read_text(encoding='utf-8')
    vertices, _ = sequence.geometry(16 if len(sequence.states) > 16 else 0)
    low, high = np.full((3, 3), np.inf), np.full((3, 3), -np.inf)
    for sample_index in range(len(sequence.states)):
        sample, _ = sequence.geometry(sample_index)
        xyz = sample.reshape(3, -1, 3)
        low, high = np.minimum(low, xyz.min(axis=1)), np.maximum(high, xyz.max(axis=1))
    faces = sequence.rotor.tpl.faces()
    allocated = []
    try:
        for index in range(3):
            material = review._texture_material(PREFIX + f'Material.B{index+1}',
                                               textures.images[index], textures.data['atlas'])
            obj = review._mesh(scene, PREFIX + f'B{index+1}', vertices[index].reshape(-1, 3), faces, material)
            allocated.append(obj)
            obj.location = OFFSET
            obj['split_texture_owned'] = True
            obj['split_texture_blade'] = index
            obj['split_texture_bounds'] = low[index].tolist() + high[index].tolist()
            obj.data.color_attributes.new(name='evidence', type='FLOAT_COLOR', domain='POINT')
            review._add_uv(obj, sequence.rotor, faces)
        target = np.array(OFFSET) + sequence.rotor.hub
        cam = review._camera(scene, CAMERA, target + (300., 0., 0.), target)
        allocated.append(cam)
        cam['split_texture_owned'] = True
        cam.data.type = 'ORTHO'
        cam.data.ortho_scale = sequence.rotor.cfg.tip_radius_m * 2.35
        _embed(scene, 'recon', raw)
        _embed(scene, 'texture', json.dumps(textures.manifest, ensure_ascii=False, sort_keys=True))
        scene['split_texture_source_label'] = sequence.source_label
        scene['split_texture_blade_names'] = [obj.name for obj in allocated[:3]]
        scene['split_texture_camera_name'] = cam.name
        scene['split_texture_source_path'] = str(sequence.path)
        scene['split_texture_texture_path'] = str(textures.path)
        scene['split_texture_model_sha256'] = sequence.model_sha256
        scene['split_texture_samples'] = len(sequence.states)
        scene['split_texture_fps'] = sequence.fps
        scene['split_texture_time_start_s'] = float(sequence.times[0])
        scene['split_texture_time_end_s'] = float(sequence.times[-1])
        scene['split_texture_clock_relation'] = 'Independent synthetic sample cursor; not synchronized MAPPO reconstruction'
        scene[INDEX] = min(16, len(sequence.states)-1)
        scene[DETAIL] = True
        scene[MARKER] = True
        value = dict(sequence=sequence, blades=allocated[:3], index=None)
        _SESSIONS[scene.as_pointer()] = value
        return _activate(scene, value, new=True)
    except Exception:
        for obj in allocated:
            bpy.data.objects.remove(obj, do_unlink=True)
        scene.pop(MARKER, None)
        _SESSIONS.pop(scene.as_pointer(), None)
        raise


def apply(scene, *, value=None):
    import numpy as np
    from . import blade_recon_review as review, blade_recon_inspection as inspection
    value = value or _SESSIONS.get(scene.as_pointer()) or _restore(scene)
    sequence = value['sequence']
    index = min(max(int(scene.get(INDEX, 16)), 0), len(sequence.states)-1)
    if scene.get(INDEX) != index:
        scene[INDEX] = index
    if value['index'] != index:
        vertices, _ = sequence.geometry(index)
        for blade_index, obj in enumerate(value['blades']):
            obj.data.vertices.foreach_set('co', vertices[blade_index].reshape(-1).astype(np.float32))
            colors = np.where(np.repeat(sequence.observed[index, blade_index], sequence.rotor.cfg.n_ring)[:, None],
                              review.GREEN, review.ORANGE)
            obj.data.color_attributes['evidence'].data.foreach_set('color', colors.astype(np.float32).reshape(-1))
            obj.data.update()
        value['index'] = index
    inspection.update(scene, value['blades'], sequence.rotor)
    return value


def information(scene):
    """Read committed sample information; safe in Blender panel/HUD draw.

    Drawing must never restore JSON, allocate objects, or update shader IDs.
    The native lifecycle timer owns restoration and geometry/material commits.
    """
    value = _SESSIONS.get(scene.as_pointer())
    if value is None or value['index'] is None:
        return None
    sequence, index = value['sequence'], value['index']
    return dict(index=index, samples=len(sequence.states), time_s=float(sequence.times[index]),
                fps=sequence.fps, source_label=sequence.source_label,
                source_frame=int(sequence.source_frames[index]))


def step(scene, direction):
    value = ensure(scene)
    scene[INDEX] = min(max(value['index'] + direction, 0), len(value['sequence'].states)-1)
    apply(scene, value=value)


def toggle(scene):
    from . import blade_recon_inspection as inspection
    value = ensure(scene)
    return inspection.toggle_texture_mode(scene, value['blades'], value['sequence'].rotor)


def focus(context, area, point=None):
    from . import blade_recon_inspection as inspection
    scene = context.scene
    value = ensure(scene)
    if point:
        label, radius, tau = POINTS[point]
        scene.wfrl_recon_inspect_radius, scene.wfrl_recon_inspect_tau = radius, tau
        scene.wfrl_recon_inspect_blade = '1'
        scene.wfrl_recon_inspect_size = 1.6
        scene['split_texture_inspection_point'] = label
    result = inspection.focus(context, value['sequence'], value['blades'], areas=[area],
                              scene=scene, index=value['index'])
    area.spaces.active.show_region_ui = False
    area.spaces.active.overlay.show_overlays = False
    scene[DETAIL] = True
    return result


def clear_runtime():
    _SESSIONS.clear()
