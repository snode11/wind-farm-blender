from pathlib import Path
import sys
import bpy
ROOT = Path(__file__).resolve().parents[2]
frame = bpy.context.scene.frame_current
blade = getattr(bpy.context.scene, 'wfrl_deflection_blade', '1')
trails = getattr(bpy.context.scene, 'wfrl_flex_show_tip_trails', False)
sys.path[:0] = [str(ROOT/'blender_frontend'), str(ROOT)]
import wfrl_blender
wfrl_blender.register()
if bpy.context.area and bpy.context.area.type == 'CONSOLE':
    bpy.context.area.type = 'VIEW_3D'
wfrl_blender.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_deflection_blade = blade
scene.wfrl_flex_show_tip_trails = trails
bpy.ops.wfrl.deflection_view()
scene.frame_set(frame)
scene.wfrl_farm_panel_page = 'DEFLECTION'
scene.wfrl_capture_directory = str(ROOT/'evidence/panel-layout-20260916')
print('PANEL_LAYOUT_PREVIEW_READY', flush=True)
