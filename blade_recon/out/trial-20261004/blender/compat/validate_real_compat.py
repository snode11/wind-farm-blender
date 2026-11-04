"""Original Blender viewer on real 200-frame reconstruction. Disposable scene only."""
import json,runpy,sys
from pathlib import Path
import bpy,numpy as np
ROOT=Path('/Users/eason/Desktop/wfcrl/blade_recon'); OUT=ROOT/'out/trial-20261004/blender'
sys.path.insert(0,str(ROOT))
from model import Rotor,TurbineConfig
reconpath=ROOT/'out/trial-20261004/synth20/recon.json'
r=json.loads(reconpath.read_text()); truth=json.loads((ROOT/'data/synth20/truth.json').read_text())
sys.argv=['blender_view.py','--','--recon',str(reconpath),'--truth',str(ROOT/'data/synth20/truth.json'),'--cameras',str(ROOT/'data/synth20/cameras.json')]
runpy.run_path(str(OUT/'compat/blender_view_compat.py'),run_name='__main__')
rotor=Rotor(TurbineConfig.from_dict(r['turbine'])); sc=bpy.context.scene
GREEN=np.array([.15,.75,.25,1]); ORANGE=np.array([1,.55,.1,1])
results={'scope':'Compatibility wrapper for original viewer; real original recon output; standalone factory-startup; no rendering; original root source unchanged','blender_version':bpy.app.version_string,'recon_path':str(reconpath),'recon_frames':len(r['frames']),'frames':[],'checks':{}}
for scene_frame in [1,51,101,200]:
 sc.frame_set(scene_frame); fr=r['frames'][scene_frame-1]; expected,_=rotor.forward(np.array(fr['state'])); expected_truth,_=rotor.forward(np.array(truth['frames'][fr['frame']]['state']))
 frame={'scene_frame':scene_frame,'original_frame':fr['frame'],'blades':[]}
 for b in range(3):
  ob=bpy.data.objects['Recon_Blade%d'%(b+1)]
  got=np.empty(len(ob.data.vertices)*3,np.float32); ob.data.vertices.foreach_get('co',got); got=got.reshape(-1,3)
  ghost=bpy.data.objects['Truth_Blade%d'%(b+1)]
  gt=np.empty(len(ghost.data.vertices)*3,np.float32); ghost.data.vertices.foreach_get('co',gt); gt=gt.reshape(-1,3)
  col=np.empty(len(ob.data.color_attributes['evidence'].data)*4,np.float32); ob.data.color_attributes['evidence'].data.foreach_get('color',col); col=col.reshape(-1,4)
  seen=np.repeat(np.array(fr['observed_sections'][b],bool),rotor.cfg.n_ring)
  wanted=np.where(seen[:,None],GREEN,ORANGE)
  frame['blades'].append({'blade':b+1,'vertices':len(got),'faces':len(ob.data.polygons),'max_abs_vertex_error_m':float(np.max(abs(got-expected[b].reshape(-1,3)))),'max_abs_truth_error_m':float(np.max(abs(gt-expected_truth[b].reshape(-1,3)))),'max_abs_color_error':float(np.max(abs(col-wanted))),'expected_green_vertices':int(seen.sum()),'actual_green_vertices':int((np.linalg.norm(col-GREEN,axis=1)<.01).sum()),'expected_orange_vertices':int((~seen).sum()),'actual_orange_vertices':int((np.linalg.norm(col-ORANGE,axis=1)<.01).sum())})
 results['frames'].append(frame)
rows=[a for f in results['frames'] for a in f['blades']]
results['checks']['real_geometry_matches_forward']=all(a['max_abs_vertex_error_m']<1e-4 for a in rows)
results['checks']['truth_ghost_matches_original_frame']=all(a['max_abs_truth_error_m']<1e-4 for a in rows)
results['checks']['observed_section_colors_match']=all(a['max_abs_color_error']<.01 and a['expected_green_vertices']==a['actual_green_vertices'] and a['expected_orange_vertices']==a['actual_orange_vertices'] for a in rows)

