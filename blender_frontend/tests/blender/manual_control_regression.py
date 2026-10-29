"""Native manual-control motion, persistence, and MAPPO isolation checks."""
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
from unittest.mock import patch

import bpy
from mathutils import Euler, Matrix, Vector
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
control = importlib.import_module(MODULE + '.manual_control')
manual_scene = importlib.import_module(MODULE + '.manual_scene')
farm_flex = importlib.import_module(MODULE + '.farm_flex')
clearance = importlib.import_module(MODULE + '.clearance_replay')
runtime = importlib.import_module(MODULE + '.runtime')
live_scene = importlib.import_module(MODULE + '.live_scene')
expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected:
    assert Path(addon.__file__).resolve().parent == Path(expected).resolve()
OUT = Path(os.environ['WFRL_TEST_OUTPUT'])
assert not OUT.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
OUT.mkdir(parents=True)


def vertices(obj):
    values = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', values)
    return values.reshape((-1, 3))


def snapshot(scene, prefix, *, identities=True):
    bpy.context.view_layer.update()
    result = {}
    for obj in scene.objects:
        if not obj.name.startswith(prefix):
            continue
        item = dict(matrix=tuple(value for row in obj.matrix_world for value in row),
                    parent=obj.parent.name if obj.parent else None,
                    hidden=(obj.hide_viewport, obj.hide_render))
        if identities:
            item['object'] = obj.as_pointer()
        if obj.type == 'MESH':
            item['vertices'] = hashlib.sha256(vertices(obj).tobytes()).hexdigest()
            item['materials'] = tuple((mat.name, tuple(mat.diffuse_color),
                                       mat.roughness, mat.metallic)
                                      for mat in obj.data.materials if mat)
            if identities:
                item['mesh'] = obj.data.as_pointer()
                item['material_ids'] = tuple(mat.as_pointer()
                                             for mat in obj.data.materials if mat)
        result[obj.name] = item
    assert result, (scene.name, prefix)
    return result


def entries(scene):
    return {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}


def parameters(scene):
    return {entry.turbine_id: (entry.yaw_deg, entry.pitch_deg, entry.rpm,
                               entry.azimuth_deg, entry.running, entry.individual_pitch,
                               entry.pitch_b1_deg, entry.pitch_b2_deg, entry.pitch_b3_deg)
            for entry in scene.wfrl_manual_turbines}


def action(value):
    assert bpy.ops.wfrl.manual_action(action=value) == {'FINISHED'}, value


def close(a, b, tolerance=1e-5):
    assert abs(a - b) < tolerance, (a, b)


def angle_delta(after, before):
    return (after - before) % 360.0


def rejected(call):
    try:
        result = call()
    except (ValueError, RuntimeError):
        return
    if result == {'CANCELLED'}:
        return
    raise AssertionError('Manual scene accepted a demo/backend writer')


def view_snapshot():
    result = []
    for index, area in enumerate(bpy.context.screen.areas):
        if area.type != 'VIEW_3D':
            continue
        space, region = area.spaces.active, area.spaces.active.region_3d
        result.append(dict(area_index=index, location=tuple(region.view_location),
                           rotation=tuple(region.view_rotation), distance=region.view_distance,
                           perspective=region.view_perspective,
                           camera=space.camera.name if space.camera else None,
                           local_camera=space.use_local_camera,
                           clip_start=space.clip_start, clip_end=space.clip_end,
                           shading=space.shading.type, color_type=space.shading.color_type,
                           overlays=space.overlay.show_overlays, sidebar=space.show_region_ui))
    assert result, 'Viewport persistence requires a VIEW_3D area'
    return result


def distinctive_source_views(source):
    for index, area in enumerate(bpy.context.screen.areas):
        if area.type != 'VIEW_3D':
            continue
        space, region = area.spaces.active, area.spaces.active.region_3d
        region.view_location = (17.25 + index, -23.5, 41.75)
        region.view_rotation = Euler((.3, -.5, .7 + index * .1), 'XYZ').to_quaternion()
        region.view_distance = 287.5 + index
        region.view_perspective = 'ORTHO'
        space.camera = source.camera
        space.use_local_camera = True
        space.clip_start, space.clip_end = .25, 4321.0
        space.shading.type, space.shading.color_type = 'WIREFRAME', 'OBJECT'
        space.overlay.show_overlays = True
        space.show_region_ui = False
    return view_snapshot()


