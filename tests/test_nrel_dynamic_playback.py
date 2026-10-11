"""Evidence contract checks using synthetic rows; no Blender or movie pixels."""
import copy
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dynamic_playback_evidence", ROOT / "scripts/blender/check_nrel_dynamic_playback.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


def stable(frame=44, group="G3", available=True):
    return {"applied_identity": [frame, group], "scene_frame": frame, "scene_group": group,
            "hud_identity": (frame, group), "pending": False, "geometry_visible": available,
            "available": available, "movie_frames": {camera: frame for camera in checks.CAMERAS},
            "movie_auto_refresh": {camera: False for camera in checks.CAMERAS},
            "readout_frame_id": frame - 1 if available else None,
            "readout_group": group if available else None,
            "all_geometry_effectively_hidden": not available, "any_geometry_effectively_hidden": not available,
            "geometry_identities": {"surface": [frame, ("G1", "G2", "G3").index(group)]}}


def test_stable_commit_uses_shared_frame_group_and_tuple_hud():
    assert checks.identity_failures(stable()) == []
    assert checks.identity_failures(stable(47, available=False)) == []


@pytest.mark.parametrize("edit", [
    {"movie_frames": {"C1": 43, "C2": 44, "C3": 44}},
    {"readout_frame_id": 44}, {"hud_identity": [45, "G3"]},
    {"geometry_identities": {"surface": [44, 1]}},
    {"movie_bound_cameras": ["C1", "C2"]},
    {"movie_auto_refresh": {"C1": True}},
])
def test_commit_rejects_mixed_frames_or_group_and_auto_refresh(edit):
    observed = stable()
    observed.update(edit)
    assert checks.identity_failures(observed)


def test_pending_external_request_can_change_movie_request_but_hides_old_estimate():
    observed = stable()
    observed.update(pending=True, scene_frame=47, movie_frames={camera: 47 for camera in checks.CAMERAS},
                    hud_identity=None, readout_frame_id=None, geometry_visible=False,
                    all_geometry_effectively_hidden=True)
    assert checks.identity_failures(observed) == []
    observed["all_geometry_effectively_hidden"] = False
    assert "pending request does not suppress actual geometry" in checks.identity_failures(observed)


def test_unloaded_package_must_clear_geometry_movie_bindings_and_readout():
    observed = {"package_available": False, "readout_frame_id": None, "geometry_visible": False,
                "object_count": 0, "movie_bound_cameras": []}
    assert checks.identity_failures(observed) == []
    observed["movie_bound_cameras"] = ["C1"]
    assert checks.identity_failures(observed)


def evidence():
    events = [{"reason": "playback", "scene_frame": frame, "elapsed_ms": 1., "observer_ms": .2}
              for frame in range(2, 101)]
    draws = [{"route": route, "monotonic_s": frame / 20., "applied_identity": [frame, "G3"]}
             for route in checks.ROUTES for frame in range(2, 101)]
    return events, draws


def test_full_no_skip_commit_and_per_route_draw_coverage_is_separate_from_speed():
    events, draws = evidence()
    result = checks.playback_summary(events, draws, started=0., ended=10.)
    assert result["no_skip_committed_sequence"]
    assert result["source_progress_per_wall_s"] == .495
    assert all(route["all_committed_frames_observed_at_draw"] for route in result["routes"].values())
    assert result["main_thread_commit_time"]["count"] == 99


@pytest.mark.parametrize("change", ["skip", "duplicate", "missing_terminal_draw"])
def test_partial_evidence_cannot_be_reported_as_full_commits_and_draws(change):
    events, draws = evidence()
    if change == "skip":
        del events[42]
    elif change == "duplicate":
        events.insert(42, copy.deepcopy(events[42]))
    else:
        draws = [row for row in draws if not (row["route"] == "C3" and row["applied_identity"][0] == 100)]
    result = checks.playback_summary(events, draws, started=0., ended=10.)
    if change == "missing_terminal_draw":
        assert result["no_skip_committed_sequence"]
        assert not result["routes"]["C3"]["all_committed_frames_observed_at_draw"]
    else:
        assert not result["no_skip_committed_sequence"]
