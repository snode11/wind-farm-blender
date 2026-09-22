"""Full 60 Hz replay camera audit and same-frame optical comparison artifacts."""
from pathlib import Path
import sys,os,json,math,collections
import bpy
import numpy as np
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'blender_frontend'),str(ROOT)]
import wfrl_blender
from wfrl_blender import cameras,farm_flex,tip_tracking,clearance_replay
wfrl_blender.register();wfrl_blender.load_demo_scene(farm_flex.default_package())
s=bpy.context.scene;a=farm_flex._ACTIVE
out=Path(os.environ['WFRL_TEST_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
s.render.resolution_x=1280;s.render.resolution_y=720;s.render.resolution_percentage=100
bpy.ops.wfrl.farm_flex_view(turbine='all')
rows={};cams={};objects={};render_frames={}
for tid in a.readers:
 old=cameras.ensure_gimbal(s,tid);cameras.down_gimbal(old)
 new=cameras.ensure_nacelle_gimbal(s,tid)
 cams[tid]=(old,new)
 objects[tid]=[o for o in s.objects if o.name.startswith(f'WFRL.Turbine.{tid}.') and o.type=='MESH' and not any(x in o.name for x in ('Reference','Deflection','ClearanceRadar','TipTrail'))]
 rows[tid]=dict(frames=0,old_region_samples=0,new_in_frame=0,apex_visible=0,terminal_surface_visible=0,
               occluders=collections.Counter(),min_camera_blade_m=1000.,min_camera_tower_m=1000.,
               min_pivot_blade_m=1000.,min_bracket_blade_m=1000.,tip_width_ratio=[],pixel_positions=[],
               mount_motion_error_m=0.)

def inside(p):return p.z>0 and 0<=p.x<=1 and 0<=p.y<=1

def blockers(obs,origin,target):
 result=[]
 for ob in obs:
  inv=ob.matrix_world.inverted();start=inv@origin;d=inv@target-start
  hit,p,_,_=ob.ray_cast(start,d.normalized(),distance=d.length)
  if hit and (ob.matrix_world@p-target).length>.03:result.append(ob.name)
 return result

for f in range(1,s.frame_end+1):
 s.frame_set(f);bpy.context.view_layer.update()
 for tid in a.readers:
  row=rows[tid];row['frames']+=1;old,new=cams[tid]
  yaw=new.parent;origin=new.matrix_world.translation
  row['mount_motion_error_m']=max(row['mount_motion_error_m'],(origin-yaw.matrix_world@Vector(cameras.NACELLE_LOCATION)).length)
  blades=[s.objects[f'WFRL.Turbine.{tid}.Blade{b}'] for b in (1,2,3)]
  tower=s.objects[f'WFRL.Turbine.{tid}.Tower']
  pivot=yaw.matrix_world@Vector(new['bracket_pivot_m'])
  anchor=yaw.matrix_world@Vector(new['bracket_anchor_m'])
  for ob in blades+[tower]:
   inv=ob.matrix_world.inverted()
   for point,key in [(origin,'min_camera_blade_m' if ob in blades else 'min_camera_tower_m')]+(
        [(pivot,'min_pivot_blade_m'),((pivot+anchor)*.5,'min_bracket_blade_m'),(anchor,'min_bracket_blade_m')] if ob in blades else []):
    hit,p,_,_=ob.closest_point_on_mesh(inv@point)
    if hit:row[key]=min(row[key],(ob.matrix_world@p-point).length)
  tips=[(ob,ob.matrix_world@ob.data.vertices[-1].co) for ob in blades]
  blade,tip=min(tips,key=lambda pair:pair[1].z)
  uv_old=world_to_camera_view(s,old,tip)
  if tip.z>40 or not inside(uv_old):continue
  row['old_region_samples']+=1
  uv=world_to_camera_view(s,new,tip)
  if inside(uv):row['new_in_frame']+=1
  hits=blockers(objects[tid],origin,tip)
  row['occluders'].update(hits)
  if inside(uv) and not hits:row['apex_visible']+=1
  # Eight points on the 1.3666 m terminal shoulder, plus the apex. This is
  # a discrete visibility diagnostic, never a camera-derived clearance.
  shoulder=[blade.matrix_world@blade.data.vertices[len(blade.data.vertices)-1154+j].co for j in range(0,48,6)]
  projected=[[world_to_camera_view(s,cam,p) for p in shoulder] for cam in (old,new)]
  widths=[max(p.x for p in points)-min(p.x for p in points) for points in projected]
  if widths[0]>1e-6:row['tip_width_ratio'].append(widths[1]/widths[0])
  visible=not hits and inside(uv)
  if not visible:
   for p,q in zip(shoulder,projected[1]):
    if inside(q) and not blockers(objects[tid],origin,p):visible=True;break
  if visible:row['terminal_surface_visible']+=1
  if f%30==1:row['pixel_positions'].append(dict(frame=f,old=list(uv_old)[:2],new=list(uv)[:2]))
  # One complete approach/closest/exit comparison per turbine.
  if f<160:
   previous=render_frames.get(tid,(1000,0))
   if tip.z<previous[0]:render_frames[tid]=(tip.z,f)
 if f%600==1:print('VISIBILITY_FRAME',f,flush=True)
for tid,row in rows.items():
 arr=np.array(row.pop('tip_width_ratio'))
 row['terminal_width_new_over_old_p05_median_p95']=np.quantile(arr,[.05,.5,.95]).tolist()
 assert row['frames']==s.frame_end and row['mount_motion_error_m']<.0001
 assert row['min_camera_blade_m']>.2 and row['min_camera_tower_m']>.2
 assert row['min_pivot_blade_m']>.2 and row['min_bracket_blade_m']>.2
# Save numerical results before optional GPU rendering.
result=dict(status='PASS_MOUNT_AUDIT',blender=bpy.app.version_string,segment=a.manifest['segment'],
            sample_rate_hz=60,scope='All displayed frames; point rays and bracket samples, not continuous collision certification',
            rows=rows,render_frames=render_frames,resolution=[1280,720],pixel_aspect=[1,1])
(out/'visibility.json').write_text(json.dumps(result,indent=2)+'\n')
s.render.engine='BLENDER_WORKBENCH';s.display.shading.light='STUDIO';s.display.shading.color_type='MATERIAL'
s.display.shading.show_shadows=True;s.display.shading.show_cavity=True
for tid,(_,middle) in render_frames.items():
 bpy.ops.wfrl.farm_flex_view(turbine=tid,angle='DOWN')
 for frame in range(max(1,middle-40),middle+13):
  s.frame_set(frame)
  if frame not in (max(1,middle-12),middle,middle+12):continue
  for label,cam in zip(('down','nacelle'),cams[tid]):
   s.camera=cam;s.render.filepath=str(out/f'{tid}-{frame:04d}-{label}.png');bpy.ops.render.render(write_still=True)
# A saved scene provides a repeatable source-code demonstration, paused on T1 Down.
bpy.ops.wfrl.farm_flex_view(turbine='T1',angle='DOWN');s.frame_set(render_frames['T1'][1])
s.wfrl_farm_panel_page='RADAR'
bpy.ops.wm.save_as_mainfile(filepath=str(out/'camera-review.blend'))
print('NACELLE_VISIBILITY_COMPLETE',flush=True)
