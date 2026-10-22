"""Independent NREL height and mount-rig checks; factory-startup Blender."""
import sys, math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy
from mathutils import Vector
from wfrl_blender.scene_builder import _make_turbine
from wfrl_blender.scene_model import TurbineDTO
from wfrl_blender.turbine_geometry import hub_position, geometry_data
c=bpy.data.collections.new('WFRL_Scene');bpy.context.scene.collection.children.link(c)
_make_turbine(c,TurbineDTO('Check',123,456))
bpy.context.view_layer.update()
p='WFRL.Turbine.Check';yaw=bpy.data.objects[p+'.YawRoot'];rotor=bpy.data.objects[p+'.Rotor']
assert abs(rotor.matrix_world.translation.z-90)<1e-4,rotor.matrix_world.translation.z
assert abs(hub_position()[2]-90)<1e-4
# All fixed nacelle fittings retain their local placement through yaw.
for angle in (0,45,180,270):
 yaw.rotation_euler.z=math.radians(angle);bpy.context.view_layer.update()
 expected=yaw.matrix_world @ Vector((-5.0191*math.cos(math.radians(5)),0,2.4000033884))
 assert (rotor.matrix_world.translation-expected).length<1e-4
 for suffix in ('.Nacelle','.NacelleRoof','.MainShaft','.RoofGasket','.Vent.Recess-1'):
  obj=bpy.data.objects[p+suffix]
  assert obj.parent==yaw
  assert (obj.matrix_world.translation-(yaw.matrix_world @ obj.location)).length<1e-4
body=bpy.data.objects[p+'.Nacelle']
assert abs(body.location.z-(1.96256+.15))<1e-5
pedestal=bpy.data.objects[p+'.YawPedestal']
bpy.context.view_layer.update()
world=[pedestal.matrix_world @ Vector(v) for v in pedestal.bound_box]
assert min(v.z for v in world)<=87.6
assert max(v.z for v in world)>=87.6+1.96256+.15-1.65
print('HUB_MOUNT_REGRESSION=PASS')
from wfrl_blender.scene_model import SceneDTO
from wfrl_blender.scene_builder import _make_sensor_fixtures, _make_wake
from wfrl_blender.cameras import build_cameras, ensure_gimbal
from wfrl_blender import cinematic
import wfrl_blender
wfrl_blender.register()
dto=SceneDTO.from_mapping({'layout':[{'id':'Check','x':123,'y':456}]})
_make_sensor_fixtures(c,dto);_make_wake(c,dto);build_cameras(dto)
gimbal=ensure_gimbal(bpy.context.scene,'Check')
sensor=bpy.data.objects['WFRL.Camera.T1.Sensor']
assert abs(sensor.location.z-(-2.4+1.96256))<1e-5
# Gimbal is now a movable observation camera, not the old fixed roof mount.
radius=geometry_data()['scalars']['TipRad']
assert abs(gimbal.location.z-radius*.30)<1e-5
assert gimbal['gimbal_fov']==62
for suffix in ('LidarRay1','LidarRay2','LidarRay3','SensorFrustum'):
 fixture=bpy.data.objects['WFRL.Fixture.T1.'+suffix]
 assert fixture.parent==yaw
for angle in (0,90,180,270):
 yaw.rotation_euler.z=math.radians(angle);bpy.context.view_layer.update()
 for cam in (sensor,gimbal):
  assert cam.parent==yaw
  assert (cam.matrix_world.translation-yaw.matrix_world@cam.location).length<1e-4
 bpy.context.scene.wfrl_wake_display='CINEMATIC'
 cinematic.update(bpy.context.scene,0)
 bpy.context.view_layer.update()
 assert abs(bpy.data.objects['WFRL.Cinematic.Check'].location.z-rotor.matrix_world.translation.z)<1e-4
print('SENSOR_CAMERA_WAKE_MOUNTS=PASS')
