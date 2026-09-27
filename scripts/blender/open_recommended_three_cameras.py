"""Open the installed extension with the reviewed fixed three-camera layout.

Run in a fresh Blender process with --factory-startup. Reuses recorded MAPPO
data, starts paused, and does not overwrite an existing saved scene.
"""
from pathlib import Path
import importlib
import json
import os
import bpy

MODULE = 'bl_ext.user_default.wfrl_blender'
if bpy.data.filepath:
    raise RuntimeError('请用随附的 command 在新窗口中打开推荐布局')
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE + '.custom_cameras')
addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
scene.frame_set(1)
scene.wfrl_flex_show_tip_trails = False
scene.wfrl_deflection_visible = False
importlib.import_module(MODULE + '.tip_tracking').disable(scene, 'recommended_three_camera_view')
layout_path = Path(addon.__file__).parent / 'assets/cameras/t1-three-camera-default.json'
scene.camera = core.get_camera(scene, 2)
scene.render.resolution_x, scene.render.resolution_y = 1920, 1080
scene.render.resolution_percentage = 100
bpy.context.window_manager.wfrl_custom_slot = 2

def open_views():
    area = next(a for a in bpy.context.screen.areas if a.type == 'VIEW_3D')
    area.spaces.active.region_3d.view_perspective = 'CAMERA'
    area.spaces.active.show_region_ui = True
    for region in area.regions:
        if region.type == 'UI':
            try:
                region.active_panel_category = 'View'
            except AttributeError:
                pass
    with bpy.context.temp_override(area=area):
        bpy.ops.wfrl.native_camera_view(mode='TRIPLE')
    bpy.app.timers.register(verify_views, first_interval=.5)
    return None

_waits = 0
def verify_views():
    global _waits
    native = importlib.import_module(MODULE + '.native_camera_views')
    if native._ACTIVE is None or len(native._ACTIVE.entries) != 3:
        _waits += 1
        if _waits > 20:
            raise RuntimeError('三路窗口未成功打开，请在 View 面板重新点击三路对照')
        return .5
    evidence = os.environ.get('WFRL_LAUNCH_EVIDENCE_DIR')
    if evidence:
        folder = Path(evidence)
        folder.mkdir(parents=True, exist_ok=True)
        with bpy.context.temp_override(window=native._ACTIVE.window):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            bpy.ops.screen.screenshot(filepath=str(folder / 'launcher-three-views.png'))
        (folder / 'launcher-validation.json').write_text(json.dumps({
            'status': 'PASS', 'module': MODULE, 'module_file': addon.__file__,
            'views': 3, 'frame': scene.frame_current,
            'tip_trails': scene.wfrl_flex_show_tip_trails,
            'deflection_helpers': scene.wfrl_deflection_visible,
            'layout': str(layout_path), 'paused': not bpy.context.screen.is_animation_playing,
        }, indent=2), encoding='utf-8')
    print('RECOMMENDED_THREE_CAMERAS_READY', str(layout_path), flush=True)
    return None

bpy.app.timers.register(open_views, first_interval=1.)
