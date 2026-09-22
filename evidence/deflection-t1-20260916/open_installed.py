import importlib
import json
from pathlib import Path
import bpy
name = 'bl_ext.user_default.wfrl_blender'
addon = importlib.import_module(name)
if name not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module=name)
addon.load_demo_scene()
bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
Path(__file__).with_name('window-runtime.json').write_text(json.dumps({'module': addon.__file__, 'version': '0.3.1', 'time_s': addon.farm_flex._ACTIVE.comparison.rows[1]['time']}, indent=2))
print('T1_DEFLECTION_INSTALLED_WINDOW_READY', flush=True)
