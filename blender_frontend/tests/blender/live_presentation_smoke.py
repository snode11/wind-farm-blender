import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy,wfrl_blender
from wfrl_blender.live_scene import build_live_scene
from wfrl_blender.scene_model import SceneDTO
wfrl_blender.register()
dto=SceneDTO.from_mapping({'name':'check','backend':'fastfarm','turbine':'nrel5mw','dt':3,'layout':[{'id':f'T{i+1}','x':x,'y':0} for i,x in enumerate([0,504,1008])],'inflow':{'speed':8,'direction':270}})
build_live_scene(dto)
s=bpy.context.scene
assert s.world.node_tree.nodes.get('WFRL.Atmosphere.HDRI').image
proxies=[o for o in s.objects if o.name.startswith('WFRL.WakeProxy.') and not o.name.endswith('.Volume')]
assert proxies and all(not o.hide_render for o in proxies)
assert bpy.data.objects.get('WFRL.Landscape.ServiceRoad')
print('LIVE_DISPLAY_PASS',len(proxies))
from wfrl_blender.live_scene import animate_illustrative_wake
ring=bpy.data.objects['WFRL.WakeProxy.T1.Ring0']
before=tuple(ring.data.splines[0].points[0].co)
animate_illustrative_wake(s, .1, running=True)
after=tuple(ring.data.splines[0].points[0].co)
assert before != after
animate_illustrative_wake(s, .1, running=False)
assert tuple(ring.data.splines[0].points[0].co) == after
print('LIVE_WAKE_ANIMATION_PAUSE_PASS')
