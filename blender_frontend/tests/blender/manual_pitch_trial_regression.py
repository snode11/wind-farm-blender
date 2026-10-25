"""Native rigid pitch trials, independent Down mounting and saved-state recovery."""
import importlib
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control, cameras, manual_scene, farm, clearance = [importlib.import_module(MODULE + '.' + name)
    for name in ('manual_control', 'cameras', 'manual_scene', 'farm_flex', 'clearance_replay')]
assert Path(addon.__file__).resolve().parent == Path(os.environ.get(
    'WFRL_EXPECTED_ADDON_ROOT', ROOT / 'blender_frontend/wfrl_blender')).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
OUT.mkdir(parents=True, exist_ok=False)


def close(a, b, tolerance=1e-4):
    assert abs(a - b) < tolerance, (a, b)


def assert_pose(entry, angle, pitches):
    close(entry.azimuth_deg, angle % 360)
    close(math.degrees(entry.rotor_object.rotation_euler.x), angle % 360)
    for index, pitch in enumerate(pitches, 1):
        close(control.effective_pitches(entry)[index - 1], pitch)
        close(-math.degrees(getattr(entry, f'blade{index}_object').rotation_euler.z), pitch)


def geometry(scene):
    bpy.context.view_layer.update()
    return {obj.name: (obj.parent.name if obj.parent else None,
                      tuple(v for row in obj.matrix_world for v in row), obj.hide_render,
                      obj.hide_viewport, obj.data.as_pointer() if obj.data else None)
            for obj in scene.objects}


def trial_op(action):
    assert bpy.ops.wfrl.manual_trial(action=action) == {'FINISHED'}


def entries(scene):
    return {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}


def assert_native_rotation_rejected(scene):
    entry = entries(scene)['T1']
    trial = scene.wfrl_manual_trial
    objects = [getattr(entry, field) for field in control._POINTER_FIELDS]
    before = (control.pose_snapshot(scene), trial.configuration, trial.elapsed_s, trial.engaged,
              entry.running, [(obj.rotation_mode, tuple(obj.rotation_euler)) for obj in objects],
              {key: value.__dict__.copy() for key, value in control._CLOCKS.items()})
    try:
        bpy.ops.wfrl.manual_trial(action='CONFIRM')
    except RuntimeError as exc:
        assert '原生旋转与手动控制参数不一致' in str(exc), str(exc)
    else:
        raise AssertionError('Native pose edits were accepted without an explicit error')
    after = (control.pose_snapshot(scene), trial.configuration, trial.elapsed_s, trial.engaged,
             entry.running, [(obj.rotation_mode, tuple(obj.rotation_euler)) for obj in objects],
             {key: value.__dict__.copy() for key, value in control._CLOCKS.items()})
    assert after == before, (before, after)


