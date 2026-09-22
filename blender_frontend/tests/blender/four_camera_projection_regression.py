"""Cross-check the independent pinhole matrix against actual Blender evaluation."""
import sys
from pathlib import Path
import math
import bpy
from mathutils import Matrix, Vector
sys.path[:0]=[str(Path(__file__).resolve().parents[3]/'blender_frontend')]
from wfrl_blender import camera_projection as p
cam=bpy.data.objects.new('ProjectionTest',bpy.data.cameras.new('ProjectionTest'))
bpy.context.scene.collection.objects.link(cam)
cam.data.sensor_fit='HORIZONTAL';cam.data.sensor_width=36
cam.data.clip_start=.005;cam.data.clip_end=30000
for h,v in [(90,60),(40,110),(75,75),(1,1),(170,120)]:
    cam.data.lens=36/(2*math.tan(math.radians(h/2)))
    bpy.context.view_layer.update()
    for edge in [640,1920,3840]:
        w,height=p.resolution(h,v,edge);sx,sy=p.pixel_scales(h,v,w,height)
        m=cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=w,y=height,scale_x=sx,scale_y=sy)
        expected=Matrix(p.projection(h,v))
        assert max(abs(m[i][j]-expected[i][j]) for i in range(4) for j in range(4))<2e-5,(h,v,m,expected)
        for corner in p.frustum_corners(h,v,10):
            q=m @ Vector((*corner,1))
            assert abs(abs(q.x/q.w)-1)*w/2<.01
            assert abs(abs(q.y/q.w)-1)*height/2<.01
print('FOUR_CAMERA_PROJECTION_PASS', bpy.app.version_string)
