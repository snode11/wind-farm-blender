"""Default regional framing through the complete existing 60-second clip.

Conservative triangle/frustum rejection on the outer 10% of every T1 blade:
no triangle may potentially intersect C1/C2. This is a geometric exclusion test,
not a claim of continuous blade visibility, physical lens calibration or stitching.
"""
import os, sys, json, time
from pathlib import Path
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[os.environ.get('WFRL_ADDON_ROOT',str(ROOT/'blender_frontend')),str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import stacked_camera_rig as rig, custom_cameras as core, farm_flex
addon.register();addon.load_demo_scene()
scene=bpy.context.scene
layout=core.layout_dict(scene)
cameras=core.enabled_cameras(scene)
blade_rows=[]
for obj,rest,*_ in farm_flex._ACTIVE.blades[:3]:
    obj.data.calc_loop_triangles()
    faces=np.asarray([tuple(t.vertices) for t in obj.data.loop_triangles])
    spans=rest[:,2]-1.5
    tip_faces=faces[(spans[faces]>=55.35).any(axis=1)]
    blade_rows.append((obj,spans,tip_faces))
report={'status':'RUNNING','reference_frame':1,'reference_time_s':117.,'step':int(os.environ.get('WFRL_FRAME_STEP','1')),
        'outer_tip_test_span_m':[55.35,61.5], 'layout':layout, 'routes':{str(n):{'tip_intersections':[], 'blade_frames':0, 'max_span_m':None} for n in (1,2,3)}}
start=time.perf_counter()
for frame in range(1,scene.frame_end+1,report['step']):
    scene.frame_set(frame);bpy.context.view_layer.update()
    for camera in cameras:
        slot=str(camera['wfrl_custom_slot']);p=core.parameters(camera)
        half=np.tan(np.radians([p.fov,p.vfov])/2)
        inverse=np.asarray(camera.matrix_world.inverted())
        seen=False;tip=False
        for obj,spans,faces in blade_rows:
            co=np.empty(len(obj.data.vertices)*3,dtype=np.float32);obj.data.vertices.foreach_get('co',co)
            matrix=inverse@np.asarray(obj.matrix_world)
            view=co.reshape(-1,3)@matrix[:3,:3].T+matrix[:3,3]
            depth=-view[:,2]
            planes=np.column_stack((depth-p.clip_near_m,p.clip_far_m-depth,depth*half[0]+view[:,0],depth*half[0]-view[:,0],depth*half[1]+view[:,1],depth*half[1]-view[:,1]))
            inside=(planes>=0).all(axis=1)
            if inside.any():
                seen=True;maximum=float(spans[inside].max())
                old=report['routes'][slot]['max_span_m'];report['routes'][slot]['max_span_m']=max(old or 0,maximum)
            # Any separating frustum plane rejects the whole triangle. The
            # remaining set is conservative (may overcount corner grazing).
            if (~(planes[faces]<0).all(axis=1).any(axis=1)).any():tip=True
        report['routes'][slot]['blade_frames']+=int(seen)
        if tip:report['routes'][slot]['tip_intersections'].append(frame)
    if frame%300==1:print('FRAMING_FRAME',frame,flush=True)
assert core.layout_dict(scene)==layout,'fixed camera optics/pose changed during replay'
assert not report['routes']['1']['tip_intersections'], report['routes']['1']
assert not report['routes']['2']['tip_intersections'], report['routes']['2']
assert report['routes']['3']['tip_intersections'], 'C3 never shows the tip'
assert report['routes']['1']['max_span_m']<19., 'C1 extends beyond the selected root patch'
report.update(status='PASS', elapsed_s=time.perf_counter()-start, frames_checked=len(range(1,scene.frame_end+1,report['step'])))
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
(out/'framing.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print('DEMO_FRAMING_PASS',report['frames_checked'],report['elapsed_s'],flush=True)
