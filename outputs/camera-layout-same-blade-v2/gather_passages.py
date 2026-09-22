from pathlib import Path
import sys,json
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import farm_flex,custom_cameras as c
OUT=Path(__file__).parent
addon.register();addon.load_demo_scene();s=bpy.context.scene
obj,rest,*_=next(r for r in farm_flex._ACTIVE.blades if r[0].name=='WFRL.Turbine.T1.Blade1');sets=[]
for passage in json.loads((OUT/'passage-times.json').read_text())['passages']:
 s.frame_set(passage['blender_frame'],subframe=passage['blender_subframe']);bpy.context.view_layer.update()
 co=np.empty(len(obj.data.vertices)*3,np.float32);obj.data.vertices.foreach_get('co',co);co=co.reshape(-1,3)
 m=np.array(s.objects[c.ROOT_NAME].matrix_world.inverted()@obj.matrix_world);sets.append(co@m[:3,:3].T+m[:3,3])
faces=[list(p.vertices) for p in obj.data.polygons]
(OUT/'blade-faces.json').write_text(json.dumps(faces))
np.savez(OUT/'passage-geometry.npz',points=np.array(sets),spans=rest[:,2]-1.5)
print('PASSAGE_GEOMETRY_DONE',flush=True)
