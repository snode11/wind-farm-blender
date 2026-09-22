from pathlib import Path
import sys,time,json
root=Path('/Users/eason/Desktop/wfcrl/wind farm RL');sys.path.insert(0,str(root/'blender_frontend'))
import bpy,wfrl_blender
from wfrl_blender import landscape,cinematic,backend_inflow
wfrl_blender.register()
# Same scene before/after; disable only the optimization for baseline build.
opt=landscape.optimize_shrubs
landscape.optimize_shrubs=lambda c:None
wfrl_blender.load_demo_scene();landscape.optimize_shrubs=opt
s=bpy.context.scene;s.wfrl_wake_display='CINEMATIC';s.frame_set(351)
collection=bpy.data.collections['WFRL_Scene']
turbines={o.name:len(o.data.vertices) for o in s.objects if o.type=='MESH' and o.name.startswith('WFRL.Turbine.')}
s.render.resolution_x=1100;s.render.resolution_y=700;s.render.resolution_percentage=100
s.render.engine='BLENDER_EEVEE';s.eevee.taa_render_samples=16
report={}
def render(name):
 s.render.filepath=str(root/'evidence/landscape-flow'/f'{name}.png')
 t=time.perf_counter();bpy.ops.render.render(write_still=True);return time.perf_counter()-t
originals={o.name:o.data for o in collection.objects if o.name.startswith('WFRL.Landscape.Shrub')}
report['render_before_s']=render('before')
report['shrubs']=opt(collection)
assert report['shrubs']['after']<report['shrubs']['before']*.5
assert turbines=={o.name:len(o.data.vertices) for o in s.objects if o.type=='MESH' and o.name.startswith('WFRL.Turbine.')}
report['render_after_s']=render('after')
reduced={o.name:o.data for o in collection.objects if o.name in originals}
report['warm_before_s']=[];report['warm_after_s']=[]
for _ in range(3):
 for label,mapping in [('before',originals),('after',reduced)]:
  for name,data in mapping.items():s.objects[name].data=data
  report['warm_'+label+'_s'].append(render(label))
report['repeat']=opt(collection)
assert report['repeat']['before']==report['repeat']['after']
# Snapshot clock overrides local random controls; reset and calm handling.
s['wfrl_scene_kind']='live';s.wfrl_cinematic_wind_mode='RANDOM'
def feed(t,v,d):
 backend_inflow.record(s,{'timestamp':{'value':t},'scene':{'inflow':{'speed':v,'direction':d}}})
 cinematic.update(s,999)
feed(10,8,270)
o=s.objects['WFRL.Cinematic.T1'];coords=[tuple(p.co) for p in o.data.splines[0].points]
cinematic.update(s,1234)
assert coords==[tuple(p.co) for p in o.data.splines[0].points]
feed(11,8,260)
assert coords!=[tuple(p.co) for p in o.data.splines[0].points]
feed(12,0,260);assert o.hide_render
feed(0,8,270);assert not o.hide_render and len(backend_inflow.history(s))==1
report['backend']='PASS: heading, fixed snapshot time, step, calm, reset'
(root/'evidence/landscape-flow/report.json').write_text(json.dumps(report,indent=2))
print(report)
