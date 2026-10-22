"""Native manual-pose presets, relative edits and file persistence checks."""
import copy
import importlib
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
from mathutils import Vector


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

FIELDS = ('yaw_deg', 'pitch_deg', 'rpm', 'azimuth_deg', 'individual_pitch',
          'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg')


def entries(scene):
    return {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}


def parameters(scene):
    return {tid: {field: getattr(entry, field) for field in (*FIELDS, 'running')}
            for tid, entry in entries(scene).items()}


def close(actual, expected, tolerance=1e-4):
    assert math.isclose(actual, expected, abs_tol=tolerance, rel_tol=0), (actual, expected)


def assert_parameters(actual, expected):
    assert actual.keys() == expected.keys()
    for tid, values in expected.items():
        assert actual[tid].keys() == values.keys()
        for field, value in values.items():
            if isinstance(value, bool):
                assert actual[tid][field] is value, (tid, field, actual[tid][field], value)
            else:
                close(actual[tid][field], value)


def action(value):
    assert bpy.ops.wfrl.manual_action(action=value) == {'FINISHED'}, value


def preset(value):
    assert bpy.ops.wfrl.manual_pose_preset(preset=value) == {'FINISHED'}, value


def nudge(field, delta):
    assert bpy.ops.wfrl.manual_pose_nudge(field=field, delta=delta) == {'FINISHED'}, field


def rejected(call):
    try:
        result = call()
    except (ValueError, RuntimeError):
        return
    assert result == {'CANCELLED'}, result


def assert_preserved(before, after, *, changed_tids=(), changed_fields=()):
    for tid, values in before.items():
        for field, value in values.items():
            if tid in changed_tids and field in changed_fields:
                continue
            assert after[tid][field] == value, (tid, field, value, after[tid][field])


def matrices(scene):
    for view_layer in scene.view_layers:
        view_layer.update()
    return {obj.name: tuple(value for row in obj.matrix_world for value in row)
            for obj in scene.objects}


def assert_matrices(scene, expected):
    actual = matrices(scene)
    assert actual.keys() == expected.keys()
    for name, values in expected.items():
        for component, (after, before) in enumerate(zip(actual[name], values)):
            assert math.isclose(after, before, abs_tol=1e-5, rel_tol=0), (name, component, after, before)


