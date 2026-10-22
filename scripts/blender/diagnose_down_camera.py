"""Diagnose the T1 Down camera: is it inside the rotor sweep plane?

Loads MAPPO, finds the frame where Blade1 points down, reports the hub world
position, rotor-plane x, and the current camera world position + distance to
the blade. Then renders the actual down_gimbal view plus a downwind variant.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler, Vector

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
hub = scene.objects.get("WFRL.Turbine.T1.Rotor")
yawroot = scene.objects.get("WFRL.Turbine.T1.YawRoot")
hub_world = hub.matrix_world.translation
print(f"hub world: ({hub_world.x:.2f},{hub_world.y:.2f},{hub_world.z:.2f})  (rotor plane x={hub_world.x:.2f})")

best_frame, best_z = scene.frame_start, 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 4):
    scene.frame_set(f); bpy.context.view_layer.update()
    tipz = min((blade.matrix_world @ v.co).z for v in blade.data.vertices)
    if tipz < best_z:
        best_z, best_frame = tipz, f
scene.frame_set(best_frame); bpy.context.view_layer.update()
print(f"BDC frame {best_frame}")

cam = scene.camera
cam_world = cam.matrix_world.translation
print(f"current camera world: ({cam_world.x:.2f},{cam_world.y:.2f},{cam_world.z:.2f})")
print(f"camera x vs rotor plane x: {cam_world.x:.2f} vs {hub_world.x:.2f} "
      f"({'IN rotor plane - BAD' if abs(cam_world.x - hub_world.x) < 2 else 'downwind - OK'})")

mw = blade.matrix_world
co = list(blade.data.vertices)
dmin = min((mw @ v.co - cam_world).length for v in co)
print(f"nearest blade vertex to camera: {dmin:.1f}m "
      f"({'TOO CLOSE / inside' if dmin < 3 else 'clear'})")

outdir = (ROOT / "outputs/down-diagnose").resolve()
outdir.mkdir(parents=True, exist_ok=True)
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.image_settings.file_format = "PNG"

# Render current pose
scene.render.filepath = str(outdir / "current_down_gimbal.png")
bpy.ops.render.render(write_still=True)
print("WROTE", scene.render.filepath)

# Downwind variants: camera behind rotor plane (hub.x + offset), looking down-forward
# In YawRoot local frame. hub is at x≈-5; downwind means x less negative (toward 0).
for name, loc, eul, fov in [
    ("dw_x-3_down", (-3.0, 0.0, 0.0), (20, 0, 90), 75),
    ("dw_x-2_down", (-2.0, 0.0, 0.5), (25, 0, 90), 75),
    ("dw_x-3_fwd",  (-3.0, 0.0, 0.5), (30, 0, 90), 80),
]:
    cam.location = loc
    cam.rotation_euler = Euler([math.radians(a) for a in eul], "XYZ")
    cam.data.sensor_width = 36.0
    cam.data.lens = 36.0 / (2 * math.tan(math.radians(fov) / 2))
    bpy.context.view_layer.update()
    cw = cam.matrix_world.translation
    dmin = min((mw @ v.co - cw).length for v in co)
    print(f"{name}: cam_world x={cw.x:.1f} nearest_blade={dmin:.1f}m")
    scene.render.filepath = str(outdir / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
