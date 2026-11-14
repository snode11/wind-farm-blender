"""One X100 switches between original and side mounts without changing scene time."""
from pathlib import Path
import importlib
import json
import sys
from types import SimpleNamespace

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.append(str(Path(__file__).resolve().parent))
import _native
MODULE = _native.addon_module()
addon = importlib.import_module(MODULE)
if MODULE == 'wfrl_blender':
    addon.register()
_native.check_addon_root(addon)
farm = importlib.import_module(MODULE + '.farm_flex')
radar = importlib.import_module(MODULE + '.radar_x100.runtime')
scene_api = importlib.import_module(MODULE + '.radar_x100.scene')
capture = importlib.import_module(MODULE + '.custom_camera_capture')
OUT = _native.output_directory()

addon.load_demo_scene(farm.default_package())
scene = bpy.context.scene
scene.wfrl_x100_candidates = 512
frame = 1 + scene['wfrl_clearance_timebase_fps']
scene.frame_set(int(frame), subframe=frame - int(frame))
clock = (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s'))
assert bpy.ops.wfrl.x100_mount_preset(preset='ORIGINAL') == {'FINISHED'}
original = radar.session_for(scene)
original_points = original.frame.points.tobytes()
original_calibration = original.sampler.calibration_id
original_frame, original_source = original.frame, original.source_session
original_mount = original.mount
assert bpy.ops.wfrl.x100_mount_preset(preset='ORIGINAL') == {'FINISHED'}
assert radar.session_for(scene) is original
assert original.frame is original_frame and original.source_session == original_source
np.testing.assert_allclose(scene.wfrl_x100_location, scene_api.ORIGINAL_LOCATION, atol=1e-6)
np.testing.assert_allclose(scene.wfrl_x100_rotation, scene_api.ORIGINAL_ROTATION)
assert bpy.ops.wfrl.x100_mount_preset(preset='NACELLE_SIDE') == {'FINISHED'}
side = radar.session_for(scene)
assert side.mount is original_mount and side.sampler.calibration_id != original_calibration
assert side.source_session != original_source
assert side.frame.source_kind == 'GEOMETRY_SIMULATION' and len(side.frame.points) > 0
assert side.frame.time.source_time_s == clock[2]
assert (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')) == clock
forward = side.sampler.mount_from_sensor[:3, 0]
assert forward[1] < -.95 and abs(forward[2]) < 1e-6
surface = (side.sampler.shell_vertices @ side.sampler.shell_local[:3, :3].T
           + side.sampler.shell_local[:3, 3])
assert scene.wfrl_x100_location[1] < surface[:, 1].min()
assert abs(scene.wfrl_x100_location[2] - surface[:, 2].mean()) < .01
assert len([o for o in scene.objects if o.get('wfrl_x100_role') == 'physical']) == 1
side_location, side_rotation = tuple(scene.wfrl_x100_location), tuple(scene.wfrl_x100_rotation)
side_points = len(side.frame.points)

side.fanout = object()
try:
    try:
        radar.apply_mount_preset(scene, 'ORIGINAL')
    except ValueError:
        pass
    else:
        raise AssertionError('Active recording must reject a calibration switch')
    assert radar.session_for(scene) is side and tuple(scene.wfrl_x100_location) == side_location
finally:
    side.fanout = None
saved_capture = capture._ACTIVE
capture._ACTIVE = SimpleNamespace(scene=scene, closed=False)
try:
    try:
        radar.apply_mount_preset(scene, 'ORIGINAL')
    except ValueError:
        pass
    else:
        raise AssertionError('Camera capture must reject an installation switch')
    assert radar.session_for(scene) is side and tuple(scene.wfrl_x100_location) == side_location
finally:
    capture._ACTIVE = saved_capture

assert bpy.ops.wfrl.x100_mount_preset(preset='ORIGINAL') == {'FINISHED'}
restored = radar.session_for(scene)
assert restored.frame.points.tobytes() == original_points
assert restored.sampler.calibration_id == original_calibration
assert len([o for o in scene.objects if o.get('wfrl_x100_role') == 'physical']) == 1
assert (scene.frame_current, scene.frame_subframe, scene.get('wfrl_clearance_time_s')) == clock

# The Down camera preset co-locates the radar with the T1 Down camera and adopts its rectangular FOV.
assert bpy.ops.wfrl.x100_mount_preset(preset='DOWN_CAMERA') == {'FINISHED'}
down = radar.session_for(scene)
camera = scene.objects['WFRL.Camera.T1.Gimbal']
bpy.context.view_layer.update()
mount_world, camera_world = np.array(down.mount.matrix_world), np.array(camera.matrix_world)
np.testing.assert_allclose(mount_world[:3, 3], camera_world[:3, 3], atol=1e-4)
camera_axes = camera_world[:3, :3] / np.linalg.norm(camera_world[:3, :3], axis=0)
np.testing.assert_allclose(mount_world[:3, 0], -camera_axes[:, 2], atol=1e-4)
np.testing.assert_allclose(mount_world[:3, 2], camera_axes[:, 1], atol=1e-4)
down_fov = (camera.data.angle_x / 2, np.arctan(np.tan(camera.data.angle_x / 2) / (16 / 9)))
np.testing.assert_allclose(down.sampler.field_of_view, np.degrees(down_fov), atol=1e-4)
np.testing.assert_allclose(down.frame.provenance['field_of_view_deg'], down.sampler.field_of_view)
np.testing.assert_allclose(down.fov.data['wfrl_x100_field_of_view_deg'], down.sampler.field_of_view)
# The Down camera preset gates with the camera's image rectangle, not the angular box.
assert down.sampler.field_of_view_shape == scene.wfrl_x100_field_of_view_shape == 'PROJECTIVE'
assert down.frame.provenance['field_of_view_shape'] == 'PROJECTIVE'
assert down.fov.data['wfrl_x100_field_of_view_shape'] == 'PROJECTIVE' and len(down.fov.data.vertices) == 5
sensor_points = np.column_stack([down.frame.points[axis] for axis in 'xyz'])
assert radar.simulation.fov_mask(sensor_points, down.sampler.field_of_view, 'PROJECTIVE').all()
# The radar shares the camera's optical centre: its display housing hides instead of filling the image.
view_api = importlib.import_module(MODULE + '.radar_x100.view')
assert view_api.housing(down.mount).hide_viewport and view_api.housing(down.mount).hide_render
from mathutils import Vector
depsgraph = bpy.context.evaluated_depsgraph_get()
ray = (camera.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
hit = scene.ray_cast(depsgraph, camera.matrix_world.translation + ray * camera.data.clip_start, ray)
assert not hit[0] or hit[4].get('wfrl_x100_role') != 'physical', hit[4]
assert tuple(down.applied_config['field_of_view']) == tuple(scene.wfrl_x100_field_of_view)
assert down.frame.source_kind == 'GEOMETRY_SIMULATION' and len(down.frame.points) > 0
assert down.mount is original_mount and down.frame.time.source_time_s == clock[2]
assert not radar.configuration.pending(scene, down.applied_config, abspath=bpy.path.abspath)
down_points, down_fov_deg = len(down.frame.points), tuple(down.sampler.field_of_view)
assert bpy.ops.wfrl.x100_mount_preset(preset='ORIGINAL') == {'FINISHED'}
restored = radar.session_for(scene)
assert restored.frame.points.tobytes() == original_points
np.testing.assert_allclose(scene.wfrl_x100_field_of_view, (radar.schema.PROFILE['azimuth_deg'],
                                                           radar.schema.PROFILE['elevation_deg']))
np.testing.assert_allclose(restored.fov.data['wfrl_x100_field_of_view_deg'], tuple(scene.wfrl_x100_field_of_view))
assert restored.sampler.field_of_view_shape == scene.wfrl_x100_field_of_view_shape == 'ANGULAR'
assert restored.fov.data['wfrl_x100_field_of_view_shape'] == 'ANGULAR'
assert not view_api.housing(restored.mount).hide_viewport and not view_api.housing(restored.mount).hide_render
assert len([m for m in bpy.data.meshes if m.name.startswith('WFRL.X100.FOV')]) == 1

# The rear hoisting hatch sits below the nacelle tail, 2 m to the side, aimed at the 6 o'clock tip.
assert bpy.ops.wfrl.x100_mount_preset(preset='REAR_HATCH') == {'FINISHED'}
rear = radar.session_for(scene)
rear_location, rear_rotation = scene_api.rear_hatch_installation(rear.sampler)
np.testing.assert_allclose(scene.wfrl_x100_location, rear_location, atol=1e-4)
np.testing.assert_allclose(scene.wfrl_x100_rotation, rear_rotation, atol=1e-4)
shell = scene.objects['WFRL.Turbine.T1.Nacelle']
shell_x = [(shell.matrix_parent_inverse @ shell.matrix_basis @ v.co).x for v in shell.data.vertices]
assert abs(rear_location[0] - (min(shell_x) + max(shell_x)) / 2) > 2, 'rear hatch must be near the nacelle tail'
assert abs(rear_location[1] - scene_api.REAR_HATCH_LATERAL_M) < 0.5
rear_local = scene_api.installation_matrix(rear_location, rear_rotation)
np.testing.assert_allclose(rear.sampler.mount_from_sensor, rear_local, atol=1e-5)
# Sensor axes in the YawRoot frame: boresight down and toward the rotor, azimuth axis level.
assert rear_local[2, 0] < -0.9, rear_local[:3, 0]
assert abs(rear_local[2, 1]) < 1e-6, rear_local[:3, 1]
np.testing.assert_allclose(rear.sampler.field_of_view, (radar.schema.PROFILE['azimuth_deg'],
                                                        radar.schema.PROFILE['elevation_deg']))
assert rear.frame.source_kind == 'GEOMETRY_SIMULATION' and len(rear.frame.points) > 0
assert rear.mount is original_mount and rear.frame.time.source_time_s == clock[2]
rear_points = len(rear.frame.points)
assert bpy.ops.wfrl.x100_mount_preset(preset='ORIGINAL') == {'FINISHED'}
restored = radar.session_for(scene)
assert restored.frame.points.tobytes() == original_points

draft = (4.0, 5.0, 6.0)
scene.wfrl_x100_location = draft
active_frame, active_sampler, active_source = restored.frame, restored.sampler, restored.source_session
active_matrix = restored.mount.matrix_basis.copy()
simulate = radar.simulation.simulate
def fail_sampling(*args, **kwargs):
    raise RuntimeError('Injected sampling failure')
radar.simulation.simulate = fail_sampling
try:
    try:
        radar.apply_mount_preset(scene, 'NACELLE_SIDE')
    except RuntimeError as exc:
        assert str(exc) == 'Injected sampling failure'
    else:
        raise AssertionError('Failed sampling must reject the installation switch')
finally:
    radar.simulation.simulate = simulate
assert radar.session_for(scene) is restored
assert restored.frame is active_frame and restored.sampler is active_sampler
assert restored.source_session == active_source and restored.display_valid
np.testing.assert_array_equal(np.array(restored.mount.matrix_basis), np.array(active_matrix))
assert tuple(scene.wfrl_x100_location) == draft

shared = bpy.data.scenes.new('X100.SharedInstallation')
collection = next(c for c in scene.collection.children if c.get('wfrl_x100_owner') == restored.owner)
shared.collection.children.link(collection)
try:
    try:
        radar.apply_mount_preset(scene, 'NACELLE_SIDE')
    except ValueError:
        pass
    else:
        raise AssertionError('A switch must not move another scene\'s linked installation')
    assert restored.frame is active_frame and restored.sampler is active_sampler
finally:
    bpy.data.scenes.remove(shared)

housing = next(o for o in scene.objects if o.get('wfrl_x100_role') == 'physical')
housing.hide_render = True
housing.select_set(True)
side.fov.hide_set(True, view_layer=scene.view_layers[0])
assert bpy.ops.wfrl.x100_mount_preset(preset='NACELLE_SIDE') == {'FINISHED'}
assert restored.mount is original_mount
assert housing.hide_render and housing.select_get()
assert restored.fov.hide_get(view_layer=scene.view_layers[0])
assert tuple(scene.wfrl_x100_location) == side_location
assert tuple(scene.wfrl_x100_rotation) == side_rotation
points = radar.session_for(scene).frame.points.tobytes()
assert bpy.ops.wm.save_as_mainfile(filepath=str(OUT / 'mount-presets.blend')) == {'FINISHED'}
assert bpy.ops.wm.open_mainfile(filepath=str(OUT / 'mount-presets.blend')) == {'FINISHED'}
scene = bpy.context.scene
radar.tick()
assert radar.session_for(scene).frame.points.tobytes() == points, scene.get('wfrl_x100_error')
assert bpy.ops.wfrl.x100_mount_preset(preset='ORIGINAL') == {'FINISHED'}
assert radar.session_for(scene).frame.points.tobytes() == original_points
original_count = len(radar.session_for(scene).frame.points)
sink = radar.recording.Recorder(OUT / 'source-replay')
sink.append(radar.session_for(scene).frame, 0.)
sink.close()
radar.stop(scene)
scene.wfrl_x100_source, scene.wfrl_x100_path = 'REPLAY', str(OUT / 'source-replay')
replay = radar.start(scene)
scene.wfrl_x100_source = 'SIMULATION'
try:
    radar.apply_mount_preset(scene, 'NACELLE_SIDE')
except ValueError:
    pass
else:
    raise AssertionError('Unapplied source selection must not replace the replay session')
assert radar.session_for(scene) is replay and replay.frame is not None
scene.wfrl_x100_source = 'REPLAY'

result = dict(status='PASS', addon=addon.__file__, source_time_s=clock[2],
              original_points=original_count, side_points=side_points,
              down_camera_points=down_points, down_camera_field_of_view_deg=down_fov_deg,
              rear_hatch_points=rear_points, rear_hatch_location_m=list(rear_location),
              rear_hatch_rotation_deg=list(rear_rotation),
              side_location_m=side_location, side_rotation_deg=side_rotation,
              single_sensor_roundtrip=True, clock_unchanged=True, recording_guard=True,
              camera_capture_guard=True, saved_side_reopens_and_switches_back=True,
              repeated_preset_preserves_frame_identity=True, failure_preserves_active_and_draft=True,
              linked_installation_guard=True, selection_and_visibility_preserved=True,
              unapplied_source_preserves_replay=True)
(OUT / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
print('X100_MOUNT_PRESETS_PASS', json.dumps(result), flush=True)
