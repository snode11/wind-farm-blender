"""Native Blender regression: mounted camera orientation and turbine switching."""
import math
import sys
from pathlib import Path
import bpy
from mathutils import Vector
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
wfrl_blender.register()
from wfrl_blender import cameras
assert hasattr(cameras, 'ensure_gimbal'), 'Missing mounted gimbal camera support'
scene = bpy.context.scene
for i in range(1, 4):
    root = bpy.data.objects.new(f'WFRL.Turbine.T{i}.YawRoot', None)
    scene.collection.objects.link(root)
    root.location = (i * 504, 0, 90)
    camera = cameras.ensure_gimbal(scene, f'T{i}')
    assert camera.parent == root
    position = camera.location.copy()
    cameras.aim_gimbal(camera, 35, -90, 75)
    direction = camera.rotation_euler.to_quaternion() @ Vector((0, 0, -1))
    assert direction.dot(Vector((0, 0, -1))) > .999999
    cameras.aim_gimbal(camera, 90, 0, 40)
    direction = camera.rotation_euler.to_quaternion() @ Vector((0, 0, -1))
    assert direction.dot(Vector((0, 1, 0))) > .999999
    assert (camera.location - position).length < 1e-8
    assert abs(math.degrees(camera.data.angle) - 40) < .001
    root.rotation_euler.z = math.pi / 2
    bpy.context.view_layer.update()
    direction = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    assert direction.dot(Vector((-1, 0, 0))) > .999999
    assert cameras.ensure_gimbal(scene, f'T{i}') == camera
assert len([o for o in scene.objects if o.name.endswith('.Gimbal')]) == 3
wfrl_blender.unregister()
assert not hasattr(bpy.types.Scene, 'wfrl_gimbal_turbine')
wfrl_blender.register()
wfrl_blender.unregister()
print('GIMBAL_SMOKE_OK')
