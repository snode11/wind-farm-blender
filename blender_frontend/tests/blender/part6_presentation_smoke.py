"""Background check: native camera targeting and capture RNA registration."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wfrl_blender.cameras import configure_camera
from wfrl_blender.panels import presentation

for cls in presentation.CLASSES:
    bpy.utils.register_class(cls)
presentation.register_properties()
try:
    root = bpy.data.objects.new('WFRL.Turbine.custom.YawRoot', None)
    bpy.context.scene.collection.objects.link(root)
    root.location = (100, 50, 90)
    camera = bpy.data.objects.new('WFRL.Camera.Test', bpy.data.cameras.new('WFRL.Test.Data'))
    bpy.context.scene.collection.objects.link(camera)
    camera.location = (300, -200, 250)
    bpy.context.view_layer.update()
    configure_camera(bpy.context.scene, camera.name, 75, -30, 'custom')
    assert bpy.context.scene.camera == camera
    assert abs(camera.data.angle * 180 / 3.141592653589793 - 75) < .001
    direction = camera.rotation_euler.to_quaternion() @ __import__('mathutils').Vector((0, 0, -1))
    target = (root.matrix_world.translation - camera.location).normalized()
    assert direction.dot(target) > .99999
    assert not presentation.WFRL_OT_CaptureRecording.poll(bpy.context)
    print('PART6_PRESENTATION_SMOKE_OK')
finally:
    presentation.unregister_properties()
    for cls in reversed(presentation.CLASSES):
        bpy.utils.unregister_class(cls)
