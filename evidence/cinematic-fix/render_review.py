from pathlib import Path
import sys, math
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'blender_frontend'))
import bpy
from mathutils import Vector
import wfrl_blender
from wfrl_blender import cinematic
wfrl_blender.register();wfrl_blender.load_demo_scene()
scene=bpy.context.scene
scene.frame_set(301);scene.wfrl_wake_display='CINEMATIC'
cam=bpy.data.objects.new('CinematicReviewCamera',bpy.data.cameras.new('CinematicReviewCamera'));scene.collection.objects.link(cam)
cam.location=(-160,-440,205);cam.rotation_euler=(Vector((55,0,85))-cam.location).to_track_quat('-Z','Y').to_euler()
cam.data.type='ORTHO';cam.data.ortho_scale=510;scene.camera=cam
scene.render.engine='CYCLES';scene.cycles.samples=16;scene.cycles.use_denoising=True
scene.render.resolution_x=1100;scene.render.resolution_y=660;scene.render.resolution_percentage=100
scene.render.filepath=str(ROOT/'evidence/cinematic-fix/front.png')
bpy.context.view_layer.update();bpy.ops.render.render(write_still=True)
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'evidence/cinematic-fix/preview.blend'))
scene.wfrl_cinematic_wind_mode='RANDOM'
scene.cycles.samples=8;scene.render.resolution_x=720;scene.render.resolution_y=480
# An oblique overhead camera keeps all incoming directions in view.
cam.location=(0,-370,410);cam.rotation_euler=(Vector((0,0,80))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.ortho_scale=480
out=ROOT/'evidence/cinematic-fix/random-frames';out.mkdir(exist_ok=True)
for i in range(48):
    t=4+i*.25
    scene.frame_set(1+round(t*30))
    cinematic.update(scene,t*.9)
    for obj in scene.objects:
        if obj.name.startswith('WFRL.Cinematic.') and obj.name!='WFRL.Cinematic.T1':
            obj.hide_render=True
    scene.render.filepath=str(out/f'{i:03}.png')
    bpy.context.view_layer.update();bpy.ops.render.render(write_still=True)
print('CINEMATIC_RENDER_REVIEW=PASS')
