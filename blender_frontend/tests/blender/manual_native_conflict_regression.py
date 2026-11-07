"""Manual trial and fixed Down reject competing native Blender controls."""
import importlib
import json
import os
from pathlib import Path
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module('wfrl_blender')
from wfrl_blender import manual_control as control, manual_scene

assert bpy.app.background
assert Path(addon.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
report = dict(addon_file=addon.__file__, blender=bpy.app.version_string)


def state(scene):
    trial = scene.wfrl_manual_trial
    return (control.pose_snapshot(scene), trial.configuration, trial.elapsed_s, trial.engaged,
            [(entry.running, [tuple(getattr(entry, field).rotation_euler)
                              for field in control._POINTER_FIELDS])
             for entry in scene.wfrl_manual_turbines])


def rejected(call):
    try:
        call()
    except ValueError as exc:
        assert '约束、动作或驱动' in str(exc) or '镜头含动作或驱动' in str(exc), str(exc)
    else:
        raise AssertionError('Native animation or constraint was accepted')


addon.register()
try:
    source = bpy.context.scene
    source_before = {obj.name: tuple(obj.matrix_world) for obj in source.objects}
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    t1 = control._t1(scene)
    target = bpy.data.objects.new('Native rotation target', None)
    scene.collection.objects.link(target)
    target.rotation_euler.x = .4
    cases = []
    controlled = [getattr(t1, field) for field in control._POINTER_FIELDS]
    controlled.extend((t1.yaw_object.parent, t1.blade1_object.parent))
    for obj in controlled:
        constraint = obj.constraints.new('COPY_ROTATION')
        constraint.target = target
        before = state(scene)
        rejected(lambda: control.confirm_trial(scene, now=100))
        assert state(scene) == before, obj.name
        obj.constraints.remove(constraint)
        obj.driver_add('rotation_euler', 1).driver.expression = '0.0'
        before = state(scene)
        rejected(lambda: control.confirm_trial(scene, now=100))
        assert state(scene) == before, obj.name
        obj.animation_data_clear()
        cases.append(obj.name)
    control.confirm_trial(scene, now=100)
    constraint = t1.rotor_object.constraints.new('COPY_ROTATION')
    constraint.target = target
    control.tick(now=104)
    assert not t1.running, 'A trial kept running with a competing native constraint'
    assert '约束、动作或驱动' in control.validate_scene(scene)[1]
    t1.rotor_object.constraints.remove(constraint)
    control.trial_action(scene, 'END', now=104)
    camera = manual_scene.down_view(bpy.context)
    camera.data.lens = 31
    constraint = camera.constraints.new('TRACK_TO')
    constraint.target = t1.blade1_object
    camera_before = (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens)
    rejected(lambda: manual_scene.down_view(bpy.context))
    assert (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens) == camera_before
    camera.constraints.remove(constraint)
    camera.data.driver_add('lens').driver.expression = '31'
    rejected(lambda: manual_scene.down_view(bpy.context))
    assert (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens) == camera_before
    camera.data.animation_data_clear()
    assert manual_scene.down_view(bpy.context) == camera
    assert {obj.name: tuple(obj.matrix_world) for obj in source.objects} == source_before
    report.update(status='PASS', controlled_object_cases=cases,
                  constraints_and_drivers_rejected_atomically=True,
                  competing_control_pauses_running_trial=True,
                  fixed_down_tracking_and_lens_animation_rejected=True,
                  original_scene_preserved=True)
except BaseException:
    report.update(status='FAILED', error=traceback.format_exc())
    raise
finally:
    addon.unregister()
    (OUT / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print('MANUAL_NATIVE_CONFLICT_PASS ' + json.dumps(report), flush=True)
