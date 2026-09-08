"""Build, save, and render the Part 2 presentation scene in Blender 5.2.1."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "blender_frontend"))

import bpy  # type: ignore

import wfrl_blender


def render(camera_name: str, output: Path, frame: int) -> None:
    scene = bpy.context.scene
    scene.camera = bpy.data.objects[camera_name]
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    scene.render.filepath = str(output)
    scene.render.use_stamp = True
    scene.render.use_stamp_note = True
    scene.render.stamp_note_text = 'WFRL | SYNTH | Offline demo / no physical backend | Terrain excluded from physics'
    scene.render.stamp_font_size = 18
    for field in ('date', 'time', 'render_time', 'frame', 'frame_range', 'memory', 'hostname', 'camera', 'lens', 'scene', 'marker', 'filename', 'sequencer_strip'):
        key = 'use_stamp_' + field
        if hasattr(scene.render, key): setattr(scene.render, key, False)
    bpy.ops.render.render(write_still=True)


def main() -> None:
    output = ROOT / "evidence" / "part2"
    output.mkdir(parents=True, exist_ok=True)
    wfrl_blender.register()
    wfrl_blender.load_demo_scene()
    
    render("WFRL.Camera.World", output / "world_view.png", 450)
    wake_proxy = [obj for obj in bpy.data.objects if obj.name.startswith("WFRL.WakeProxy")]
    for obj in wake_proxy:
        obj.hide_render = True
    render("WFRL.Camera.T1.Sensor", output / "nacelle_camera.png", 451)
    render("WFRL.Camera.T1.Closeup", output / "turbine_closeup.png", 451)
    for obj in wake_proxy:
        obj.hide_render = False
    render("WFRL.Camera.Top", output / "top_view.png", 450)
    print("WFRL_PART2_RENDER=PASS")


if __name__ == "__main__":
    main()
