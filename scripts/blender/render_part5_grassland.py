"""Native Blender rebuild/render for the Part 5 grassland presentation."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blender_frontend"))

import bpy
import wfrl_blender

wfrl_blender.register()
wfrl_blender.load_demo_scene()
scene = bpy.context.scene
scene.frame_set(301)
scene.render.engine = "BLENDER_EEVEE"
scene.render.resolution_percentage = 75
scene.view_settings.exposure = 1.2
scene.render.filepath = str(ROOT / "evidence/part5_grassland_preview.png")
bpy.context.view_layer.update()
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "evidence/part5_grassland.blend"))
bpy.ops.render.render(write_still=True)
print("WFRL_NATIVE_GRASSLAND=PASS")
