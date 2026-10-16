"""Native pinhole, mechanical datum and persistent nacelle-camera contract."""
from pathlib import Path
import importlib
import json
import math
import os
import sys
import tempfile

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
cameras = importlib.import_module(MODULE + '.cameras')
nominal = importlib.import_module(MODULE + '.nacelle_camera')
expected_root = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
if expected_root:
    assert Path(addon.__file__).resolve().parent == Path(expected_root).resolve()

scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
yaw = bpy.data.objects.new('WFRL.Turbine.T1.YawRoot', None)
scene.collection.objects.link(yaw)
yaw.location = (100, 30, 87.6)
rotor = bpy.data.objects.new('WFRL.Turbine.T1.Rotor', None)
scene.collection.objects.link(rotor)
rotor.parent = yaw
rotor.location = (-5.0, 0.0, 2.4)
shell = bpy.data.objects.new('WFRL.Turbine.T1.Nacelle', bpy.data.meshes.new('NacelleDatumTest'))
scene.collection.objects.link(shell)
shell.parent = yaw
camera = cameras.ensure_nacelle_gimbal(scene, 'T1')
scene.camera = camera
cameras.apply_nacelle_output(scene, camera)
bpy.context.view_layer.update()
assert (camera.location - Vector((-3.5, 0, .9))).length < 1e-6
assert abs(camera.data.lens - 8) < 1e-6
assert abs(camera.data.sensor_width - nominal.SENSOR_WIDTH_MM) < 1e-6
assert abs(camera.data.sensor_height - nominal.SENSOR_HEIGHT_MM) < 1e-6
assert camera.data.type == 'PERSP' and camera.data.sensor_fit == 'HORIZONTAL'
assert scene.objects.get('WFRL.Turbine.T1.NacelleCameraBracket') is None
assert camera['gimbal_yaw'] == 180 and camera['gimbal_pitch'] == -82 and camera['gimbal_roll'] == 180
cameras.select_camera(scene, camera.name, fov_deg=59)
assert abs(camera.data.lens - 8) < 1e-6
matrix = camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
    x=scene.render.resolution_x, y=scene.render.resolution_y,
    scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
hfov = math.degrees(2 * math.atan(1 / matrix[0][0]))
vfov = math.degrees(2 * math.atan(1 / matrix[1][1]))
assert abs(hfov - 59) < 1e-5 and abs(vfov - 40) < 1e-5, (hfov, vfov)

motion_error = 0.0
for frame, heading, roll, tilt in ((1, 0, 0, 0), (301, 73, .8, -.6), (901, -130, -1.1, .7)):
    scene.frame_set(frame)
    yaw.rotation_euler = tuple(map(math.radians, (roll, tilt, heading)))
    yaw.location.z = 87.6 + .2 * math.sin(frame)
    rotor.rotation_euler.x = math.radians(frame % 360)
    bpy.context.view_layer.update()
    expected = yaw.matrix_world @ Vector((-3.5, 0, .9))
    motion_error = max(motion_error, (camera.matrix_world.translation - expected).length)
    assert motion_error < 1e-4
    local_axis = camera.rotation_euler.to_matrix() @ Vector((0, 0, -1))
    expected_axis = Vector((-math.sin(math.radians(8)), 0, -math.cos(math.radians(8))))
    assert (local_axis - expected_axis).length < 1e-6

frame = scene.frame_current
cameras.aim_gimbal(camera, 173, -79, 50)
saved = (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens)
assert cameras.ensure_nacelle_gimbal(scene, 'T1') == camera
assert (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens) == saved
assert scene.frame_current == frame
with tempfile.TemporaryDirectory() as folder:
    blend = str(Path(folder) / 'nacelle-optics.blend')
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    bpy.ops.wm.open_mainfile(filepath=blend)
    scene = bpy.context.scene
    camera = cameras.ensure_nacelle_gimbal(scene, 'T1')
    assert (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens) == saved
    assert scene.frame_current == frame