def assert_views_match(expected):
    actual = view_snapshot()
    assert len(actual) == len(expected)
    numeric = {'location', 'rotation', 'distance', 'clip_start', 'clip_end'}
    for before, after in zip(expected, actual):
        for key in before:
            if key in numeric:
                np.testing.assert_allclose(after[key], before[key], atol=1e-5, rtol=1e-6,
                                           err_msg=f'Source viewport {key} was not restored')
            else:
                assert after[key] == before[key], (key, before[key], after[key])


def run_empty_regression():
    source = bpy.context.scene
    assert 'Cube' in source.objects, 'Use --factory-startup for the blank-scene check'
    assert not farm_flex.is_active(source) and clearance.reader_for(source) is None
    assert not any(obj.name.startswith('WFRL.Turbine.') for obj in bpy.data.objects)
    source_before = snapshot(source, '')
    source_frame = source.frame_current
    source_view = distinctive_source_views(source)
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    manual = bpy.context.scene
    assert manual != source and control.is_manual(manual)
    assert control.validate_scene(manual)[0], control.validate_scene(manual)
    assert set(entries(manual)) == {'T1', 'T2', 'T3'}
    assert source.wfrl_manual_scene == manual and manual.wfrl_manual_return_scene == source
    assert clearance.reader_for(manual) is None and not farm_flex.is_active(manual)
    t1 = entries(manual)['T1']
    manual.wfrl_manual_target = 'T1'
    clock = [100.0]
    with patch.object(control.time, 'monotonic', side_effect=lambda: clock[0]):
        t1.yaw_deg, t1.pitch_deg, t1.rpm, t1.azimuth_deg = 35.0, 17.0, 10.0, 12.0
        close(math.degrees(t1.yaw_object.rotation_euler.z), 35.0, 1e-3)
        close(math.degrees(t1.blade1_object.rotation_euler.z), -17.0, 1e-3)
        action('START')
        control.tick(now=clock[0])
        clock[0] += .5
        control.tick(now=clock[0])
        close(t1.azimuth_deg, 42.0, 1e-3)
        action('PAUSE')
        assert not t1.running and t1.rpm == 10.0
        held = t1.azimuth_deg
        clock[0] += .5
        control.tick(now=clock[0])
        close(t1.azimuth_deg, held)
    assert snapshot(source, '') == source_before and source.frame_current == source_frame
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    assert bpy.context.scene == source
    assert snapshot(source, '') == source_before and source.frame_current == source_frame
    assert_views_match(source_view)
    assert not farm_flex.is_active(source) and clearance.reader_for(source) is None
    assert not any(obj.name.startswith('WFRL.Turbine.') for obj in bpy.data.objects)
    addon.unregister()
    assert not bpy.app.timers.is_registered(control._timer)
    result = dict(status='PASS', scope='Factory startup without a loaded demo',
                  addon_module=MODULE, addon_file=addon.__file__, demo_loaded=False,
                  controls_without_demo=True, source_cube_preserved=True,
                  yaw_pitch_rpm=True, pause_and_return=True, source_viewport_restore=True)
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MANUAL_CONTROL_EMPTY_PASS ' + json.dumps(result), flush=True)



