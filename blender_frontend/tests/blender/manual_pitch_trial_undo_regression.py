"""Native trial confirmation, reset and completion undo with paused recovery."""
import importlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
assert Path(addon.__file__).resolve().parent == Path(os.environ.get(
    'WFRL_EXPECTED_ADDON_ROOT', ROOT / 'blender_frontend/wfrl_blender')).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


def state(*, paused=True):
    scene = bpy.context.scene
    assert control.validate_scene(scene)[0], control.validate_scene(scene)
    if paused:
        assert not any(entry.running for entry in scene.wfrl_manual_turbines)
    return dict(poses=control.pose_snapshot(scene), configuration=scene.wfrl_manual_trial.configuration,
                elapsed_s=scene.wfrl_manual_trial.elapsed_s,
                engaged=scene.wfrl_manual_trial.engaged,
                model=scene['wfrl_manual_blade_model'])


addon.register()
try:
    bpy.context.preferences.edit.use_global_undo = True
    bpy.ops.ed.undo_push(message='Factory startup')
    assert bpy.ops.wfrl.manual_enter('EXEC_DEFAULT', True) == {'FINISHED'}
    scene = bpy.context.scene
    scene.wfrl_manual_turbines[0].azimuth_deg = 72
    scene.wfrl_manual_trial.duration_s = 8
    scene.wfrl_manual_trial.select_b2 = True
    before = state()
    bpy.ops.ed.undo_push(message='Trial inputs')
    clock = [100.0]
    with patch.object(control.time, 'monotonic', side_effect=lambda: clock[0]):
        assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action='CONFIRM') == {'FINISHED'}
        clock[0] += 2
        control.tick(now=clock[0])
        assert bpy.ops.ed.undo() == {'FINISHED'}
        assert state() == before
        assert bpy.ops.ed.redo() == {'FINISHED'}
        assert control.trial_active(bpy.context.scene)
        assert state()['elapsed_s'] == 0
        for operator, target in ((bpy.ops.wfrl.manual_trial, 'T1'),
                                 (bpy.ops.wfrl.manual_action, 'T1'),
                                 (bpy.ops.wfrl.manual_action, 'ALL')):
            scene = bpy.context.scene
            scene.wfrl_manual_target = target
            if target == 'ALL':
                t2 = next(entry for entry in scene.wfrl_manual_turbines if entry.turbine_id == 'T2')
                t2.yaw_deg, t2.pitch_deg, t2.rpm = 24, 18, 13
                scene.wfrl_manual_turbines.move(0, 2)
            assert operator('EXEC_DEFAULT', True, action='START') == {'FINISHED'}
            clock[0] += 2
            control.tick(now=clock[0])
            clock[0] += 1
            moving = state(paused=False)
            moving['elapsed_s'] = 3
            assert operator('EXEC_DEFAULT', True, action='RESET') == {'FINISHED'}
            reset = state()
            assert reset['elapsed_s'] == 0
            assert bpy.ops.ed.undo() == {'FINISHED'}
            assert state() == moving, (target, state(), moving)
            assert bpy.ops.ed.redo() == {'FINISHED'}
            assert state() == reset
        assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action='START') == {'FINISHED'}
        clock[0] += 2
        control.tick(now=clock[0])
        clock[0] += 1
        moving = state(paused=False)
        moving['elapsed_s'] = 3
        assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action='END') == {'FINISHED'}
        ended = state()
        assert not ended['engaged'] and ended['configuration'] == moving['configuration']
        assert ended['elapsed_s'] == 3 and ended['poses'] == moving['poses']
        assert bpy.ops.ed.undo() == {'FINISHED'}
        assert state() == moving
        assert bpy.ops.ed.redo() == {'FINISHED'}
        assert state() == ended
        for action in ('RESET', 'END'):
            for trial_running in (True, False):
                for rotor_running in (True, False):
                    scene = bpy.context.scene
                    if control.trial_active(scene):
                        control.trial_action(scene, 'END', now=clock[0])
                    control.pause_all()
                    scene.wfrl_manual_target = 'ALL'
                    assert bpy.ops.wfrl.manual_action(action='RESET') == {'FINISHED'}
                    scene.wfrl_manual_target = 'T2'
                    t2 = next(entry for entry in scene.wfrl_manual_turbines if entry.turbine_id == 'T2')
                    t2.rpm = 9.0 if rotor_running else 0.0
                    if rotor_running:
                        assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
                    else:
                        t2.pitch_deg = 0.0
                    assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action='CONFIRM') == {'FINISHED'}
                    control.tick(now=clock[0])
                    clock[0] += 2.0
                    control.tick(now=clock[0])
                    assert control.effective_pitches(t2) == (80.0,) * 3
                    if not trial_running:
                        assert bpy.ops.wfrl.manual_trial(action='PAUSE') == {'FINISHED'}
                    clock[0] += 1.0
                    assert bpy.ops.wfrl.manual_trial('EXEC_DEFAULT', True, action=action) == {'FINISHED'}
                    assert bpy.ops.ed.undo() == {'FINISHED'}
                    restored = bpy.context.scene
                    t2 = next(entry for entry in restored.wfrl_manual_turbines if entry.turbine_id == 'T2')
                    assert control.effective_pitches(t2) == (75.0,) * 3
                    assert restored.wfrl_manual_trial.elapsed_s == (3.0 if trial_running else 2.0)
                    assert abs(t2.azimuth_deg - (162.0 if rotor_running else 0.0)) < 1e-5
                    assert not t2.running and t2.pitch_paused
                    for blade in range(1, 4):
                        from math import degrees
                        assert abs(-degrees(getattr(t2, f'blade{blade}_object').rotation_euler.z) - 75.0) < 1e-4
                    assert bpy.ops.ed.redo() == {'FINISHED'}
                    redone = next(entry for entry in bpy.context.scene.wfrl_manual_turbines
                                  if entry.turbine_id == 'T2')
                    assert control.effective_pitches(redone) == (75.0,) * 3
    result = dict(status='PASS', addon_file=addon.__file__, blade_model='rootkink',
                  confirm_undo_redo=True, reset_undo_redo=True, end_undo_redo=True,
                  running_reset_undo_redo=True, running_end_undo_redo=True,
                  generic_t1_and_all_reset_undo_redo=True, unsampled_elapsed_preserved=True,
                  all_turbines_checkpoint_same_time=True,
                  other_turbine_pitch_checkpoints=True, stationary_rotor_pitch_checkpoints=True,
                  paused_trial_other_motion_checkpoints=True,
                  restored_motion_paused=True, saved_trial_clock_rebound=True)
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2))
    print('MANUAL_PITCH_TRIAL_UNDO_PASS ' + json.dumps(result), flush=True)
finally:
    addon.unregister()
