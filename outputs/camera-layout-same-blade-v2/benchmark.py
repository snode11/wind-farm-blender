"""Serial real-window playback checks of the delivered layout; no user scene saved."""
from pathlib import Path
import os,sys,time,json,traceback,resource,subprocess,statistics,hashlib,platform
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[os.environ.get('WFRL_ADDON_PATH',str(ROOT/'blender_frontend')),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import custom_cameras as c,native_camera_views as native,custom_camera_preview as preview
OUT=Path(__file__).parent/'performance';OUT.mkdir(exist_ok=True)
MODE=os.environ.get('LAYOUT_BENCH_MODE','quad');DURATION=float(os.environ.get('LAYOUT_BENCH_SECONDS','60'))
state={};samples=[];draws={};last={};timer_lags=[];offscreen_calls=0;lifecycle=[]
saved_shutdown=native.shutdown
def traced_shutdown(*args,**kwargs):
 lifecycle.append({'wall_s':time.time(),'windows':[w.as_pointer() for w in bpy.context.window_manager.windows],'stack':traceback.format_stack(limit=7)})
 return saved_shutdown(*args,**kwargs)
native.shutdown=traced_shutdown
saved_render=preview.render_group
def traced_render(*args,**kwargs):
 global offscreen_calls
 offscreen_calls+=1;return saved_render(*args,**kwargs)
preview.render_group=traced_render

def rss():return int(subprocess.check_output(['/bin/ps','-o','rss=','-p',str(os.getpid())],text=True).strip())/1024**2

def draw_count():
 if not state.get('measuring'):return
 key='original'
 if native._ACTIVE and bpy.context.window==native._ACTIVE.window:
  entry=next((e for e in native._ACTIVE.entries if e['area']==bpy.context.area),None)
  if entry:key=f"C{entry['slot']}"
 frame=bpy.context.scene.frame_current
 if last.get(key)!=frame:draws[key]=draws.get(key,0)+1;last[key]=frame

def tick():
 try:
  now=time.perf_counter()
  if state.get('due') is not None:timer_lags.append(max(0,now-state['due']))
  phase=state.get('phase',0)
  if phase==0:
   addon.register();addon.load_demo_scene();s=bpy.context.scene
   layout_path=Path(__file__).parent/'T1-same-blade-four-cameras-v2.json';c.import_layout(s,json.loads(layout_path.read_text()),overwrite=True)
   state.update(original=bpy.context.window,layout=c.layout_dict(s),phase=1,layout_sha256=hashlib.sha256(layout_path.read_bytes()).hexdigest())
   area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
   if MODE=='down':
    with bpy.context.temp_override(area=area):bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='DOWN')
   else:
    with bpy.context.temp_override(area=area):bpy.ops.wfrl.native_camera_view(mode='WATCH' if MODE=='single' else 'QUAD')
   return 3.
  if phase==1:
   if MODE!='down' and (not native._ACTIVE or len(native._ACTIVE.entries)<(1 if MODE=='single' else 4)):return .5
   if MODE=='quad_shadow_half':bpy.context.scene.eevee.shadow_resolution_scale=.5
   state['play_window']=native._ACTIVE.window if native._ACTIVE else state['original']
   state['play_window_pointer']=state['play_window'].as_pointer()
   bpy.context.scene.frame_set(1)
   with bpy.context.temp_override(window=state['play_window']):bpy.ops.screen.animation_play()
   state['phase']=2;return 3.
  if phase==2:
   state['environment']={'blender':bpy.app.version_string,'platform':platform.platform(),'addon_path':str(Path(addon.__file__).resolve()),'frame_range':[bpy.context.scene.frame_start,bpy.context.scene.frame_end],'scene_fps':bpy.context.scene.render.fps/bpy.context.scene.render.fps_base,'windows':[{'width':w.width,'height':w.height,'viewports':[{'width':a.width,'height':a.height,'shading':a.spaces.active.shading.type} for a in w.screen.areas if a.type=='VIEW_3D']} for w in bpy.context.window_manager.windows]}
   bpy.context.scene.frame_set(1);state.update(phase=3,start=time.perf_counter(),measuring=True,due=None)
   state['handle']=bpy.types.SpaceView3D.draw_handler_add(draw_count,(),'WINDOW','POST_PIXEL')
   state['baseline_rss_gib']=rss();timer_lags.clear();return .2
  if phase==3:
   elapsed=now-state['start'];samples.append({'t_s':elapsed,'rss_gib':rss(),'frame':bpy.context.scene.frame_current,'preview_renderers':len(preview._RENDERERS),'native_proxy_count':sum(o.name.startswith('T1.Native.') for o in bpy.context.scene.objects),'draw_counts':draws.copy(),'window_pointers':[w.as_pointer() for w in bpy.context.window_manager.windows],'playing_windows':[w.as_pointer() for w in bpy.context.window_manager.windows if w.screen.is_animation_playing]})
   assert state['play_window_pointer'] in samples[-1]['window_pointers'],'Playback window disappeared'
   if elapsed<DURATION:state['due']=time.perf_counter()+.5;return .5
   unchanged_since=samples[0]['t_s'];previous=samples[0]['draw_counts'];max_stall=0.
   for sample in samples[1:]:
    if sample['draw_counts']!=previous:unchanged_since=sample['t_s'];previous=sample['draw_counts']
    else:max_stall=max(max_stall,sample['t_s']-unchanged_since)
   assert max_stall<2.,f'Playback stopped or stalled for {max_stall:.2f} seconds; not a valid uninterrupted playback benchmark'
   state['measuring']=False;state['due']=None
   with bpy.context.temp_override(window=state['play_window']):bpy.ops.screen.animation_cancel(restore_frame=False)
   bpy.types.SpaceView3D.draw_handler_remove(state['handle'],'WINDOW')
   assert c.layout_dict(bpy.context.scene)==state['layout']
   sorted_lags=sorted(timer_lags)
   state['report']={'mode':MODE,'seconds':elapsed,'draws':{k:{'frames':v,'fps':v/elapsed} for k,v in draws.items()},'rss_start_gib':state['baseline_rss_gib'],'rss_end_gib':samples[-1]['rss_gib'],'rss_max_observed_gib':max(x['rss_gib'] for x in samples),'process_peak_rss_gib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**3,'offscreen_render_calls':offscreen_calls,'max_preview_renderers':max(x['preview_renderers'] for x in samples),'max_native_proxies':max(x['native_proxy_count'] for x in samples),'timer_lateness_p95_s':sorted_lags[min(len(sorted_lags)-1,int(len(sorted_lags)*.95))] if sorted_lags else 0,'timer_lateness_max_s':max(timer_lags,default=0),'timer_note':'scheduled callback lateness; not mouse-input latency','samples':samples,'layout_unchanged':True}
   native.shutdown();state['phase']=4;return 2.
  if phase==4:
   state['report'].update(environment=state['environment'],layout_sha256=state['layout_sha256'],status='PASS',window_lifecycle=lifecycle,draw_note='POST_PIXEL callbacks with a changed integer scene frame, divided by wall time; not physical display presentation rate')
   expected={'original'} | ({'C1'} if MODE=='single' else {'C1','C2','C3','C4'} if MODE.startswith('quad') else set())
   assert expected <= draws.keys() and all(draws[k]>0 for k in expected),draws
   state['report']['rss_after_close_gib']=rss();state['report']['proxies_after_close']=sum(o.name.startswith('T1.Native.') for o in bpy.context.scene.objects)
   assert state['report']['proxies_after_close']==0
   (OUT/(MODE+'.json')).write_text(json.dumps(state['report'],indent=2));print('BENCH_PASS',MODE,flush=True)
 except Exception:
  (OUT/(MODE+'-failure-details.json')).write_text(json.dumps({'status':'FAIL','samples':samples,'draws':draws,'window_lifecycle':lifecycle,'play_window_pointer':state.get('play_window_pointer'),'phase':state.get('phase'),'error':traceback.format_exc()},indent=2))
  (OUT/(MODE+'-error.txt')).write_text(traceback.format_exc());print(traceback.format_exc(),flush=True)
 bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick,first_interval=1.)
