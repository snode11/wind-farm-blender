import bpy,json,time,statistics
from pathlib import Path
out=Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/landscape-flow')
s=bpy.context.scene
main=max(bpy.context.screen.areas,key=lambda a:a.width*a.height)
for a in bpy.context.screen.areas:
 if a != main and a.type=='VIEW_3D':a.type='PROPERTIES'
main.type='VIEW_3D';space=main.spaces.active
space.region_3d.view_perspective='CAMERA';space.shading.type='RENDERED'
space.overlay.show_overlays=False;space.clip_end=30000;space.show_region_ui=True
from importlib import import_module
import_module(m.__name__+'.cameras').fill_camera_view(main,s)
shrubs=[o for o in s.objects if o.name.startswith('WFRL.Landscape.Shrub')]
reduced={o.name:o.data for o in shrubs}
originals={}
for o in shrubs:
 key=o.data.materials[0].name
 if key not in originals or len(o.data.vertices)>len(originals[key].vertices):originals[key]=o.data
results={'before':[],'after':[]};stage=['before'];last=[None]
def bench(scene,*args):
 now=time.perf_counter()
 if last[0] is not None:results[stage[0]].append(now-last[0])
 last[0]=now
 if len(results[stage[0]])>=55:
  bpy.app.handlers.frame_change_post.remove(bench)
  bpy.ops.screen.animation_cancel(restore_frame=False)
  if stage[0]=='before':
   for o in shrubs:o.data=reduced[o.name]
   stage[0]='after';bpy.app.timers.register(begin,first_interval=.5)
  else:
   (out/'viewport-playback.json').write_text(json.dumps(results))
   bpy.ops.wm.save_as_mainfile(filepath=str(out/'review.blend'))
def begin():
 s.frame_set(351);last[0]=None
 bpy.app.handlers.frame_change_post.append(bench)
 bpy.ops.screen.animation_play()
 return None
for o in shrubs:o.data=originals[o.data.materials[0].name]
bpy.app.timers.register(begin,first_interval=2)
