"""Finite native-window review playback checks, without render or video output.

The measurements here are instrumented main-thread commits and POST_PIXEL
callbacks. Movie ImageUser indices do not establish decoded movie pixels, and
draw callbacks do not establish monitor scanout or a displayed FPS guarantee.
Pure evidence helpers remain importable without Blender for targeted tests.
"""
import json
from pathlib import Path
import time
import traceback


CAMERAS = ("C1", "C2", "C3")
ROUTES = (*CAMERAS, "MODEL")


def timing_summary(values):
    values = sorted(float(value) for value in values)
    def percentile(fraction):
        if not values:
            return None
        index = (len(values) - 1) * fraction
        low = int(index)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (index - low)
    return {"count": len(values), "p50_ms": percentile(.5), "p95_ms": percentile(.95),
            "max_ms": values[-1] if values else None}


def identity_failures(snapshot):
    """Validate a committed draw identity; requested pending states are isolated."""
    failures = []
    if snapshot.get("package_available") is False:
        if (snapshot.get("readout_frame_id") is not None or snapshot.get("geometry_visible")
                or snapshot.get("object_count") or snapshot.get("movie_bound_cameras")):
            failures.append("unloaded package retains readout, geometry or movie bindings")
        return failures
    if any(snapshot.get("movie_auto_refresh", {}).values()):
        failures.append("movie automatic refresh is enabled")
    if snapshot.get("pending"):
        if snapshot.get("readout_frame_id") is not None or snapshot.get("geometry_visible"):
            failures.append("pending request exposes an old readout or geometry")
        if not snapshot.get("all_geometry_effectively_hidden"):
            failures.append("pending request does not suppress actual geometry")
        if snapshot.get("hud_identity") is not None:
            failures.append("pending request exposes a committed HUD identity")
        # Blender may change ImageUser requests while an external timeline
        # event awaits the owning timer. The unavailable state must isolate
        # geometry/readout; only a published commit claims shared identity.
        return failures
    applied = snapshot.get("applied_identity")
    if not applied or len(applied) != 2:
        return failures + ["missing applied frame/group identity"]
    frame, group = applied
    if ("movie_bound_cameras" in snapshot
            and set(snapshot["movie_bound_cameras"]) != set(CAMERAS)):
        failures.append("movie editors differ from the signed package bindings")
    if set(snapshot.get("movie_frames", {})) != set(CAMERAS):
        failures.append("three movie ImageUser bindings are missing")
    elif any(index != frame for index in snapshot["movie_frames"].values()):
        failures.append("movie ImageUser indices differ from committed frame")
    if not snapshot.get("pending"):
        if (snapshot.get("scene_frame"), snapshot.get("scene_group")) != (frame, group):
            failures.append("scene differs from committed frame/group")
        if tuple(snapshot.get("hud_identity") or ()) != (frame, group):
            failures.append("HUD differs from committed frame/group")
        available = snapshot.get("available")
        if snapshot.get("geometry_visible") != available:
            failures.append("geometry availability differs from frozen estimate")
        if available:
            if snapshot.get("readout_frame_id") != frame - 1 or snapshot.get("readout_group") != group:
                failures.append("readout differs from committed frame/group")
            if snapshot.get("any_geometry_effectively_hidden"):
                failures.append("available estimate geometry is hidden")
        elif snapshot.get("readout_frame_id") is not None or not snapshot.get("all_geometry_effectively_hidden"):
            failures.append("unavailable frame exposes an old readout or geometry")
        for identity in snapshot.get("geometry_identities", {}).values():
            if tuple(identity) != (frame, ("G1", "G2", "G3").index(group)):
                failures.append("geometry applied properties differ from committed frame/group")
                break
    return failures


