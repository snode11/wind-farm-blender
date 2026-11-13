"""Rejected samples preserve time state and missing samples split trails."""

import numpy as np
import pytest

from wfrl.viz.tip_geometry import CameraModel, TurbineGeometry
from wfrl.viz.tip_tracking import TipTracker


@pytest.fixture
def tracker():
    geom = TurbineGeometry(110, 5, 5, 68, 2)
    camera = CameraModel.look_at(640, 392, 49.2, 31.36,
                                geom.hub + [-8, 0, -3], geom.tip_position(180),
                                geom.e_lat, upright=True)
    return TipTracker(geom, camera)


def world_sample(tracker, phase=180):
    point = tracker.geom.tip_position(phase)
    return {0: {"P": point, "sections": np.array([tracker.geom.hub, point])}}


def world_update(tracker, blades, t, **kwargs):
    return tracker.update_world(blades, t=t, source="test", provenance={"test": True}, **kwargs)


@pytest.mark.parametrize("mode", ["pixels", "world"])
@pytest.mark.parametrize("invalid_t", [0.5, 2])
def test_cp_rejected_blade_ids_do_not_consume_or_reset_time(tracker, mode, invalid_t):
    if mode == "pixels":
        point = tracker.cam.project(tracker.geom.tip_position(180))[:2]
        update = lambda blades, t: tracker.update_pixels(blades, t=t)
        valid = {0: point}
    else:
        update = lambda blades, t: world_update(tracker, blades, t)
        valid = world_sample(tracker)
    update(valid, 1)
    previous = tracker.results
    with pytest.raises(ValueError):
        update({3: valid[0]}, invalid_t)
    assert tracker._last_t == 1
    assert tracker.results is previous
    assert len(tracker.trajs[0]) == 1
    assert 0 in update(valid, invalid_t)


def test_cp_pixel_sequence_accepts_missing_blade_without_losing_other_blades(tracker):
    point = tracker.cam.project(tracker.geom.tip_position(180))[:2]
    tracker.update_pixels([point, point, point], t=0)
    tracker.update_pixels([point, None, point], t=1)
    assert set(tracker.results) == {0, 2}
    assert tracker.trajs[1][-1] is None
    tracker.update_pixels([point, point, point], t=2)
    assert tracker.trajs[1][1] is None


@pytest.mark.parametrize("blade_id", [np.bool_(True), None, np.inf])
def test_cp_invalid_pixel_ids_raise_value_error_without_advancing(tracker, blade_id):
    with pytest.raises(ValueError, match="Blade IDs"):
        tracker.update_pixels({blade_id: [320, 200]}, t=1)
    assert tracker._last_t is None


def test_cp_world_sections_must_end_at_tip_with_absolute_tolerance(tracker):
    blades = world_sample(tracker)
    blades[0]["sections"][-1, 2] += 0.0003
    assert world_update(tracker, blades, 0) == {}
    assert "Sections" in tracker.errors[0]
    assert not tracker.sections and not tracker.tips


def test_cp_masked_sections_are_missing_data(tracker):
    blades = world_sample(tracker)
    blades[0]["sections"] = np.ma.array(blades[0]["sections"], mask=[[True, False, False],
                                                                 [False, False, False]])
    assert world_update(tracker, blades, 0) == {}
    assert 0 in tracker.errors and not tracker.sections


def test_cp_masked_single_pixel_is_not_unmasked_by_normalization(tracker):
    point = tracker.cam.project(tracker.geom.tip_position(180))[:2]
    result = tracker.update_pixels(np.ma.array(point, mask=[True, False]), t=0)
    assert result == {} and 0 in tracker.errors


def test_cp_world_trails_break_on_missing_and_clear_on_rewind(tracker):
    world_update(tracker, world_sample(tracker), 0)
    world_update(tracker, {}, 1)
    world_update(tracker, world_sample(tracker, 185), 2)
    assert len(tracker.trajs[0]) == 3 and tracker.trajs[0][1] is None
    world_update(tracker, world_sample(tracker, 190), 2)
    assert len(tracker.trajs[0]) == 3
    world_update(tracker, world_sample(tracker), 0)
    assert len(tracker.trajs[0]) == 1


def test_cp_world_coordinates_and_sections_are_owned_by_tracker(tracker):
    blades = world_sample(tracker)
    point = blades[0]["P"].copy()
    world_update(tracker, blades, 0)
    blades[0]["P"][:] = 0
    blades[0]["sections"][:] = 0
    np.testing.assert_array_equal(tracker.results[0]["P"], point)
    np.testing.assert_array_equal(tracker.sections[0][-1], point)


def test_cp_world_callback_failure_is_not_retried_as_bad_input(tracker):
    class Imbalance:
        def __init__(self):
            self.calls = 0

        def feed_pass(self, *args):
            self.calls += 1
            raise ValueError("imbalance callback failed")

    tracker.imb = Imbalance()
    for t in range(5):
        world_update(tracker, world_sample(tracker), t, rpm=12)
    with pytest.raises(ValueError, match="imbalance callback failed"):
        world_update(tracker, world_sample(tracker), 5, rpm=12)
    assert tracker.imb.calls == 1
    assert tracker.errors == {}

