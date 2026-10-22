"""Find a downwind camera pose clear of the rotor sweep at ALL frames.

Keeps the side offset (y~10) that gives root 10m / tip 60m, adds a downwind
x-offset so the sweeping blade never reaches the camera, aims down-and-upwind
at the blade, and renders. Checks min blade distance across all frames.
"""
import math
import sys
from pathlib import Path

import bpy
from bpy_extras.object_utils import world_to_camera_view
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

# BDC frame
best_frame, best_z = scene.frame_start, 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 4):
    scene.frame_set(f); bpy.context.view_layer.update()
    tipz = min((blade.matrix_world @ v.co).z for v in blade.data.vertices)
    if tipz < best_z:
        best_z, best_frame = tipz, f

cam = scene.camera
outdir = (ROOT / "outputs/downwind-solve").resolve()
outdir.mkdir(parents=True, exist_ok=True)
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 1280
scene.render.resolution_y = 720
scene.render.image_settings.file_format = "PNG"


def min_dist_all_frames(cam_world):
    d = 1e9
    for f in range(scene.frame_start, scene.frame_end + 1, 8):
        scene.frame_set(f); bpy.context.view_layer.update()
        mw = blade.matrix_world
        for v in blade.data.vertices:
            dd = (mw @ v.co - cam_world).length
            if dd < d:
                d = dd
    return d


# Candidates: downwind x (local -3..-1), side y=10, near hub height z~0..1.
# Aim down-and-upwind at mid-blade. Local frame; YawRoot at hub-ish.
candidates = [
    ("x-2.5_y10", (-2.5, 10.0, 0.5)),
    ("x-1.5_y10", (-1.5, 10.0, 0.5)),
    ("x-0.5_y10", (-0.5, 10.0, 1.0)),
]
for name, loc in candidates:
    cam.location = loc
    bpy.context.view_layer.update()
    cam_world = cam.matrix_world.translation
    clear = min_dist_all_frames(cam_world)
    print(f"{name}: cam_world=({cam_world.x:.1f},{cam_world.y:.1f},{cam_world.z:.1f}) "
          f"min_blade_all_frames={clear:.1f}m "
          f"({'CLEAR' if clear > 4 else 'HITS BLADE'})")

# Render the clearest one at BDC, aiming down-upwind at mid-blade, several rolls
scene.frame_set(best_frame); bpy.context.view_layer.update()
mw = blade.matrix_world
co = list(blade.data.vertices)
bs = sorted(co, key=lambda v: v.co.z)
root_w = mw @ bs[int(len(bs)*0.05)].co
tip_w = mw @ bs[-1].co
midblade = mw @ min(co, key=lambda v: abs(v.co.z - 30)).co

cam.location = (-1.5, 10.0, 0.5)
cam_world = cam.matrix_world.translation
# aim at mid-blade
fwd = (midblade - cam_world).normalized()
print(f"aim dir to mid-blade: ({fwd.x:.2f},{fwd.y:.2f},{fwd.z:.2f}) "
      f"angle from vertical={math.degrees(math.acos(-fwd.z)):.1f}deg")

# Use track-to via rotation from quaternion
for roll_deg in [0, 90, 180, 270]:
    up = Vector((0, 0, 1))
    quat = fwd.to_track_quat('-Z', 'Y')
    cam.rotation_euler = quat.to_euler()
    cam.rotation_euler.rotate_axis('Z', math.radians(roll_deg))
    cam.data.sensor_width = 36.0
    cam.data.lens = 36.0 / (2 * math.tan(math.radians(75) / 2))
    bpy.context.view_layer.update()
    nd = lambda p: tuple(world_to_camera_view(scene, cam, p))
    r, t = nd(root_w), nd(tip_w)
    print(f"roll={roll_deg}: root=({r[0]:.2f},{r[1]:.2f}) tip=({t[0]:.2f},{t[1]:.2f})")
    scene.render.filepath = str(outdir / f"downwind_roll{roll_deg}.png")
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
