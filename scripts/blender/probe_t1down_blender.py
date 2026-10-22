"""Ground-truth T1 Down framing sweep (Blender projection, BDC frame).

  blender --background --factory-startup --python scripts/blender/probe_t1down_blender.py

Blade at bottom dead centre hangs ~61 m straight down from the hub. A near
straight-down camera sees the chord left-right (leading/trailing edges) and the
span receding (root near/wide, tip far/narrow): the field reference framing.
Reports root/shoulder/tip plus shoulder leading/trailing edge in Blender NDC.
"""
import math
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]

import wfrl_blender as addon

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


def pts():
    mw = blade.matrix_world
    co = list(blade.data.vertices)
    bs = sorted(co, key=lambda v: v.co.z)
    root, tip = mw @ bs[0].co, mw @ bs[-1].co
    # shoulder ring at local z~5.6; leading edge = min chord-dir, trailing = max
    ring = [v for v in co if abs(v.co.z - 5.6) < 0.4]
    le = mw @ min(ring, key=lambda v: v.co.y).co
    te = mw @ max(ring, key=lambda v: v.co.y).co
    return root, tip, le, te, [mw @ v.co for v in co]


best_frame, best_z = scene.frame_start, 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 4):
    scene.frame_set(f); bpy.context.view_layer.update()
    _, tip, *_ = pts()
    if tip.z < best_z:
        best_z, best_frame = tip.z, f
scene.frame_set(best_frame); bpy.context.view_layer.update()
root_w, tip_w, le_w, te_w, allv = pts()
print(f"BDC frame {best_frame}")
camera = scene.camera


def metrics(loc, eul_deg, fov):
    camera.location = loc
    camera.rotation_euler = Euler([math.radians(a) for a in eul_deg], "XYZ")
    camera.data.sensor_width = 36.0
    camera.data.lens = 36.0 / (2 * math.tan(math.radians(fov) / 2))
    bpy.context.view_layer.update()
    nd = lambda p: tuple(world_to_camera_view(scene, camera, p))
    r, t, le, te = nd(root_w), nd(tip_w), nd(le_w), nd(te_w)
    xs = [world_to_camera_view(scene, camera, p).x for p in allv
          if world_to_camera_view(scene, camera, p).z > 1.0]
    ys = [world_to_camera_view(scene, camera, p).y for p in allv
          if world_to_camera_view(scene, camera, p).z > 1.0]
    return r, t, le, te, (max(xs) - min(xs) if xs else 0), (max(ys) - min(ys) if ys else 0)


print("=== loc(local to YawRoot); eulerX(90+pitch); zrot; fov -> "
      "root(x,y) tip(x,y) LE.x TE.x | w h ===")
for lx in (-6.0, -5.0, -4.0):
    for lz in (1.0, 2.5):
        for ex in (2, 10, 18):          # ex=0 is straight down; small tilt
            for zrot in (0, 90, 180, 270):
                for fov in (60, 80):
                    r, t, le, te, w, h = metrics((lx, 0.0, lz), (ex, 0, zrot), fov)
                    if not (r[2] > 1 and t[2] > 1):
                        continue
                    inside = all(0 <= c[0] <= 1 and 0 <= c[1] <= 1 for c in (r, t))
                    if not inside:
                        continue
                    sep = le[0] - te[0]        # leading-edge should be left of trailing
                    print(f"loc=({lx},0,{lz}) ex={ex} zrot={zrot} fov={fov} -> "
                          f"root=({r[0]:.2f},{r[1]:.2f}) tip=({t[0]:.2f},{t[1]:.2f}) "
                          f"LE.x={le[0]:.2f} TE.x={te[0]:.2f} sep={sep:+.2f} "
                          f"w={w:.2f} h={h:.2f}")
