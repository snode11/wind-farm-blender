"""Open the workspace manual controls in a fresh visible Blender process."""
import hashlib
import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
ADDON_ROOT = ROOT / 'blender_frontend/wfrl_blender'
if bpy.app.background or bpy.context.window is None:
    raise RuntimeError('手动控制入口需要独立的可见 Blender 窗口')
if hasattr(bpy.types.Scene, 'wfrl_manual_turbines'):
    raise RuntimeError('请使用独立工厂启动进程加载工作区源码')
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
import wfrl_blender as addon

assert Path(addon.__file__).resolve().parent == ADDON_ROOT.resolve()
addon.register()


def select_panel():
    window = bpy.context.window
    pending = False
    for area in window.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        region = next(region for region in area.regions if region.type == 'WINDOW')
        sidebar = next((region for region in area.regions if region.type == 'UI'), None)
        with bpy.context.temp_override(window=window, area=area, region=region):
            if sidebar is None or sidebar.width == 0:
                area.tag_redraw()
                pending = True
                continue
            sidebar.active_panel_category = '风机控制'
            area.tag_redraw()
    return .1 if pending else None


def open_controls():
    assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
    for area in bpy.context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        area.spaces.active.show_region_ui = True
        area.tag_redraw()
    bpy.app.timers.register(select_panel, first_interval=.25)
    print('WFRL_MANUAL_SOURCE ' + json.dumps(dict(
        addon_file=addon.__file__, blender=bpy.app.version_string,
        blade_model=bpy.context.scene['wfrl_manual_blade_model'],
        geometry_sha256=hashlib.sha256((ADDON_ROOT / 'assets/rootkink_geometry.json').read_bytes()).hexdigest())),
        flush=True)
    return None


bpy.app.timers.register(open_controls, first_interval=.5)
