"""Open the complete installed-style MAPPO demonstration without recording."""
from pathlib import Path
import os
import sys
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender
from wfrl_blender import farm_flex
wfrl_blender.register()
folder = Path(os.environ.get('WFRL_FARM_FLEX_PACKAGE', farm_flex.default_package()))
wfrl_blender.load_demo_scene(folder)
preview = farm_flex._ACTIVE
scene = bpy.context.scene
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.show_region_ui = True
            area.spaces.active.overlay.show_overlays = False
            area.spaces.active.shading.type = 'MATERIAL'
print('FARM_FLEX_READY', flush=True)
