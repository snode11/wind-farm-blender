import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy
import wfrl_blender
from wfrl_blender.live_scene import build_live_scene
from wfrl_blender.scene_model import SceneDTO
wfrl_blender.register()
wfrl_blender.load_demo_scene()
s=bpy.context.scene
s.wfrl_wake_display='CINEMATIC'
def counts():
    return {'mode':s.wfrl_wake_display,'cinematic_visible':sum(not o.hide_render for o in s.objects if o.name.startswith('WFRL.Cinematic.')),'green_visible':sum(not o.hide_render for o in s.objects if o.name.startswith('WFRL.WakeProxy.') and not o.name.endswith('.Volume'))}
print('BEFORE_LIVE_REBUILD',json.dumps(counts()))
dto=SceneDTO.from_mapping({'name':'audit','backend':'fake','layout':[{'id':'T1','x':0,'y':0}], 'inflow':{'speed':8,'direction':270}})
build_live_scene(dto)
print('AFTER_LIVE_REBUILD',json.dumps(counts()))

assert counts()['cinematic_visible'] == 1, 'Paused rebuild lost cinematic layer'
assert counts()['green_visible'] == 0, 'Paused rebuild exposed green layer'
print('CINEMATIC_LIVE_REGRESSION=PASS')
