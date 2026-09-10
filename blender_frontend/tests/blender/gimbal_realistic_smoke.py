"""Check mounted cameras against the user's realistic scene and render down view."""
import sys
from pathlib import Path
import bpy
from mathutils import Vector
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
import wfrl_blender
wfrl_blender.register()
bpy.ops.wm.open_mainfile(filepath=str(ROOT / 'evidence/part5_realistic.blend'))
from wfrl_blender.cameras import ensure_gimbal, aim_gimbal
scene = bpy.context.scene
scene.frame_set(301)
for turbine in ('T1', 'T2', 'T3'):
    camera = ensure_gimbal(scene, turbine)
    bpy.context.view_layer.update()
    assert camera.parent.name == f'WFRL.Turbine.{turbine}.YawRoot'
    origin = camera.matrix_world.translation
    direction = camera.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    hit, position, normal, face, obj, matrix = scene.ray_cast(bpy.context.evaluated_depsgraph_get(), origin, direction)
    assert not hit or '.Blade' in obj.name or (position-origin).length > 5, f'{turbine}: lens obstructed by {obj.name}'
scene.camera = ensure_gimbal(scene, 'T1')
scene.render.engine = 'CYCLES'
scene.cycles.samples = 8
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y = 960, 540
scene.render.resolution_percentage = 100
scene.render.filepath = str(ROOT / 'evidence/gimbal_down_preview.png')
bpy.ops.render.render(write_still=True)
print('GIMBAL_REALISTIC_SMOKE_OK')
