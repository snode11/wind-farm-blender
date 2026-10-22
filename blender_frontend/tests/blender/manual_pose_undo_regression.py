"""Native undo and redo for presets, relative nudges and pose-file loading."""
import importlib
import json
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


def snapshot():
    scene = bpy.context.scene
    assert control.validate_scene(scene)[0], control.validate_scene(scene)
    assert not any(entry.running for entry in scene.wfrl_manual_turbines)
    return control.pose_snapshot(scene)


def round_trip(operation):
    before = snapshot()
    assert operation() == {'FINISHED'}
    after = snapshot()
    assert before != after
    assert bpy.ops.ed.undo() == {'FINISHED'}
    assert snapshot() == before
    assert bpy.ops.ed.redo() == {'FINISHED'}
    assert snapshot() == after


addon.register()
try:
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message='Factory startup')
    assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
    scene = bpy.context.scene
    scene.wfrl_manual_target = 'ALL'
    for index, entry in enumerate(scene.wfrl_manual_turbines, 1):
        entry.yaw_deg = index * 10
        entry.individual_pitch = True
        entry.pitch_b2_deg = index * 4
        entry.azimuth_deg = index * 15
    bpy.ops.ed.undo_push(message='Manual pose inputs')
    pose_path = OUT / 'poses.json'
    assert bpy.ops.wfrl.manual_pose_export(filepath=str(pose_path)) == {'FINISHED'}
    round_trip(lambda: bpy.ops.wfrl.manual_pose_preset('EXEC_DEFAULT', True, preset='FEATHER'))
    round_trip(lambda: bpy.ops.wfrl.manual_pose_nudge('EXEC_DEFAULT', True, field='yaw_deg', delta=2.5))
    round_trip(lambda: bpy.ops.wfrl.manual_pose_import('EXEC_DEFAULT', True, filepath=str(pose_path)))
    result = dict(status='PASS', addon_module=MODULE, addon_file=addon.__file__,
                  presets=True, relative_nudges=True, pose_file_import=True,
                  per_turbine_parameters_preserved=True, restored_motion_paused=True)
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MANUAL_POSE_UNDO_PASS ' + json.dumps(result), flush=True)
finally:
    addon.unregister()