def run_demo_regression():
    addon.load_demo_scene(camera_rig=False)
    source = bpy.context.scene
    source.name = 'Manual Control Source MAPPO'
    source.frame_set(121)
    source.use_fake_user = True
    source_blade = source.objects['WFRL.Turbine.T1.Blade1']
    source_material = source_blade.data.materials[0]
    source_material.diffuse_color = (.12, .31, .63, 1.0)
    source_material.roughness = .37
    source_before = snapshot(source, 'WFRL.Turbine.')
    source_reader = clearance.reader_for(source)
    source_runtime = farm_flex.active_for(source)
    source_time = clearance.sample(source)['time_s']
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    manual = bpy.context.scene
    assert manual != source and manual.get('wfrl_scene_kind') == 'manual'
    assert source.wfrl_manual_scene == manual
    assert manual.wfrl_manual_return_scene == source
    assert control.validate_scene(manual)[0], control.validate_scene(manual)
    assert clearance.reader_for(manual) is None and not farm_flex.is_active(manual)
    assert snapshot(source, 'WFRL.Turbine.') == source_before
    assert clearance.reader_for(source) is source_reader
    assert farm_flex.active_for(source) is source_runtime
    assert clearance.sample(source)['time_s'] == source_time
    assert source.frame_current == 121
    assert set(entries(manual)) == {'T1', 'T2', 'T3'}
    assert all(not entry.running for entry in entries(manual).values())

    source_meshes = {obj.data.as_pointer() for obj in source.objects if obj.type == 'MESH'}
    source_materials = {mat.as_pointer() for obj in source.objects if obj.type == 'MESH'
                        for mat in obj.data.materials if mat}
    manual_meshes = {obj.data.as_pointer() for obj in manual.objects if obj.type == 'MESH'}
    manual_materials = {mat.as_pointer() for obj in manual.objects if obj.type == 'MESH'
                        for mat in obj.data.materials if mat}
    assert manual_meshes and not (source_meshes & manual_meshes)
    assert manual_materials and not (source_materials & manual_materials)
    assert not any(obj.name.startswith('WFRL.Turbine.') for obj in manual.objects)
    next(mat for obj in manual.objects if obj.type == 'MESH'
         for mat in obj.data.materials if mat).diffuse_color = (.7, .2, .1, 1.0)
    assert snapshot(source, 'WFRL.Turbine.') == source_before

    manual.wfrl_manual_target = 'T1'
    t1 = entries(manual)['T1']
    assert tuple(control.selected_entries(manual)) == (t1,)
    assert t1.pitch_deg == control.FEATHER_PITCH_DEG  # a stopped turbine enters feathered
    # Pitch geometry is checked at once; the default ramp is tested separately.
    manual.wfrl_manual_pitch_rate_deg_s = 0.0
    t1.yaw_deg = t1.pitch_deg = t1.azimuth_deg = 0.0
    control.apply_scene(manual)
    bpy.context.view_layer.update()
    blade = t1.blade1_object
    span_before = (blade.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
    chord_before = (blade.matrix_world.to_3x3() @ Vector((1, 0, 0))).normalized()
    yaw_matrix = t1.yaw_object.matrix_world.copy()
    rotor_location = t1.rotor_object.matrix_world.translation.copy()
    t1.pitch_deg = 28.0
    control.apply_scene(manual)
    bpy.context.view_layer.update()
    span_after = (blade.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
    chord_after = (blade.matrix_world.to_3x3() @ Vector((1, 0, 0))).normalized()
    assert (span_after - span_before).length < 1e-5
    close(math.degrees(chord_before.angle(chord_after)), 28.0, 1e-3)
    for index in (1, 2, 3):
        close(math.degrees(getattr(t1, f'blade{index}_object').rotation_euler.z), -28.0, 1e-3)
    t1.individual_pitch = True
    for index, pitch in enumerate((3.0, 13.0, 29.0), 1):
        setattr(t1, f'pitch_b{index}_deg', pitch)
        close(math.degrees(getattr(t1, f'blade{index}_object').rotation_euler.z), -pitch, 1e-3)
    t1.individual_pitch = False
    t1.yaw_deg = 57.0
    control.apply_scene(manual)
    bpy.context.view_layer.update()
    expected_rotor = yaw_matrix.translation + Matrix.Rotation(math.radians(57), 3, 'Z') @ (
        rotor_location - yaw_matrix.translation)
    assert (t1.rotor_object.matrix_world.translation - expected_rotor).length < 1e-4
    close(math.degrees(t1.yaw_object.rotation_euler.z), 57.0, 1e-3)
    assert entries(manual)['T2'].yaw_deg == 0.0
    assert entries(manual)['T2'].pitch_deg == control.FEATHER_PITCH_DEG
    assert snapshot(source, 'WFRL.Turbine.') == source_before

    for call in (lambda: addon.load_demo_scene(camera_rig=False), addon.build_demo_geometry,
                 lambda: runtime.select_mode('demo'), lambda: runtime.connect(8765),
                 lambda: live_scene.build_live_scene(manual)):
        rejected(call)
    assert snapshot(source, 'WFRL.Turbine.') == source_before
    assert clearance.reader_for(source) is source_reader
    assert clearance.reader_for(manual) is None

    # An explicit clock makes speed and pause checks independent of machine load.
    clock = [100.0]
    with patch.object(control.time, 'monotonic', side_effect=lambda: clock[0]):
        t1.rpm = 12.0
        t1.azimuth_deg = 14.0
        action('START')
        control.tick(now=clock[0])
        phase = t1.azimuth_deg
        clock[0] += .25
        control.tick(now=clock[0])
        close(angle_delta(t1.azimuth_deg, phase), 18.0, 1e-3)
        phase = t1.azimuth_deg
        t1.rpm = 20.0
        close(t1.azimuth_deg, phase)
        clock[0] += .25
        control.tick(now=clock[0])
        close(angle_delta(t1.azimuth_deg, phase), 30.0, 1e-3)
        action('PAUSE')
        phase = t1.azimuth_deg
        assert not t1.running and t1.rpm == 20.0
        clock[0] += .25
        control.tick(now=clock[0])
        close(t1.azimuth_deg, phase)
        action('START')
        control.tick(now=clock[0])
        clock[0] += .25
        control.tick(now=clock[0])
        close(angle_delta(t1.azimuth_deg, phase), 30.0, 1e-3)
        action('STOP')
        phase = t1.azimuth_deg
        assert not t1.running and t1.rpm == 0.0
        clock[0] += .25
        control.tick(now=clock[0])
        close(t1.azimuth_deg, phase)
        action('RESET')
        assert not t1.running
        assert (t1.yaw_deg, t1.pitch_deg, t1.azimuth_deg) == (0.0, control.FEATHER_PITCH_DEG, 0.0)
        t1.yaw_deg = 13.0
        entries(manual)['T2'].pitch_deg = 7.0
        before_selection = parameters(manual)
        manual.wfrl_manual_target = 'ALL'
        assert parameters(manual) == before_selection
        assert {entry.turbine_id for entry in control.selected_entries(manual)} == {'T1', 'T2', 'T3'}
        manual.wfrl_manual_batch.rpm = 11.0
        assert all(entry.rpm == 11.0 for entry in entries(manual).values())
        assert t1.yaw_deg == 13.0 and entries(manual)['T2'].pitch_deg == 7.0
        manual.wfrl_manual_batch.yaw_deg = -23.0
        assert all(entry.yaw_deg == -23.0 for entry in entries(manual).values())
        assert entries(manual)['T2'].pitch_deg == 7.0
        for index, entry in enumerate(entries(manual).values(), 1):
            entry.rpm = float(index * 4)
        action('START')
        control.tick(now=clock[0])
        phases = {tid: entry.azimuth_deg for tid, entry in entries(manual).items()}
        clock[0] += .25
        control.tick(now=clock[0])
        for tid, entry in entries(manual).items():
            assert entry.running
            close(angle_delta(entry.azimuth_deg, phases[tid]), 1.5 * entry.rpm, 1e-3)
        action('PAUSE')
        assert all(not entry.running for entry in entries(manual).values())

    held = parameters(manual)
    manual.frame_set(9000)
    assert parameters(manual) == held

    assert snapshot(source, 'WFRL.Turbine.') == source_before
    manual_before = snapshot(manual, 'WFRLManual.')
    saved_parameters = parameters(manual)
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    assert bpy.context.scene == source
    assert snapshot(source, 'WFRL.Turbine.') == source_before
    addon.load_demo_scene(camera_rig=False)
    assert snapshot(manual, 'WFRLManual.') == manual_before
    assert parameters(manual) == saved_parameters
    source_view = distinctive_source_views(source)
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    assert bpy.context.scene == manual and parameters(manual) == saved_parameters

    linked = manual.copy()
    linked.name = 'Manual Linked Copy'
    valid, reason = control.validate_scene(linked)
    assert not valid and reason, 'Linked scene acquired another writer for the same manual objects'
    bpy.context.window.scene = linked
    rejected(lambda: bpy.ops.wfrl.manual_action(action='START'))
    assert parameters(manual) == saved_parameters
    bpy.context.window.scene = manual
    bpy.data.scenes.remove(linked)
    assert control.validate_scene(manual)[0]

    manual.wfrl_manual_target = 'T2'
    entries(manual)['T2'].yaw_deg = -42.0
    entries(manual)['T2'].pitch_deg = 16.0
    entries(manual)['T2'].rpm = 9.0
    entries(manual)['T2'].individual_pitch = True
    entries(manual)['T2'].pitch_b2_deg = 22.0
    action('START')
    saved_parameters = parameters(manual)
    saved_geometry = snapshot(manual, 'WFRLManual.', identities=False)
    manual_name, source_name = manual.name, source.name
    saved_path = OUT / 'manual-controls.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_path))
    bpy.ops.wm.open_mainfile(filepath=str(saved_path))
    manual, source = bpy.data.scenes[manual_name], bpy.data.scenes[source_name]
    assert bpy.context.scene == manual
    assert manual.wfrl_manual_return_scene == source and source.wfrl_manual_scene == manual
    assert control.validate_scene(manual)[0], control.validate_scene(manual)
    for tid, entry in entries(manual).items():
        assert (entry.yaw_deg, entry.pitch_deg, entry.rpm, entry.azimuth_deg) == saved_parameters[tid][:4]
        assert not entry.running
        assert (entry.individual_pitch, entry.pitch_b1_deg, entry.pitch_b2_deg,
                entry.pitch_b3_deg) == saved_parameters[tid][5:]
        for prop in ('yaw_object', 'rotor_object', 'blade1_object', 'blade2_object', 'blade3_object'):
            assert getattr(entry, prop).name in manual.objects
    assert snapshot(manual, 'WFRLManual.', identities=False) == saved_geometry
    assert bpy.ops.wfrl.manual_leave() == {'FINISHED'}
    assert bpy.context.scene == source and farm_flex.is_active(source)
    assert_views_match(source_view)
    source.frame_set(121)
    before = vertices(source.objects['WFRL.Turbine.T1.Blade1'])
    source.frame_set(181)
    after = vertices(source.objects['WFRL.Turbine.T1.Blade1'])
    mesh_change = float(np.max(np.linalg.norm(after - before, axis=1)))
    assert mesh_change > .01, mesh_change
    assert clearance.sample(source)['time_s'] == float(farm_flex.active_for(source).times[0]) + 3.0
    assert snapshot(manual, 'WFRLManual.', identities=False) == saved_geometry

    for _ in range(2):
        addon.unregister()
        assert not bpy.app.timers.is_registered(control._timer)
        assert not control._CLOCKS
        assert all(function not in getattr(bpy.app.handlers, name)
                   for name, function in control._HANDLERS)
        addon.register()
        assert control.validate_scene(manual)[0]
        assert bpy.app.timers.is_registered(control._timer)
        assert all(getattr(bpy.app.handlers, name).count(function) == 1
                   for name, function in control._HANDLERS)
    addon.unregister()
    assert not bpy.app.timers.is_registered(control._timer)
    result = dict(status='PASS', scope='Isolated background Blender software regression',
                  addon_module=MODULE, addon_file=addon.__file__, source_scene=source_name,
                  manual_scene=manual_name, isolated_meshes=len(manual_meshes),
                  isolated_materials=len(manual_materials), yaw_pitch_axes=True, individual_pitch=True,
                  rpm_phase_continuity=True, pause_stop_reset=True, all_targets=True,
                  demo_writer_guards=True, demo_reload_isolation=True,
                  linked_scene_rejected=True, save_reopen_paused=True,
                  source_viewport_restore=True, source_viewport_count=len(source_view),
                  demo_mesh_change_max_m=mesh_change, unregister_timer_cleanup=True)
    (OUT / 'checks.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('MANUAL_CONTROL_REGRESSION_PASS ' + json.dumps(result), flush=True)


addon.register()
if os.environ.get('WFRL_MANUAL_EMPTY_ONLY') == '1':
    run_empty_regression()
else:
    run_demo_regression()
