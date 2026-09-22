import bpy,sys,importlib,json,time
from pathlib import Path
root=Path('/Users/eason/Desktop/wfcrl/wind farm RL')
sys.path.insert(0,str(root/'blender_frontend'))
m=next((m for n,m in sys.modules.items() if n.endswith('wfrl_blender') and hasattr(m,'load_demo_scene')),None)
if m is None:
 import wfrl_blender as m
 m.register()
land=importlib.import_module(m.__name__+'.landscape')
opt=land.optimize_shrubs
land.optimize_shrubs=lambda c:None
m.load_demo_scene();land.optimize_shrubs=opt
s=bpy.context.scene;s.wfrl_wake_display='CINEMATIC';s.frame_set(351)
# Save a separate review copy after the interactive benchmark.
result={'before':[], 'after':[]};stage=['before'];last=[None]
def measure(scene,*args):
 now=time.perf_counter()
 if last[0] is not None:result[stage[0]].append(now-last[0])
 last[0]=now
 if len(result[stage[0]])>=75:
  bpy.app.handlers.frame_change_post.remove(measure)
  bpy.ops.screen.animation_cancel(restore_frame=False)
  if stage[0]=='before':
   result['shrubs']=opt(bpy.data.collections['WFRL_Scene'])
   stage[0]='after';last[0]=None
   bpy.app.timers.register(start,first_interval=.5)
  else:
   (root/'evidence/landscape-flow/playback.json').write_text(json.dumps(result))
   bpy.ops.wm.save_as_mainfile(filepath=str(root/'evidence/landscape-flow/review.blend'))
   print('PLAYBACK_BENCHMARK_COMPLETE')
def start():
 s.frame_set(351);last[0]=None
 bpy.app.handlers.frame_change_post.append(measure)
 bpy.ops.screen.animation_play()
 return None
# Once console switches back to 3D, benchmark the same frames twice.
bpy.app.timers.register(start,first_interval=12)
print('Demo ready; paired playback benchmark starts in 12 s')
