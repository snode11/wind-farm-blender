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
# Ordinary cameras use the rounded render size, pixel aspect and sensor fit.
del cam['wfrl_custom_camera']
cam.data.lens = 50.
cam.data.sensor_width = 36.
cam.data.sensor_height = 24.
maximum_legacy_difference = 0.
for fit in ('HORIZONTAL', 'VERTICAL', 'AUTO'):
    cam.data.sensor_fit = fit
    for aspect in ((1., 1.), (2., .7), (.8, 1.8)):
        scene.render.pixel_aspect_x, scene.render.pixel_aspect_y = aspect
        for shift in ((0., 0.), (.17, -.23), (-.19, .31)):
            cam.data.shift_x, cam.data.shift_y = shift
            for rx, ry, percentage in ((1001, 1000, 50), (1001, 1000, 100), (1000, 800, 50)):
                scene.render.resolution_x = rx
                scene.render.resolution_y = ry
                scene.render.resolution_percentage = percentage
                bpy.context.view_layer.update()
                d = _camera_descriptor(cam, scene)
                width, height = round(rx*percentage/100), round(ry*percentage/100)
                assert (d['width'], d['height']) == (width, height)
                native = cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
                    x=width, y=height, scale_x=scene.render.pixel_aspect_x,
                    scale_y=scene.render.pixel_aspect_y)
                assert d['P'] == tuple(tuple(row) for row in native)
                assert math.isclose(d['fx'], float(native[0][0])*width/2, rel_tol=1e-9)
                assert math.isclose(d['fy'], float(native[1][1])*height/2, rel_tol=1e-9)
                # Python arithmetic avoids additional mathutils float32 rounding.
                for u, v in ((0., 0.), (width-1., height-1.), (-.5, -.5),
                             (width-.5, height-.5), (123.25, 87.75)):
                    ray = ((u-d['cx'])/d['fx'], -(v-d['cy'])/d['fy'], -1., 1.)
                    clip = [sum(d['P'][i][j]*ray[j] for j in range(4)) for i in range(4)]
                    pixel = ((clip[0]/clip[3]+1)*width/2-.5,
                             (1-clip[1]/clip[3])*height/2-.5)
                    assert max(abs(pixel[0]-u), abs(pixel[1]-v)) < 1e-9, (fit, aspect, shift, pixel)
                frame = cam.data.view_frame(scene=scene)
                xs, ys = [v.x/-v.z for v in frame], [v.y/-v.z for v in frame]
                fx, fy = width/(max(xs)-min(xs)), height/(max(ys)-min(ys))
                legacy = (fx, fy, -.5-min(xs)*fx, -.5+max(ys)*fy)
                actual = (d['fx'], d['fy'], d['cx'], d['cy'])
                if (rx, ry, percentage) != (1001, 1000, 50):
                    difference = max(abs(a-b) for a, b in zip(actual, legacy))
                    maximum_legacy_difference = max(maximum_legacy_difference, difference)
                    assert difference < .001, (fit, aspect, shift, actual, legacy)
                elif fit == 'HORIZONTAL' and aspect == (1., 1.) and shift == (0., 0.):
                    assert .69 < fy-d['fy'] < .70
                    print('ORDINARY_CAMERA_ROUNDING', 'legacy_fy=', fy, 'projection_fy=', d['fy'])
print('ORDINARY_CAMERA_LEGACY_MAX_DIFFERENCE_PX', maximum_legacy_difference)
print('CAMERA_PINHOLE_PROJECTION_PASS' , bpy.app.version_string)
