"""Analytic shape recovery, Doppler ambiguity, uncertainty and causal validity."""
from dataclasses import replace
import numpy as np
import pytest
from wfrl.radar_x100 import clearance
from wfrl.radar_x100.tracking import BladePrior


def fixture():
    z = np.linspace(0., 60., 31)
    theta = np.linspace(0., 2*np.pi, 48, endpoint=False)
    radius = .8-.006*z
    rings = np.stack((radius[:, None]*np.cos(theta), 2*radius[:, None]*np.sin(theta)), axis=-1)
    shape = clearance.BladeShape(z, rings, np.zeros((len(z), 2)), [0., 0., 60.], 'analytic unloaded elliptic loft')
    priors = []
    for b in range(3):
        matrix = np.eye(4)
        matrix[1, 3] = b*80.
        priors.append(BladePrior(b+1, matrix, 60.))
    # Only the illuminated half of the loft is observed. The axial contraction
    # is calculated analytically, independently of the estimator's integration.
    span = np.repeat(np.linspace(28., 59., 45), 12)
    angle = np.tile(np.linspace(np.pi/2, 3*np.pi/2, 12), 45)
    u = span/60.
    a, b = np.array([.7, -.2]), np.array([.8, .3])
    shift = -(4/3*np.dot(a, a)*u**3 + 3*np.dot(a, b)*u**4 + 9/5*np.dot(b, b)*u**5)/120.
    xy = np.column_stack(((.8-.006*span)*np.cos(angle), 2*(.8-.006*span)*np.sin(angle)))
    points = np.column_stack((xy+u[:, None]**2*a+u[:, None]**3*b, span+shift))
    truth = np.r_[a+b, 60.-(4/3*np.dot(a, a)+3*np.dot(a, b)+9/5*np.dot(b, b))/120.]
    vertices = np.array([[-8., -300., -10.], [-8., 300., -10.], [-8., 300., 90.], [-8., -300., 90.]])
    triangles = [[0, 1, 2], [0, 2, 3]]
    return shape, priors, points, truth, vertices, triangles


def update(tracker, points=None, *, time_s=1., identity='source-a', frame_key=1, velocity=0.):
    _, priors, generated, _, vertices, triangles = fixture()
    points = generated if points is None else points
    return tracker.update(points, np.full(len(points), velocity), priors, vertices, triangles,
        time_s=time_s, identity=identity, frame_key=frame_key, position_std_m=.05,
        observed_power_w=np.ones(len(points)), observed_snr_linear=np.full(len(points), 30.))


def test_one_sided_returns_recover_structural_tip_and_tower_clearance():
    shape, _, _, truth, _, _ = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    result = update(tracker)
    row = result['blades'][0]
    assert row['status'] == 'SUPPORTED'
    np.testing.assert_allclose(row['tip_local'], truth, atol=.035)
    assert abs(row['clearance_m']-(8+truth[0])) < .035
    assert row['clearance_interval95_m'][0] < 8+truth[0] < row['clearance_interval95_m'][1]
    assert row['clearance_std_m'] >= tracker.config.model_std_m
    assert np.linalg.eigvalsh(row['tip_covariance_local_m2']).min() > 0
    assert all(r['clearance_m'] is None for r in result['blades'][1:])


def test_zero_doppler_blade_and_stationary_tower_are_separated_without_labels():
    _, priors, points, _, _, _ = fixture()
    tower = np.column_stack((np.full(20, -8.), np.linspace(-1, 1, 20), np.linspace(20, 50, 20)))
    isolated = [[5., 7., 40.]]
    points = np.vstack((points, tower, isolated, [np.nan, 0, 0]))
    velocity = np.zeros(len(points))
    velocity[-2] = 15.
    distance = np.abs(points[:, 0]+8.)
    result = clearance.separate_echoes(points, velocity, distance, priors, position_std_m=.05)
    assert np.all(result['labels'][:-22] == clearance.BLADE)
    assert np.all(result['labels'][-22:-2] == clearance.TOWER)
    assert result['labels'][-2:].tolist() == [clearance.OTHER, clearance.OTHER]
    assert result['blade_assignment'][-22:].tolist() == [0]*22


def test_moving_near_tower_ambiguity_and_sensor_motion_do_not_become_blades():
    _, priors, _, _, _, _ = fixture()
    points = np.column_stack((np.full(10, -8.), np.zeros(10), np.linspace(20, 21, 10)))
    speeds = np.full(10, .4)
    speeds[-1] = 2.
    result = clearance.separate_echoes(points, speeds, np.full(10, .03), priors)
    assert result['static_radial_velocity_m_s'] == .4
    assert result['labels'][:-1].tolist() == [clearance.TOWER]*9
    assert result['labels'][-1] == clearance.OTHER


