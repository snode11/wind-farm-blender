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
runpy.run_path(str(ROOT/'blender_view.py'),run_name='__main__')
rotor=Rotor(TurbineConfig.from_dict(r['turbine'])); sc=bpy.context.scene
GREEN=np.array([.15,.75,.25,1]); ORANGE=np.array([1,.55,.1,1])
results={'scope':'Original unmodified blender_view.py; real original recon output; standalone factory-startup; no rendering','blender_version':bpy.app.version_string,'recon_path':str(reconpath),'recon_frames':len(r['frames']),'frames':[],'checks':{}}
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
sc.frame_set(1)
blendpath=OUT/'synth20_original_view_validation.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blendpath))
results['saved_blend']=str(blendpath)
results['saved_frame']=sc.frame_current
results['handler_persistence_note']='frame_change_pre update function exists in the running process only, not stored by this .blend. Run original blender_view.py with --recon/--truth/--cameras to recreate playback; opening this .blend alone provides the saved frame 1 snapshot.'
(OUT/'real_reconstruction_results.json').write_text(json.dumps(results,indent=2))
print(json.dumps({'checks':results['checks'],'max_abs_vertex_error_m':max(a['max_abs_vertex_error_m'] for a in rows),'saved_blend':str(blendpath)},indent=2))
