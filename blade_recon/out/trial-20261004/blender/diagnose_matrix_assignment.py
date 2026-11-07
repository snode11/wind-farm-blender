import json,sys
from pathlib import Path
import bpy,numpy as np
from mathutils import Matrix
ROOT=Path('/Users/eason/Desktop/wfcrl/blade_recon')
OUT=ROOT/'out/trial-20261004/blender'
d=json.loads((ROOT/'data/synth20/cameras.json').read_text())['cameras'][0]
T=np.array(d['T_cv_from_world']); R,t=T[:3,:3],T[:3,3]
M=np.eye(4); M[:3,:3]=R.T@np.diag([1,-1,-1]); M[:3,3]=-R.T@t
out={'expected_matrix':M.tolist(),'R_det':float(np.linalg.det(R))}
for label,value in [('nested_list',[list(r) for r in M]),('mathutils_Matrix',Matrix(M.tolist()))]:
 ob=bpy.data.objects.new(label,None); bpy.context.collection.objects.link(ob)
 ob.matrix_world=value
 bpy.context.view_layer.update()
 out[label+'_before_scale']={'matrix':np.array(ob.matrix_world).tolist(),'location':list(ob.location),'rotation':list(ob.rotation_euler)}
 ob.scale=(3,3,3); bpy.context.view_layer.update()
 got=np.array(ob.matrix_world); got[:3,:3]/=np.linalg.norm(got[:3,:3],axis=0); got[:3,:3]=got[:3,:3]@np.diag([1,-1,-1])
 back=np.linalg.inv(got)
 out[label+'_after_scale']={'matrix':np.array(ob.matrix_world).tolist(),'T':back.tolist(),'max_T_error':float(np.max(abs(back-T)))}
(OUT/'matrix_assignment_diagnosis.json').write_text(json.dumps(out,indent=2))
print(json.dumps(out,indent=2))