def test_unknown_doppler_quality_and_nonfinite_points_are_not_fabricated():
    _, priors, points, _, _, _ = fixture()
    speeds = np.ones(len(points))
    speeds[0] = np.nan
    power = np.ones(len(points))
    power[1] = np.nan
    snr = np.full(len(points), 30.)
    snr[2] = 1.
    result = clearance.separate_echoes(points, speeds, points[:, 0]+8., priors,
        observed_power_w=power, observed_snr_linear=snr)
    assert result['labels'][:3].tolist() == [clearance.OTHER]*3
    assert np.all(result['labels'][3:] == clearance.BLADE)


def test_duplicate_root_corridors_are_ambiguous():
    _, priors, points, _, _, _ = fixture()
    priors[1] = replace(priors[0], blade=2)
    result = clearance.separate_echoes(points, np.ones(len(points)), points[:, 0]+8., priors)
    assert result['ambiguous_points'] == len(points)
    assert not np.any(result['labels'] == clearance.BLADE)


def test_empty_frame_retracts_estimate_and_temporal_support():
    shape, _, points, _, _, _ = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    first = update(tracker)
    assert update(tracker) is first
    second = update(tracker, time_s=1+1/30, frame_key=2)
    assert second['blades'][0]['consecutive_frames'] == 2
    empty = update(tracker, np.empty((0, 3)), time_s=1+2/30, frame_key=3)
    assert all(r['clearance_m'] is None for r in empty['blades'])
    assert not tracker.tracks
    recovered = update(tracker, points, time_s=1+3/30, frame_key=4)
    assert recovered['blades'][0]['consecutive_frames'] == 1


@pytest.mark.parametrize('stamp,identity,key', [(.9, 'source-a', 2), (2., 'source-a', 2),
    (1+1/30, 'source-b', 2), (1., 'source-a', 2)])
def test_history_resets_at_identity_clock_and_gap_boundaries(stamp, identity, key):
    shape, *_ = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    update(tracker)
    row = update(tracker, time_s=stamp, identity=identity, frame_key=key)['blades'][0]
    assert row['consecutive_frames'] == 1


def test_observation_span_and_uncertainty_gate_never_hold_a_previous_tip():
    shape, _, points, _, _, _ = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    update(tracker)
    short = update(tracker, points[points[:, 2] < 48.], time_s=1+1/30, frame_key=2)['blades'][0]
    assert short['status'] == 'EXTRAPOLATION_TOO_FAR' and short['clearance_m'] is None
    thin = update(tracker, points[points[:, 2] > 58.], time_s=1+2/30, frame_key=3)['blades'][0]
    assert thin['status'] == 'INSUFFICIENT_SPAN' and thin['tip_world'] is None
    uncertain = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape),
        replace(clearance.ClearanceConfig(), maximum_clearance_std_m=.05))
    row = update(uncertain)['blades'][0]
    assert row['status'] == 'UNCERTAIN'
    assert row['tip_world'] is row['clearance_m'] is row['clearance_interval95_m'] is None


def test_root_coordinates_avoid_rotation_lag_and_preserve_rigid_equivariance():
    shape, priors, points, _, vertices, triangles = fixture()
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    first = update(tracker)['blades'][0]
    angle = .3
    rotation = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
    offset = np.array([1e5, -2e5, 5e5])
    moved = []
    for p in priors:
        m = p.world_from_blade.copy()
        m[:3, 3] = rotation@m[:3, 3]+offset
        m[:3, :3] = rotation@m[:3, :3]
        moved.append(replace(p, world_from_blade=m))
    second = tracker.update(points@rotation.T+offset, np.zeros(len(points)), moved,
        vertices@rotation.T+offset, triangles, time_s=1+1/30, identity='source-a', frame_key=2,
        position_std_m=.05)['blades'][0]
    np.testing.assert_allclose(second['tip_world'], rotation@first['tip_world']+offset, atol=1e-6)
    assert abs(second['clearance_m']-first['clearance_m']) < 1e-6


def test_input_contracts_reject_mismatched_priors_and_bad_uncertainty():
    shape, priors, points, _, vertices, triangles = fixture()
    with pytest.raises(ValueError):
        replace(clearance.ClearanceConfig(), max_gap_s=0.)
    with pytest.raises(ValueError):
        replace(clearance.ClearanceConfig(), minimum_neighbours=2.5)
    with pytest.raises(ValueError):
        replace(shape, spans_m=shape.spans_m[::-1])
    with pytest.raises(ValueError):
        shape.tip_local_m[0] = 12.
    with pytest.raises(ValueError):
        clearance.separate_echoes(points, np.zeros(len(points)), points[:, 0]+8., priors, position_std_m=np.nan)
    tracker = clearance.ClearanceTracker(dict.fromkeys((1, 2, 3), shape))
    priors[0] = replace(priors[0], tip_span_m=65.)
    with pytest.raises(ValueError):
        tracker.update(points, np.zeros(len(points)), priors, vertices, triangles,
            time_s=1., identity='a', frame_key=1)
