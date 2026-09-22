from pathlib import Path
import sys,json,math
from dataclasses import asdict
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
from mathutils import Vector
import wfrl_blender as addon
from wfrl_blender import farm_flex,custom_cameras as c,camera_projection as cp,custom_camera_preview as preview
OUT=Path(__file__).parent
addon.register();addon.load_demo_scene();s=bpy.context.scene;s.frame_set(1);bpy.context.view_layer.update()
obj,rest,*_=next(r for r in farm_flex._ACTIVE.blades if r[0].name=='WFRL.Turbine.T1.Blade1')
root=s.objects[c.ROOT_NAME];world_from_root=np.array(root.matrix_world);root_from_world=np.linalg.inv(world_from_root)
co=np.empty(len(obj.data.vertices)*3,np.float32);obj.data.vertices.foreach_get('co',co);co=co.reshape(-1,3)
m=root_from_world@np.array(obj.matrix_world);points=co@m[:3,:3].T+m[:3,3]
spans=rest[:,2]-1.5
faces=[list(p.vertices) for p in obj.data.polygons]
used=np.unique(np.concatenate(faces));ranges=[(0,16.5),(15,32),(30.5,47.5),(46,61.5)]
centers=np.array([points[ids].mean(axis=0) for ids in faces]);face_span=np.array([spans[ids].mean() for ids in faces])
normals=np.array([np.cross(points[ids[1]]-points[ids[0]],points[ids[2]]-points[ids[0]]) for ids in faces]);normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-12)
def world(v):return world_from_root[:3,:3]@v+world_from_root[:3,3]
def fit(location,ids):
 target=points[ids];origin=np.asarray(location)
 directions=target-origin;directions/=np.linalg.norm(directions,axis=1)[:,None];d=directions.mean(axis=0);d/=np.linalg.norm(d)
 for _ in range(4):
  yaw=math.degrees(math.atan2(d[1],d[0]))%360;pitch=math.degrees(math.asin(d[2]));base=np.array(cp.rotation(yaw,pitch,0))
  projected=(target-origin)@base;xy=projected[:,:2]/-projected[:,2,None]
  byspan=np.argsort(spans[ids]);delta=xy[byspan[-min(48,len(ids)):]].mean(axis=0)-xy[byspan[:min(48,len(ids))]].mean(axis=0)
  roll=math.degrees(math.atan2(delta[1],delta[0]));basis=np.array(cp.rotation(yaw,pitch,roll));uv=(target-origin)@basis;uv=uv[:,:2]/-uv[:,2,None]
  shift=(uv.min(axis=0)+uv.max(axis=0))/2;d=basis@np.array([*shift,-1]);d/=np.linalg.norm(d)
 yaw=math.degrees(math.atan2(d[1],d[0]))%360;pitch=math.degrees(math.asin(d[2]));basis=np.array(cp.rotation(yaw,pitch,roll));v=(target-origin)@basis;uv=v[:,:2]/-v[:,2,None]
 half=max(np.abs(uv[:,0]).max(),np.abs(uv[:,1]).max()*16/9)*1.12
 return yaw,pitch,roll,math.degrees(2*math.atan(half)),math.degrees(2*math.atan(half*9/16))
def visibility(location,lo,hi):
 select=np.where((face_span>=lo)&(face_span<=hi)&(np.sum(normals*(np.asarray(location)-centers),axis=1)>0))[0]
 if not len(select):return 0
 select=select[np.linspace(0,len(select)-1,min(90,len(select))).astype(int)];visible=0;occluders={}
 for idx in select:
  origin=world(np.asarray(location));target=world(centers[idx]);ray=target-origin;dist=np.linalg.norm(ray)
  hit,loc,normal,face,hitobj,matrix=s.ray_cast(bpy.context.evaluated_depsgraph_get(),Vector(origin),Vector(ray/dist),distance=dist+.1)
  good=hit and hitobj.original==obj and np.linalg.norm(np.asarray(loc)-target)<.15
  visible+=good
  if not good:occluders[hitobj.name if hitobj else 'none']=occluders.get(hitobj.name if hitobj else 'none',0)+1
 return float(visible/len(select)),occluders
results=[];geometry=c._geometry(s)[0];tree=geometry[-1]
with preview.without_annotations(s,bpy.context.view_layer):
 for x in [-3.,-1.,1.,2.5,3.]:
  for z in [.8,1.7,2.7]:
   p,n,*_=tree.find_nearest(Vector((x,-10,z)));location=tuple(p+n*.3)
   try:anchor=c.validate_position(s,location)[0]
   except ValueError:continue
   candidate={'location':location,'anchor':asdict(anchor),'segments':[]}
   for lo,hi in ranges:
    ids=used[(spans[used]>=lo)&(spans[used]<=hi)];fitdata=fit(location,ids);vis,occl=visibility(location,lo,hi)
    candidate['segments'].append({'range_m':[lo,hi],'ypr_hv':fitdata,'front_face_visible_fraction':vis,'occluders':occl})
   results.append(candidate)
   print('CANDIDATE',*[round(v,2) for v in location],[(round(r['front_face_visible_fraction'],2),round(r['ypr_hv'][3],1)) for r in candidate['segments']],flush=True)
(OUT/'candidate-analysis.json').write_text(json.dumps(results,indent=2))
np.savez(OUT/'design-geometry.npz',points=points,spans=spans,used=used)
print('DESIGN_DONE',flush=True)
