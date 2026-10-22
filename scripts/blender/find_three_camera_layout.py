"""Search supported T1 mounts using the existing recorded blade geometry."""
from pathlib import Path
import json
import math
import os
import sys
from dataclasses import asdict
import bpy
import numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import stacked_camera_rig as rig, custom_cameras as core
from wfrl_blender import camera_projection as projection, custom_camera_preview as preview
OUT=Path(os.environ.get('WFRL_TEST_OUTPUT','/tmp/wfrl-three-camera-search'))
OUT.mkdir(parents=True,exist_ok=True)
INTERVALS=((0.,18.),(14.,38.),(34.,61.5))
addon.register();addon.load_demo_scene()
scene=bpy.context.scene;scene.frame_set(1)
scene.wfrl_flex_show_tip_trails=False
scene.wfrl_deflection_visible=False
from wfrl_blender import tip_tracking
tip_tracking.disable(scene,'camera_layout_search')
baseline=core.layout_dict(scene)
obj,points,spans,used=rig.blade_geometry(scene)
root=scene.objects[core.ROOT_NAME]
world=np.asarray(root.matrix_world)
geometry=core._geometry(scene)[0];tree=geometry[-1]
faces=np.asarray([list(f.vertices)[:3] for f in obj.data.polygons if len(f.vertices)>=3])
centres=points[faces].mean(axis=1);face_spans=spans[faces].mean(axis=1)
normals=np.cross(points[faces[:,1]]-points[faces[:,0]],points[faces[:,2]]-points[faces[:,0]])

def score_camera(cam,interval):
    p=core.parameters(cam);basis=np.asarray(projection.rotation(p.yaw,p.pitch,p.roll))
    origin=np.asarray(p.location)
    samples=np.flatnonzero((face_spans>=interval[0])&(face_spans<=interval[1])
                          & (np.einsum('ij,ij->i',normals,origin-centres)>0))
    samples=samples[::max(1,len(samples)//80)]
    view=(centres[samples]-origin)@basis
    half=np.tan(np.radians([p.fov,p.vfov])/2)
    inside=(view[:,2]<0)&(np.abs(view[:,:2])<=-view[:,2,None]*half).all(axis=1)
    visible=0;occluders={}
    deps=bpy.context.evaluated_depsgraph_get();eye=cam.matrix_world.translation.copy()
    for index,within in zip(samples,inside):
        if not within:continue
        target=Vector(world[:3,:3]@centres[index]+world[:3,3])
        d=target-eye
        hit,location,_,_,ob,_=scene.ray_cast(deps,eye,d.normalized(),distance=d.length+.1)
        if hit and ob.original==obj and (location-target).length<.15:visible+=1
        else:
            name=ob.original.name if hit else 'none';occluders[name]=occluders.get(name,0)+1
    blocked=0
    for u in np.linspace(-1,1,13):
        for v in np.linspace(-1,1,7):
            direction=Vector(world[:3,:3]@basis@np.array([u*half[0],v*half[1],-1.])).normalized()
            hit,*_=scene.ray_cast(deps,eye,direction,distance=.5)
            blocked+=int(hit)
    return {'visible':visible,'sampled':len(samples),'visible_ratio':visible/max(1,len(samples)),
            'near_blocked':blocked,'near_total':91,'occluders':occluders,
            'hfov':p.fov,'vfov':p.vfov,'interval':interval}

candidates=[]
for x in [0.,1.,2.]:
    for z in [1.,1.6,2.2]:candidates.append(((x,-10,z),(0,1,0)))
from itertools import product
results=[];failed=[]
with preview.without_annotations(scene,bpy.context.view_layer):
    for i,((origin,direction),aim) in enumerate(product(candidates,(0.,-15.,-30.,-45.,-60.))):
        point,normal,*_=tree.ray_cast(Vector(origin),Vector(direction))
        if point is None:continue
        try:
            anchor=core.SurfaceAnchor(tuple(point),tuple(normal),0.,geometry[0])
            pose=rig.surface_pose(scene,anchor,aim=aim)
            layout=rig.transformed_layout(scene,baseline,pose)
            for record,interval in zip(layout['cameras'],INTERVALS):
                p=rig.fit_segment(points[used],spans[used],record['parameters']['location'],interval)
                record['parameters']=asdict(p)
                record['label']=f"C{record['slot_id']} · "+('叶根','中段','叶尖')[record['slot_id']-1]
                record['image_width_px'],record['image_height_px']=projection.resolution(p.fov,p.vfov,p.output_long_edge_px)
            core.restore_layout(scene,layout);bpy.context.view_layer.update()
            scores=[score_camera(core.get_camera(scene,slot),interval) for slot,interval in zip(core.SLOTS,INTERVALS)]
            value=min(s['visible_ratio'] for s in scores)-max(s['near_blocked']/s['near_total'] for s in scores)
            results.append({'index':i,'aim_deg':aim,'score':value,'point':list(point),'normal':list(normal),'cameras':scores,'layout':core.layout_dict(scene)})
            print('CANDIDATE',i,round(value,3),[round(s['visible_ratio'],3) for s in scores],[s['near_blocked'] for s in scores],flush=True)
        except (ValueError,RuntimeError) as exc:failed.append({'index':i,'reason':str(exc)})
results.sort(key=lambda r:r['score'],reverse=True)
assert results,'no valid supported camera layout'
(OUT/'search.json').write_text(json.dumps({'intervals':INTERVALS,'reference_frame':1,'reference_time_s':117.,
    'results':results,'rejected':failed},ensure_ascii=False,indent=2))
for i,row in enumerate(results[:5]):
    (OUT/f'candidate-{i+1}.json').write_text(json.dumps(row['layout'],ensure_ascii=False,indent=2))
print('BEST',[(r['index'],r['score'],r['point']) for r in results[:5]],flush=True)

# Full-length overlapping sections: retain the whole selected section in frame.
layout=results[0]['layout']
core.restore_layout(scene,layout);bpy.context.view_layer.update()
(OUT/'recommended.json').write_text(json.dumps(core.layout_dict(scene),ensure_ascii=False,indent=2))
(OUT/'recommended-check.json').write_text(json.dumps({'intervals':INTERVALS,'framing_scale':[1.,1.,1.],
    'cameras':results[0]['cameras'],'mount':rig.pose(scene),'layout_hash':core.layout_hash(core.layout_dict(scene)),
    'method':'front-facing triangle-centre samples and near-ray grid; not continuous coverage'},ensure_ascii=False,indent=2))
print('RECOMMENDED',results[0]['cameras'],flush=True)
