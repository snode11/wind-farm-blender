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
from wfrl_blender import custom_cameras
from wfrl_blender.nrel_defects.defect_visibility import _camera_descriptor
from wfrl_blender.custom_camera_preview import matrices
scene=bpy.context.scene
cam['wfrl_custom_camera']=True
cam['wfrl_custom_slot']=1
for h,v in [(75,75),(90,60),(40,110),(75,42.1875)]:
    params=custom_cameras.CameraParameters((0.,0.,0.),fov=h,vfov=v,output_long_edge_px=640)
    custom_cameras.apply_parameters(cam,params)
    for old_aspect in [(1.,1.),(2.,.7),(.8,1.8)]:
        scene.render.pixel_aspect_x,scene.render.pixel_aspect_y=old_aspect
        bpy.context.view_layer.update()
        descriptor=_camera_descriptor(cam,scene)
        w,height,view,P,world=matrices(cam,bpy.context.evaluated_depsgraph_get(),640)
        assert descriptor['width']==w and descriptor['height']==height
        assert descriptor['K']==p.intrinsics(h,v,w,height)
        assert descriptor['matrix_world']==[list(row) for row in world]
        assert max(abs(descriptor['P'][i][j]-P[i][j]) for i in range(4) for j in range(4))<2e-6
        sx,sy=p.pixel_scales(h,v,w,height)
        native=cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=w,y=height,scale_x=sx,scale_y=sy)
        assert max(abs(native[i][j]-P[i][j]) for i in range(4) for j in range(4))<2e-5
print('CAMERA_PINHOLE_PROJECTION_PASS' , bpy.app.version_string)