cameras.reset_nacelle_gimbal(camera)
assert abs(camera.data.lens - 8) < 1e-6
camera.location = (-1.5, -2.3, 0)
camera['gimbal_yaw'], camera['gimbal_pitch'] = 180, math.degrees(math.atan2(-60.6, 11.7))
camera['gimbal_fov'], camera['gimbal_roll'] = 120, 180
camera['wfrl_nacelle_view_revision'] = 2
camera.data.lens = 13
camera.data.shift_x = .1
camera.rotation_euler = (.3, .2, .1)
native_edit = (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens, camera.data.shift_x)
assert cameras.ensure_nacelle_gimbal(scene, 'T1') == camera
assert (tuple(camera.location), tuple(camera.rotation_euler), camera.data.lens, camera.data.shift_x) == native_edit
cameras.reset_nacelle_gimbal(camera)
other = bpy.data.objects.new('OtherCamera', bpy.data.cameras.new('OtherCamera'))
scene.collection.objects.link(other)
cameras.apply_nacelle_output(scene, other)
assert scene.render.resolution_x == 1920 and scene.render.resolution_y == 1080
assert scene.render.pixel_aspect_x == scene.render.pixel_aspect_y == 1

farm_flex = importlib.import_module(MODULE + '.farm_flex')
clearance = importlib.import_module(MODULE + '.clearance_replay')
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
if not hasattr(bpy.types.Scene, 'wfrl_gimbal_kind'):
    addon.register()
addon.load_demo_scene(farm_flex.default_package())
scene = bpy.context.scene
area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
region = next(region for region in area.regions if region.type == 'WINDOW')
product_frames = (scene.frame_start, 67, 901, 1801, scene.frame_end)
with bpy.context.temp_override(area=area, region=region):
    frame = scene.frame_current
    reading = clearance.sample(scene)
    assert bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='NACELLE') == {'FINISHED'}
    assert scene.frame_current == frame and clearance.sample(scene) == reading
    camera = scene.camera
    assert camera.name == 'WFRL.Camera.T1.NacelleGimbal'
    assert abs(camera.data.lens - 8) < 1e-6
    rotor = scene.objects['WFRL.Turbine.T1.Rotor']
    for frame in product_frames:
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        location = Vector(nominal.mount_location(rotor))
        assert (camera.location - location).length < 1e-6
        expected = camera.parent.matrix_world @ location
        assert (camera.matrix_world.translation - expected).length < 1e-4
        matrix = camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
            x=scene.render.resolution_x, y=scene.render.resolution_y,
            scale_x=scene.render.pixel_aspect_x, scale_y=scene.render.pixel_aspect_y)
        assert abs(math.degrees(2 * math.atan(1 / matrix[0][0])) - 59) < 1e-5
        assert abs(math.degrees(2 * math.atan(1 / matrix[1][1])) - 40) < 1e-5

result = dict(status='PASS', module=MODULE, addon_file=addon.__file__, blender=bpy.app.version_string,
              effective_hfov_deg=hfov, effective_vfov_deg=vfov,
              sensor_mm=[nominal.SENSOR_WIDTH_MM, nominal.SENSOR_HEIGHT_MM],
              focal_length_mm=8, hub_offset_m=list(nominal.HUB_OFFSET_M),
              synthetic_tower_yaw_motion_error_m=motion_error, manual_ptz_persistent=True,
              reset_restores_nominal=True, save_reload=True, output_restored=True,
              legacy_native_optics_preserved=True,
              product_entry_operator=True, product_replay_frames_checked=list(product_frames))
out = Path(os.environ.get('WFRL_TEST_OUTPUT', '/tmp/wfrl-nacelle-intrinsics'))
out.mkdir(parents=True, exist_ok=True)
(out / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
print('NACELLE_INTRINSICS_PASS', json.dumps(result), flush=True)