def playback_summary(events, draws, *, started, ended, start_frame=1, end_frame=100, fps=20.):
    """Require the declared no-skip committed sequence independently of speed."""
    progression = [event for event in events if event.get("reason") == "playback"]
    frames = [event["scene_frame"] for event in progression]
    expected = list(range(start_frame + 1, end_frame + 1))
    wall_s = ended - started
    routes = {}
    for route in ROUTES:
        rows = [row for row in draws if row["route"] == route and started <= row["monotonic_s"] <= ended]
        intervals = [(b["monotonic_s"] - a["monotonic_s"]) * 1000 for a, b in zip(rows, rows[1:])]
        observed = sorted({row["applied_identity"][0] for row in rows if row.get("applied_identity")})
        routes[route] = {"draw_callbacks": len(rows), "committed_frames_observed_at_draw": observed,
                         "all_committed_frames_observed_at_draw": set(expected).issubset(observed),
                         "callback_intervals": timing_summary(intervals),
                         "callback_rate_hz": len(rows) / wall_s if wall_s > 0 else None}
    return {"no_skip_committed_sequence": frames == expected, "committed_sequence": frames,
            "target_frame_interval": [start_frame, end_frame], "target_source_fps": fps,
            "source_progress_s": (end_frame - start_frame) / fps, "wall_s": wall_s,
            "source_progress_per_wall_s": (end_frame - start_frame) / fps / wall_s if wall_s > 0 else None,
            "main_thread_commit_time": timing_summary([row["elapsed_ms"] for row in progression]),
            "observer_verification_time": timing_summary([row.get("observer_ms", 0.) for row in progression]),
            "routes": routes,
            "evidence_scope": "instrumented main-thread commits and POST_PIXEL callbacks; no movie pixel, scanout, or displayed FPS proof"}


