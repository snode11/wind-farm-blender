from pathlib import Path
import sys,json,traceback,time,resource,os
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[os.environ.get('WFRL_ADDON_PATH',str(ROOT/'blender_frontend')),str(ROOT)]
import bpy
import wfrl_blender as addon
from wfrl_blender import native_camera_views as native,custom_cameras as core
OUT=Path(os.environ.get('WFRL_TEST_OUTPUT',str(ROOT/'outputs/native-camera-views')));OUT.mkdir(exist_ok=True)
state={}
def count_draw():
 if native._ACTIVE and bpy.context.window==native._ACTIVE.window and state.get('stage')==2:
  slot=next((e['slot'] for e in native._ACTIVE.entries if e['area']==bpy.context.area),None)
  if slot:state['draws'][slot].add(bpy.context.scene.frame_current)

def tick():
 try:
  if not state:
   addon.register();addon.load_demo_scene();scene=bpy.context.scene
   area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
   state.update(stage=1,original=bpy.context.workspace,camera=scene.camera,layout=core.layout_dict(scene),render=(scene.render.resolution_x,scene.render.resolution_y),start=time.monotonic())
   with bpy.context.temp_override(area=area):assert bpy.ops.wfrl.native_camera_view(mode='TRIPLE')=={'FINISHED'}
   return 2
  s=bpy.context.scene
  if state['stage']==1:
   print('STATE',native._ACTIVE, [(w.as_pointer(),w.scene.name,[(a.type,a.width,a.height) for a in w.screen.areas]) for w in bpy.context.window_manager.windows],flush=True)
   if native._ACTIVE and len(native._ACTIVE.entries)<3:return 1
   assert native._ACTIVE and len(native._ACTIVE.entries)==3
   assert all(e['area'].spaces.active.shading.type=='MATERIAL' for e in native._ACTIVE.entries)
   assert all(e['area'].spaces.active.shading.use_scene_world for e in native._ACTIVE.entries)
   print('AREAS',[(e['area'].type,e['area'].width,e['area'].height) for e in native._ACTIVE.entries],flush=True)
   bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
   with bpy.context.temp_override(window=native._ACTIVE.window):
    bpy.ops.screen.screenshot(filepath=str(OUT/'triple.png'))
    bpy.ops.screen.animation_play()
   state.update(stage=2,start=time.monotonic(),start_frame=s.frame_current,draws={slot:set() for slot in core.SLOTS})
   state['handler']=bpy.types.SpaceView3D.draw_handler_add(count_draw,(), 'WINDOW','POST_PIXEL')
   return float(os.environ.get("WFRL_NATIVE_SECONDS","10"))
  if state['stage']==2:
   assert native._ACTIVE.window.screen.is_animation_playing
   state.update(elapsed=time.monotonic()-state['start'],end_frame=s.frame_current)
   with bpy.context.temp_override(window=native._ACTIVE.window):bpy.ops.screen.animation_cancel(restore_frame=False)
   bpy.types.SpaceView3D.draw_handler_remove(state['handler'],'WINDOW')
   native.shutdown()
   assert bpy.context.workspace==state['original']
   assert core.layout_dict(s)==state['layout']
   assert s.camera==state['camera']
   assert (s.render.resolution_x,s.render.resolution_y)==state['render']
   assert not any(o.name.startswith('T1.Native.') for o in s.objects)
   state['stage']=25;return 1
  if state['stage']==25:
   area=next(a for a in bpy.context.screen.areas if a.type=='VIEW_3D')
   with bpy.context.temp_override(area=area):assert bpy.ops.wfrl.native_camera_view(mode='WATCH')=={'FINISHED'}
   state['stage']=3;return 2
  if state['stage']==3:
   assert len(native._ACTIVE.entries)==1
   bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP',iterations=1)
   with bpy.context.temp_override(window=native._ACTIVE.window):bpy.ops.screen.screenshot(filepath=str(OUT/'single.png'))
   with bpy.context.temp_override(window=native._ACTIVE.window):bpy.ops.wm.window_close()
   state['stage']=4;return 1
  if state['stage']==4:
   assert native._ACTIVE is None
   assert not any(o.name.startswith('T1.Native.') for o in s.objects)
   (OUT/'check.json').write_text(json.dumps({'status':'PASS','elapsed':state['elapsed'],'frames_advanced':state['end_frame']-state['start_frame'],'unique_frames_drawn':{str(k):len(v) for k,v in state['draws'].items()},'peak_rss_gib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**3},indent=2))
   print('NATIVE_PASS',flush=True)
 except Exception:
  (OUT/'error.txt').write_text(traceback.format_exc());print(traceback.format_exc(),flush=True)
 bpy.ops.wm.quit_blender()
bpy.app.timers.register(tick,first_interval=1)
