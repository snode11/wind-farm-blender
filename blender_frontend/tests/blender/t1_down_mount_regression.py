"""T1 Down exterior mount, camera persistence and recorded-playback contract."""
from pathlib import Path
import importlib
import json
import math
import os
import sys

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_TEST_RUNTIME', os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender'))
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()
if MODULE != 'wfrl_blender':
    assert not Path(addon.__file__).resolve().is_relative_to(ROOT / 'blender_frontend')
cameras, farm_flex, clearance, playback = [importlib.import_module(MODULE + '.' + name)
    for name in ('cameras', 'farm_flex', 'clearance_replay', 'playback')]
gimbal = importlib.import_module(MODULE + '.panels.gimbal')
output = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
output.mkdir(parents=True, exist_ok=False)
if MODULE == 'wfrl_blender':
    assert not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'), 'Source checks require --factory-startup'
    addon.register()
else:
    assert MODULE in bpy.context.preferences.addons and hasattr(bpy.types.Scene, 'wfrl_gimbal_kind')
addon.load_demo_scene(farm_flex.default_package())
scene = bpy.context.scene


def pose(camera):
    return dict(location=list(camera.location), rotation=list(camera.rotation_euler),
                lens=camera.data.lens, sensor_width=camera.data.sensor_width,
                sensor_height=camera.data.sensor_height,
                shift_x=camera.data.shift_x, shift_y=camera.data.shift_y,
                yaw=camera['gimbal_yaw'], pitch=camera['gimbal_pitch'],
                roll=camera['gimbal_roll'], fov=camera['gimbal_fov'])


def assert_default_pose(camera, expected):
    actual = pose(camera)
    # Recomputed shell transforms use Blender float32 coordinates.
    assert (Vector(actual['location']) - Vector(expected['location'])).length < 1e-5
    assert {key: value for key, value in actual.items() if key != 'location'} == {
        key: value for key, value in expected.items() if key != 'location'}


