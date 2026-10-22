"""Render the actual down_gimbal (as coded) at the BDC frame, plus roll variants.

Confirms no blade clipping and shows the composition for user confirmation.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]

import wfrl_blender as addon
from wfrl_blender import cameras

addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
scene["wfrl_blade_model"] = "rootkink"
scene.wfrl_deflection_visible = False
scene.wfrl_flex_show_tip_trails = False
from wfrl_blender import farm_flex
farm_flex.attach(scene, farm_flex.saved_package(scene))
bpy.ops.wfrl.farm_flex_view(turbine="T1", angle="DOWN")

blade = scene.objects.get("WFRL.Turbine.T1.Blade1")
best_frame, best_z = scene.frame_start, 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 4):
    scene.frame_set(f); bpy.context.view_layer.update()
    tipz = min((blade.matrix_world @ v.co).z for v in blade.data.vertices)
    if tipz < best_z:
        best_z, best_frame = tipz, f
scene.frame_set(best_frame); bpy.context.view_layer.update()

cam = scene.camera
outdir = (ROOT / "outputs/final-down").resolve()
outdir.mkdir(parents=True, exist_ok=True)
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.image_settings.file_format = "PNG"

# User-spec down camera; also check blade clearance across all frames + fov variants
import numpy as np
cam.location = (-3.0, 0.0, -1.5)
cam_world = cam.matrix_world.translation
dmin = 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 8):
    scene.frame_set(f); bpy.context.view_layer.update()
    mw = blade.matrix_world
    for v in blade.data.vertices:
        dd = (mw @ v.co - cam_world).length
        if dd < dmin:
            dmin = dd
print(f"min blade distance all frames: {dmin:.2f}m ({'CLEAR' if dmin > 2 else 'CLIPS'})")
scene.frame_set(best_frame); bpy.context.view_layer.update()

for roll in [0, 180]:
    for fov in [60, 75, 95]:
        cam.location = (-3.0, 0.0, -1.5)
        cam["gimbal_roll"] = float(roll)
        cameras.aim_gimbal(cam, 180.0, -82.0, fov)
        bpy.context.view_layer.update()
        scene.render.filepath = str(outdir / f"spec_roll{roll}_fov{fov}.png")
        bpy.ops.render.render(write_still=True)
        print("WROTE", scene.render.filepath)
