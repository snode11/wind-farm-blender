"""Factory-startup Blender compatibility harness. No rendering or user scene access.
Input is a clearly marked compatibility fixture copied from truth, not a reconstruction.
Original delivered scripts are run unchanged via runpy.
"""
import json
import runpy
import sys
from pathlib import Path
import bpy
import numpy as np

ROOT=Path('/Users/eason/Desktop/wfcrl/blade_recon')
OUT=ROOT/'out/trial-20261004/blender'
sys.path.insert(0,str(ROOT))
from model import Rotor,TurbineConfig
fixture=json.loads((OUT/'compatibility_fixture.json').read_text())
truth=json.loads((ROOT/'data/synth20/truth.json').read_text())
cameras=json.loads((ROOT/'data/synth20/cameras.json').read_text())
results={'scope':'Independent background factory-startup Blender; no rendering; truth-derived compatibility fixture; original source files unchanged', 'blender_version':bpy.app.version_string,'fixture_original_frames':[f['frame'] for f in fixture['frames']], 'checks':{}}
sys.argv=['blender_view.py','--','--recon',str(OUT/'compatibility_fixture.json'),'--truth',str(ROOT/'data/synth20/truth.json'),'--cameras',str(ROOT/'data/synth20/cameras.json')]
runpy.run_path(str(ROOT/'blender_view.py'),run_name='__main__')
sc=bpy.context.scene
rotor=Rotor(TurbineConfig.from_dict(fixture['turbine']))
S,N=rotor.cfg.n_sections,rotor.cfg.n_ring
GREEN=np.array([.15,.75,.25,1]); ORANGE=np.array([1,.55,.1,1])
objects=[]
for ob in bpy.data.objects:
 objects.append({'name':ob.name,'type':ob.type,'vertices':len(ob.data.vertices) if ob.type=='MESH' else None,'faces':len(ob.data.polygons) if ob.type=='MESH' else None})
results['objects']=objects
results['timeline']={'start':sc.frame_start,'end':sc.frame_end,'fps':sc.render.fps,'active_camera':sc.camera.name if sc.camera else None}
geometry=[]
first=None
for scene_frame,fr in enumerate(fixture['frames'],1):
 sc.frame_set(scene_frame)
 expected,_=rotor.forward(np.array(fr['state']))
 expected_truth,_=rotor.forward(np.array(truth['frames'][fr['frame']]['state']))
 rows=[]
 current=[]
 for b in range(3):
  ob=bpy.data.objects['Recon_Blade%d'%(b+1)]
  got=np.empty(len(ob.data.vertices)*3,np.float32)
  ob.data.vertices.foreach_get('co',got); got=got.reshape(-1,3)
  ghost=bpy.data.objects['Truth_Blade%d'%(b+1)]
  gt=np.empty(len(ghost.data.vertices)*3,np.float32)
  ghost.data.vertices.foreach_get('co',gt); gt=gt.reshape(-1,3)
  col=np.empty(len(ob.data.color_attributes['evidence'].data)*4,np.float32)
  ob.data.color_attributes['evidence'].data.foreach_get('color',col); col=col.reshape(-1,4)
  wanted=np.where(np.repeat(np.array(fr['observed_sections'][b],bool),N)[:,None],GREEN,ORANGE)
  green_count=int((np.linalg.norm(col-GREEN,axis=1)<.01).sum())
  orange_count=int((np.linalg.norm(col-ORANGE,axis=1)<.01).sum())
  rows.append({'blade':b+1,'max_abs_vertex_error_m':float(np.max(np.abs(got-expected[b].reshape(-1,3)))),'max_abs_truth_vertex_error_m':float(np.max(np.abs(gt-expected_truth[b].reshape(-1,3)))),'max_abs_color_error':float(np.max(np.abs(col-wanted))), 'color_attribute_domain':ob.data.color_attributes['evidence'].domain,'color_attribute_type':ob.data.color_attributes['evidence'].data_type,'green_vertices':green_count,'orange_vertices':orange_count,'unique_colors':np.unique(col,axis=0).tolist()})
  current.append(got.copy())
 if first is None: first=current
 geometry.append({'scene_frame':scene_frame,'original_frame':fr['frame'],'max_abs_vertex_change_from_first_m':float(max(np.max(np.abs(v-z)) for v,z in zip(current,first))),'blades':rows})
results['geometry_frames']=geometry
results['checks']['mesh_creation']=all(len(bpy.data.objects['Recon_Blade%d'%(b+1)].data.vertices)==S*N for b in range(3))
results['checks']['geometry_matches_forward_model']=all(r['max_abs_vertex_error_m']<1e-4 and r['max_abs_truth_vertex_error_m']<1e-4 for f in geometry for r in f['blades'])
results['checks']['frame_change_moves_vertices']=all(f['max_abs_vertex_change_from_first_m']>1 for f in geometry[1:])
results['checks']['colors_follow_observed_sections']=all(r['max_abs_color_error']<.01 and r['green_vertices']==20*N and r['orange_vertices']==20*N for f in geometry for r in f['blades'])

