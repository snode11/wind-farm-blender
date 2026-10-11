"""Native default repair, rigid/flexible attachment and original load lifecycle.

WFRL_ADDON_MODULE selects the installed extension; WFRL_TEST_RUNTIME selects
explicit development source. WFRL_REPAIR_TEST_OUTPUT is an additive directory.
No rendering or solver runs.
"""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
if os.environ.get('WFRL_TEST_RUNTIME'):
    sys.path.insert(0, os.environ['WFRL_TEST_RUNTIME'])
    sys.path.insert(0, str(ROOT))
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'bl_ext.user_default.wfrl_blender')
addon = importlib.import_module(MODULE)
repair = importlib.import_module(MODULE+'.repair_marks')
flex = importlib.import_module(MODULE+'.farm_flex')
spec = importlib.util.spec_from_file_location('repair_example_check', ROOT/'scripts/blender/add_nrel_b1_repair.py')
check_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_module)
out = Path(os.environ['WFRL_REPAIR_TEST_OUTPUT'])
out.mkdir(parents=True, exist_ok=True)
if not hasattr(bpy.types.Scene, 'wfrl_selected_turbine'):
    addon.register()
checks = []


def resources():
    return tuple(len(getattr(bpy.data, name)) for name in ('objects','meshes','materials','node_groups'))


def check(scene):
    patch = scene.objects[repair.NAME]
    blade = scene.objects[repair.TARGET_NAME]
    source = scene.objects[repair.SOURCE_NAME]
    assert patch.parent == blade and source.parent == blade
    assert source.data == blade.data and not source.modifiers
    assert source.hide_render and source.hide_get()
    assert len([o for o in scene.objects if o.get('kind') == 'synthetic_repaint']) == 1
    assert patch['repair_generation_version'] == repair.VERSION
    assert patch['target_topology_sha256'] == repair.topology_signature(blade.data)
    bpy.context.view_layer.update()
    row = check_module.check_attachment(scene, patch)
    evaluated = patch.evaluated_get(bpy.context.evaluated_depsgraph_get())
    center = sum((evaluated.matrix_world @ v.co for v in evaluated.data.vertices), Vector())/len(evaluated.data.vertices)
    return row, center


assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
scene = bpy.context.scene
assert flex.is_active(scene)
initial_resources = resources()
patch = scene.objects[repair.NAME]
for _ in range(10):
    assert repair.ensure(scene) == patch
assert resources() == initial_resources
centers = []
for frame in (1,52,901,1801,2701,3601,1):
    scene.frame_set(frame)
    row,center = check(scene)
    row['case'] = 'default_flexible'
    checks.append(row)
    centers.append(center)
assert (centers[2]-centers[0]).length > 1
path = out/'flexible-load-check.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
assert flex.is_active(scene)
for frame in (901,3601,1):
    scene.frame_set(frame)
    row,_ = check(scene)
    row['case'] = 'reopened_flexible'
    checks.append(row)
for _ in range(3):
    assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
    scene = bpy.context.scene
    row,_ = check(scene)
    row['case'] = 'repeated_default_reload'
    checks.append(row)

# A saved rigid legacy-style 66 s file must gain the mark on its original
# timeline even when the original cosmetic Bevel remains enabled.
flex.detach()
addon.build_demo_geometry()
scene = bpy.context.scene
scene['wfrl_scene_kind'] = 'demo'
if 'wfrl_farm_flex_path' in scene:
    del scene['wfrl_farm_flex_path']
scene.frame_start = 1
scene.frame_end = 1651
scene.render.fps = 25
blade = scene.objects[repair.TARGET_NAME]
bevels = [m for m in blade.modifiers if m.type == 'BEVEL']
assert bevels and all(m.show_viewport and m.show_render for m in bevels)
rotor = scene.objects['WFRL.Turbine.T1.Rotor']
rotor.rotation_euler.x = 0
rotor.keyframe_insert('rotation_euler', index=0, frame=1)
rotor.rotation_euler.x = 2.7
rotor.keyframe_insert('rotation_euler', index=0, frame=1651)
for frame in (1,825,1651,1):
    scene.frame_set(frame)
    row,_ = check(scene)
    row['case'] = 'rigid_66_seconds_with_bevel'
    checks.append(row)
repair.remove(scene)
assert scene.objects.get(repair.NAME) is None
legacy = out/'legacy-66-load-check.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(legacy))
bpy.ops.wm.open_mainfile(filepath=str(legacy))
scene = bpy.context.scene
assert (scene.frame_start,scene.frame_end,scene.render.fps) == (1,1651,25)
for frame in (1,825,1651,1):
    scene.frame_set(frame)
    row,_ = check(scene)
    row['case'] = 'reopened_rigid_66_default_added'
    checks.append(row)

# The user's real launcher first loads the bundled replay, then builds this
# configured YAML through live_scene after an explicit backend connection.
# Verify that same scene builder without starting a Bridge or FAST.Farm run.
from_scene = ROOT/'outputs/nrel-b1-repair-default-20261005/launcher-scene.json'
if from_scene.exists():
    scene_model = importlib.import_module(MODULE+'.scene_model')
    live = importlib.import_module(MODULE+'.live_scene')
    dto = scene_model.SceneDTO.from_mapping(json.loads(from_scene.read_text()))
    live.build_live_scene(dto)
    scene = bpy.context.scene
    assert scene['wfrl_scene_kind'] == 'live'
    row,_ = check(scene)
    row['case'] = 'user_launcher_turb3_row_scene_builder'
    checks.append(row)
report = {'status':'PASS','addon_path':addon.__file__,'checks':checks,
          'scope':'default creation, idempotence, multiple reloads, native rigid/flex replay, save/reopen; no render or solver'}
(out/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('DEFAULT_B1_REPAIR_PASS '+json.dumps(report,ensure_ascii=False))
