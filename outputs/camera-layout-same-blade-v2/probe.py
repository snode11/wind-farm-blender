from pathlib import Path
import sys,json,math
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import farm_flex,custom_cameras as c,turbine_geometry as tg
addon.register();addon.load_demo_scene();s=bpy.context.scene;farm=farm_flex._ACTIVE
OUT=Path(__file__).parent
result={'scalars':tg.geometry_data()['scalars'],'scene_frames':[s.frame_start,s.frame_end],'times':[float(farm.times[0]),float(farm.times[-1])],'samples':[]}
for frame in [1,31,61,91,121,151,181,211,241,271,301,331,361]:
 s.frame_set(frame);bpy.context.view_layer.update();inverse=np.array(s.objects[c.ROOT_NAME].matrix_world.inverted())
 row={'frame':frame,'time_s':s['wfrl_clearance_time_s'],'blades':[]}
 for obj,rest,*_ in farm.blades[:3]:
  vertices=np.empty(len(obj.data.vertices)*3,np.float32);obj.data.vertices.foreach_get('co',vertices);vertices=vertices.reshape(-1,3)
  m=inverse@np.array(obj.matrix_world);vertices=vertices@m[:3,:3].T+m[:3,3]
  spans=rest[:,2];zz=np.unique(np.round(spans,5));centers=[]
  for z in [spans.min(),10,25,40,55,spans.max()]:
   ids=np.abs(spans-z)<max(.5,np.min(np.abs(spans-z))+.01);centers.append([float(z),*vertices[ids].mean(axis=0).tolist()])
  row['blades'].append({'name':obj.name,'centers':centers})
 row['azimuth_deg']=float(s.objects['WFRL.Turbine.T1.Rotor'].rotation_euler.x*180/math.pi)
 result['samples'].append(row)
s.frame_set(1);bpy.context.view_layer.update();inv=np.array(s.objects[c.ROOT_NAME].matrix_world.inverted());payload={}
for obj,rest,*_ in farm.blades[:3]:
 vertices=np.empty(len(obj.data.vertices)*3,np.float32);obj.data.vertices.foreach_get('co',vertices);vertices=vertices.reshape(-1,3)
 m=inv@np.array(obj.matrix_world);vertices=vertices@m[:3,:3].T+m[:3,3]
 payload[obj.name.split('.')[-1]]=vertices;payload[obj.name.split('.')[-1]+'_spans']=rest[:,2]
result['mounts']=[]
for name,verts,faces,normals,tree in c._geometry(s):
 for target in [(-3,0,-10),(-3,-10,1),(-3,10,1),(0,0,-10),(-4,0,1),(-3,0,10)]:
  from mathutils import Vector
  point,normal,*_=tree.find_nearest(Vector(target));pos=point+normal*.3
  try:anchors=c.validate_position(s,tuple(pos));result['mounts'].append({'target':target,'location':list(pos),'surface':name,'valid':True})
  except Exception as e:result['mounts'].append({'target':target,'location':list(pos),'error':str(e)})
np.savez(OUT/'frame1-geometry.npz',**payload)
(OUT/'probe.json').write_text(json.dumps(result,indent=2));print('PROBE_DONE',flush=True)