def effective_K(ob,W,H):
 p=np.array(ob.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=W,y=H,scale_x=sc.render.pixel_aspect_x,scale_y=sc.render.pixel_aspect_y))
 return np.array([[W*.5*p[0,0],0,W*.5*(1-p[0,2])-.5],[0,H*.5*p[1,1],H*.5*(1+p[1,2])-.5],[0,0,1]])

def calibration_snapshot(label):
 rows=[]
 W=int(sc.render.resolution_x*sc.render.resolution_percentage/100)
 H=int(sc.render.resolution_y*sc.render.resolution_percentage/100)
 for d in cameras['cameras']:
  ob=bpy.data.objects[d['name']]
  M=np.array(ob.matrix_world); M[:3,:3]/=np.linalg.norm(M[:3,:3],axis=0)
  M[:3,:3]=M[:3,:3]@np.diag([1,-1,-1]); gotT=np.linalg.inv(M)
  K=effective_K(ob,W,H)
  rows.append({'name':d['name'],'W':W,'H':H,'input_W':d['W'],'input_H':d['H'],'effective_K':K.tolist(),'input_K':d['K'],'max_abs_K_error_px':float(np.max(np.abs(K-np.array(d['K'])))),'max_abs_T_error':float(np.max(np.abs(gotT-np.array(d['T_cv_from_world']))))})
 results[label]=rows
 return rows

def export_original(name):
 target=OUT/name
 sys.argv=['blender_export_cameras.py','--','--cams','C1','C2','C3','--hub','Hub','--upwind','1','0','0','--out',str(target)]
 runpy.run_path(str(ROOT/'blender_export_cameras.py'),run_name='__main__')
 data=json.loads(target.read_text()); rows=[]
 for d,ref in zip(data['cameras'],cameras['cameras']):
  rows.append({'name':d['name'],'W':d['W'],'H':d['H'],'K':d['K'],'max_abs_K_error_px':float(np.max(np.abs(np.array(d['K'])-np.array(ref['K'])))),'max_abs_T_error':float(np.max(np.abs(np.array(d['T_cv_from_world'])-np.array(ref['T_cv_from_world']))))})
 return rows

native=calibration_snapshot('camera_native_scene')
results['original_export_native_scene']=export_original('cameras_roundtrip_original_native.json')
sc.render.resolution_x=cameras['cameras'][0]['W']; sc.render.resolution_y=cameras['cameras'][0]['H']; sc.render.resolution_percentage=100
matched=calibration_snapshot('camera_after_resolution_only')
results['original_export_resolution_only']=export_original('cameras_roundtrip_original_resolution_only.json')
results['checks']['camera_extrinsics_match_input']=all(r['max_abs_T_error']<1e-4 for r in native)
results['checks']['camera_native_intrinsics_match_input']=all(r['max_abs_K_error_px']<1e-3 and r['W']==r['input_W'] and r['H']==r['input_H'] for r in native)
results['checks']['camera_intrinsics_match_after_resolution_only']=all(r['max_abs_K_error_px']<1e-3 for r in matched)
results['checks']['original_export_extrinsics_roundtrip']=all(r['max_abs_T_error']<1e-4 for r in results['original_export_native_scene'])
# Diagnostic workaround solely inside this disposable process: scene output dimensions and
# pixel aspect, camera lens and shifts; no original source or .blend is written.
d=cameras['cameras'][0]; K=np.array(d['K']); W,H=d['W'],d['H']
sc.render.pixel_aspect_x=1; sc.render.pixel_aspect_y=float(K[0,0]/K[1,1])
for d in cameras['cameras']:
 K=np.array(d['K']); ob=bpy.data.objects[d['name']]; cd=ob.data
 cd.sensor_fit='HORIZONTAL'; cd.lens=float(K[0,0]*cd.sensor_width/d['W'])
 cd.shift_x=float(((d['W']-1)/2-K[0,2])/d['W'])
 cd.shift_y=float((K[1,2]-(d['H']-1)/2)*sc.render.pixel_aspect_y/d['W'])
fixed=calibration_snapshot('camera_diagnostic_K_workaround')
results['checks']['diagnostic_workaround_restores_K']=all(r['max_abs_K_error_px']<1e-3 for r in fixed)
results['original_export_with_pixel_aspect']=export_original('cameras_roundtrip_original_pixel_aspect.json')
results['checks']['original_export_retains_asymmetric_focal_lengths']=all(r['max_abs_K_error_px']<1e-3 for r in results['original_export_with_pixel_aspect'])
(OUT/'compatibility_results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps({'checks':results['checks'],'geometry_max_error_m':max(r['max_abs_vertex_error_m'] for f in geometry for r in f['blades']),'results_path':str(OUT/'compatibility_results.json')},ensure_ascii=False,indent=2))
