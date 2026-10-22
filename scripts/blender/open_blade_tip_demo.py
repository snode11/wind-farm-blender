"""Open the video-reference inner blade-tip view in the existing Camera workflow.

Source checkout only; reuses the delivered offline simulation clip. The camera
position and night-visible paint are illustrative, not a measured installation.
"""
from pathlib import Path
import runpy
import bpy

ROOT = Path(__file__).resolve().parents[2]
presentation = runpy.run_path(str(ROOT / 'scripts/blender/open_clearance_demo.py'))
from wfrl_blender.cameras import ensure_gimbal, down_gimbal
from wfrl_blender import clearance_replay

scene = bpy.context.scene
camera = ensure_gimbal(scene, 'T1')
down_gimbal(camera)
scene.camera = camera
scene['wfrl_camera'] = camera.name
scene.wfrl_clearance_show_camera = True
# The real-video reference has no synthetic beam overlay. Hide only the guide
# lines in this isolated presentation; calibration and measurements remain live.
for obj in scene.objects:
    if '.ClearanceRadar.Beam' in obj.name:
        obj.hide_set(True)
        obj.hide_render = True
scene['wfrl_tip_reference_note'] = 'Inner-tip reference framing; guide lines hidden; original simulation data'
# Start on an actual recorded near-bottom blade pass, then the normal Camera
# playback button continues through the original clip at its existing timebase.
reader = clearance_replay.reader_for(scene)
# Find a downward blade (azimuth 60 modulo 120), without editing its pose.
frame = min(range(scene.frame_start, scene.frame_end + 1), key=lambda f:
            abs((reader.at(reader.start_s + (f-1)/scene['wfrl_clearance_timebase_fps'])['motion']['azimuth_deg'] % 120) - 60))
scene.frame_set(frame)
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            space.use_local_camera = True
            space.camera = camera
            space.region_3d.view_perspective = 'CAMERA'
            space.region_3d.view_camera_zoom = 0
            space.region_3d.view_camera_offset = (0, 0)
            space.overlay.show_overlays = False
            space.shading.type = 'MATERIAL'
            space.shading.use_scene_world = True
            space.shading.use_scene_lights = True

# Pin the dedicated launcher's existing Camera panel to the saved View tab.
# Resolve after Blender has finished restoring the loaded screen.
def prepare_tip_sidebar():
    from wfrl_blender.panels.gimbal import WFRL_PT_Gimbal
    if WFRL_PT_Gimbal.is_registered:
        bpy.utils.unregister_class(WFRL_PT_Gimbal)
    WFRL_PT_Gimbal.bl_category = 'View'
    bpy.utils.register_class(WFRL_PT_Gimbal)
    for name in ('VIEW3D_PT_view3d_properties', 'VIEW3D_PT_view3d_cursor',
                 'VIEW3D_PT_collections', 'VIEW3D_PT_quad_view', 'VIEW3D_PT_view3d_lock'):
        panel = getattr(bpy.types, name, None)
        if panel is not None and panel.is_registered:
            bpy.utils.unregister_class(panel)
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.spaces.active.show_region_ui = True
                area.tag_redraw()
    print('BLADE_TIP_SIDEBAR_READY', flush=True)

if not bpy.app.background:
    bpy.app.timers.register(prepare_tip_sidebar, first_interval=1.0)