def run_checks(viewer, output):
    import bpy
    import numpy as np

    root = Path(output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "playback_check.json"
    progress_path = root / "playback_progress.json"
    if path.exists() or progress_path.exists():
        raise ValueError("Native playback check output already exists")
    if bpy.app.background:
        raise ValueError("Playback checks require a native window event loop")
    scene = bpy.context.scene
    package_path = scene.nrel_review_path
    package = viewer.ACTIVE["package"]
    initial_manifest_sha256 = package.manifest_sha256
    state = {"started": time.monotonic(), "phase": -1, "phase_started": None,
             "done": False, "commits": [], "draws": [], "phases": [], "failures": [],
             "runs": [], "active_run": None, "handlers": [], "control_notes": {}}
    viewer.ACTIVE["checks_complete"] = False

    def effective_hidden(obj):
        # Drivers guarding pending requests are evaluated on the depsgraph;
        # original hide_get alone cannot establish their visible state.
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return bool(obj.hide_get() or obj.hide_viewport or evaluated.hide_viewport)

    def snapshot():
        active = viewer.ACTIVE
        runtime = viewer.playback_snapshot()
        current_package = active["package"]
        applied = active["last_applied_frame"]
        value = active["value"] if not active["pending"] else None
        hidden = [effective_hidden(obj) for obj in active["objects"].values()]
        available = bool(current_package.at_scene_frame(applied[0], applied[1])) if applied and current_package else False
        hud_identity = runtime.get("hud_identity")
        return {"monotonic_s": time.monotonic(), "scene_frame": scene.frame_current,
                "scene_group": scene.nrel_review_group, "applied_identity": list(applied) if applied else None,
                "hud_identity": list(hud_identity) if hud_identity else None,
                "commit_id": active["commit_id"], "pending": active["pending"],
                "playing": active["playing"],
                "native_animation_playing": any(window.screen.is_animation_playing for window in bpy.context.window_manager.windows),
                "movie_frames": {camera: area.spaces.active.image_user.frame_current for camera, area in active["movie_areas"].items()},
                "movie_auto_refresh": {camera: area.spaces.active.image_user.use_auto_refresh for camera, area in active["movie_areas"].items()},
                "movie_bound_cameras": [camera for camera, area in active["movie_areas"].items()
                    if camera in active["movies"] and area.spaces.active.image == active["movies"][camera]],
                "package_available": current_package is not None, "object_count": len(hidden),
                "geometry_identities": runtime["geometry_identities"],
                "available": available, "geometry_visible": active["geometry_visible"],
                "all_geometry_effectively_hidden": bool(hidden) and all(hidden),
                "any_geometry_effectively_hidden": any(hidden),
                "readout_frame_id": value["record"]["frame_id"] if value is not None else None,
                "readout_group": next((group for group in ("G1", "G2", "G3")
                    if value is not None and current_package is not None
                    and value["record"] is current_package.records[group].get(value["record"]["frame_id"])), None),
                "distance_m": value["record"]["d_m"] if value is not None else None,
                "runtime": runtime}

    def failure(where, message, observed=None):
        state["failures"].append({"where": where, "message": message, "snapshot": observed})

    def check_identity(where, observed):
        for message in identity_failures(observed):
            failure(where, message, observed)
        if state["active_run"] is not None and observed["native_animation_playing"]:
            failure(where, "native animation is active during controlled full playback", observed)

    def committed(event):
        started = time.monotonic()
        try:
            observed = snapshot()
            check_identity("commit", observed)
            current_package = viewer.ACTIVE["package"]
            if current_package is None:
                expected = None
            else:
                assert current_package.manifest_sha256 == initial_manifest_sha256, "restored manifest differs from signed original"
                frame, group = observed["applied_identity"]
                expected = current_package.at_scene_frame(frame, group)
            if expected is not None:
                actual = np.empty(len(viewer.ACTIVE["objects"]["surface"].data.vertices) * 3, dtype=np.float32)
                viewer.ACTIVE["objects"]["surface"].data.vertices.foreach_get("co", actual)
                reference = expected["vertices_world"].astype(np.float32).ravel()
                assert np.array_equal(actual, reference), "surface vertices differ from saved float32 estimate"
                for key, field in (("point", "point_world_m"), ("foot", "foot_world_m")):
                    assert np.array_equal(np.asarray(viewer.ACTIVE["objects"][key].location, dtype=np.float32),
                        np.asarray(expected["record"][field], dtype=np.float32)), key + " differs from saved estimate"
                assert viewer.ACTIVE["value"]["record"] is current_package.records[group][frame - 1]
            row = dict(event, observation=observed, observer_ms=(time.monotonic() - started) * 1000,
                       run=state["active_run"])
            state["commits"].append(row)
        except Exception:
            failure("commit observer", traceback.format_exc())

    def draw(route):
        if state["done"]:
            return
        area = bpy.context.area
        expected = viewer.ACTIVE["view"] if route == "MODEL" else viewer.ACTIVE["movie_areas"].get(route)
        if area != expected:
            return
        try:
            observed = snapshot()
            check_identity("POST_PIXEL " + route, observed)
            state["draws"].append(dict(observed, route=route, run=state["active_run"]))
        except Exception:
            failure("POST_PIXEL " + route, traceback.format_exc())

    def write_progress():
        progress_path.write_text(json.dumps({"status": "RUNNING", "phase": state["phase"],
            "phase_name": phases[state["phase"]]["name"] if state["phase"] >= 0 else "setup",
            "elapsed_s": time.monotonic() - state["started"], "commit_count": len(state["commits"]),
            "draw_count": len(state["draws"]), "failure_count": len(state["failures"])}, indent=2) + "\n")

    def settled(frame, group="G3"):
        return (not viewer.ACTIVE["pending"] and viewer.ACTIVE["last_applied_frame"] == (frame, group)
                and scene.frame_current == frame and scene.nrel_review_group == group)

    def operator(name, **kwargs):
        result = getattr(bpy.ops.nrel_review, name)(**kwargs)
        assert result == {"FINISHED"}, (name, result)

    def seek(frame):
        operator("seek", frame=frame)

    def group(name):
        scene.nrel_review_group = name

    def toggle():
        operator("play_pause")

    def stopped():
        return not viewer.ACTIVE["playing"]

    phases = []
    def phase(name, action=lambda: None, ready=lambda: True, *, hold=0., timeout=5., verify=None):
        phases.append({"name": name, "action": action, "ready": ready, "hold": hold,
                       "timeout": timeout, "verify": verify})

    def record_pause():
        state["pause_identity"] = (scene.frame_current, scene.nrel_review_group, viewer.ACTIVE["commit_id"])

    def verify_pause():
        assert stopped()
        assert state["pause_identity"] == (scene.frame_current, scene.nrel_review_group, viewer.ACTIVE["commit_id"]), "paused frame/group/commit changed"

    phase("initial exact paused state", ready=lambda: settled(44), verify=lambda: (assert_stopped()))
    for frame in (46, 43, 47, 46):
        phase("seek " + str(frame), action=lambda frame=frame: seek(frame), ready=lambda frame=frame: settled(frame))
    for name in ("G1", "G2", "G3"):
        phase("group " + name, action=lambda name=name: group(name), ready=lambda name=name: settled(46, name))
    phase("play from saved frame", action=lambda: (seek(43)), ready=lambda: settled(43))
    phase("play then pause", action=toggle, ready=lambda: scene.frame_current >= 45 and not viewer.ACTIVE["pending"],
          verify=lambda: toggle())
    phase("paused hold", action=record_pause, hold=.3, verify=verify_pause)
    phase("play before step", action=toggle, ready=lambda: viewer.ACTIVE["playing"])
    phase("step pauses active playback", action=lambda: operator("step", delta=-1),
          ready=lambda: stopped() and not viewer.ACTIVE["pending"])
    phase("seek pauses active playback", action=lambda: (toggle(), seek(44)), ready=lambda: stopped() and settled(44))
    phase("group pauses active playback", action=lambda: (toggle(), group("G2")), ready=lambda: stopped() and settled(44, "G2"))
    phase("rapid seek and group keeps final request", action=lambda: (seek(43), seek(46), group("G1"), group("G3"), seek(44)),
          ready=lambda: stopped() and settled(44))
    phase("external timeline request unavailable", action=lambda: scene.frame_set(47), ready=lambda: settled(47))
    phase("external timeline request restores exact frame", action=lambda: scene.frame_set(44), ready=lambda: settled(44))

    def start_native_animation():
        area = viewer.ACTIVE["view"]
        region = next(region for region in area.regions if region.type == "WINDOW")
        with bpy.context.temp_override(window=bpy.context.window, area=area, region=region):
            assert bpy.ops.screen.animation_play() == {"FINISHED"}
        state["control_notes"]["native_animation_was_started"] = any(window.screen.is_animation_playing for window in bpy.context.window_manager.windows)
        assert state["control_notes"]["native_animation_was_started"]
    phase("native animation conflict taken over by controlled playback", action=start_native_animation,
          ready=lambda: not any(window.screen.is_animation_playing for window in bpy.context.window_manager.windows)
          and not viewer.ACTIVE["pending"] and viewer.ACTIVE["playing"], verify=toggle)
    phase("restore after native animation conflict", action=lambda: seek(44), ready=lambda: settled(44))

    def invalid_reload():
        fixture = root / "playback-invalid-package-fixture"
        fixture.mkdir(exist_ok=False)
        (fixture / "manifest.json").write_text("{}\n", encoding="utf-8")
        scene.nrel_review_path = str(fixture)
        toggle()
        operator("reload")
    def verify_invalid():
        assert stopped() and viewer.ACTIVE["package"] is None and viewer.ACTIVE["value"] is None
        assert not viewer.ACTIVE["objects"] and not viewer.ACTIVE["movies"]
        assert all(area.spaces.active.image is None for area in viewer.ACTIVE["movie_areas"].values())
    phase("invalid reload during playback clears movies geometry readout", action=invalid_reload,
          ready=lambda: viewer.ACTIVE["package"] is None and viewer.ACTIVE["load_request"] is None,
          verify=verify_invalid)
    def valid_reload():
        scene.nrel_review_path = package_path
        operator("reload")
    def set_restored_package():
        nonlocal package
        package = viewer.ACTIVE["package"]
    phase("valid reload restores signed package", action=valid_reload,
          ready=lambda: viewer.ACTIVE["package"] is not None and not viewer.ACTIVE["pending"], verify=set_restored_package)

    def assert_stopped():
        assert stopped(), "controlled playback still active"

    for run in (1, 2):
        phase(f"run {run} prepare 1", action=lambda: (group("G3"), seek(1)), ready=lambda: settled(1))
        def run_start(run=run):
            state["active_run"] = run
            state["run_started"] = time.monotonic()
            toggle()
            assert viewer.ACTIVE["playing"]
        def run_finish(run=run):
            ended = time.monotonic()
            events = [row for row in state["commits"] if row["run"] == run]
            draws = [row for row in state["draws"] if row["run"] == run]
            summary = playback_summary(events, draws, started=state["run_started"], ended=ended)
            summary["run"] = run
            assert summary["no_skip_committed_sequence"], "full controlled playback skipped or repeated committed frames"
            assert all(route["all_committed_frames_observed_at_draw"] for route in summary["routes"].values()), "a route lacks POST_PIXEL evidence for a committed playback frame"
            state["runs"].append(summary)
            state["active_run"] = None
            record_pause()
        phase(f"run {run} full controlled playback 1 to 100", action=run_start,
              ready=lambda: stopped() and settled(100) and viewer.display_step_drawn()
                  and all(any(row["route"] == route and row["run"] == state["active_run"]
                  and row["applied_identity"][0] == 100 for row in state["draws"]) for route in ROUTES),
              timeout=25., verify=run_finish)
        phase(f"run {run} terminal clamp paused hold", hold=.3, verify=verify_pause)
    phase("terminal step clamps at 100", action=lambda: operator("step", delta=1), ready=lambda: settled(100), verify=assert_stopped)
    phase("final G3 saved frame 44", action=lambda: seek(44), ready=lambda: settled(44), verify=assert_stopped)

    def finish(error=None):
        state["done"] = True
        viewer.playback_pause()
        if committed in viewer.ACTIVE["commit_observers"]:
            viewer.ACTIVE["commit_observers"].remove(committed)
        for space_type, handle in state["handlers"]:
            space_type.draw_handler_remove(handle, "WINDOW")
        failures = state["failures"]
        if error:
            failure("native checker", error)
        replay = {}
        if len(state["runs"]) == 2:
            replay["same_committed_frame_sequence"] = state["runs"][0]["committed_sequence"] == state["runs"][1]["committed_sequence"]
            histories = [[(row["scene_frame"], row["group"], row["observation"]["distance_m"], row["observation"]["available"])
                for row in state["commits"] if row["run"] == run and row.get("reason") == "playback"] for run in (1, 2)]
            replay["same_saved_measurement_and_availability_sequence"] = histories[0] == histories[1]
            if not all(replay.values()):
                failure("replay", "repeat playback identities or saved measurements differed")
        if not failures:
            try:
                viewer.focus_tip()
                viewer.save_review_blend()
            except Exception:
                failure("final save", traceback.format_exc())
        result = {"status": "ERROR" if error else "FAIL" if failures else "PASS",
                  "blender_version": bpy.app.version_string, "execution": "native window timer phases",
                  "elapsed_s": time.monotonic() - state["started"], "timeout_s": 90.,
                  "completed_phases": state["phases"], "runs": state["runs"], "repeat_playback": replay,
                  "controls": state["control_notes"], "commits": state["commits"], "post_pixel_draws": state["draws"],
                  "failures": failures, "final_state": snapshot(),
                  "scope": "REVIEW_ONLY / PRIOR_DEFINED_STRUCTURAL_POINT; DY1 not passed",
                  "evidence_scope": "ImageUser RNA identities, saved geometry and readout, instrumented main-thread timing and POST_PIXEL callbacks; decoded movie pixels require separate CUA evidence; no scanout/FPS/field accuracy acceptance"}
        with path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        viewer.ACTIVE["checks_complete"] = True
        print("NREL_DYNAMIC_PLAYBACK_CHECK", result["status"], str(path), flush=True)
        return None

    def tick():
        try:
            now = time.monotonic()
            if now - state["started"] > 90.:
                raise TimeoutError("Finite native playback checks exceeded 90 seconds")
            if state["failures"]:
                return finish()
            current = phases[state["phase"]] if state["phase"] >= 0 else None
            if current is not None:
                if now - state["phase_started"] > current["timeout"]:
                    raise TimeoutError("Native playback phase exceeded deadline: " + current["name"])
                if not current["ready"]() or now - state["phase_started"] < current["hold"]:
                    return .02
                if current["verify"]:
                    current["verify"]()
                observed = snapshot()
                if viewer.ACTIVE["package"] is not None:
                    check_identity("phase " + current["name"], observed)
                state["phases"].append({"name": current["name"], "elapsed_s": now - state["phase_started"], "state": observed})
            state["phase"] += 1
            if state["phase"] == len(phases):
                return finish()
            state["phase_started"] = time.monotonic()
            phases[state["phase"]]["action"]()
            write_progress()
            return .02
        except Exception:
            return finish(traceback.format_exc())

    try:
        viewer.ACTIVE["commit_observers"].append(committed)
        for route in ROUTES:
            space_type = bpy.types.SpaceView3D if route == "MODEL" else bpy.types.SpaceImageEditor
            handle = space_type.draw_handler_add(draw, (route,), "WINDOW", "POST_PIXEL")
            state["handlers"].append((space_type, handle))
        bpy.app.timers.register(tick, first_interval=.1)
        print("NREL_DYNAMIC_PLAYBACK_CHECK_RUNNING", str(path), flush=True)
        return {"status": "RUNNING", "output": str(path)}
    except Exception:
        return finish(traceback.format_exc())
