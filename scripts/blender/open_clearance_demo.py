"""Open the isolated clearance presentation with the current source add-on.

/Applications/Blender.app/Contents/MacOS/Blender --factory-startup --python scripts/blender/open_clearance_demo.py
Does not install or replace an existing extension or start a physical backend.
"""
from pathlib import Path
import json
import sys
import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender

from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal
# A fresh native workspace opens its Tool sidebar by default. Scope this
# presentation-only placement to this process; installed extension stays unchanged.
WFRL_PT_Gimbal.bl_category = 'Tool'
wfrl_blender.register()
# Keep the dedicated presentation sidebar focused; this does not save preferences.
for name in ('VIEW3D_PT_tools_object_options_transform', 'VIEW3D_PT_tools_object_options',
             'VIEW3D_PT_active_tool_duplicate', 'VIEW3D_PT_active_tool',
             'WORKSPACE_PT_addons', 'WORKSPACE_PT_custom_props', 'WORKSPACE_PT_main'):
    panel = getattr(bpy.types, name, None)
    if panel is not None and panel.is_registered:
        bpy.utils.unregister_class(panel)
path = ROOT / 'evidence/lidar-frontend/clearance-demo.blend'
if path.is_file():
    bpy.ops.wm.open_mainfile(filepath=str(path))
else:
    # Source previews must also work after historical evidence is cleaned up.
    # Rebuild the same project scene; physical packages are still validated below.
    wfrl_blender.load_demo_scene()
    bpy.context.scene.wfrl_show_wake = False
# The source load handler strictly verifies and reloads the saved result package.
# A bad or missing package stays visibly unavailable instead of using a fixture.
wfrl_blender._cancel_playback()

# Saved presentation files remain historical evidence. Select the reviewed
# revision explicitly without overwriting their embedded legacy paths.
from wfrl_blender import clearance_replay
delivery_path = ROOT / 'dist/lidar-delivery.json'
try:
    delivery = json.loads(delivery_path.read_text())
    scene = bpy.context.scene
    scene.wfrl_clearance_normal_path = str(ROOT / delivery['packages']['normal'])
    scene.wfrl_clearance_near_tower_path = str(ROOT / delivery['packages']['close'])
    clearance_replay.load(scene, scene.wfrl_clearance_normal_path, 'normal')
    bpy.ops.wfrl.clearance_view(view='MEASUREMENT')
except (OSError, ValueError, KeyError, TypeError) as exc:
    clearance_replay.clear(bpy.context.scene, '修订交付未就绪：' + str(exc))
    raise

# Read the actually selected sidebar category after loading the saved workspace.
# The category is read-only in Blender; place our process-local panel there.
def prepare_sidebar():
    screen = bpy.context.screen or bpy.context.window_manager.windows[0].screen
    main = next((a for a in screen.areas if a.type == 'VIEW_3D'), None)
    if main is not None:
        region = next((r for r in main.regions if r.type == 'UI'), None)
        category = region.active_panel_category if region else 'Tool'
        bpy.utils.unregister_class(WFRL_PT_Gimbal)
        WFRL_PT_Gimbal.bl_category = category or 'Tool'
        bpy.utils.register_class(WFRL_PT_Gimbal)
        # Remove stock editing panels from this dedicated presentation tab only.
        for panel in reversed(bpy.types.Panel.__subclasses__()):
            if (panel.__name__.startswith(('VIEW3D_PT_', 'WORKSPACE_PT_'))
                    and getattr(panel, 'bl_space_type', '') == 'VIEW_3D'
                    and getattr(panel, 'bl_region_type', '') == 'UI'
                    and getattr(panel, 'bl_category', '') == WFRL_PT_Gimbal.bl_category
                    and panel.is_registered):
                bpy.utils.unregister_class(panel)
        main.spaces.active.show_region_ui = True

bpy.app.timers.register(prepare_sidebar, first_interval=.1)
