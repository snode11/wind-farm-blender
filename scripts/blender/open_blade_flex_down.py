"""Open the verified blade-flex clip through the existing gimbal Down view."""
from pathlib import Path
import runpy
import os
import bpy

ROOT = Path(__file__).resolve().parents[2]
runpy.run_path(str(ROOT / 'scripts/blender/open_blade_tip_demo.py'))
from wfrl_blender import clearance_replay, blade_flex_preview, tip_tracking
from wfrl_blender.cameras import ensure_gimbal, down_gimbal, fill_camera_view

scene = bpy.context.scene
scene.render.fps = 60
scene.render.fps_base = 1
scene['wfrl_clearance_timebase_fps'] = 60.0
clearance_replay.load(scene, scene.wfrl_clearance_near_tower_path, 'near_tower')
preview = blade_flex_preview.attach(scene, ROOT / 'evidence/blade-flex-short/close-flex.npz')
scene.wfrl_flex_show_tip_trails = True
scene['wfrl_flex_tip_trails'] = False
tip_tracking.enable(scene, turbine_id='T1')
scene.camera = ensure_gimbal(scene, 'T1')
down_gimbal(scene.camera)
scene['wfrl_camera'] = scene.camera.name
scene['wfrl_flex_view'] = 'Down gimbal'
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.spaces.active.camera = scene.camera
            area.spaces.active.region_3d.view_perspective = 'CAMERA'
            fill_camera_view(area, scene)
scene.frame_set(1)
print('BLADE_FLEX_DOWN_READY', flush=True)

def play_down_preview():
    for window in bpy.context.window_manager.windows:
        if window.scene == scene and not window.screen.is_animation_playing:
            with bpy.context.temp_override(window=window, screen=window.screen):
                bpy.ops.screen.animation_play()
            break

if not bpy.app.background and os.environ.get('WFRL_AUTOPLAY') == '1':
    bpy.app.timers.register(play_down_preview, first_interval=2.0)
