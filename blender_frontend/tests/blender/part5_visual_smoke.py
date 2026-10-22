"""Part 5 Blender smoke: visual layers, topology reuse and metadata."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "blender_frontend"))

import bpy
import wfrl_blender
from wfrl_blender import atmosphere, cameras, wake
from wfrl_blender.landscape import road_clearance


def main():
    wfrl_blender.register()
    wfrl_blender.load_demo_scene()
    scene = bpy.context.scene
    assert {"clear", "overcast", "dusk"} == set(atmosphere.PRESETS)
    assert scene.wfrl_atmosphere_preset == "clear"
    assert bpy.data.objects.get("WFRL.Fixture.T1.SensorFrustum") is not None
    assert bpy.data.objects["WFRL.Fixture.T1.SensorFrustum"]["fidelity"] == "SYNTH"
    grass = bpy.data.objects["WFRL.Landscape.GrassTufts"]
    soil = bpy.data.objects["WFRL.Landscape.BareSoilPatches"]
    cover = bpy.data.objects["WFRL.Landscape.GrassCoverPatches"]
    assert grass["cluster_count"] >= 30
    assert grass["tuft_count"] >= 5000
    assert len(grass.data.materials) == 4
    assert soil["cluster_count"] >= 8
    assert cover["cluster_count"] >= 25
    shrub_roots = [obj.location for obj in bpy.data.objects if obj.name.startswith("WFRL.Landscape.Shrub")]
    assert shrub_roots
    assert min(road_clearance(point.x, point.y) for point in shrub_roots) >= 10.0

    before = len(bpy.data.objects)
    for index in range(600):
        wake.update_proxy_objects(scene, phase=(index * 0.013) % 1.0)
    assert len(bpy.data.objects) == before, "proxy animation leaked Blender objects"

    frame = wake.WakeFrame("disxy", 1, "EXPORTED", "case.Low.DisXY01.001.vtk", "valid",
                           ("T1",), 1.0, (8.0, 7.0, 7.5, 8.0), (2, 2),
                           (0.0, 10.0), (0.0, 10.0), 90.0)
    obj = wake.apply_disxy_frame(frame)
    mesh_id = obj.data.as_pointer()
    assert obj["fidelity"] == "EXPORTED"
    frame2 = wake.WakeFrame("disxy", 2, "EXPORTED", "case.Low.DisXY01.002.vtk", "valid",
                            ("T1",), 2.0, (8.0, 6.5, 7.0, 8.0), (2, 2),
                            (0.0, 10.0), (0.0, 10.0), 90.0)
    obj2 = wake.apply_disxy_frame(frame2)
    assert obj2.data.as_pointer() == mesh_id, "DisXY update replaced reusable mesh topology"
    assert obj2["sequence"] == 2

    atmosphere.apply_preset(scene, "dusk", enabled=False, quality="render")
    assert scene["wfrl_atmosphere_preset"] == "dusk"
    assert scene["wfrl_atmosphere_enabled"] is False
    cameras.select_camera(scene, "WFRL.Camera.T1.Sensor", fov_deg=75.0, focus="T1")
    assert scene.camera.name == "WFRL.Camera.T1.Sensor"
    print("WFRL_PART5_VISUAL_SMOKE=PASS")
    print(f"Proxy objects stable at {before}; DisXY mesh datablock reused")


if __name__ == "__main__":
    main()
