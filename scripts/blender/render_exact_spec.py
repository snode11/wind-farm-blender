"""Render with the exact user-spec camera: loc=(-5,10,-0.07), pitch~-81.

Computed from distance constraints: root(r=3) 10m away, tip 60m away, at the
inspection pose (blade pointing straight down). Renders 4 zrot variants at BDC
frame to find the correct image orientation matching image #21.
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

# BDC frame
best_frame, best_z = scene.frame_start, 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 4):
    scene.frame_set(f); bpy.context.view_layer.update()
    mw = blade.matrix_world
    tipz = min((mw @ v.co).z for v in blade.data.vertices)
    if tipz < best_z:
        best_z, best_frame = tipz, f
scene.frame_set(best_frame); bpy.context.view_layer.update()
print(f"Using BDC frame {best_frame}")

mw = blade.matrix_world
co = list(blade.data.vertices)
bs = sorted(co, key=lambda v: v.co.z)
root_w = mw @ bs[int(len(bs)*0.05)].co  # ~r=3, visible root outside spinner
tip_w = mw @ bs[-1].co
shoulder_w = mw @ min(co, key=lambda v: abs(v.co.z - 5.6)).co

camera = scene.camera
camera.location = (-5.0, 10.0, -0.07)

outdir = (ROOT / "outputs/exact-spec-camera").resolve()
outdir.mkdir(parents=True, exist_ok=True)
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.image_settings.file_format = "PNG"

for zrot in [0, 90, 180, 270]:
    camera.rotation_euler = Euler([math.radians(9), 0, math.radians(zrot)], "XYZ")
    camera.data.sensor_width = 36.0
    camera.data.lens = 36.0 / (2 * math.tan(math.radians(70) / 2))  # fov 70
    bpy.context.view_layer.update()

    nd = lambda p: tuple(world_to_camera_view(scene, camera, p))
    r, s, t = nd(root_w), nd(shoulder_w), nd(tip_w)

    # leading/trailing edge at shoulder
    ring = [v for v in co if abs(v.co.z - 5.6) < 0.4]
    le_w = mw @ min(ring, key=lambda v: v.co.y).co
    te_w = mw @ max(ring, key=lambda v: v.co.y).co
    le, te = nd(le_w), nd(te_w)

    print(f"zrot={zrot}: root=({r[0]:.2f},{r[1]:.2f},d={r[2]:.1f}) "
          f"shoulder=({s[0]:.2f},{s[1]:.2f}) tip=({t[0]:.2f},{t[1]:.2f},d={t[2]:.1f}) "
          f"LE.x={le[0]:.2f} TE.x={te[0]:.2f}")

    scene.render.filepath = str(outdir / f"exact_zrot{zrot}_fov70.png")
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
