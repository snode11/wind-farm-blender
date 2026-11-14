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
try:
    bpy.ops.wfrl.manual_down_view(turbine='T1')
except RuntimeError as exc:
    assert '三维视图' in str(exc), str(exc)
else:
    raise AssertionError('Manual Down must report a missing calling area')

# Background screens retain RNA view state even without a calling area or GPU drawing.
area = next(area for area in bpy.context.screen.areas if area.type == 'VIEW_3D')
window_region = next(region for region in area.regions if region.type == 'WINDOW')
with bpy.context.temp_override(area=area, region=window_region):
    assert bpy.ops.wfrl.manual_down_view(turbine='T1') == {'FINISHED'}
space = area.spaces.active
region = space.region_3d
for perspective in ('PERSP', 'ORTHO', 'CAMERA'):
    region.view_perspective = perspective
    saved = view._view_state(space)
    space.use_local_camera = not saved['local']
    space.camera = None
    region.view_location = (9, 8, 7)
    region.view_rotation = (0, 1, 0, 0)
    region.view_distance = 99
    region.view_camera_zoom = 55
    region.view_camera_offset = (.3, -.4)
    region.view_perspective = 'PERSP'
    space.show_region_ui = not saved['region_ui']
    view._copy_view(saved, space)
    restored = view._view_state(space)
    assert restored['camera'] == saved['camera'] and restored['local'] == saved['local']
    assert restored['perspective'] == saved['perspective']
    assert restored['region_ui'] == saved['region_ui']
    assert (restored['location'] - saved['location']).length < 1e-5
    assert all(abs(a - b) < 1e-5 for a, b in zip(restored['rotation'], saved['rotation']))
    assert all(abs(a - b) < 1e-5 for row_a, row_b in zip(restored['matrix'], saved['matrix'])
               for a, b in zip(row_a, row_b))
    assert restored['distance'] == saved['distance']
    assert restored['camera_zoom'] == saved['camera_zoom']
    assert restored['camera_offset'] == saved['camera_offset']
assert bpy.types.IMAGE_HT_header._dyn_ui_initialize().count(view._draw_header) == 1
assert view._on_load_post in bpy.app.handlers.load_post
assert view._on_load_post in bpy.app.handlers.load_post_fail
addon.unregister()
assert not hasattr(bpy.types.WindowManager, 'wfrl_live_url')
assert view._draw_header not in bpy.types.IMAGE_HT_header._dyn_ui_initialize()
assert view._on_load_pre not in bpy.app.handlers.load_pre
assert view._on_load_post not in bpy.app.handlers.load_post
assert view._on_load_post not in bpy.app.handlers.load_post_fail
addon.register()
addon.unregister()
print('LIVE_STREAM_REGISTER_PASS', flush=True)
