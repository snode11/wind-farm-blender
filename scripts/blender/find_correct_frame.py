"""Find the frame where Blade1 root is ~9m from camera, tip ~60m.

User spec: camera at (-4.5, 9, -1), looking down ~9° from vertical. At the right
azimuth, Blade1's root should be 9-10m away and tip 58-62m. Scan all frames.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]

import wfrl_blender as addon

addon.register()
addon.load_demo_scene()
scene = bpy.context.scene
scene["wfrl_blade_model"] = "rootkink"
from wfrl_blender import farm_flex
farm_flex.attach(scene, farm_flex.saved_package(scene))
bpy.ops.wfrl.farm_flex_view(turbine="T1", angle="DOWN")

blade = scene.objects.get("WFRL.Turbine.T1.Blade1")
cam_loc = Vector((-4.5, 9.0, -1.0))

print("=== Scanning frames for Blade1 root~9m, tip~60m from camera ===")
best = []
for f in range(scene.frame_start, scene.frame_end + 1, 2):
    scene.frame_set(f)
    bpy.context.view_layer.update()
    mw = blade.matrix_world
    co = list(blade.data.vertices)
    bs = sorted(co, key=lambda v: v.co.z)
    root_w = mw @ bs[0].co
    tip_w = mw @ bs[-1].co
    d_root = (root_w - cam_loc).length
    d_tip = (tip_w - cam_loc).length
    if 8 < d_root < 12 and 55 < d_tip < 65:
        best.append((f, d_root, d_tip, root_w, tip_w))
        if len(best) <= 10:
            print(f"frame {f}: root dist={d_root:.1f}m tip dist={d_tip:.1f}m "
                  f"root_world=({root_w.x:.1f},{root_w.y:.1f},{root_w.z:.1f})")

if best:
    print(f"\nFound {len(best)} candidate frames. Using frame {best[0][0]}.")
else:
    print("No frame satisfies root~9m + tip~60m. Check camera position or blade motion.")
