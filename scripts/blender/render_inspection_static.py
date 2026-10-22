"""Render T1 Down at inspection pose (blade at 6 o'clock, static).

No MAPPO replay. Manually sets Blade1 to point straight down (inspection pose),
places camera at (-5, 10, -0.07) per user spec (root 10m, tip 60m, pitch ~9° from
vertical), and renders with 4 zrot variants to match image #21 framing.
"""
import math
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Euler, Matrix, Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]

import wfrl_blender as addon

addon.register()
# Load demo WITHOUT flex (static inspection pose)
addon.load_demo_scene()
scene = bpy.context.scene
scene["wfrl_blade_model"] = "rootkink"

# Switch to T1 Down angle (sets up camera parent to YawRoot)
bpy.ops.wfrl.farm_flex_view(turbine="T1", angle="DOWN")

# Get Blade1 and manually rotate to 6 o'clock (azimuth 180°)
rotor = scene.objects.get("WFRL.Turbine.T1.Rotor")
if rotor:
    rotor.rotation_euler[2] = math.radians(180)  # azimuth for Blade1 down
bpy.context.view_layer.update()

blade = scene.objects.get("WFRL.Turbine.T1.Blade1")
if not blade:
    print("ERROR: Blade1 not found"); sys.exit(1)

mw = blade.matrix_world
co = list(blade.data.vertices)
bs = sorted(co, key=lambda v: v.co.z)
root_w = mw @ bs[int(len(bs)*0.05)].co  # ~r=3
tip_w = mw @ bs[-1].co
shoulder_w = mw @ min(co, key=lambda v: abs(v.co.z - 5.6)).co

camera = scene.camera
camera.location = (-5.3, 10.0, 0.07)  # YawRoot local, exact from distance constraints

outdir = (ROOT / "outputs/inspection-static").resolve()
outdir.mkdir(parents=True, exist_ok=True)
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.image_settings.file_format = "PNG"

print(f"Blade1 world tip z: {tip_w.z:.1f}")
cam_world = scene.objects.get("WFRL.Turbine.T1.YawRoot").matrix_world @ camera.location
print(f"Camera world: {cam_world}")
print(f"Distance root: {(root_w - cam_world).length:.1f}m, tip: {(tip_w - cam_world).length:.1f}m")

for zrot in [0, 90, 180, 270]:
    camera.rotation_euler = Euler([math.radians(9), 0, math.radians(zrot)], "XYZ")
    camera.data.sensor_width = 36.0
    camera.data.lens = 36.0 / (2 * math.tan(math.radians(75) / 2))  # fov 75
    bpy.context.view_layer.update()

    nd = lambda p: tuple(world_to_camera_view(scene, camera, p))
    r, s, t = nd(root_w), nd(shoulder_w), nd(tip_w)

    # LE/TE at shoulder
    ring = [v for v in co if abs(v.co.z - 5.6) < 0.4]
    le_w = mw @ min(ring, key=lambda v: v.co.y).co
    te_w = mw @ max(ring, key=lambda v: v.co.y).co
    le, te = nd(le_w), nd(te_w)

    print(f"zrot={zrot}: root=({r[0]:.2f},{r[1]:.2f},d={r[2]:.1f}) "
          f"tip=({t[0]:.2f},{t[1]:.2f},d={t[2]:.1f}) "
          f"shoulder=({s[0]:.2f},{s[1]:.2f}) LE.x={le[0]:.2f} TE.x={te[0]:.2f}")

    scene.render.filepath = str(outdir / f"static_zrot{zrot}.png")
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
