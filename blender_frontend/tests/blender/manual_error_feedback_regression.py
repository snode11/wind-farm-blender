"""Native protection feedback, explicit recovery and ordinary pause checks."""
import importlib
import json
import math
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
from wfrl_blender import manual_control as control
from wfrl_blender.panels.manual_control import draw_feedback

assert bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


class FeedbackLayout:
    def __init__(self):
        self.labels, self.operators = [], []

    def box(self):
        return self

    def row(self):
        return self

    def label(self, *, text, **kwargs):
        self.labels.append(text)

    def operator(self, name, **kwargs):
        self.operators.append(name)


addon.register()
try:
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    entry = control._t1(scene)
    now = [100.0]
    with patch.object(control.time, 'monotonic', side_effect=lambda: now[0]):
        scene.wfrl_manual_pitch_rate_deg_s = 2
        entry.pitch_deg = 20
        assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
        now[0] += .5
        control.tick(now[0])
        actual = control.effective_pitches(entry)
        azimuth = entry.azimuth_deg
        entry.rotor_object.rotation_euler.x += .1
        external = tuple(entry.rotor_object.rotation_euler)
        now[0] += 2
        control.tick(now[0])
        assert not entry.running and entry.pitch_paused
        assert scene.wfrl_manual_status == 'ERROR'
        assert scene.wfrl_manual_error_turbine == 'T1'
        assert '旋转' in scene.wfrl_manual_error
        assert tuple(entry.rotor_object.rotation_euler) == external
        assert control.effective_pitches(entry) == actual and entry.azimuth_deg == azimuth
        assert not control.recheck_feedback(scene)[0]
        error = scene.wfrl_manual_error
        entry.rpm = 12
        assert tuple(entry.rotor_object.rotation_euler) == external
        assert scene.wfrl_manual_error == error and not entry.running
        ui = FeedbackLayout()
        draw_feedback(ui, SimpleNamespace(scene=scene, region=SimpleNamespace(width=280),
                                         preferences=bpy.context.preferences))
        assert any('保护暂停 · T1' in text for text in ui.labels)
        assert any('撤销原生旋转' in text for text in ui.labels)
        assert 'wfrl.manual_recheck' in ui.operators
        entry.rotor_object.rotation_euler.x = math.radians(entry.azimuth_deg)
        control.tick(now[0] + 5)
        assert scene.wfrl_manual_error == error and not entry.running
        assert bpy.ops.wfrl.manual_recheck() == {'FINISHED'}
        assert not scene.wfrl_manual_error and not entry.running and entry.pitch_paused
        assert control.effective_pitches(entry) == actual
        assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
        now[0] += .25
        control.tick(now[0])
        assert entry.running and entry.azimuth_deg > azimuth
        assert bpy.ops.wfrl.manual_action(action='PAUSE') == {'FINISHED'}
        assert scene.wfrl_manual_status == 'USER' and not scene.wfrl_manual_error

        constraint = entry.rotor_object.constraints.new('LIMIT_ROTATION')
        try:
            bpy.ops.wfrl.manual_action(action='START')
        except RuntimeError:
            pass
        assert scene.wfrl_manual_error_turbine == 'T1' and '约束' in scene.wfrl_manual_error
        assert not entry.running and not control.recheck_feedback(scene)[0]
        entry.rotor_object.constraints.remove(constraint)
        assert bpy.ops.wfrl.manual_recheck() == {'FINISHED'}

        assert bpy.ops.wfrl.manual_action(action='RESET') == {'FINISHED'}
        control.confirm_trial(scene, now=now[0])
        now[0] += .5
        control.tick(now[0])
        elapsed = scene.wfrl_manual_trial.elapsed_s
        actual = control.effective_pitches(entry)
        entry.rotor_object.rotation_euler.x += .1
        external = tuple(entry.rotor_object.rotation_euler)
        now[0] += 3
        control.tick(now[0])
        assert not entry.running and scene.wfrl_manual_error
        assert scene.wfrl_manual_trial.elapsed_s == elapsed
        assert control.effective_pitches(entry) == actual
        assert tuple(entry.rotor_object.rotation_euler) == external
        entry.rotor_object.rotation_euler.x = math.radians(entry.azimuth_deg)
        assert bpy.ops.wfrl.manual_recheck() == {'FINISHED'}
        control.trial_action(scene, 'START', now=now[0])
        now[0] += .25
        control.tick(now[0])
        assert abs(scene.wfrl_manual_trial.elapsed_s - elapsed - .25) < 1e-5
        control.trial_action(scene, 'END', now=now[0])

        assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
        removed = entry.rotor_object
        bpy.data.objects.remove(removed, do_unlink=True)
        control.tick(now[0] + 1)
        assert not entry.running and scene.wfrl_manual_error_turbine == 'T1'
        assert '缺失' in scene.wfrl_manual_error and '重新创建' in scene.wfrl_manual_recovery
        assert not control.recheck_feedback(scene)[0]
    report = dict(status='PASS', addon_file=addon.__file__, blender=bpy.app.version_string,
                  running_rotation_and_constraint_feedback=True,
                  validated_recovery_stays_paused=True, user_pause_has_no_error=True,
                  pitch_and_trial_time_preserved=True, deleted_object_pauses=True,
                  sidebar_feedback_draw=True)
    (OUT / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('MANUAL_ERROR_FEEDBACK_PASS ' + json.dumps(report), flush=True)
finally:
    addon.unregister()