# Validate after both frame_set and explicit depsgraph update.
sc.frame_set(1); bpy.context.view_layer.update()
camera_json=json.loads((ROOT/'data/synth20/cameras.json').read_text())
W=int(sc.render.resolution_x*sc.render.resolution_percentage/100); H=int(sc.render.resolution_y*sc.render.resolution_percentage/100)
camrows=[]
for d in camera_json['cameras']:
 ob=bpy.data.objects[d['name']]
 M=np.array(ob.matrix_world); M[:3,:3]/=np.linalg.norm(M[:3,:3],axis=0)
 M[:3,:3]=M[:3,:3]@np.diag([1,-1,-1]); gotT=np.linalg.inv(M)
 P=np.array(ob.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=W,y=H,scale_x=sc.render.pixel_aspect_x,scale_y=sc.render.pixel_aspect_y))
 gotK=np.array([[W*.5*P[0,0],0,W*.5*(1-P[0,2])-.5],[0,H*.5*P[1,1],H*.5*(1+P[1,2])-.5],[0,0,1]])
 refT=np.array(d['T_cv_from_world'])
 C=-refT[:3,:3].T@refT[:3,3]
 camrows.append({'name':d['name'],'W':W,'H':H,'actual_camera_center_world':list(ob.matrix_world.translation),'max_abs_camera_center_error_m':float(np.max(abs(np.array(ob.matrix_world.translation)-C))),'max_abs_T_error':float(np.max(abs(gotT-refT))),'effective_K':gotK.tolist(),'max_abs_K_error_px':float(np.max(abs(gotK-np.array(d['K']))))})
results['cameras']=camrows
results['checks']['camera_extrinsics_match_after_frame_and_depsgraph_update']=all(a['max_abs_T_error']<1e-4 for a in camrows)
results['checks']['camera_output_dimensions_match']=all(a['W']==d['W'] and a['H']==d['H'] for a,d in zip(camrows,camera_json['cameras']))
results['checks']['camera_intrinsics_exact_match']=all(a['max_abs_K_error_px']<1e-3 for a in camrows)
target=OUT/'compat/cameras_roundtrip_compat.json'
sys.argv=['blender_export_cameras.py','--','--cams','C1','C2','C3','--hub','Hub','--upwind','1','0','0','--out',str(target)]
runpy.run_path(str(ROOT/'blender_export_cameras.py'),run_name='__main__')
exported=json.loads(target.read_text()); erows=[]
for d,ref in zip(exported['cameras'],camera_json['cameras']):
 erows.append({'name':d['name'],'W':d['W'],'H':d['H'],'max_abs_T_error':float(np.max(abs(np.array(d['T_cv_from_world'])-np.array(ref['T_cv_from_world'])))),'max_abs_K_error_px':float(np.max(abs(np.array(d['K'])-np.array(ref['K']))))})
results['original_export_roundtrip']=erows
results['checks']['original_export_extrinsics_roundtrip']=all(a['max_abs_T_error']<1e-4 for a in erows)
results['checks']['original_export_intrinsics_exact_roundtrip']=all(a['max_abs_K_error_px']<1e-3 for a in erows)

sc.frame_set(1)
blendpath=OUT/'compat/synth20_compat_view_validation.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blendpath))
results['saved_blend']=str(blendpath)
results['saved_frame']=sc.frame_current
results['handler_persistence_note']='frame_change_pre update function exists in the running process only, not stored by this .blend. Run compat/blender_view_compat.py with --recon/--truth/--cameras to recreate playback; opening this .blend alone provides the saved frame 1 snapshot.'
(OUT/'compat/real_reconstruction_compat_results.json').write_text(json.dumps(results,indent=2))
print(json.dumps({'checks':results['checks'],'max_abs_vertex_error_m':max(a['max_abs_vertex_error_m'] for a in rows),'saved_blend':str(blendpath)},indent=2))
