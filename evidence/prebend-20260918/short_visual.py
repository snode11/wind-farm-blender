import bpy,importlib
from wfrl_blender import deflection,farm_flex
from wfrl_blender.panels import farm_replay
importlib.reload(farm_replay)
s=bpy.context.scene
s.wfrl_farm_panel_page='DEFLECTION'
s.wfrl_deflection_visible=True
bpy.ops.wfrl.deflection_view()
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':
        area.spaces.active.overlay.show_overlays=False
        area.spaces.active.show_region_ui=True
        for region in area.regions:
            if region.type=='UI':
                try:region.active_panel_category='MAPPO'
                except Exception:pass
