"""A slow UI tick must not silently stretch an eight-second preview cycle."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import bpy
import wfrl_blender
from wfrl_blender.live_scene import animate_illustrative_wake
wfrl_blender.register()
s=bpy.context.scene;s['wfrl_scene_kind']='live';s['wfrl_proxy_phase']=0.0
animate_illustrative_wake(s,.8,running=True)
assert abs(s['wfrl_proxy_phase']-.72)<1e-8, 'Slow frames discarded elapsed preview time'
animate_illustrative_wake(s,5,running=False)
assert abs(s['wfrl_proxy_phase']-.72)<1e-8, 'Paused preview advanced'
print('CINEMATIC_CLOCK_REGRESSION=PASS')
