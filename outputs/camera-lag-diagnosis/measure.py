"""Isolated profiling experiment. Never modifies the installed add-on or saves a blend."""
from pathlib import Path
import os,sys,time,json,resource,statistics,traceback,functools
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import native_camera_views as native,custom_cameras as core,farm_flex
MODE=os.environ.get('LAG_MODE','quad')
OUT=ROOT/'outputs/camera-lag-diagnosis'
state={}; timings={}; samples={}; stage=0

def wrap(owner,name,label):
 original=getattr(owner,name)
 @functools.wraps(original)
 def wrapped(*args,**kwargs):
  t=time.perf_counter()
  try:return original(*args,**kwargs)
  finally:
   if state.get('measuring'):timings.setdefault(label,[]).append(time.perf_counter()-t)
 setattr(owner,name,wrapped)

def count():
 if not state.get('measuring'):return
 key='other'
 if native._ACTIVE and bpy.context.window==native._ACTIVE.window:
  e=next((e for e in native._ACTIVE.entries if e['area']==bpy.context.area),None)
  key=f"C{e['slot']}" if e else 'other'
 else:key='original'
 samples.setdefault(key,[]).append((bpy.context.scene.frame_current,time.perf_counter()))

def tick():
 global stage
 try:
  if stage==0:
   addon.register();addon.load_demo_scene();s=bpy.context.scene
   core.import_layout(s,json.loads((ROOT/'outputs/camera-layout-test-v1/T1-four-cameras-v1.json').read_text()),overwrite=True)
   if MODE.startswith('quad'):
    from collections import Counter
    objects=[o for o in s.objects if o.type=='MESH' and not o.hide_get()]
    inventory={'meshes':len(objects),'prefix_counts':dict(Counter('.'.join(o.name.split('.')[:2]) for o in objects)),
     'largest_meshes':sorted([(o.name,len(o.data.vertices)) for o in objects],key=lambda x:-x[1])[:15],
     'lights':[(o.name,o.data.type,o.data.energy) for o in s.objects if o.type=='LIGHT'],
     'images':[(i.name,list(i.size)) for i in bpy.data.images]}
    (OUT/'scene-inventory.json').write_text(json.dumps(inventory,indent=2))
   state['original_window']=bpy.context.window
   area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
   wrap(farm_flex.FarmFlex,'update','farm_update')
   wrap(farm_flex.FarmFlex,'deformed_at','deformed_at')
   wrap(native.NativeSession,'sync','native_sync')
   from wfrl_blender import deflection
   wrap(deflection.ComparisonView,'update','comparison_update')
   if MODE.startswith('down'):
    with bpy.context.temp_override(area=area):bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='DOWN')
   else:
    with bpy.context.temp_override(area=area):bpy.ops.wfrl.native_camera_view(mode='WATCH' if MODE.startswith('single') else 'QUAD')
   stage=1;return 2.
  if stage==1:
   if not MODE.startswith('down') and (not native._ACTIVE or len(native._ACTIVE.entries)<(1 if MODE.startswith('single') else 4)):return .5
   if 'nosync' in MODE:native._ACTIVE.sync=lambda:None
   if 'original_off' in MODE:
    for a in state['original_window'].screen.areas:
     if a.type=='VIEW_3D':a.type='INFO'
   if 'frozen' in MODE:
    bpy.app.handlers.frame_change_pre.clear();bpy.app.handlers.frame_change_post.clear()
   if 'samples1' in MODE:bpy.context.scene.eevee.taa_samples=1
   if 'no_shadows' in MODE:bpy.context.scene.eevee.use_shadows=False
   if 'shadow_quarter' in MODE:bpy.context.scene.eevee.shadow_resolution_scale=.25
   if 'shadow_half' in MODE:bpy.context.scene.eevee.shadow_resolution_scale=.5
   if 'no_area_shadow' in MODE:
    for obj in bpy.context.scene.objects:
     if obj.type=='LIGHT' and obj.data.type=='AREA':obj.data.use_shadow=False
   if 'no_sun_shadow' in MODE:
    for obj in bpy.context.scene.objects:
     if obj.type=='LIGHT' and obj.data.type=='SUN':obj.data.use_shadow=False
   if 'no_gi' in MODE:bpy.context.scene.eevee.use_fast_gi=False
   if 'no_sky' in MODE:
    for obj in bpy.context.scene.objects:
     if obj.name.startswith(('WFRL.Sky.','WFRL.Cloud.')):obj.hide_set(True)
   if MODE=='quad_studio':
    for e in native._ACTIVE.entries:
     e['area'].spaces.active.shading.use_scene_lights=False
     e['area'].spaces.active.shading.use_scene_world=False
   window=native._ACTIVE.window if native._ACTIVE else state['original_window']
   state['play_window']=window
   bpy.context.scene.frame_set(301)
   with bpy.context.temp_override(window=window):bpy.ops.screen.animation_play()
   stage=2;return 3.
  if stage==2:
   state['draw_handle']=bpy.types.SpaceView3D.draw_handler_add(count,(), 'WINDOW','POST_PIXEL')
   state.update(measuring=True,start=time.perf_counter(),frame0=bpy.context.scene.frame_current)
   stage=3;return 8.
  if stage==3:
   elapsed=time.perf_counter()-state['start'];s=bpy.context.scene
   state['measuring']=False
   with bpy.context.temp_override(window=state['play_window']):bpy.ops.screen.animation_cancel(restore_frame=False)
   bpy.types.SpaceView3D.draw_handler_remove(state['draw_handle'],'WINDOW')
   report={'mode':MODE,'elapsed_s':elapsed,'frame_advance':s.frame_current-state['frame0'],
    'draws':{k:{'unique_frames':len(set(x[0] for x in v)),'draw_calls':len(v),'unique_fps':len(set(x[0] for x in v))/elapsed} for k,v in samples.items()},
    'timings':{k:{'calls':len(v),'total_s':sum(v),'median_ms':statistics.median(v)*1000,'max_ms':max(v)*1000} for k,v in timings.items()},
    'peak_rss_gib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**3,
    'native_objects':len([o for o in s.objects if o.name.startswith('T1.Native.')]),
    'visible_meshes':sum(o.type=='MESH' and not o.hide_get() for o in s.objects),
    'vertices':sum(len(o.data.vertices) for o in s.objects if o.type=='MESH' and not o.hide_get())}
   (OUT/(MODE+'.json')).write_text(json.dumps(report,indent=2));print('LAG_DONE',json.dumps(report),flush=True)
   native.shutdown()
 except Exception:
  (OUT/(MODE+'-error.txt')).write_text(traceback.format_exc());print(traceback.format_exc(),flush=True)
 bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick,first_interval=1)
