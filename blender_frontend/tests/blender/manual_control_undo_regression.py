"""Native undo boundaries for manual-scene creation, entry and recovery."""
import importlib
import json
import math
import os
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)
records = []


def record(name):
    state = dict(name=name, current_scene=bpy.context.scene.name,
                 scenes=[dict(name=scene.name, kind=scene.get('wfrl_scene_kind'),
                              valid=control.validate_scene(scene)[0],
                              controls=[dict(id=entry.turbine_id, yaw=entry.yaw_deg)
                                        for entry in scene.wfrl_manual_turbines])
                         for scene in bpy.data.scenes])
    records.append(state)
    (OUT / 'states.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))


def expect_manual(name, yaw):
    scene = bpy.context.scene
    assert scene.name == name, (scene.name, name)
    assert control.validate_scene(scene)[0], control.validate_scene(scene)
    entry = scene.wfrl_manual_turbines[0]
    assert math.isclose(entry.yaw_deg, yaw, abs_tol=1e-5), (entry.yaw_deg, yaw)
    assert math.isclose(entry.yaw_object.rotation_euler.z, math.radians(yaw), abs_tol=1e-5)
    return scene


def edit_yaw(value):
    assert bpy.ops.wm.context_set_float(
        'EXEC_DEFAULT', True,
        data_path='scene.wfrl_manual_turbines[0].yaw_deg', value=value) == {'FINISHED'}


addon.register()
bpy.context.preferences.edit.use_global_undo = True
source_name = bpy.context.scene.name
assert 'Cube' in bpy.context.scene.objects, 'Use --factory-startup'
# Background startup requires a single initial state before any product action.
bpy.ops.ed.undo_push(message='Factory startup')
record('initial')

assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
manual_name = bpy.context.scene.name
record('created')
edit_yaw(45.0)
record('first_edit')
assert bpy.ops.ed.undo() == {'FINISHED'}
expect_manual(manual_name, 0.0)
record('first_edit_undone')
assert bpy.ops.ed.redo() == {'FINISHED'}
expect_manual(manual_name, 45.0)
record('first_edit_redone')

for _ in range(3):
    assert bpy.ops.wfrl.manual_leave('EXEC_DEFAULT', True) == {'FINISHED'}
    assert bpy.context.scene.name == source_name
    assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
expect_manual(manual_name, 45.0)
assert bpy.ops.ed.undo() == {'FINISHED'}
expect_manual(manual_name, 0.0)
record('edit_undone_after_three_round_trips')
assert bpy.ops.ed.undo() == {'FINISHED'}
assert bpy.context.scene.name == source_name
assert bpy.data.scenes.get(manual_name) is None
record('creation_undone')
assert bpy.ops.ed.redo() == {'FINISHED'}
expect_manual(manual_name, 0.0)
assert bpy.ops.ed.redo() == {'FINISHED'}
expect_manual(manual_name, 45.0)
record('creation_and_edit_redone')

scene = bpy.context.scene
for obj in scene.objects:
    obj.select_set(False)
blade = scene.wfrl_manual_turbines[0].blade1_object
blade.select_set(True)
bpy.context.view_layer.objects.active = blade
assert bpy.ops.object.delete('EXEC_DEFAULT', True) == {'FINISHED'}
assert not control.validate_scene(bpy.context.scene)[0]
record('damaged')
assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
recreated_name = bpy.context.scene.name
assert recreated_name != manual_name
expect_manual(recreated_name, 0.0)
record('recreated')
edit_yaw(-32.0)
assert bpy.ops.ed.undo() == {'FINISHED'}
expect_manual(recreated_name, 0.0)
record('recreated_first_edit_undone')
assert bpy.ops.ed.redo() == {'FINISHED'}
expect_manual(recreated_name, -32.0)
record('recreated_first_edit_redone')
assert bpy.ops.ed.undo() == {'FINISHED'}
expect_manual(recreated_name, 0.0)
assert bpy.ops.ed.undo() == {'FINISHED'}
assert bpy.data.scenes.get(recreated_name) is None
assert bpy.context.scene.name == manual_name
assert not control.validate_scene(bpy.context.scene)[0]
record('recreation_undone_to_damaged_scene')
assert bpy.ops.ed.redo() == {'FINISHED'}
expect_manual(recreated_name, 0.0)
record('recreation_redone')

scene = bpy.context.scene
for obj in scene.objects:
    obj.select_set(False)
blade = scene.wfrl_manual_turbines[0].blade2_object
blade.select_set(True)
bpy.context.view_layer.objects.active = blade
assert bpy.ops.object.delete('EXEC_DEFAULT', True) == {'FINISHED'}
assert bpy.ops.wfrl.manual_leave('EXEC_DEFAULT', True) == {'FINISHED'}
assert bpy.context.scene.name == source_name
assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
reentered_name = bpy.context.scene.name
assert reentered_name not in {manual_name, recreated_name}
expect_manual(reentered_name, 0.0)
edit_yaw(67.0)
assert bpy.ops.ed.undo() == {'FINISHED'}
expect_manual(reentered_name, 0.0)
assert bpy.ops.ed.redo() == {'FINISHED'}
expect_manual(reentered_name, 67.0)
record('source_entry_recreation_first_edit_undo_redo')

bpy.context.preferences.edit.use_global_undo = False
source = bpy.data.scenes.new('Undo disabled source')
bpy.context.window.scene = source
assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
assert control.validate_scene(bpy.context.scene)[0]
record('creation_with_global_undo_disabled')

summary = dict(status='PASS', module=str(Path(addon.__file__).resolve()),
               blender=bpy.app.version_string,
               checks=['first_edit_undo_redo', 'normal_round_trips_without_undo_step',
                       'creation_undo_redo', 'damaged_recreation_first_edit_undo_redo',
                       'recreation_undo_redo', 'source_entry_recreation_first_edit_undo_redo',
                       'creation_with_global_undo_disabled'])
(OUT / 'checks.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
addon.unregister()
print(json.dumps(summary, ensure_ascii=False), flush=True)