def span_direction(entry):
    bpy.context.view_layer.update()
    return (entry.blade1_object.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()


def configure_distinct_poses(scene):
    for index, entry in enumerate(entries(scene).values(), 1):
        entry.yaw_deg = float(index * 11)
        entry.pitch_deg = float(index * 7)
        entry.individual_pitch = True
        entry.pitch_b1_deg = float(index * 3)
        entry.pitch_b2_deg = float(index * 5)
        entry.pitch_b3_deg = float(index * 9)
        entry.rpm = float(index * 4)
        entry.azimuth_deg = float(index * 13)


def run():
    source = bpy.context.scene
    assert 'Cube' in source.objects, 'Use --factory-startup for the blank-scene check'
    assert not any(obj.name.startswith('WFRL.Turbine.') for obj in bpy.data.objects)
    source_before = matrices(source)
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    scene = bpy.context.scene
    assert scene != source and control.validate_scene(scene)[0]
    assert set(entries(scene)) == {'T1', 'T2', 'T3'}
    configure_distinct_poses(scene)

    scene.wfrl_manual_target = 'ALL'
    action('START')
    assert all(entry.running for entry in entries(scene).values())
    scene.wfrl_manual_target = 'T1'
    before = parameters(scene)
    preset('BLADE_DOWN')
    after = parameters(scene)
    close(after['T1']['azimuth_deg'], 180.0)
    assert not after['T1']['running']
    assert span_direction(entries(scene)['T1']).z < -.98
    assert_preserved(before, after, changed_tids=('T1',),
                     changed_fields=('azimuth_deg', 'running'))

    before = after
    preset('BLADE_SIDE')
    after = parameters(scene)
    close(after['T1']['azimuth_deg'], 90.0)
    assert abs(span_direction(entries(scene)['T1']).z) < .02
    assert_preserved(before, after, changed_tids=('T1',),
                     changed_fields=('azimuth_deg', 'running'))

    before = after
    preset('FEATHER')
    after = parameters(scene)
    assert not after['T1']['individual_pitch'] and not after['T1']['running']
    for field in ('pitch_deg', 'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg'):
        close(after['T1'][field], 90.0)
    assert_preserved(before, after, changed_tids=('T1',),
                     changed_fields=('pitch_deg', 'pitch_b1_deg', 'pitch_b2_deg',
                                     'pitch_b3_deg', 'individual_pitch', 'running'))

    scene.wfrl_manual_target = 'ALL'
    action('PAUSE')
    configure_distinct_poses(scene)
    before = parameters(scene)
    nudge('yaw_deg', 2.5)
    after = parameters(scene)
    for tid in before:
        close(after[tid]['yaw_deg'], before[tid]['yaw_deg'] + 2.5)
    assert_preserved(before, after, changed_tids=tuple(before), changed_fields=('yaw_deg',))

    before = after
    nudge('pitch_deg', 1.0)
    after = parameters(scene)
    for tid in before:
        close(after[tid]['pitch_deg'], before[tid]['pitch_deg'] + 1.0)
        for field in ('pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg'):
            close(after[tid][field], after[tid]['pitch_deg'])
    assert_preserved(before, after, changed_tids=tuple(before),
                     changed_fields=('pitch_deg', 'pitch_b1_deg', 'pitch_b2_deg', 'pitch_b3_deg'))

    for index, entry in enumerate(entries(scene).values(), 1):
        entry.pitch_b2_deg = float(index * 8)
    before = parameters(scene)
    nudge('pitch_b2_deg', -.5)
    after = parameters(scene)
    for tid in before:
        close(after[tid]['pitch_b2_deg'], before[tid]['pitch_b2_deg'] - .5)
        close(math.degrees(entries(scene)[tid].blade2_object.rotation_euler.z),
              -after[tid]['pitch_b2_deg'])
    assert_preserved(before, after, changed_tids=tuple(before), changed_fields=('pitch_b2_deg',))

    for entry, phase in zip(entries(scene).values(), (359.25, .5, 358.0)):
        entry.azimuth_deg = phase
    before = parameters(scene)
    nudge('azimuth_deg', 2.0)
    after = parameters(scene)
    for tid in before:
        close(after[tid]['azimuth_deg'], (before[tid]['azimuth_deg'] + 2.0) % 360.0)
    assert_preserved(before, after, changed_tids=tuple(before), changed_fields=('azimuth_deg',))

    action('START')
    before = parameters(scene)
    rejected(lambda: bpy.ops.wfrl.manual_pose_nudge(field='azimuth_deg', delta=1.0))
    assert parameters(scene) == before
    nudge('yaw_deg', 1.0)
    after = parameters(scene)
    for tid in before:
        assert after[tid]['running']
        close(after[tid]['yaw_deg'], before[tid]['yaw_deg'] + 1.0)
    assert_preserved(before, after, changed_tids=tuple(before), changed_fields=('yaw_deg',))
    before = after
    nudge('pitch_b2_deg', .5)
    after = parameters(scene)
    for tid in before:
        assert after[tid]['running']
        close(after[tid]['pitch_b2_deg'], before[tid]['pitch_b2_deg'] + .5)
    assert_preserved(before, after, changed_tids=tuple(before), changed_fields=('pitch_b2_deg',))
    action('PAUSE')

    for entry, yaw in zip(entries(scene).values(), (179.0, -179.0, 0.0)):
        entry.yaw_deg = yaw
    nudge('yaw_deg', 5.0)
    for entry, yaw in zip(entries(scene).values(), (180.0, -174.0, 5.0)):
        close(entry.yaw_deg, yaw)
    nudge('yaw_deg', -500.0)
    assert all(entry.yaw_deg == -180.0 for entry in entries(scene).values())
    for entry, pitch in zip(entries(scene).values(), (89.0, -29.0, 10.0)):
        entry.pitch_deg = pitch
    nudge('pitch_deg', 5.0)
    for entry, pitch in zip(entries(scene).values(), (90.0, -24.0, 15.0)):
        close(entry.pitch_deg, pitch)
    nudge('pitch_deg', -500.0)
    assert all(entry.pitch_deg == -30.0 for entry in entries(scene).values())

    configure_distinct_poses(scene)
    saved = parameters(scene)
    scene.wfrl_manual_target = 'T2'
    pose_path = OUT / 'manual-poses.json'
    assert bpy.ops.wfrl.manual_pose_export(filepath=str(pose_path)) == {'FINISHED'}
    payload = json.loads(pose_path.read_text(encoding='utf-8'))
    assert payload['schema'] == 'wfrl.manual-pose.v1'
    assert set(payload['turbines']) == {'T1', 'T2', 'T3'}
    for tid, values in payload['turbines'].items():
        assert set(values) == set(FIELDS)
        assert 'running' not in values
        for field, value in values.items():
            assert value == saved[tid][field]

    for entry in entries(scene).values():
        entry.yaw_deg = -45.0
        entry.pitch_deg = 31.0
        entry.azimuth_deg = 123.0
        entry.rpm = 7.0
    scene.wfrl_manual_target = 'T3'
    action('START')
    scene.wfrl_manual_target = 'T2'
    before = parameters(scene)
    assert bpy.ops.wfrl.manual_pose_import(filepath=str(pose_path)) == {'FINISHED'}
    after = parameters(scene)
    assert_parameters({'T2': after['T2']}, {'T2': saved['T2']})
    assert_preserved(before, after, changed_tids=('T2',), changed_fields=(*FIELDS, 'running'))
    assert after['T3']['running']

    scene.wfrl_manual_target = 'ALL'
    before = parameters(scene)
    missing = copy.deepcopy(payload)
    del missing['turbines']['T3']
    missing_path = OUT / 'missing-turbine.json'
    missing_path.write_text(json.dumps(missing), encoding='utf-8')
    rejected(lambda: bpy.ops.wfrl.manual_pose_import(filepath=str(missing_path)))
    assert parameters(scene) == before
    invalid = copy.deepcopy(payload)
    invalid['turbines']['T1']['yaw_deg'] = 22.0
    invalid['turbines']['T2']['yaw_deg'] = 181.0
    invalid_path = OUT / 'invalid-range.json'
    invalid_path.write_text(json.dumps(invalid), encoding='utf-8')
    rejected(lambda: bpy.ops.wfrl.manual_pose_import(filepath=str(invalid_path)))
    assert parameters(scene) == before

    assert bpy.ops.wfrl.manual_pose_import(filepath=str(pose_path)) == {'FINISHED'}
    assert_parameters(parameters(scene), saved)
    assert all(not entry.running for entry in entries(scene).values())
    assert_matrices(source, source_before)

    saved_geometry = matrices(scene)
    manual_name, source_name = scene.name, source.name
    saved_path = OUT / 'manual-poses.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(saved_path)) == {'FINISHED'}
    assert bpy.ops.wm.open_mainfile(filepath=str(saved_path)) == {'FINISHED'}
    scene, source = bpy.data.scenes[manual_name], bpy.data.scenes[source_name]
    assert bpy.context.scene == scene and control.validate_scene(scene)[0]
    assert_parameters(parameters(scene), saved)
    assert_matrices(scene, saved_geometry)
    assert_matrices(source, source_before)
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    assert bpy.context.scene == source

    result = dict(status='PASS', scope='Isolated background Blender software regression',
                  addon_module=MODULE, addon_file=addon.__file__,
                  independent_factory_scene=True, presets_and_geometry=True,
                  selected_turbine_isolation=True, relative_batch_nudges=True,
                  collective_and_individual_pitch=True, nudge_bounds=True,
                  running_phase_guard=True, running_yaw_pitch_preserved=True,
                  export_all_turbines=True, import_selected_turbine=True,
                  import_atomic_validation=True, import_paused=True,
                  source_scene_preserved=True, save_reopen_paused=True)
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MANUAL_POSE_REGRESSION_PASS ' + json.dumps(result), flush=True)


addon.register()
try:
    with patch.object(control.time, 'monotonic', return_value=100.0):
        run()
finally:
    addon.unregister()
