import bpy,sys,time,json,traceback,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).parent
sys.path.insert(0,str(ROOT/'blender_frontend'))
import wfrl_blender
from wfrl_blender import runtime,charts
wfrl_blender.register();runtime.select_mode('interactive_training');runtime.connect(8879)
report={'backend':'FAST.Farm','mode':'interactive_training','gui':not bpy.app.background,'samples':[],'checks':{},'errors':[]}
started=time.monotonic();training_start=None;stop_sent=False;last_step=None;last_log=0;camera_index=0

def finish():
 report['final_status']=runtime.get_state().run_status;report['elapsed_seconds']=time.monotonic()-started
 report['checks']['history_export']=charts.export_job.poll()=='COMPLETE'
 report['passed']=not report['errors'] and report['final_status']=='STOPPED' and len(report['samples'])>=2 and report['checks'].get('history_export',False)
 (OUT/'report.json').write_text(json.dumps(report,indent=2,default=str));print('ACCEPTANCE_RESULT',report['passed'],flush=True)
 runtime.shutdown();bpy.ops.wm.quit_blender()

def tick():
 global training_start,stop_sent,last_step,last_log,camera_index
 try:
  now=time.monotonic();ui=runtime.get_state()
  if training_start is None and ui.connection=='CONNECTED' and runtime.allows_command('start'):
   runtime.send_command('start',{'options':{'scene':str(ROOT/'scenes/turb3_ctrl3.yaml'),'iters':100,'n_steps':8,'episode_steps':0,'warmup_steps':0}});training_start=now;print('REAL_TRAINING_STARTED',flush=True)
  snap=runtime.kinematics.snapshot
  if snap and snap['step']!=last_step:
   last_step=snap['step'];entry={'seconds':now-started,'step':last_step,'phase':snap.get('phase'),'channels':snap['turbines'][0]['channels'],'rotor_angles':dict(runtime.kinematics.rotor_angles),'objects':len(bpy.data.objects)}
   blade=bpy.data.objects.get('WFRL.Turbine.T1.Blade1');entry['blade_pitch_degrees']=math.degrees(blade.rotation_euler.z) if blade else None
   report['samples'].append(entry)
   names=['WFRL.Camera.World','WFRL.Camera.Top','WFRL.Camera.Side','WFRL.Camera.T1.Closeup']
   if camera_index<len(names):
    name=names[camera_index]
    assert bpy.ops.wfrl.select_camera(camera_name=name)=={'FINISHED'}
    assert bpy.context.scene.camera.name==name
    report['checks'][name]=True;camera_index+=1
   if len(report['samples'])==3:
    bpy.context.scene.wfrl_capture_directory=str(OUT)
    report['checks']['screenshot']=bpy.ops.wfrl.capture_screenshot()=={'FINISHED'}
  if now-last_log>20:
   print('PROGRESS',round(now-started),ui.connection,ui.run_status,'step',last_step,flush=True);last_log=now
  if ui.error and str(ui.error) not in report['errors']:report['errors'].append(str(ui.error))
  if training_start and (now-training_start>180 or report['errors']) and not stop_sent and runtime.allows_command('stop'):
   runtime.send_command('stop');stop_sent=True;report['stop_requested_seconds']=now-training_start;print('STOP_REQUESTED',flush=True)
  if training_start and ui.run_status in {'STOPPED','FAILED'}:
   if 'export_started' not in report:
    report['export_started']=True
    report['checks']['export_operator']=bpy.ops.wfrl.export_history(filepath=str(OUT/'history.json'))=={'FINISHED'}
   elif charts.export_job.poll()!='WRITING':finish();return None
  if now-started>420:
   report['errors'].append('Frontend deadline exceeded');finish();return None
 except Exception:
  report['errors'].append(traceback.format_exc());print(report['errors'][-1],flush=True)
  if training_start and runtime.allows_command('stop') and not stop_sent:runtime.send_command('stop');stop_sent=True
  elif not training_start:finish();return None
 return .1
bpy.app.timers.register(tick,first_interval=.5)
