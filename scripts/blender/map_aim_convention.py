"""Empirically map aim_gimbal(yaw,pitch,fov) -> actual world view direction.

Resolves aim_gimbal's rotation convention using Blender itself, so a desired
look direction can be converted to the right yaw/pitch. Also inverts: given a
target aim direction, find yaw/pitch by search.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]

import wfrl_blender as addon
from wfrl_blender import cameras

addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
from wfrl_blender import farm_flex
farm_flex.attach(scene, farm_flex.saved_package(scene))
bpy.ops.wfrl.farm_flex_view(turbine="T1", angle="DOWN")
cam = scene.camera
cam["gimbal_roll"] = 0.0


def view_dir(yaw, pitch):
    cameras.aim_gimbal(cam, yaw, pitch, 70)
    bpy.context.view_layer.update()
    # camera looks along local -Z in world
    return (cam.matrix_world.to_quaternion() @ Vector((0, 0, -1)))


print("=== aim_gimbal(yaw,pitch) -> world view dir (x,y,z) ===")
for yaw in (0, 90, 180, 270):
    for pitch in (-85, -45, 0):
        v = view_dir(yaw, pitch)
        print(f"yaw={yaw:3} pitch={pitch:4}: ({v.x:+.2f},{v.y:+.2f},{v.z:+.2f})")

# Invert: want to aim down-and-upwind at the blade. Target dir ~(-0.25,-0.32,-0.91).
target = Vector((-0.25, -0.32, -0.91)).normalized()
best = None
for yi in range(0, 360, 3):
    for pi in range(-90, 1, 2):
        v = view_dir(yi, pi)
        d = v.dot(target)
        if best is None or d > best[0]:
            best = (d, yi, pi)
print(f"\nbest aim for target {tuple(round(c,2) for c in target)}: "
      f"yaw={best[1]} pitch={best[2]} (dot={best[0]:.3f})")
