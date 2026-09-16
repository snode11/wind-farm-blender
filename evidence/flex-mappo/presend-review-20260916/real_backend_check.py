import bpy, importlib, time, json, traceback, math
from pathlib import Path
ROOT=Path('/Users/eason/Desktop/wfcrl/wind farm RL')
OUT=Path('/tmp/wfrl-presend-review-20260916/backend-result.json')
MODULE='bl_ext.user_default.wfrl_blender'
addon=importlib.import_module(MODULE)
if MODULE not in bpy.context.preferences.addons: bpy.ops.preferences.addon_enable(module=MODULE)
r=addon.runtime
report={'passed':False,'checks':[],'runtime':str(Path(addon.__file__).resolve()),'backend':'FAST.Farm','scene':'scenes/turb3_row.yaml'}
def record(name):
 report['checks'].append(name); print('CHECK_PASS',name,flush=True)
def wait_for(predicate,timeout=90):
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  r.tick()
  state=r.get_state()
  if state.run_status=='FAILED' or (state.error and r.progress() not in {'Connecting','Awaiting handshake'}): raise AssertionError(str(state))
  if predicate(): return
  time.sleep(.02)
 raise AssertionError('Timeout '+str(r.get_state())+' pending='+str(r._pending_command))
try:
 r.select_mode('replay');r.connect(8766)
 wait_for(lambda:r.get_state().connection=='CONNECTED' and r.get_state().confirmed,15)
 record('real Bridge handshake')
 r.send_workflow_command('scene.load',{'scene':str(ROOT/'scenes/turb3_row.yaml')})
 wait_for(lambda:bool(r.workflow_scene) and r.configuration_editable())
 assert len(r.workflow_scene['layout'])==3
 record('scene.load acknowledged; three-turbine scene built')
 r.send_command('start',{'options':{'scene':str(ROOT/'scenes/turb3_row.yaml'),'ckpt_path':str(ROOT/'results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt'),'replay_steps':8,'warmup_steps':0,'seed':0}})
 wait_for(lambda:r.get_state().run_status=='RUNNING' and r._pending_command is None)
 r.send_command('pause')
 wait_for(lambda:r.get_state().run_status=='PAUSED' and r._pending_command is None and r.kinematics.snapshot is not None,240)
 record('FAST.Farm started, real snapshot arrived, pause acknowledged')
 first=r.kinematics.snapshot['step']
 report['first_snapshot']=r.kinematics.snapshot
 r.send_command('step')
 wait_for(lambda:r._pending_command is None and r.kinematics.snapshot['step']>first,120)
 assert r.kinematics.snapshot['step']==first+1
 record('single step advanced exactly one backend step')
 snapshot=r.kinematics.snapshot
 assert len(snapshot['turbines'])==3
 for t in snapshot['turbines']:
  for key in ['yaw','pitch','rotor_speed','power','torque']:
   c=t['channels'][key];assert c['value'] is not None and math.isfinite(c['value']),(t['turbine_id'],key,c)
   if key == 'rotor_speed':
    assert c['fidelity']=='SYNTH' and 'FastFarmDriver._rotor_speed' in c['provenance'].get('formula',''),c
   else:
    assert c['fidelity']=='DIRECT',(key,c)
 report['verified_snapshot']=snapshot
 record('three turbines: finite DIRECT yaw/pitch/power/torque; rpm explicitly formula-derived')
 sid=r.get_state().session_id
 r.disconnect();r.connect(8766)
 wait_for(lambda:r.get_state().connection=='CONNECTED' and r.get_state().confirmed)
 assert r.get_state().session_id==sid and r.get_state().run_status=='PAUSED'
 record('disconnect/reconnect resumed same paused session')
 r.send_command('resume');wait_for(lambda:r.get_state().run_status=='RUNNING' and r._pending_command is None)
 wait_for(lambda:r.kinematics.snapshot['step']>snapshot['step'],120)
 r.send_command('stop');wait_for(lambda:r.get_state().run_status=='STOPPED' and r._pending_command is None,150)
 record('resume produced another snapshot; stop confirmed STOPPED')
 r.send_command('reset');wait_for(lambda:r.get_state().run_status=='READY' and r._pending_command is None)
 record('reset returned READY')
 report['passed']=True
except Exception:
 report['error']=traceback.format_exc();print(report['error'],flush=True)
finally:
 report['final_state']=vars(r.get_state()).copy();report['final_state']['capabilities']=list(report['final_state']['capabilities'])
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str))
 try:
  if r.allows_command('stop'):
   r.send_command('stop');wait_for(lambda:r.get_state().run_status in {'STOPPED','FAILED'},150)
 except Exception: traceback.print_exc()
 r.disconnect();addon.unregister()
if not report['passed']: raise AssertionError(report.get('error'))
print('REAL_BACKEND_END_TO_END_PASS',flush=True)
