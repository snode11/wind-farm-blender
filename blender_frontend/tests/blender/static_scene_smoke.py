"""Run with Blender 5.2.1: blender --background --python static_scene_smoke.py."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import bpy  # type: ignore

import wfrl_blender


def _camera_sees_object(scene, camera, obj):
    from bpy_extras.object_utils import world_to_camera_view
    previous = scene.camera
    scene.camera = camera
    bpy.context.view_layer.update()
    try:
        point = world_to_camera_view(scene, camera, obj.matrix_world.translation)
    finally:
        scene.camera = previous
        bpy.context.view_layer.update()
    return 0.0 <= point.x <= 1.0 and 0.0 <= point.y <= 1.0 and point.z > 0.0


def main() -> None:
    foreign_data = bpy.data.meshes.new("Foreign.Mesh")
    foreign = bpy.data.objects.new("Foreign.Object", foreign_data)
    bpy.context.scene.collection.objects.link(foreign)
    wfrl_blender.register()
    wfrl_blender.load_demo_scene()
    assert bpy.data.workspaces.get("WFRL Workspace") is not None
    collection = bpy.data.collections.get("WFRL_Scene")
    assert collection is not None
    names = {obj.name for obj in collection.objects}
    for turbine_id in ("T1", "T2", "T3"):
        assert f"WFRL.Turbine.{turbine_id}" in names
        assert f"WFRL.Turbine.{turbine_id}.Rotor" in names
        assert f"WFRL.Turbine.{turbine_id}.Blade1" in names
    assert bpy.data.objects["WFRL.Camera.World"] == bpy.context.scene.camera
    assert _camera_sees_object(bpy.context.scene, bpy.data.objects["WFRL.Camera.T1.Closeup"], bpy.data.objects["WFRL.Turbine.T1.Hub"])
    assert bpy.context.scene.get("wfrl_run_status") == "READY"
    assert collection.get("fidelity") == "SYNTH"
    assert bpy.context.scene.frame_end == 1651
    assert bpy.context.scene.render.fps == 25
    assert bpy.data.objects["WFRL.Turbine.T1.Rotor"].animation_data is not None
    first_names = {obj.name for obj in collection.objects}
    owned_data = lambda: sum(sum(d.name.startswith("WFRL.") for d in group) for group in (bpy.data.meshes, bpy.data.curves, bpy.data.cameras, bpy.data.lights, bpy.data.actions))
    first_data_count = owned_data()
    wfrl_blender.load_demo_scene()
    rebuilt = bpy.data.collections["WFRL_Scene"]
    assert {obj.name for obj in rebuilt.objects} == first_names
    assert owned_data() == first_data_count, "Scene rebuild leaked owned datablocks"
    assert bpy.data.objects.get("Foreign.Object") is foreign
    wfrl_blender.unregister()
    wfrl_blender.register()
    yaw_root = bpy.data.objects["WFRL.Turbine.T2.YawRoot"]
    wfrl_blender.apply_demo_state([0.0, 8.0, 15.0], [2.0, 2.0, 2.0], [10.8, 10.3, 9.8])
    assert round(yaw_root.rotation_euler[2], 4) == round(8.0 * 0.017453292519943295, 4)
    print("WFRL_PART2_STATIC_SCENE_SMOKE=PASS")


if __name__ == "__main__":
    main()
