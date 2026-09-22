from pathlib import Path
import sys,json,math
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
from mathutils import Vector
import wfrl_blender as addon
from wfrl_blender import farm_flex,custom_cameras as c,camera_projection as cp,custom_camera_preview as preview
OUT=Path(__file__).parent
addon.register();addon.load_demo_scene();s=bpy.context.scene
c.import_layout(s,json.loads((OUT/'T1-same-blade-four-cameras-v2.json').read_text()),overwrite=True)
obj,rest,*_=next(r for r in farm_flex._ACTIVE.blades if r[0].name=='WFRL.Turbine.T1.Blade1')
spans=rest[:,2]-1.5;face_span=np.array([spans[list(p.vertices)].mean() for p in obj.data.polygons]);ranges=[(0,16.5),(15,32),(30.5,47.5),(46,61.5)]
results=[]
for passage in json.loads((OUT/'passage-times.json').read_text())['passages']:
 s.frame_set(passage['blender_frame'],subframe=passage['blender_subframe']);bpy.context.view_layer.update()
 record={'time_s':passage['time_s'],'revolution':passage['revolution'],'cameras':[]}
 with preview.without_annotations(s,bpy.context.view_layer):
  deps=bpy.context.evaluated_depsgraph_get()
  for slot,(lo,hi) in enumerate(ranges,1):
   cam=c.get_camera(s,slot);p=c.parameters(cam);matrix=np.asarray(cam.matrix_world);origin=Vector(matrix[:3,3]);values=[];other={};total=0
   for y in np.linspace(-1,1,13):
    for x in np.linspace(-1,1,21):
     direction=Vector(matrix[:3,:3]@np.array([x*math.tan(math.radians(p.fov/2)),y*math.tan(math.radians(p.vfov/2)),-1.]));direction.normalize()
     hit,loc,n,face,hitobj,m=s.ray_cast(deps,origin,direction,distance=300);total+=1
     if hit and hitobj.original==obj and 0<=face<len(face_span):values.append(float(face_span[face]))
     else:other[hitobj.name if hitobj else 'background']=other.get(hitobj.name if hitobj else 'background',0)+1
   values=np.array(values);inside=(values>=lo)&(values<=hi)
   record['cameras'].append({'slot':slot,'target_pixel_fraction':float(inside.sum()/total),'blade_pixel_fraction':len(values)/total,'desired_span_fraction_among_blade_hits':float(inside.mean()) if len(values) else 0,'observed_span_quantiles':np.quantile(values,[.05,.5,.95]).tolist() if len(values) else [],'other':other})
 results.append(record);print('PASSAGE',round(passage['time_s'],3),[(v['slot'],round(v['target_pixel_fraction'],2),round(v['desired_span_fraction_among_blade_hits'],2)) for v in record['cameras']],flush=True)
(OUT/'passage-coverage.json').write_text(json.dumps({'method':'21x13 first-hit scene rays at each same-azimuth passage; diagnostic sampling, not exhaustive surface coverage','results':results},indent=2));print('PASSAGE_CHECK_DONE',flush=True)