def assert_nadir(camera):
    bpy.context.view_layer.update()
    forward = (camera.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    parent_down = (camera.parent.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    assert (forward - parent_down).length < 1e-6, (tuple(forward), tuple(parent_down))


def exterior_mount(camera):
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    shell = scene.objects['WFRL.Turbine.T1.Nacelle'].evaluated_get(graph)
    anchor = Vector(camera['wfrl_mount_anchor'])
    offset = Vector(camera['wfrl_mount_offset'])
    assert (offset - Vector((0, 0, -.4))).length < 1e-6
    assert (camera.location - anchor - offset).length < 1e-6
    local = shell.matrix_local
    inverse = local.inverted()
    hit, point, normal, _ = shell.ray_cast(
        inverse @ Vector((-3.8, 0, -5)),
        (inverse.to_3x3() @ Vector((0, 0, 1))).normalized())
    assert hit, 'The mounting anchor must intersect the nacelle underside'
    expected_anchor = local @ point
    assert (anchor - expected_anchor).length < 1e-5
    assert (anchor - Vector((-3.8, 0, .46256))).length < 1e-4
    root_normal = (inverse.to_3x3().transposed() @ normal).normalized()
    assert root_normal.dot(Vector((0, 0, -1))) > .95, 'Anchor must be on the outward underside face'
    local_origin = inverse @ camera.location
    direction = point - local_origin
    hit, entry, _, _ = shell.ray_cast(local_origin, direction.normalized(), distance=direction.length + .01)
    assert hit and (entry - point).length < 1e-4, 'The camera-to-anchor segment must stay outside the shell'
    return dict(anchor=list(anchor), offset=list(offset),
                bracket_length_m=offset.length,
                surface_anchor_error_m=(anchor - expected_anchor).length)


def replay_state():
    return dict(frame=scene.frame_current, subframe=scene.frame_subframe,
                playing=playback.is_playing(scene), reading=clearance.sample(scene))


def view_context():
    area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
    region = next(region for region in area.regions if region.type == 'WINDOW')
    return bpy.context.temp_override(area=area, region=region)


down = cameras.ensure_gimbal(scene, 'T1')
cameras.down_gimbal(down)
root = scene.objects['WFRL.Turbine.T1.YawRoot']
rotor = scene.objects['WFRL.Turbine.T1.Rotor']
assert down.parent == root and down.parent != rotor
assert down.get('wfrl_gimbal_mount_revision') == 3
nominal = pose(down)
assert [nominal[key] for key in ('yaw', 'pitch', 'roll', 'fov')] == [180, -90, 180, 110]
assert abs(nominal['shift_x'] - .1) < 1e-6 and abs(nominal['shift_y'] + .07) < 1e-6
assert abs(math.degrees(down.data.angle_x) - 110) < 1e-4
assert_nadir(down)
mount_geometry = exterior_mount(down)
other_presets = {}
for turbine in ('T2', 'T3'):
    camera = cameras.ensure_gimbal(scene, turbine)
    cameras.down_gimbal(camera)
    assert camera.parent == scene.objects[f'WFRL.Turbine.{turbine}.YawRoot']
    assert camera.get('wfrl_gimbal_mount_revision') == 3
    assert_default_pose(camera, nominal)
    assert_nadir(camera)
    assert camera.data.show_passepartout and camera.data.passepartout_alpha == 1
    other_presets[turbine] = pose(camera)
nacelle = cameras.ensure_nacelle_gimbal(scene, 'T1')
assert nacelle != down and nacelle.data != down.data
nacelle_pose = pose(nacelle)

scene.frame_set(902)
with view_context():
    before = replay_state()
    assert bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='DOWN') == {'FINISHED'}
    assert replay_state() == before
    assert scene.camera == down
    assert_default_pose(down, nominal)
    selected_location = list(down.location)
    gimbal.change(down, dx=7, dy=3, zoom=-4)
    down.data.shift_x += .011
    down.data.shift_y -= .009
    custom = pose(down)
    assert custom != nominal and custom['location'] == selected_location
    for _ in range(3):
        assert cameras.ensure_gimbal(scene, 'T1') == down
        assert pose(down) == custom and replay_state() == before
    assert bpy.ops.wfrl.gimbal_preset(preset='RESET') == {'FINISHED'}
    assert_default_pose(down, nominal)
    assert replay_state() == before
    assert pose(nacelle) == nacelle_pose
    for angle in ('NACELLE', 'DOWN', 'DOWN'):
        assert bpy.ops.wfrl.farm_flex_view(turbine='T1', angle=angle) == {'FINISHED'}
        assert replay_state() == before
    assert_default_pose(down, nominal)
    assert pose(nacelle) == nacelle_pose

mount = down.location.copy()
motion_error = 0.0
for frame in (1, 67, 901, 1801, scene.frame_end):
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    assert_nadir(down)
    assert (down.location - mount).length < 1e-6
    expected = root.matrix_world @ mount
    motion_error = max(motion_error, (down.matrix_world.translation - expected).length)
    assert motion_error < 1e-4
    exterior_mount(down)
    saved_world = down.matrix_world.copy()
    rotor_rotation = rotor.rotation_euler.copy()
    rotor.rotation_euler.x += math.radians(73)
    bpy.context.view_layer.update()
    assert max(abs(a - b) for row_a, row_b in zip(down.matrix_world, saved_world)
               for a, b in zip(row_a, row_b)) < 1e-6
    rotor.rotation_euler = rotor_rotation
    bpy.context.view_layer.update()

scene.frame_set(902)
gimbal.change(down, dx=5, dy=-2, zoom=-3)
down.data.shift_x -= .012
down.data.shift_y += .008
custom = pose(down)
saved_state = replay_state()
current_path = output / 't1-down.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(current_path))
bpy.ops.wm.open_mainfile(filepath=str(current_path))
scene = bpy.context.scene
down = cameras.ensure_gimbal(scene, 'T1')
assert pose(down) == custom and replay_state() == saved_state
assert farm_flex.is_active(scene) and scene.camera == down
exterior_mount(down)

# Saved cameras with either supported mount revision migrate in place.
legacy_mounts = {
    1: dict(location=(-3.5, 1.2, -37.8), yaw=180, pitch=-67, fov=85),
    2: dict(location=(-2.502865, 0, .16256), yaw=180, pitch=-84, fov=85),
}
for revision, legacy in legacy_mounts.items():
    down['wfrl_gimbal_mount_revision'] = revision
    for key in ('wfrl_mount_anchor', 'wfrl_mount_offset'):
        if key in down:
            del down[key]
    down.location = legacy['location']
    down['gimbal_roll'] = 180.0
    down.data.shift_x = down.data.shift_y = 0
    cameras.aim_gimbal(down, legacy['yaw'], legacy['pitch'], legacy['fov'])
    camera_name, data_name = down.name, down.data.name
    camera_count = sum(obj.type == 'CAMERA' for obj in scene.objects)
    legacy_path = output / f't1-down-revision-{revision}.blend'
    bpy.ops.wm.save_as_mainfile(filepath=str(legacy_path))
    bpy.ops.wm.open_mainfile(filepath=str(legacy_path))
    scene = bpy.context.scene
    with view_context():
        before = replay_state()
        assert bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='DOWN') == {'FINISHED'}
        assert replay_state() == before
    down = scene.camera
    assert down.name == camera_name and down.data.name == data_name
    assert down.get('wfrl_gimbal_mount_revision') == 3
    assert sum(obj.type == 'CAMERA' for obj in scene.objects) == camera_count
    assert down.parent == scene.objects['WFRL.Turbine.T1.YawRoot']
    assert_default_pose(down, nominal)
    assert_nadir(down)
    exterior_mount(down)
assert pose(scene.objects['WFRL.Camera.T1.NacelleGimbal']) == nacelle_pose
for turbine, state in other_presets.items():
    assert pose(cameras.ensure_gimbal(scene, turbine)) == state

result = dict(status='PASS', module=MODULE, addon_file=addon.__file__,
              blender=bpy.app.version_string, mount=nominal, exterior_mount=mount_geometry,
              parent_motion_error_m=motion_error, rotor_spin_independent=True,
              optical_axis_matches_parent_negative_z=True,
              repeated_ensure_preserves_ptz=True, reset_restores_mount=True,
              selection_preserves_replay=True, save_reopen_preserves_ptz=True,
              saved_scene_mount_migrations=[1, 2],
              independent_nacelle_unchanged=True, other_down_presets_unchanged=True,
              visible_window='NOT_RUN' if bpy.app.background else 'SCRIPTED_CHECK_ONLY')
(output / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
print('T1_DOWN_MOUNT_PASS', json.dumps(result), flush=True)
