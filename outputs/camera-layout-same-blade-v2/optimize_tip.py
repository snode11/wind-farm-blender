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
addon.register();addon.load_demo_scene();s=bpy.context.scene
seed=json.loads((OUT/'experiments/reference-fit/T1-same-blade-four-cameras-v2.json').read_text());c.import_layout(s,seed,overwrite=True)
obj,rest,*_=next(r for r in farm_flex._ACTIVE.blades if r[0].name=='WFRL.Turbine.T1.Blade1');span=rest[:,2]-1.5;face_span=np.array([span[list(p.vertices)].mean() for p in obj.data.polygons]);cam=c.get_camera(s,4);base=c.parameters(cam);basis=np.array(cp.rotation(base.yaw,base.pitch,base.roll));candidates=[]
for shift in [0,.25,.5,.75,1.]:
 d=basis@np.array([0,math.tan(math.radians(shift)),-1.]);d/=np.linalg.norm(d);yaw=math.degrees(math.atan2(d[1],d[0]))%360;pitch=math.degrees(math.asin(d[2]))
 for v in [3,3.5,4,4.5,5]:candidates.append({'shift':shift,'vfov':v,'yaw':yaw,'pitch':pitch,'samples':[]})
for passage in json.loads((OUT/'passage-times.json').read_text())['passages']:
 s.frame_set(passage['blender_frame'],subframe=passage['blender_subframe']);bpy.context.view_layer.update();root=np.asarray(s.objects[c.ROOT_NAME].matrix_world);origin=Vector(root[:3,:3]@np.array(base.location)+root[:3,3])
 with preview.without_annotations(s,bpy.context.view_layer):
  deps=bpy.context.evaluated_depsgraph_get()
  for candidate in candidates:
   b=root[:3,:3]@np.array(cp.rotation(candidate['yaw'],candidate['pitch'],base.roll));values=[]
   for y in np.linspace(-1,1,11):
    for x in np.linspace(-1,1,17):
     ray=Vector(b@np.array([x*math.tan(math.radians(base.fov/2)),y*math.tan(math.radians(candidate['vfov']/2)),-1.]));ray.normalize()
     hit,loc,n,face,hitobj,m=s.ray_cast(deps,origin,ray,distance=300)
     if hit and hitobj.original==obj and 0<=face<len(face_span):values.append(face_span[face])
   values=np.array(values);inside=values>=46
   candidate['samples'].append({'time_s':passage['time_s'],'target_fraction':float(inside.sum()/187),'purity':float(inside.mean()) if len(values) else 0})
 print('TIP_PASSAGE',passage['revolution'],flush=True)
for t in candidates:
 t['min_fraction']=min(v['target_fraction'] for v in t['samples']);t['mean_purity']=float(np.mean([v['purity'] for v in t['samples']]));t['min_purity']=min(v['purity'] for v in t['samples'])
 t['score']=t['mean_purity']+t['min_purity']*.25+t['min_fraction']*.5-max(0,.22-t['min_fraction'])*6
candidates.sort(key=lambda t:-t['score']);best=candidates[0];c.apply_parameters(cam,replace(base,yaw=best['yaw'],pitch=best['pitch'],vfov=best['vfov']))
layout=c.layout_dict(s);c.validate_layout(s,layout);(OUT/'T1-same-blade-four-cameras-v2.json').write_text(json.dumps(layout,ensure_ascii=False,indent=2));(OUT/'tip-motion-search.json').write_text(json.dumps(candidates,indent=2))
print('TIP_BEST',json.dumps(best),flush=True)
