from pathlib import Path
import sys,json,math
from dataclasses import replace
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
from mathutils import Vector
import wfrl_blender as addon
from wfrl_blender import farm_flex,custom_cameras as c,camera_projection as cp,custom_camera_preview as preview
OUT=Path(__file__).parent
addon.register();addon.load_demo_scene();s=bpy.context.scene;s.frame_set(1);bpy.context.view_layer.update()
layout=json.loads((OUT/'T1-same-blade-four-cameras-v2.json').read_text());c.import_layout(s,layout,overwrite=True)
obj,rest,*_=next(r for r in farm_flex._ACTIVE.blades if r[0].name=='WFRL.Turbine.T1.Blade1');spans=rest[:,2]-1.5
root=s.objects[c.ROOT_NAME];mw=np.asarray(root.matrix_world);im=np.linalg.inv(mw)
co=np.empty(len(obj.data.vertices)*3,np.float32);obj.data.vertices.foreach_get('co',co);co=co.reshape(-1,3);m=im@np.array(obj.matrix_world);points=co@m[:3,:3].T+m[:3,3]
faces=[list(p.vertices) for p in obj.data.polygons];centers=np.array([points[ids].mean(axis=0) for ids in faces]);face_span=np.array([spans[ids].mean() for ids in faces]);normal=np.array([np.cross(points[f[1]]-points[f[0]],points[f[2]]-points[f[0]]) for f in faces]);areas=np.linalg.norm(normal,axis=1);normal/=np.maximum(areas[:,None],1e-12)
ranges=[(0,16.5),(15,32),(30.5,47.5),(46,61.5)]
def frame(location,target):
 d=target-location;d/=np.linalg.norm(d);yaw=math.degrees(math.atan2(d[1],d[0]))%360;pitch=math.degrees(math.asin(d[2]));basis=np.array(cp.rotation(yaw,pitch,0))
 tang=points[np.argmax(spans)]-points[np.argmin(spans)];v=tang@basis;roll=math.degrees(math.atan2(v[1],v[0]))
 return yaw,pitch,roll

def raster(location,angles,h,v):
 basis=mw[:3,:3]@np.array(cp.rotation(*angles));origin=Vector(mw[:3,:3]@location+mw[:3,3]);half=math.tan(math.radians(h/2));values=[];other={}
 deps=bpy.context.evaluated_depsgraph_get()
 for y in np.linspace(-1,1,23):
  for x in np.linspace(-1,1,41):
   direction=Vector(basis@np.array([x*half,y*math.tan(math.radians(v/2)),-1]));direction.normalize()
   hit,loc,n,face,hitobj,m=s.ray_cast(deps,origin,direction,distance=300)
   if hit and hitobj.original==obj and 0<=face<len(face_span):values.append(float(face_span[face]))
   else:other[hitobj.name if hitobj else 'background']=other.get(hitobj.name if hitobj else 'background',0)+1
 return np.array(values),other


previous=json.loads((OUT/'refinement.json').read_text());results=[]
widths=[[20,25,30,35],[12,16,20,24,28],[5,6,7,8,9,10],[3.5,4,5,6]]
with preview.without_annotations(s,bpy.context.view_layer):
 for slot,record in enumerate(previous,1):
  best=record['trials'][0];lo,hi=record['range_m'];cam=c.get_camera(s,slot);location=np.array(c.parameters(cam).location)
  angles=list(best['angles']);angles[2]+=90;v=best['hfov'];trials=[]
  for h in widths[slot-1]:
   vals,other=raster(location,angles,h,v);inside=(vals>=lo)&(vals<=hi);purity=float(inside.mean());fraction=float(inside.sum()/(41*23));quantiles=np.quantile(vals,[.05,.5,.95]).tolist()
   score=-abs(fraction-.70)-max(0,.95-purity)*10
   trials.append(dict(hfov=h,vfov=v,angles=angles,purity=purity,target_fraction=fraction,span_quantiles=quantiles,score=score,other=other))
  trials.sort(key=lambda t:-t['score']);chosen=trials[0];params=replace(c.parameters(cam),yaw=angles[0],pitch=angles[1],roll=angles[2],fov=chosen['hfov'],vfov=chosen['vfov'])
  c.apply_parameters(cam,params);results.append(dict(slot=slot,range_m=record['range_m'],chosen=chosen,trials=trials))
  print('CHOSEN',slot,json.dumps(chosen),flush=True)
layout=c.layout_dict(s);c.validate_layout(s,layout)
(OUT/'T1-same-blade-four-cameras-v2.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2))
(OUT/'framing-check.json').write_text(json.dumps(results,indent=2));print('FINAL_DESIGN_PASS',flush=True)
