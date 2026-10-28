"""Three nacelle-mounted Down views, saved-camera migration and independent optics."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_TEST_RUNTIME', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()
if MODULE != 'wfrl_blender':
    assert not Path(addon.__file__).resolve().is_relative_to(ROOT / 'blender_frontend')
cameras, farm, clearance, playback, custom = [importlib.import_module(MODULE + '.' + name)
    for name in ('cameras', 'farm_flex', 'clearance_replay', 'playback', 'custom_cameras')]
gimbal = importlib.import_module(MODULE + '.panels.gimbal')
output = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
output.mkdir(parents=True, exist_ok=False)
if MODULE == 'wfrl_blender':
    assert not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'), 'Source checks require --factory-startup'
    addon.register()
else:
    assert MODULE in bpy.context.preferences.addons and hasattr(bpy.types.Scene, 'wfrl_gimbal_kind')
addon.load_demo_scene(farm.default_package())
scene = bpy.context.scene
TURBINES = ('T1', 'T2', 'T3')


def pose(camera):
    return dict(name=camera.name, data=camera.data.name, parent=camera.parent.name,
                location=list(camera.location), rotation=list(camera.rotation_euler),
                scale=list(camera.scale), lens=camera.data.lens,
                parent_inverse=[list(row) for row in camera.matrix_parent_inverse],
                clip_start=camera.data.clip_start, clip_end=camera.data.clip_end,
                passepartout=camera.data.show_passepartout, passepartout_alpha=camera.data.passepartout_alpha,
                sensor_width=camera.data.sensor_width, sensor_height=camera.data.sensor_height,
                sensor_fit=camera.data.sensor_fit, type=camera.data.type,
                shift_x=camera.data.shift_x, shift_y=camera.data.shift_y,
                yaw=camera.get('gimbal_yaw'), pitch=camera.get('gimbal_pitch'),
                roll=camera.get('gimbal_roll'), fov=camera.get('gimbal_fov'))


def assert_default_pose(actual, expected):
    assert (Vector(actual['location']) - Vector(expected['location'])).length < 1e-4, (actual['location'], expected['location'])
    assert {key: value for key, value in actual.items() if key != 'location'} == {
        key: value for key, value in expected.items() if key != 'location'}


def assert_down_axis(camera):
    bpy.context.view_layer.update()
    forward = (camera.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    local_axis = Vector((math.cos(math.radians(-82.5))*math.cos(math.radians(181.2)),
                         math.cos(math.radians(-82.5))*math.sin(math.radians(181.2)),
                         math.sin(math.radians(-82.5))))
    expected = (camera.parent.matrix_world.to_3x3() @ local_axis).normalized()
    assert (forward - expected).length < 1e-6, (camera.name, tuple(forward), tuple(expected))


def assert_nominal(camera, turbine):
    assert camera.parent == scene.objects[f'WFRL.Turbine.{turbine}.YawRoot']
    assert camera.get('wfrl_gimbal_mount_revision') == 5
    assert [camera[key] for key in ('gimbal_yaw', 'gimbal_pitch', 'gimbal_roll', 'gimbal_fov')] == [181.2, -82.5, 175.9, cameras.DOWN_HFOV_DEG]
    assert abs(camera.data.shift_x) < 1e-6 and abs(camera.data.shift_y) < 1e-6
    assert abs(math.degrees(camera.data.angle_x) - cameras.DOWN_HFOV_DEG) < 1e-4
    assert abs(camera.data.lens - 8.0) < 1e-5
    assert abs(math.degrees(2*math.atan(camera.data.sensor_width/(2*camera.data.lens)/(16/9))) - 60) < 1e-4
    assert not camera.data.show_passepartout and camera.data.passepartout_alpha == 0
    assert_down_axis(camera)
    shell = scene.objects[f'WFRL.Turbine.{turbine}.Nacelle'].evaluated_get(bpy.context.evaluated_depsgraph_get())
    local = shell.matrix_local
    inverse = local.inverted()
    hit, point, normal, _ = shell.ray_cast(
        inverse @ Vector((-3.5, -1.15, -5)),
        (inverse.to_3x3() @ Vector((0, 0, 1))).normalized())
    root_normal = (inverse.to_3x3().transposed() @ normal).normalized()
    assert hit
    assert (root_normal - Vector((0, -.55536884, -.83160424))).length < 1e-5, tuple(root_normal)
    anchor = Vector(camera['wfrl_mount_anchor'])
    offset = Vector(camera['wfrl_mount_offset'])
    assert (anchor - local @ point).length < 1e-5
    assert abs(anchor.x + 3.5) < 1e-4 and abs(anchor.y + 1.15) < 1e-4
    assert (offset - Vector((0, 0, -.2151))).length < 1e-6
    assert (camera.location - anchor - offset).length < 1e-6
    local_origin = inverse @ camera.location
    direction = point - local_origin
    hit, entry, _, _ = shell.ray_cast(local_origin, direction.normalized(), distance=direction.length + .01)
    assert hit and (entry - point).length < 1e-4, 'The mounting segment must meet the shell only at its anchor'
    return pose(camera)


def clock_state():
    return dict(frame=scene.frame_current, subframe=scene.frame_subframe,
                time_s=clearance.sample(scene)['time_s'], playing=playback.is_playing(scene))


def context():
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return bpy.context.temp_override(area=area, region=region)


def complete_gate(camera):
    window = SimpleNamespace(type='WINDOW', x=0, y=0, width=1200, height=700)
    sidebar = SimpleNamespace(type='UI', x=900, y=0, width=300, height=700)
    view = SimpleNamespace(view_camera_zoom=0., view_camera_offset=(0., 0.))
    space = SimpleNamespace(camera=camera, use_local_camera=True, region_3d=view,
                            show_region_ui=True, show_region_toolbar=False)
    area = SimpleNamespace(regions=[window, sidebar], spaces=SimpleNamespace(active=space))
    before = pose(camera)
    cameras.fill_camera_view(area, scene)
    assert pose(camera) == before, 'Viewport fitting must preserve camera optics and pose'
    scale = (math.sqrt(2) + view.view_camera_zoom / 50) ** 2 / 4
    aspect = scene.render.resolution_x * scene.render.pixel_aspect_x / (scene.render.resolution_y * scene.render.pixel_aspect_y)
    width, height = window.width * scale, window.width * scale / aspect
    center_x = window.width / 2 - view.view_camera_offset[0] * 2 * scale * window.width
    center_y = window.height / 2 - view.view_camera_offset[1] * 2 * scale * window.height
    bounds = [center_x - width / 2, center_y - height / 2,
              center_x + width / 2, center_y + height / 2]
    assert bounds[0] >= 15.99 and bounds[1] >= 15.99, bounds
    assert bounds[2] <= 884.01 and bounds[3] <= 684.01, bounds
    return bounds


scene.frame_set(902)
downs = {turbine: cameras.ensure_gimbal(scene, turbine) for turbine in TURBINES}
nominal = {}
for turbine, camera in downs.items():
    cameras.down_gimbal(camera)
    nominal[turbine] = assert_nominal(camera, turbine)
    # Shell parent transforms use float32 at each turbine's world offset.
    assert (camera.location - downs['T1'].location).length < 1e-4
independent = [cameras.ensure_nacelle_gimbal(scene, turbine) for turbine in TURBINES]
independent += [custom.get_camera(scene, slot) for slot in (1, 2, 3)]
assert all(camera is not None for camera in independent)
independent_state = {camera.name: pose(camera) for camera in independent}
identities = {turbine: (camera.name, camera.data.name) for turbine, camera in downs.items()}
camera_count = sum(obj.type == 'CAMERA' for obj in scene.objects)

gimbal.change(downs['T1'], dx=7, dy=-2, zoom=-3)
downs['T1'].location += Vector((.23, -.17, .11))
downs['T1'].data.shift_y += .013
t1_custom = pose(downs['T1'])
assert cameras.ensure_gimbal(scene, 'T1') == downs['T1']
assert pose(downs['T1']) == t1_custom
for turbine in ('T2', 'T3'):
    camera = downs[turbine]
    camera['wfrl_gimbal_mount_revision'] = 1
    camera.location = (-3.5, 1.2, -37.8)
    camera['gimbal_roll'] = 180.
    camera.data.shift_x = camera.data.shift_y = 0
    cameras.aim_gimbal(camera, 180, -67, 85)
    del camera['wfrl_mount_anchor']
    del camera['wfrl_mount_offset']
saved_clock = clock_state()
path = output / 'farm-down-legacy.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
assert clock_state() == saved_clock and farm.is_active(scene)
loaded_objects = {turbine: scene.objects[identities[turbine][0]] for turbine in TURBINES}
loaded_data = {turbine: camera.data for turbine, camera in loaded_objects.items()}
downs = {turbine: cameras.ensure_gimbal(scene, turbine) for turbine in TURBINES}
assert pose(downs['T1']) == t1_custom, 'T1 current-revision hand pose must survive another turbine migration'
assert sum(obj.type == 'CAMERA' for obj in scene.objects) == camera_count
for turbine in TURBINES:
    assert downs[turbine] == loaded_objects[turbine] and downs[turbine].data == loaded_data[turbine]
    assert (downs[turbine].name, downs[turbine].data.name) == identities[turbine]
for turbine in ('T2', 'T3'):
    assert_default_pose(assert_nominal(downs[turbine], turbine), nominal[turbine])

scene.frame_set(902, subframe=.25)
gates = {}
with context():
    for turbine in TURBINES:
        before = clock_state()
        assert bpy.ops.wfrl.farm_flex_view(turbine=turbine, angle='DOWN') == {'FINISHED'}
        camera = downs[turbine]
        assert scene.camera == camera and gimbal.current(scene) == camera
        assert clock_state() == before
        assert_nominal(camera, turbine)
        untouched = {other: pose(downs[other]) for other in TURBINES if other != turbine}
        gimbal.change(camera, dx=4, dy=3, zoom=-2)
        camera.data.shift_x += .012
        hand_pose = pose(camera)
        for _ in range(3):
            assert cameras.ensure_gimbal(scene, turbine) == camera
            assert pose(camera) == hand_pose and clock_state() == before
        assert bpy.ops.wfrl.gimbal_preset(preset='RESET') == {'FINISHED'}
        assert_default_pose(assert_nominal(camera, turbine), nominal[turbine])
        assert clock_state() == before
        assert {other: pose(downs[other]) for other in untouched} == untouched
        gates[turbine] = complete_gate(camera)
    before = clock_state()
    for turbine in ('T3', 'T1', 'T2', 'T1'):
        assert bpy.ops.wfrl.farm_flex_view(turbine=turbine, angle='DOWN') == {'FINISHED'}
        assert clock_state() == before and scene.camera == downs[turbine]

fixed_mounts = {turbine: camera.location.copy() for turbine, camera in downs.items()}
motion_errors = dict.fromkeys(TURBINES, 0.)
for frame in (1, 67, 901, 1801, scene.frame_end):
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    for turbine, camera in downs.items():
        assert_down_axis(camera)
        mount = fixed_mounts[turbine]
        assert (camera.location - mount).length < 1e-6
        expected = camera.parent.matrix_world @ mount
        error = (camera.matrix_world.translation - expected).length
        motion_errors[turbine] = max(error, motion_errors[turbine])
        assert error < 1e-3, (turbine, error)
        rotor = scene.objects[f'WFRL.Turbine.{turbine}.Rotor']
        saved_world, saved_rotation = camera.matrix_world.copy(), rotor.rotation_euler.copy()
        rotor.rotation_euler.x += math.radians(73)
        bpy.context.view_layer.update()
        assert max(abs(a - b) for row_a, row_b in zip(camera.matrix_world, saved_world)
                   for a, b in zip(row_a, row_b)) < 1e-6
        rotor.rotation_euler = saved_rotation
        bpy.context.view_layer.update()
for name, state in independent_state.items():
    assert pose(scene.objects[name]) == state, name

scene.frame_set(902)
for turbine, camera in downs.items():
    gimbal.change(camera, dx=3, dy=-1, zoom=-2)
    camera.data.shift_y += .007
saved_poses = {turbine: pose(camera) for turbine, camera in downs.items()}
saved_clock = clock_state()
path = output / 'farm-down-hand-poses.blend'
for camera in downs.values():
    camera.data.show_passepartout = True
    camera.data.passepartout_alpha = 1
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
scene = bpy.context.scene
assert clock_state() == saved_clock
assert all(not obj.data.show_passepartout and obj.data.passepartout_alpha == 0
           for obj in scene.objects if obj.type == 'CAMERA' and obj.name.startswith('WFRL.'))
for turbine, state in saved_poses.items():
    assert pose(cameras.ensure_gimbal(scene, turbine)) == state
for name, state in independent_state.items():
    assert pose(scene.objects[name]) == state, name

result = dict(status='PASS', module=MODULE, addon_file=addon.__file__,
              blender=bpy.app.version_string, default_poses=nominal,
              migrated_revision_one=['T2', 'T3'], t1_current_revision_hand_pose_preserved=True,
              parent_motion_error_m=motion_errors, rotor_spin_independent=True,
              optical_axis_matches_parent_local_preset=True,
              hand_pose_and_reset_each_turbine=True, switch_preserves_replay_clock=True,
              save_reopen_preserves_all_hand_poses=True, independent_nacelle_and_c1_c3_unchanged=True,
              gate_check='1200x700 synthetic viewport with 300px right sidebar', gate_bounds_px=gates,
              visible_window='NOT_RUN')
(output / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
print('FARM_DOWN_MOUNT_PASS', json.dumps(result), flush=True)
