"""Live field-stream registration, panel drawing and background refusal.

Run with Blender --background --factory-startup --python. The split view
itself needs an interactive window and is checked by hand.
"""
import importlib
import os
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[3]
MODULE = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
if MODULE == 'wfrl_blender':
    sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
addon = importlib.import_module(MODULE)
view = importlib.import_module(MODULE + '.live_stream_view')
addon.register()
wm = bpy.context.window_manager
assert hasattr(bpy.ops.wfrl, 'live_stream_start') and hasattr(bpy.ops.wfrl, 'live_stream_stop')
wm.wfrl_live_url = 'rtmp://wvp.cluing.com:1935/rtp/demo_01'
assert wm.wfrl_live_size == '1280x720'
assert 'wfrl_live_url' not in bpy.context.scene.bl_rna.properties  # not saved with scenes
assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
# No 3D view exists in background mode, so starting is refused without side effects.
assert not bpy.ops.wfrl.live_stream_start.poll()
assert not bpy.ops.wfrl.live_stream_stop.poll()
assert view.session() is None and view.status() == []
assert bpy.types.IMAGE_HT_header._dyn_ui_initialize().count(view._draw_header) == 1
addon.unregister()
assert not hasattr(bpy.types.WindowManager, 'wfrl_live_url')
assert view._draw_header not in bpy.types.IMAGE_HT_header._dyn_ui_initialize()
assert view._on_load_pre not in bpy.app.handlers.load_pre
addon.register()
addon.unregister()
print('LIVE_STREAM_REGISTER_PASS', flush=True)
