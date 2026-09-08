"""Exercise the actual registered operators, scene callbacks and .blend reload."""
from pathlib import Path
import math
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy
import wfrl_blender
from wfrl_blender.state import END_FRAME, sample_demo, time_for_frame


def check_pose(scene):
    sample = sample_demo(time_for_frame(scene.frame_current))
    for index, tid in enumerate(("T1", "T2", "T3")):
        prefix = "WFRL.Turbine." + tid
        assert math.isclose(bpy.data.objects[prefix + ".YawRoot"].rotation_euler.z, math.radians(sample.yaw_deg[index]), abs_tol=1e-5)
        assert math.isclose(bpy.data.objects[prefix + ".Rotor"].rotation_euler.x, sample.rotor_rad[index], abs_tol=1e-5)
        assert math.isclose(scene["wfrl_rpm"][index], sample.rpm[index], abs_tol=1e-5)
        assert math.isclose(scene["wfrl_power_mw"][index], sample.power_mw[index], abs_tol=1e-5)
        for blade_index in range(1, 4):
            blade = bpy.data.objects[prefix + f".Blade{blade_index}"]
            assert math.isclose(blade.rotation_euler.z, math.radians(sample.pitch_deg[index]), abs_tol=1e-5)
            assert blade.parent.name == prefix + f".Blade{blade_index}.PitchRoot"


def main():
    wfrl_blender.register()
    assert bpy.ops.wfrl.load_demo() == {"FINISHED"}
    scene = bpy.context.scene
    assert scene.frame_current == 1 and scene.frame_end == END_FRAME
    assert scene["wfrl_run_status"] == "READY"
    check_pose(scene)
    assert bpy.ops.wfrl.demo_start() == {"FINISHED"}
    scene.frame_set(301)
    bpy.ops.wfrl.demo_pause()
    before = scene.frame_current
    assert scene["wfrl_run_status"] == "PAUSED"
    bpy.ops.wfrl.demo_resume()
    assert scene.frame_current == before, "Resume must not reset timeline"
    assert scene["wfrl_run_status"] == "RUNNING"
    bpy.ops.wfrl.demo_pause()
    bpy.ops.wfrl.demo_step()
    assert scene.frame_current == before + 1
    assert scene["wfrl_run_status"] == "PAUSED"
    check_pose(scene)
    scene.wfrl_selected_turbine = "T2"
    assert bpy.data.objects["WFRL.Turbine.T2.Nacelle"].select_get()
    for prop, prefix in (("wfrl_show_wake", "WFRL.WakeProxy."), ("wfrl_show_lidar", "WFRL.Fixture.T1.Lidar")):
        setattr(scene, prop, False)
        objects = [obj for obj in scene.objects if obj.name.startswith(prefix)]
        assert objects and all(obj.hide_get() and obj.hide_render for obj in objects)
        setattr(scene, prop, True)
        assert all(not obj.hide_get() and not obj.hide_render for obj in objects)
    scene.wfrl_channel_telemetry = False
    frozen = scene["wfrl_telemetry_time_s"]
    bpy.ops.wfrl.demo_step()
    assert scene["wfrl_telemetry_time_s"] == frozen
    scene.wfrl_channel_telemetry = True
    assert scene["wfrl_telemetry_time_s"] == time_for_frame(scene.frame_current)
    scene.wfrl_manual_enabled = True
    scene.wfrl_manual_yaw = 23
    scene.wfrl_manual_pitch = 40
    assert math.isclose(bpy.data.objects["WFRL.Turbine.T2.YawRoot"].rotation_euler.z, math.radians(23), abs_tol=1e-5)
    assert math.isclose(bpy.data.objects["WFRL.Turbine.T2.Blade3"].rotation_euler.z, math.radians(40), abs_tol=1e-5)
    assert not scene["wfrl_power_available"]
    assert scene["wfrl_rpm"][1] == 0
    bpy.ops.wfrl.demo_resume()
    assert not scene.wfrl_manual_enabled
    check_pose(scene)
    for frame in (100, 101, 376, 1201, 1401, 1451, 1650):
        scene.frame_set(frame)
        check_pose(scene)
        assert scene["wfrl_run_status"] != "STOPPED"
    scene.frame_set(END_FRAME)
    check_pose(scene)
    assert scene["wfrl_run_status"] == "STOPPED"
    assert all(value == 0 for value in scene["wfrl_rpm"]), list(scene["wfrl_rpm"])
    bpy.ops.wfrl.demo_reset()
    assert scene.frame_current == 1 and scene["wfrl_run_status"] == "READY"
    bpy.ops.wfrl.demo_start()
    scene.frame_set(450)
    bpy.ops.wfrl.demo_stop()
    assert scene.frame_current == END_FRAME and scene["wfrl_run_status"] == "STOPPED"
    bpy.ops.wfrl.demo_start()
    scene.frame_set(401)
    bpy.ops.wfrl.demo_pause()
    with tempfile.TemporaryDirectory(prefix="wfrl-reload-") as directory:
        filename = str(Path(directory) / "reopen.blend")
        bpy.ops.wm.save_as_mainfile(filepath=filename)
        bpy.ops.wm.open_mainfile(filepath=filename)
    scene = bpy.context.scene
    assert wfrl_blender._update_demo_status in bpy.app.handlers.frame_change_post
    assert wfrl_blender._on_load in bpy.app.handlers.load_post
    assert scene["wfrl_run_status"] == "PAUSED"
    bpy.ops.wfrl.demo_resume()
    assert scene.frame_current == 401
    scene.frame_set(501)
    check_pose(scene)
    wfrl_blender.register()
    assert bpy.app.handlers.frame_change_post.count(wfrl_blender._update_demo_status) == 1
    wfrl_blender.unregister()
    wfrl_blender.register()
    print("WFRL_PART2_DEMO_WORKFLOW_SMOKE=PASS")


if __name__ == "__main__":
    main()
