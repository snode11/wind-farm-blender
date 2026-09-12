"""Run with evidence/cinematic-fix/preview.blend to check actual nacelle geometry."""
from pathlib import Path
import sys
import bpy
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender import cinematic
wfrl_blender.register()
s=bpy.context.scene
s['wfrl_scene_kind']='demo'
s.wfrl_show_wake=True
s.wfrl_wake_display='CINEMATIC'
from mathutils import Vector
from mathutils.bvhtree import BVHTree
root=s.objects['WFRL.Turbine.T1.YawRoot']
# Test the visible polyline segments against the actual nacelle mesh.
results=[]
for mode in ('FRONT','RANDOM'):
 s.wfrl_cinematic_wind_mode=mode
 hits=0;cases=[]
 for t in range(0,33,2):
  cinematic.update(s,t*.9);bpy.context.view_layer.update()
  obj=s.objects['WFRL.Cinematic.T1'];body=s.objects['WFRL.Turbine.T1.Nacelle']
  tree=BVHTree.FromObject(body,bpy.context.evaluated_depsgraph_get());inv=body.matrix_world.inverted()
  for i,spline in enumerate(obj.data.splines):
   for a,b in zip(spline.points,spline.points[1:]):
    if min(a.radius,b.radius)<.05:continue
    start=inv @ obj.matrix_world @ a.co.xyz;end=inv @ obj.matrix_world @ b.co.xyz
    d=end-start
    if d.length and tree.ray_cast(start,d.normalized(),d.length)[0] is not None:
     hits+=1;cases.append((t,i));break
 results.append({'mode':mode,'nacelle_intersecting_strands':hits,'examples':cases[:8]})

print(results)
assert all(r['nacelle_intersecting_strands']==0 for r in results), results
# Follow visible moving vertices near the obstacle across adjacent frames.
s.wfrl_cinematic_wind_mode='RANDOM'
worst=0
for t in (7.9,8,15.9,16,27.9,28):
 samples=[]
 for at in (t,t+1/60):
  cinematic.update(s,at*.9)
  obj=s.objects['WFRL.Cinematic.T1']
  samples.append([[(p.co.xyz.copy(),p.radius) for p in spline.points] for i,spline in enumerate(obj.data.splines) if i%4])
 for before,after in zip(*samples):
  for (a,ra),(b,rb) in zip(before,after):
   if min(ra,rb)>.15:
    worst=max(worst,(a-b).length*60)
print('NACELLE_TRACER_SPEED',worst)
assert worst<32, worst

print('NACELLE_REGRESSION=PASS')
