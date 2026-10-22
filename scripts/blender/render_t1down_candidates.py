"""Render T1 Down candidate poses to PNG (fast workbench), BDC frame.

  blender --background --factory-startup --python scripts/blender/render_t1down_candidates.py -- OUTDIR

Each candidate sets the T1 Down camera to a (loc, euler_deg, fov) and renders a
small image so the framing can be confirmed against the field reference.
"""
import math
import sys
from pathlib import Path

import bpy
from mathutils import Euler

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "blender_frontend"), str(ROOT)]
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
outdir = (Path(argv[0]) if argv else (ROOT / "outputs/t1down-probe")).resolve()
outdir.mkdir(parents=True, exist_ok=True)

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

best_frame, best_z = scene.frame_start, 1e9
for f in range(scene.frame_start, scene.frame_end + 1, 4):
    scene.frame_set(f); bpy.context.view_layer.update()
    mw = blade.matrix_world
    tipz = min((mw @ v.co).z for v in blade.data.vertices)
    if tipz < best_z:
        best_z, best_frame = tipz, f
scene.frame_set(best_frame); bpy.context.view_layer.update()

scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = 960
scene.render.resolution_y = 540
scene.render.image_settings.file_format = "PNG"
camera = scene.camera

candidates = {
    "a_zrot270_fov80": ((-6.0, 0.0, 2.5), (10, 0, 270), 80),
    "b_zrot270_fov110": ((-6.0, 0.0, 2.5), (10, 0, 270), 110),
    "c_zrot90_fov80": ((-6.0, 0.0, 2.5), (10, 0, 90), 80),
    "d_zrot270_ex2_fov100": ((-6.0, 0.0, 2.5), (2, 0, 270), 100),
}
for name, (loc, eul, fov) in candidates.items():
    camera.location = loc
    camera.rotation_euler = Euler([math.radians(a) for a in eul], "XYZ")
    camera.data.sensor_width = 36.0
    camera.data.lens = 36.0 / (2 * math.tan(math.radians(fov) / 2))
    bpy.context.view_layer.update()
    scene.render.filepath = str(outdir / (name + ".png"))
    bpy.ops.render.render(write_still=True)
    print("WROTE", scene.render.filepath)
