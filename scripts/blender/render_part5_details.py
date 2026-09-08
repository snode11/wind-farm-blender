"""Rebuild the approved Part 5 modelling revision and render review views."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'blender_frontend'))
import bpy
from mathutils import Vector
import wfrl_blender
wfrl_blender.register()
wfrl_blender.load_demo_scene()
scene=bpy.context.scene
scene.frame_set(301)
scene.render.engine='CYCLES'
scene.cycles.samples=32
scene.cycles.use_denoising=True
scene.render.resolution_percentage=75
scene.view_settings.exposure=1.2
out=ROOT/'evidence/part5_details'
out.mkdir(parents=True,exist_ok=True)
bpy.context.view_layer.update()
# Preserve Part 5's existing line-only render convention when a newer UI
# layer synchronizes the viewport envelope visibility during frame changes.
for obj in scene.objects:
    if obj.name.startswith('WFRL.WakeProxy.') and obj.name.endswith('.Volume'):
        obj.hide_render=True
original=scene.camera
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(out/'part5_details.blend'))
def render(name):
    scene.render.filepath=str(out/(name+'.png'))
    bpy.ops.render.render(write_still=True)
render('overview')
data=bpy.data.cameras.new('WFRL.DetailReview.Camera')
camera=bpy.data.objects.new('WFRL.DetailReview.Camera',data)
scene.collection.objects.link(camera)
scene.camera=camera
data.clip_end=100000

def view(name,position,target,lens):
    camera.location=position
    camera.rotation_euler=(Vector(target)-camera.location).to_track_quat('-Z','Y').to_euler()
    data.lens=lens
    bpy.context.view_layer.update()
    render(name)
yaw=bpy.data.objects['WFRL.Turbine.T1.YawRoot'].matrix_world
view('nacelle',yaw @ Vector((4,-17,5)),yaw @ Vector((-.3,0,.3)),48)
view('foundation',(18,-25,12),(0,-2,2.8),48)
shrubs=[o for o in bpy.data.objects if o.name.startswith('WFRL.Landscape.Shrub') and o.get('cluster')==0]
center=sum((o.location for o in shrubs),Vector())/len(shrubs)
view('vegetation',center+Vector((15,-23,7)),center+Vector((0,0,.6)),48)
scene.camera=original
scene.render.filepath=str(out/'overview.png')
bpy.ops.wm.save_as_mainfile(filepath=str(out/'part5_details.blend'))
print('WFRL_PART5_DETAILS_RENDER=PASS')
