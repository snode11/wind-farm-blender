"""Open the realistic scene with current source and mounted-camera controls."""
import sys
from pathlib import Path
import bpy
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blender_frontend'))
import wfrl_blender
wfrl_blender.register()
bpy.ops.wm.open_mainfile(filepath=str(ROOT / 'evidence/part5_realistic.blend'))
from wfrl_blender.cameras import ensure_gimbal
for turbine in ('T1', 'T2', 'T3'):
    ensure_gimbal(bpy.context.scene, turbine)
def prepare_views():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.show_region_ui = True
                for region in area.regions:
                    if region.type == 'UI':
                        region.active_panel_category = 'Camera'
    print('WFRL_REALISTIC_GIMBAL_READY', flush=True)
bpy.app.timers.register(prepare_views, first_interval=.5)
