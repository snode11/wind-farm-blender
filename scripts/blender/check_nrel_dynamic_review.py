"""Finite phased Blender checks; no render, new video, installation or quit.

Visible-window mode separates every seek/load and inspection across timer
callbacks. Headless integration can explicitly drive the same phases with
``synchronous=True``; it does not establish visible movie pixels or legibility.
"""
import json
import importlib.util
import os
from pathlib import Path
import time
import traceback

import bpy
import numpy as np


def run_checks(viewer, output, *, synchronous=False):
    root = Path(output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "window_check.json"
    if path.exists():
        raise ValueError("Native review check output already exists")
    viewer.ACTIVE["checks_complete"] = False
    checks = {
        "scope": "REVIEW_ONLY / PRIOR_DEFINED_STRUCTURAL_POINT; DY1 not passed",
        "blender_version": bpy.app.version_string,
        "execution": "background phased integration" if bpy.app.background else "native window timer phases",
        "forward_backward_and_unavailable_seek": [],
        "operator_steps": [],
        "group_switch_distances_m": {},
    }
    state = {"index": 0, "started": time.monotonic(), "done": False, "result": None}
    playback_requested = os.environ.get("WFRL_DYNAMIC_PLAYBACK_CHECK") == "1"
    scene = bpy.context.scene
    package_path = scene.nrel_review_path

    def settled(frame=None, group=None):
        frame = scene.frame_current if frame is None else frame
        group = scene.nrel_review_group if group is None else group
        assert not viewer.ACTIVE["pending"], "Shared frame update has not settled"
        assert viewer.ACTIVE["last_applied_frame"] == (frame, group), (
            f"Displayed identity {viewer.ACTIVE['last_applied_frame']!r}; "
            f"expected {(frame, group)!r}; current scene {(scene.frame_current, scene.nrel_review_group)!r}")

    def verify_measurement(frame, expected_available):
        settled(frame)
        assert scene.frame_current == frame
        assert all(area.spaces.active.image_user.frame_current == frame
                   for area in viewer.ACTIVE["movie_areas"].values())
        value = viewer.ACTIVE["value"]
        assert (value is not None) == expected_available
        assert viewer.ACTIVE["geometry_visible"] == expected_available
        if value is not None:
            row = value["record"]
            assert row["frame_id"] == frame - 1
            point = np.asarray(viewer.ACTIVE["objects"]["point"].location)
            foot = np.asarray(viewer.ACTIVE["objects"]["foot"].location)
            normal = np.asarray(row["normal_world_unit"])
            display_d = float(normal @ (point - row["center_world_m"]))
            # Blender display coordinates are float32. The reader checks the
            # package's identities in float64 before any Blender mutation.
            assert abs(display_d - row["d_m"]) < 2e-5
            assert np.max(abs(foot - (point - row["d_m"] * normal))) < 2e-5
            assert not value["interpolated"]
            assert not any(obj.hide_get() for obj in viewer.ACTIVE["objects"].values())
            for key, count in (("perpendicular", 2), ("plane", 5), ("trajectory", 4)):
                assert len(viewer.ACTIVE["objects"][key].data.splines) == 1
                assert len(viewer.ACTIVE["objects"][key].data.splines[0].points) == count
        else:
            assert all(obj.hide_get() for obj in viewer.ACTIVE["objects"].values())

    def begin():
        assert viewer.ACTIVE["ready"] and viewer.ACTIVE["package"] is not None
        settled()
        checks["movie_frame_counts"] = {
            camera: image.frame_duration for camera, image in viewer.ACTIVE["movies"].items()}
        assert checks["movie_frame_counts"] == {"C1": 100, "C2": 100, "C3": 100}
        checks["movie_sources"] = {
            camera: image.filepath for camera, image in viewer.ACTIVE["movies"].items()}
        checks["movie_sha256"] = {
            camera: entry["sha256"] for camera, entry in viewer.ACTIVE["package"].manifest["videos"].items()}
        checks["movie_editor_count"] = len(viewer.ACTIVE["movie_areas"])
        if not bpy.app.background:
            assert checks["movie_editor_count"] == 3

    def seek(frame):
        assert bpy.ops.nrel_review.seek(frame=frame) == {"FINISHED"}

    def verify_seek(frame):
        verify_measurement(frame, frame != 1)
        checks["forward_backward_and_unavailable_seek"].append({
            "scene_frame": frame, "original_movie_frame": frame - 1, "available": frame != 1})

    def step(delta):
        assert bpy.ops.nrel_review.step(delta=delta) == {"FINISHED"}

    def verify_step(frame):
        verify_measurement(frame, True)
        checks["operator_steps"].append({"scene_frame": frame, "original_movie_frame": frame - 1})

    def switch_group(group):
        scene.nrel_review_group = group
        viewer.refresh(scene)

    def verify_group(group):
        settled(46, group)
        verify_measurement(46, True)
        assert viewer.ACTIVE["value"]["record"] == viewer.ACTIVE["package"].records[group][45]
        checks["group_switch_distances_m"][group] = viewer.ACTIVE["value"]["record"]["d_m"]

    def verify_group_distinction():
        distances = checks["group_switch_distances_m"]
        assert abs(distances["G3"] - distances["G2"]) > 1e-12
        checks["group_comparison_original_movie_frame"] = 45

    def invalid_load():
        fixture = root / "invalid-package-fixture"
        fixture.mkdir(exist_ok=False)
        (fixture / "manifest.json").write_text("{}\n", encoding="utf-8")
        assert viewer.load_package(fixture) is False

    def verify_invalid():
        assert viewer.ACTIVE["package"] is None and viewer.ACTIVE["value"] is None
        assert viewer.ACTIVE["objects"] == {} and not viewer.ACTIVE["geometry_visible"]
        assert viewer.ACTIVE["movies"] == {}
        assert all(area.spaces.active.image is None
                   for area in viewer.ACTIVE["movie_areas"].values())
        assert not any(image.source == "MOVIE" for image in bpy.data.images)
        checks["invalid_package_clears_reader_geometry_and_readout"] = True
        checks["invalid_package_clears_movies_and_editors"] = True

    def reload_valid():
        assert viewer.load_package(package_path) is True
        scene.nrel_review_group = "G3"
        # load_package evaluates new stable topology before this later seek.
        scene.frame_set(44)
        viewer.refresh(scene)

    def verify_reload():
        settled(44, "G3")
        verify_measurement(44, True)
        assert set(viewer.ACTIVE["movies"]) == {"C1", "C2", "C3"}
        assert all(area.spaces.active.image == viewer.ACTIVE["movies"][camera]
                   for camera, area in viewer.ACTIVE["movie_areas"].items())
        assert sum(image.source == "MOVIE" for image in bpy.data.images) == 3
        checks["valid_reload_restores_exact_saved_estimate"] = True
        checks["valid_reload_restores_three_movie_assignments"] = True
        checks["final_state"] = {"group": "G3", "scene_frame": 44, "original_movie_frame": 43}

    actions = [begin]
    for frame in (43, 46, 44, 1):
        actions.extend((lambda frame=frame: seek(frame), lambda frame=frame: verify_seek(frame)))
    actions.extend((lambda: seek(44), lambda: step(1), lambda: verify_step(45),
                    lambda: step(-1), lambda: verify_step(44), lambda: seek(46)))
    for group in ("G1", "G2", "G3"):
        actions.extend((lambda group=group: switch_group(group), lambda group=group: verify_group(group)))
    actions.extend((verify_group_distinction, lambda: seek(44), invalid_load, verify_invalid, reload_valid, verify_reload))

    def finish(error=None):
        if error is None:
            try:
                # Last phase proves settled G3/scene44 before save; no seek or
                # deletion runs in this finishing callback.
                settled(44, "G3")
                viewer.focus_tip()
                viewer.ACTIVE["checks_complete"] = not playback_requested
                if not playback_requested:
                    viewer.save_review_blend()
            except Exception:
                error = traceback.format_exc()
        result = {
            "status": "ERROR" if error else "PASS", "checks": checks,
            "elapsed_s": time.monotonic() - state["started"], "completed_timer_actions": state["index"],
            "scope": "Scripted Blender state checks; visible movie pixels and legibility require separate screenshot inspection; no engineering measurement acceptance"}
        if error:
            result["error"] = error
        viewer.ACTIVE["checks_complete"] = not playback_requested or error is not None
        state.update(done=True, result=result)
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        print("NREL_DYNAMIC_REVIEW_CHECK", result["status"], str(path), flush=True)
        if playback_requested and error is None:
            # Keep the existing 27 structural phases intact. Playback is a
            # separate native-window check with its own result and failures.
            try:
                module_path = Path(__file__).with_name("check_nrel_dynamic_playback.py")
                spec = importlib.util.spec_from_file_location("nrel_dynamic_playback_checks", module_path)
                playback = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(playback)
                playback.run_checks(viewer, root)
            except Exception:
                # Do not re-enter finish and overwrite the structural result
                # when the independent playback module cannot even start.
                failure = {"status": "ERROR", "stage": "playback module startup",
                           "error": traceback.format_exc()}
                (root / "playback_startup_failure.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
                viewer.ACTIVE["checks_complete"] = True
                print("NREL_DYNAMIC_PLAYBACK_STARTUP_ERROR", failure["error"], flush=True)
        return None

    def tick():
        if state["done"]:
            return None
        try:
            if time.monotonic() - state["started"] > 45:
                raise TimeoutError("Finite native review checks exceeded 45 seconds")
            if state["index"] == len(actions):
                return finish()
            actions[state["index"]]()
            state["index"] += 1
            if state["index"] == len(actions):
                # Final exact-state verification and save share a callback.
                # Allowing another event-loop interval here would permit user
                # playback/input to change the verified frame before saving.
                return finish()
            return .3
        except Exception:
            return finish(traceback.format_exc())

    if synchronous:
        if not bpy.app.background:
            raise ValueError("Synchronous checks are restricted to background integration")
        while not state["done"]:
            viewer.apply_pending()
            tick()
        return state["result"]
    bpy.app.timers.register(tick, first_interval=.3)
    print("NREL_DYNAMIC_REVIEW_CHECK_RUNNING", str(path), flush=True)
    return {"status": "RUNNING", "output": str(path)}