addon.register()
try:
    source = bpy.context.scene
    source_before = geometry(source)
    assert not any(obj.name.startswith('WFRL.Turbine.') for obj in bpy.data.objects)
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    turbine_geometry = importlib.import_module(MODULE + '.turbine_geometry')
    sampling = turbine_geometry.MANUAL_DISPLAY_SAMPLING[scene.wfrl_manual_display_detail]
    expected_vertices, _faces = turbine_geometry.blade_mesh(*sampling, model='rootkink')
    original_vertices, _faces = turbine_geometry.blade_mesh(*sampling, model='nrel5mw')
    assert scene['wfrl_manual_blade_model'] == 'rootkink'
    assert np.count_nonzero(np.linalg.norm(np.array(expected_vertices) - original_vertices, axis=1) > 1e-4) > 1000
    for entry in scene.wfrl_manual_turbines:
        for index in (1, 2, 3):
            mesh = getattr(entry, f'blade{index}_object').data
            actual = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            mesh.vertices.foreach_get('co', actual)
            np.testing.assert_allclose(actual.reshape(-1, 3), expected_vertices, atol=4e-6)
            assert mesh['wfrl_manual_blade_model'] == 'rootkink'
    t1 = entries(scene)['T1']
    pending = scene.wfrl_manual_trial
    clock = [100.0]
    with patch.object(control.time, 'monotonic', side_effect=lambda: clock[0]):
        t1.yaw_deg, t1.azimuth_deg = 17, 30
        t1.individual_pitch = True
        for index, pitch in enumerate((3, 7, 11), 1):
            setattr(t1, f'pitch_b{index}_deg', pitch)
        controlled_axes = ((t1.yaw_object, 2), (t1.rotor_object, 0),
                           (t1.blade1_object, 2), (t1.blade2_object, 2), (t1.blade3_object, 2))
        for obj, axis in controlled_axes:
            original = tuple(obj.rotation_euler)
            obj.rotation_euler[axis] += math.radians(10)
            assert_native_rotation_rejected(scene)
            obj.rotation_euler = original
            obj.rotation_mode = 'QUATERNION'
            assert_native_rotation_rejected(scene)
            obj.rotation_mode = 'XYZ'
            obj.rotation_euler = original
        assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
        clock[0] += .5
        t1.blade2_object.rotation_euler.z -= math.radians(10)
        assert_native_rotation_rejected(scene)
        t1.blade2_object.rotation_euler.z = -math.radians(7)
        t1.rotor_object.rotation_euler.x += math.tau
        t1.blade3_object.rotation_euler.z -= math.tau
        trial_op('CONFIRM')
        assert_pose(t1, 57, (3, 7, 11))
        trial_op('END')
        entries(scene)['T2'].yaw_deg, entries(scene)['T2'].pitch_deg = 24, 18
        entries(scene)['T3'].rpm, entries(scene)['T3'].azimuth_deg = 13, 101
        others = {tid: pose for tid, pose in control.pose_snapshot(scene).items() if tid != 'T1'}
        cases = ((True, False, False), (True, True, False), (True, True, True),
                 (False, True, False), (False, False, True), (False, True, True), (True, False, True))
        for selected in cases:
            if control.trial_active(scene):
                trial_op('END')
            t1.yaw_deg, t1.rpm, t1.azimuth_deg = 17, 9, 331
            t1.individual_pitch = True
            for index, pitch in enumerate((3, 7, 11), 1):
                setattr(t1, f'pitch_b{index}_deg', pitch)
            before = control.pose_snapshot(scene)
            for index, (enabled, target) in enumerate(zip(selected, (23, -13, 41)), 1):
                setattr(pending, f'select_b{index}', enabled)
                setattr(pending, f'target_b{index}_deg', target)
            pending.duration_s = 8
            assert control.pose_snapshot(scene) == before, 'Pending targets changed the model'
            trial_op('CONFIRM')
            assert t1.running and control._entry_key(scene, t1) not in control._CLOCKS
            start, spec = control.trial_configuration(scene)
            assert_pose(t1, 331, (3, 7, 11))
            clock[0] += 4
            sampled = control.pose_snapshot(scene)['T1']
            expected = spec.pose_at(4)
            for index, pitch in enumerate(expected.pitches_deg, 1):
                close(sampled[f'pitch_b{index}_deg'], pitch)
            control.tick(now=clock[0])
            assert_pose(t1, expected.azimuth_deg, expected.pitches_deg)
            assert t1.rpm == 9
            t1.rpm, t1.pitch_deg, t1.individual_pitch = 2, 70, False
            assert t1.rpm == 9 and t1.individual_pitch
            assert_pose(t1, expected.azimuth_deg, expected.pitches_deg)
            for elapsed in (8, 15):
                clock[0] += elapsed - pending.elapsed_s
                control.tick(now=clock[0])
                expected = spec.pose_at(elapsed)
                assert_pose(t1, expected.azimuth_deg, expected.pitches_deg)
            assert {tid: pose for tid, pose in control.pose_snapshot(scene).items() if tid != 'T1'} == others
            trial_op('RESET')
            assert not t1.running and pending.elapsed_s == 0
            assert control.pose_snapshot(scene)['T1'] == start
            trial_op('START')
            clock[0] += 2
            trial_op('PAUSE')
            paused = control.pose_snapshot(scene)
            paused_time = pending.elapsed_s
            clock[0] += 50
            control.tick(now=clock[0])
            assert control.pose_snapshot(scene) == paused and pending.elapsed_s == paused_time
            trial_op('START')
            clock[0] += 1
            control.tick(now=clock[0])
            assert_pose(t1, spec.pose_at(3).azimuth_deg, spec.pose_at(3).pitches_deg)

        trial_op('END')
        t1.individual_pitch = False
        t1.pitch_deg, t1.azimuth_deg, t1.rpm = 12, 41, 9
        for index in range(1, 4):
            setattr(pending, f'select_b{index}', index == 2)
        before = control.pose_snapshot(scene)
        assert bpy.ops.wfrl.manual_action(action='START') == {'FINISHED'}
        clock[0] += .5
        trial_op('CONFIRM')
        close(t1.azimuth_deg, 68)
        _start, spec = control.trial_configuration(scene)
        assert spec.start_pitches == (12, 12, 12)
        clock[0] += 4
        control.tick(now=clock[0])
        assert_pose(t1, 284, (12, -.5, 12))
        trial_op('PAUSE')
        state = control.pose_snapshot(scene)
        elapsed = pending.elapsed_s
        assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
        camera = scene.camera
        assert camera.parent == t1.yaw_object and camera.parent != t1.rotor_object
        assert camera.get('wfrl_manual_owner') == scene['wfrl_manual_owner']
        assert set(camera.users_scene) == {scene} and camera.data.users == 1
        owned_data = camera.data
        source_data = source.camera.data
        source_optics = (source_data.lens, source_data.shift_x, source_data.shift_y)
        camera.data = source_data
        try:
            manual_scene.down_view(bpy.context)
        except ValueError:
            pass
        else:
            raise AssertionError('Manual Down accepted another scene\'s shared lens data')
        finally:
            camera.data = owned_data
        assert (source_data.lens, source_data.shift_x, source_data.shift_y) == source_optics
        assert not source_data.get('wfrl_manual_owner')
        assert control.pose_snapshot(scene) == state and pending.elapsed_s == elapsed
        local_camera = camera.matrix_local.copy()
        cameras.down_gimbal(camera)
        close(math.degrees(camera.data.angle_x), 91.4928445)
        close(camera.data.shift_x, 0)
        close(camera.data.shift_y, 0)
        close((Vector(camera['wfrl_mount_offset']) - Vector((0, 0, -.2151))).length, 0)
        direction = camera.rotation_euler.to_matrix() @ Vector((0, 0, -1))
        close(math.degrees(direction.angle(Vector((0, 0, -1)))), 7.5)
        visibility = {obj.name: (obj.hide_render, obj.hide_viewport) for obj in scene.objects}
        assert bpy.ops.wfrl.manual_focus() == {'FINISHED'}
        assert control.pose_snapshot(scene) == state and pending.elapsed_s == elapsed
        trial_op('START')
        clock[0] += 1
        control.tick(now=clock[0])
        bpy.context.view_layer.update()
        assert camera.matrix_local == local_camera
        assert {obj.name: (obj.hide_render, obj.hide_viewport) for obj in scene.objects} == visibility
        configuration = pending.configuration
        saved_state = control.pose_snapshot(scene)
        saved_elapsed = pending.elapsed_s
        manual_name = scene.name
        bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'pitch-trial.blend'))
        bpy.ops.wm.open_mainfile(filepath=str(OUT / 'pitch-trial.blend'))
        scene = bpy.data.scenes[manual_name]
        t1, pending = entries(scene)['T1'], scene.wfrl_manual_trial
        assert not t1.running and pending.configuration == configuration
        close(pending.elapsed_s, saved_elapsed)
        assert control.pose_snapshot(scene) == saved_state
        assert scene.camera.parent == t1.yaw_object
        trial_op('RESET')
        assert_pose(t1, spec.azimuth_deg, spec.start_pitches)
        assert not t1.individual_pitch
        trial_op('END')
        for index in range(1, 4):
            setattr(pending, f'select_b{index}', False)
        before = control.pose_snapshot(scene)
        try:
            control.confirm_trial(scene)
        except ValueError:
            pass
        else:
            raise AssertionError('No-selection trial was accepted')
        assert control.pose_snapshot(scene) == before and not control.trial_active(scene)
        pending.select_b1, t1.rpm = True, 0
        before = control.pose_snapshot(scene)
        try:
            control.confirm_trial(scene)
        except ValueError:
            pass
        else:
            raise AssertionError('Zero-RPM trial was accepted')
        assert control.pose_snapshot(scene) == before and not control.trial_active(scene)
        assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
        source = bpy.context.scene
        assert set(geometry(source)) == set(source_before)
        assert not any(obj.name.startswith('WFRL.Turbine.') for obj in bpy.data.objects)

        addon.load_demo_scene(camera_rig=False)
        source = bpy.context.scene
        source.frame_set(121)
        source_state = geometry(source)
        source_time = clearance.sample(source)['time_s']
        source_reader = clearance.reader_for(source)
        assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
        scene = bpy.context.scene
        entries(scene)['T1'].rpm = 9
        scene.wfrl_manual_trial.duration_s = 8
        trial_op('CONFIRM')
        clock[0] += 4
        control.tick(now=clock[0])
        assert bpy.ops.wfrl.manual_down() == {'FINISHED'}
        assert geometry(source) == source_state
        assert clearance.reader_for(source) is source_reader
        assert clearance.sample(source)['time_s'] == source_time
        assert not farm.is_active(scene) and clearance.reader_for(scene) is None
        assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
        assert bpy.context.scene == source and geometry(source) == source_state
        assert not entries(scene)['T1'].running
    result = dict(status='PASS', addon_module=MODULE, addon_file=addon.__file__,
                  blade_model='rootkink', nine_blade_geometry_matches_asset=True,
                  blender_version=bpy.app.version_string, selection_cases=len(cases),
                  pending_targets=True, effective_start_pitch=True, constant_rpm=True,
                  native_axis_and_rotation_mode_edits_rejected=True,
                  rejected_confirmation_atomic_while_running=True, periodic_angles_accepted=True,
                  continuous_phase=True, shared_clock_pause_resume_reset=True,
                  unselected_blades_preserved=True, saved_trial_restored_paused=True,
                  independent_down_without_demo=True, down_same_geometry=True,
                  shared_camera_data_rejected=True,
                  optical_definition=True, occlusion_objects_retained=True,
                  demo_isolation_and_return=True, other_turbines_preserved=True,
                  boundary='Source background software checks; no output or physical validation')
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2))
    print('MANUAL_PITCH_TRIAL_PASS ' + json.dumps(result), flush=True)
finally:
    addon.unregister()
