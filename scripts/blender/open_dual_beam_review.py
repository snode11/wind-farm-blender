"""Open the installed, bundled dual-beam research playback."""
from pathlib import Path
import math
import os
import sys
import importlib

import bpy

ROOT = Path(__file__).resolve().parents[2]
module = os.environ.get('WFRL_ADDON_MODULE', 'bl_ext.user_default.wfrl_blender')
if module == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
wfrl_blender = importlib.import_module(module)
farm_flex = importlib.import_module(module+'.farm_flex')

package = Path(os.environ.get('WFRL_DUAL_PACKAGE', farm_flex.default_dual_package()))
if not (package / 'manifest.json').exists():
    raise RuntimeError('安装版缺少双束数据，请安装包含双束数据的扩展或显式指定结果目录。')
if module not in bpy.context.preferences.addons:
    wfrl_blender.register()
wfrl_blender.load_demo_scene(package)
scene = bpy.context.scene
scene.wfrl_farm_panel_page = 'RADAR'
reader = farm_flex._ACTIVE.readers['T1']
first = next((r['time_s'] for r in reader.rows if r['reconstruction']['valid']), reader.start_s)
# Seek just inside a saved interval to avoid source-boundary float rounding.
frame = scene.frame_start + (min(first + .01, reader.end_s) - reader.start_s) * scene['wfrl_clearance_timebase_fps']
whole = math.floor(frame)
scene.frame_set(whole, subframe=frame - whole)
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.spaces.active.show_region_ui = True
# Blender's active_panel_category is read-only. Select MAPPO in the sidebar
# rather than silently attempting a setter or re-registering product panels.
print('Select MAPPO in the right sidebar to view the dual-beam cards.', flush=True)
print('DUAL_BEAM_SELECTED_REVIEW_READY', first, flush=True)
