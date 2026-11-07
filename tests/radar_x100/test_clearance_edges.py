"""Rejected geometry and temporal numerical limits preserve missing estimates."""
from dataclasses import replace
import json
import numpy as np
import pytest
from wfrl.radar_x100 import clearance
from tests.radar_x100.test_clearance import fixture, passage_fixture, update


def strict_json(value):
    return json.dumps(value, allow_nan=False, default=lambda array: array.tolist())


def test_rank_deficient_planar_returns_have_json_safe_missing_condition():
    shape, priors, _, _, vertices, triangles = fixture()
    ring = np.array([[-1., -1.], [-1., 0.], [-1., 1.], [0., 1.],
                     [1., 1.], [1., 0.], [1., -1.], [0., -1.]])
    shape = replace(shape, sections_xy_m=np.broadcast_to(ring, (len(shape.spans_m), 8, 2)))
    points = np.column_stack((np.full(40, -1.), np.full(40, .3), np.linspace(28., 59., 40)))
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    result = tracker.update(points, np.zeros(len(points)), priors, vertices, triangles,
        time_s=1., identity='plane', frame_key=1, position_std_m=.05)
    row = result['blades'][0]
    assert row['status'] == 'ILL_CONDITIONED'
    assert row['condition'] is row['clearance_m'] is None
    assert not tracker.tracks
    strict_json(result)


@pytest.mark.parametrize('bad_distance,bad_nearest', [(-1., False), (8., True), (np.inf, False)])
def test_causal_query_failure_retracts_estimate_and_does_not_report_zero_uncertainty(bad_distance, bad_nearest):
    shape, priors, points, _, vertices, triangles = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    update(tracker)
    def query(targets):
        if len(targets) != 1:
            return np.full(len(targets), 8.), targets - [8., 0., 0.]
        return [bad_distance], np.full((1, 3), np.nan) if bad_nearest else targets.copy()
    result = tracker.update(points, np.zeros(len(points)), priors, vertices, triangles,
        time_s=1+1/30, identity='source-a', frame_key=2, position_std_m=.05, tower_query=query)
    row = result['blades'][0]
    assert row['status'] == 'UNCERTAIN'
    assert row['clearance_m'] is row['clearance_std_m'] is row['tip_world'] is None
    assert not tracker.tracks
    strict_json(result)


def test_offline_query_with_nonfinite_nearest_point_leaves_prediction_unavailable():
    times, rows, priors, surfaces, queries = passage_fixture({12})
    queries[12] = lambda points: ([8.], np.full((1, 3), np.nan))
    result = clearance.track_clearance_passage(rows, priors, surfaces, times_s=times, tower_queries=queries)
    row = result[12]
    assert row['tracking_reason'] == 'UNCERTAIN' and row['estimate_kind'] == 'UNAVAILABLE'
    assert row['clearance_m'] is row['tracking_clearance_std_m'] is None
    strict_json(result)


def test_tower_contact_uses_nonzero_covariance_in_causal_and_offline_estimates():
    shape, priors, points, _, vertices, triangles = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    def query(targets):
        return (np.zeros(len(targets)) if len(targets) == 1 else np.full(len(targets), 8.)), targets.copy()
    causal = tracker.update(points, np.zeros(len(points)), priors, vertices, triangles,
        time_s=1., identity='contact', frame_key=1, position_std_m=.05, tower_query=query)['blades'][0]
    times, rows, priors, surfaces, queries = passage_fixture({12})
    queries[12] = query
    offline = clearance.track_clearance_passage(rows, priors, surfaces,
        times_s=times, tower_queries=queries)[12]
    for row in (causal, offline):
        assert row['clearance_m'] == 0. and row['clearance_std_m'] > 0
        assert row['clearance_std_m'] == pytest.approx(np.sqrt(np.linalg.eigvalsh(row['tip_covariance_local_m2']).max()))
        assert row['clearance_interval95_m'][0] == 0. < row['clearance_interval95_m'][1]
        strict_json(row)


def test_blade_switch_cannot_reuse_another_blades_track():
    shape, _, points, _, _, _ = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    update(tracker)
    changed = update(tracker, points + [0., 80., 0.], time_s=1+1/30, frame_key=2)
    assert changed['blades'][0]['clearance_m'] is None
    assert changed['blades'][1]['consecutive_frames'] == 1
    assert set(tracker.tracks) == {2}
    assert update(tracker, points + [0., 80., 0.], time_s=1+1/30, frame_key=2) is changed
    recovered = update(tracker, time_s=1+2/30, frame_key=3)
    assert recovered['blades'][0]['consecutive_frames'] == 1


def test_numerically_singular_offline_window_leaves_missing_frame_unavailable():
    _, rows, priors, surfaces, queries = passage_fixture({1})
    rows, priors, surfaces, queries = rows[:4], priors[:4], surfaces[:4], queries[:4]
    for row in rows:
        if row['tip_local'] is not None:
            row['tip_covariance_local_m2'] = np.eye(3)*1e-100
    result = clearance.track_clearance_passage(rows, priors, surfaces,
        times_s=np.arange(4)*1e-15, tower_queries=queries)
    assert result[1]['tracking_reason'] == 'UNCERTAIN'
    assert result[1]['tip_world'] is result[1]['clearance_m'] is None
    for i in (0, 2, 3):
        assert result[i]['clearance_m'] == rows[i]['clearance_m']
    strict_json(result)
