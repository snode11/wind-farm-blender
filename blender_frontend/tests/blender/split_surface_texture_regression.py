"""Native adapter checks; visible sidebar/layout acceptance is a separate test."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(os.environ.get('WFRL_TEST_OUTPUT', ROOT / 'outputs' / 'split-surface-texture-regression'))
OUTPUT.mkdir(parents=True, exist_ok=True)
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path.insert(0, str(ROOT / 'blender_frontend'))
addon = importlib.import_module(MODULE)
split = importlib.import_module(MODULE + '.split_reconstruction')
surface = importlib.import_module(MODULE + '.split_surface_texture')
inspection = importlib.import_module(MODULE + '.blade_recon_inspection')
example = Path(addon.__file__).resolve().parent / 'assets/examples/mappo_reconstruction_split.blend'
bpy.ops.wm.open_mainfile(filepath=str(example), load_ui=False, use_scripts=False)
if not hasattr(bpy.types.Scene, 'wfrl_recon_show_texture'):
    addon.register()
scene = bpy.context.scene
scene.frame_set(37)
legacy = [scene.objects[f'SplitRecon.B{i}'] for i in (1, 2, 3)]
legacy_pointers = [obj.as_pointer() for obj in legacy]
legacy_keys = [len(obj.data.shape_keys.key_blocks) for obj in legacy]
value = surface.ensure(scene)
assert scene.frame_current == 37
assert len(value['sequence'].states) == 120
assert value['index'] == 16
assert value['sequence'].times[16] == 1.6
assert value['sequence'].fps == 10
assert 'SYNTHETIC' in value['sequence'].source_label.upper()
assert [obj.as_pointer() for obj in legacy] == legacy_pointers
assert [len(obj.data.shape_keys.key_blocks) for obj in legacy] == legacy_keys == [601] * 3
assert split.reconstruction_camera(scene).name == 'SplitTexture.Camera'

def snapshot():
    result = []
    for obj in surface.objects(scene):
        coords = np.empty(len(obj.data.vertices)*3, dtype=np.float32)
        obj.data.vertices.foreach_get('co', coords)
        uv = np.empty(len(obj.data.uv_layers['atlas'].data)*2, dtype=np.float32)
        obj.data.uv_layers['atlas'].data.foreach_get('uv', uv)
        colors = np.empty(len(obj.data.color_attributes['evidence'].data)*4, dtype=np.float32)
        obj.data.color_attributes['evidence'].data.foreach_get('color', colors)
        image = obj.data.materials[0].node_tree.nodes['SurfaceTexture'].image
        result.append([hashlib.sha256(item).hexdigest() for item in
                       (coords.tobytes(), uv.tobytes(), colors.tobytes(), image.packed_file.data)])
    return result

before = snapshot()
scene.wfrl_recon_texture_mode = 'ORIGINAL'
surface.apply(scene)
assert surface.toggle(scene) == 'FALSE_COLOR'
assert surface.toggle(scene) == 'ORIGINAL'
assert snapshot() == before
for direction, index in ((1, 17), (-1, 16)):
    surface.step(scene, direction)
    assert scene[surface.INDEX] == index
    assert scene.frame_current == 37
scene[surface.INDEX] = 0
surface.step(scene, -1)
assert scene[surface.INDEX] == 0
scene[surface.INDEX] = 119
surface.step(scene, 1)
assert scene[surface.INDEX] == 119
scene[surface.INDEX] = 16
surface.apply(scene)
bpy.context.view_layer.update()
target, normal, up = inspection._surface_sample(value['sequence'], value['blades'][0], 0, 16, 59.3, .6)
assert target.x > 9900 and normal.length > .99
other = bpy.data.scenes.new('另一个 MAPPO 场景')
other_value = surface.ensure(other)
assert surface.camera(other) is not surface.camera(scene)
assert all(obj.name.endswith('.001') for obj in surface.objects(other))
surface.clear_runtime()
assert surface.ensure(other)['index'] == 16
second_scene_name = other.name
saved = OUTPUT / 'texture-split-saved.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(saved))
bpy.ops.wm.open_mainfile(filepath=str(saved), load_ui=False, use_scripts=False)
scene = bpy.context.scene
value = surface.ensure(scene)
assert scene.frame_current == 37 and value['index'] == 16
assert snapshot() == before
assert [len(scene.objects[f'SplitRecon.B{i}'].data.shape_keys.key_blocks) for i in (1, 2, 3)] == [601] * 3
other = bpy.data.scenes[second_scene_name]
assert surface.ensure(other)['index'] == 16
assert surface.camera(other).name.endswith('.001')
# A user-edited/missing material must become a readable restoration error,
# rather than an IndexError that would stop the native lifecycle timer.
other_blade = surface.objects(other)[0]
material = other_blade.data.materials[0]
other_blade.data.materials.clear()
surface.clear_runtime()
try:
    surface.ensure(other)
except ValueError as exc:
    assert '材质缺失' in str(exc), str(exc)
else:
    raise AssertionError('missing material was accepted')
other_blade.data.materials.append(material)
surface.ensure(other)
report = {'status': 'PASS', 'addon': addon.__file__, 'example': str(example), 'saved': str(saved),
          'texture': surface.information(scene), 'left_scene_frame': scene.frame_current,
          'legacy_keys_preserved': legacy_keys, 'display_preserved_geometry_uv_evidence_packed_rgba': True,
          'independent_cursor_and_clamps': True, 'save_reopen_restored': True,
          'multiple_scene_names_and_missing_material_guard': True,
          'surface_target': list(target), 'surface_normal': list(normal)}
(OUTPUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print('SPLIT_SURFACE_TEXTURE_NATIVE_PASS ' + json.dumps(report, ensure_ascii=False))
