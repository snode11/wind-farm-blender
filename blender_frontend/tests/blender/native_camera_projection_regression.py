"""Background algebra check of masked native gates against calibrated FOV."""
from pathlib import Path
import sys,math
import bpy
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
from wfrl_blender.native_camera_views import fit_gate
from mathutils import Vector
scene=bpy.context.scene
cam=bpy.data.objects.new('NativeProjectionTest',bpy.data.cameras.new('NativeProjectionTest'))
scene.collection.objects.link(cam)
for ratio in [1.,16/9,9/16,2.4]:
 scene.render.resolution_x=1600;scene.render.resolution_y=1000
 scene.render.pixel_aspect_x=ratio/1.6 if ratio>=1.6 else 1
 scene.render.pixel_aspect_y=1 if ratio>=1.6 else 1.6/ratio
 for h,v in [(105,105),(90,60),(40,100),(1,1),(170,170),(20,130)]:
  outer,fx,fy=fit_gate(h,v,ratio)
  data=cam.data;data.type='PERSP';data.sensor_fit='HORIZONTAL'
  data.lens=min(5000.,max(1.,18./outer));data.sensor_width=2*data.lens*outer
  matrix=cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=1600,y=1000,
      scale_x=scene.render.pixel_aspect_x,scale_y=scene.render.pixel_aspect_y)
  corner=matrix@Vector((math.tan(math.radians(h/2)), math.tan(math.radians(v/2)),-1,1))
  assert abs(corner.x/corner.w-fx)<2e-5,(ratio,h,v,corner,fx)
  assert abs(corner.y/corner.w-fy)<2e-5,(ratio,h,v,corner,fy)
print('NATIVE_PROJECTION_PASS: 24 aspect/FOV combinations')
