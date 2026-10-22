"""Start the regional three-camera presentation from the current source tree.

Uses the bundled recorded MAPPO clip. No solve, save, install or publication.
"""
from pathlib import Path
import os
import sys
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [os.environ.get('WFRL_ADDON_ROOT', str(ROOT/'blender_frontend')), str(ROOT)]
import wfrl_blender as addon
from wfrl_blender import native_camera_views as native
addon.register()
addon.load_demo_scene()
bpy.context.scene.frame_set(1)

def open_views():
    window = bpy.context.window
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.wfrl.native_camera_view(mode='TRIPLE')
    print('BOSS_DEMO_READY: paused at simulation 117 s;', addon.__file__, flush=True)
    return None

bpy.app.timers.register(open_views, first_interval=1.)
