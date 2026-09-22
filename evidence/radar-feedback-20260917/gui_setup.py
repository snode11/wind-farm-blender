import bpy, importlib, sys, json
from pathlib import Path
name='bl_ext.user_default.wfrl_blender'
old=sys.modules.get(name)
if old is not None:
    old.unregister()
    for key in list(sys.modules):
        if key==name or key.startswith(name+'.'):
            del sys.modules[key]
addon=importlib.import_module(name)
addon.register()
bpy.context.area.type='VIEW_3D'
addon.load_demo_scene()
scene=bpy.context.scene
scene.wfrl_farm_panel_page='RADAR'
bpy.ops.wfrl.farm_flex_view(turbine='T1')
scene.frame_set(2992)
for area in bpy.context.screen.areas:
    if area.type=='VIEW_3D':
        area.spaces.active.show_region_ui=True
        area.spaces.active.shading.type='MATERIAL'
        area.spaces.active.overlay.show_overlays=False
        for region in area.regions:
            if region.type=='UI': region.active_panel_category='MAPPO'
        area.tag_redraw()
from bl_ext.user_default.wfrl_blender import radar_feedback,clearance_replay
assert all(radar_feedback.icon_id(s)>0 for s in radar_feedback.LABELS)
assert radar_feedback.alarm_state(clearance_replay.sample(scene))=='near_threshold'
Path('/Users/eason/Desktop/wfcrl/wind farm RL/evidence/radar-feedback-20260917/gui-load.json').write_text(json.dumps({'loaded':addon.__file__,'icons':True,'alarm':'near_threshold'},indent=2))
