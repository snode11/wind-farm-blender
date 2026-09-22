from pathlib import Path
import importlib
import json
import bpy
ROOT = Path(__file__).resolve().parents[2]
name = 'bl_ext.user_default.wfrl_blender'
addon = importlib.import_module(name)
importlib.reload(importlib.import_module(name + '.deflection'))
importlib.reload(importlib.import_module(name + '.panels.farm_replay'))
importlib.reload(addon)
frame = bpy.context.scene.frame_current
addon.register()
if bpy.context.area and bpy.context.area.type == 'CONSOLE':
    bpy.context.area.type = 'VIEW_3D'
addon.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_deflection_blade = '1'
scene.wfrl_flex_show_tip_trails = False
scene.wfrl_farm_panel_page = 'DEFLECTION'
bpy.ops.wfrl.deflection_view()
scene.frame_set(frame)
scene.wfrl_capture_directory = str(ROOT / 'evidence/panel-layout-20260916')
Path(__file__).with_name('window-runtime.json').write_text(json.dumps(dict(module=addon.__file__, version='0.3.2', page=scene.wfrl_farm_panel_page, time_s=addon.farm_flex._ACTIVE.comparison.rows[1]['time']), indent=2))
def capture():
    bpy.ops.wfrl.capture_screenshot()
    return None
bpy.app.timers.register(capture, first_interval=2)
print('PANEL_LAYOUT_INSTALLED_READY', flush=True)
