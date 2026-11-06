"""Analytic blade observations, target ambiguity and causal failure boundaries."""
from dataclasses import replace
import numpy as np
import pytest
from wfrl.radar_x100.tracking import BladePrior, BladeTracker, FitConfig, fit_blades
from wfrl.radar_x100.tracking import smooth_positions


def observations(angle=0., offset=0.):
    priors, points = [], []
    for blade in range(3):
        theta = angle + blade * 2 * np.pi / 3
        c, s = np.cos(theta), np.sin(theta)
        matrix = np.eye(4)
        matrix[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
        prior = BladePrior(blade + 1, matrix, 60.)
        priors.append(prior)
        z = np.repeat(np.linspace(34., 59.5, 30), 4)
        width = np.tile([-.3, -.1, .1, .3], 30)
        local = np.column_stack((.0005 * z*z + offset, width, z))
        points.extend(local @ matrix[:3, :3].T)
    return np.array(points), priors


def test_quadratic_fit_and_identity_use_observations_and_declared_poses():
    points, priors = observations(.4)
    result = fit_blades(points, priors, np.ones(len(points)))
    for row, prior in zip(result['blades'], priors):
        assert row['status'] == 'SUPPORTED' and row['points'] == 120
        np.testing.assert_allclose(row['tip_world'], prior.world_from_blade[:3, :3] @ [1.8, 0, 60], atol=.01)
    assert result['assignment'] == [1]*120 + [2]*120 + [3]*120


def test_background_ambiguity_missing_points_and_long_extrapolation_reject():
    points, priors = observations()
    assert all(r['tip_world'] is None for r in fit_blades(points, priors, np.zeros(len(points)))['blades'])
    duplicate = [priors[0], replace(priors[0], blade=2), priors[2]]
    result = fit_blades(points, duplicate, np.ones(len(points)))
    assert result['ambiguous'] >= 120 and result['blades'][0]['tip_world'] is None
    z = np.linspace(33., 55., 30)
    short = np.column_stack((.0005*z*z, np.zeros(len(z)), z))
    result = fit_blades(short, priors, np.ones(len(short)))
    assert result['blades'][0]['status'] == 'EXTRAPOLATION_TOO_FAR'
    assert result['blades'][0]['tip_world'] is None
    assert result['blades'][0]['curve_world'] is not None


def test_tracking_smooths_local_deflection_without_rotor_lag_and_never_fills_misses():
    tracker = BladeTracker()
    points, priors = observations()
    tracker.update(points, priors, np.ones(len(points)), time_s=1, identity='source-a', frame_key=0)
    points, priors = observations(.2, .1)
    second = tracker.update(points, priors, np.ones(len(points)), time_s=1+2/30, identity='source-a', frame_key=2)
    row = second['blades'][0]
    assert row['consecutive_frames'] == 2 and 1.8 < row['tip_local'][0] < 1.9
    np.testing.assert_allclose(row['tip_world'], priors[0].world_from_blade[:3, :3] @ row['tip_local'])
    assert tracker.update(points, priors, np.ones(len(points)), time_s=1+2/30, identity='source-a', frame_key=2) is second
    empty = tracker.update(np.empty((0,3)), priors, [], time_s=1+3/30, identity='source-a', frame_key=3)
    assert all(r['tip_world'] is None for r in empty['blades'])
    for stamp, identity in ((.5, 'source-a'), (2, 'source-a'), (2+1/30, 'source-b')):
        result = tracker.update(points, priors, np.ones(len(points)), time_s=stamp, identity=identity, frame_key=3)
        assert result['blades'][0]['consecutive_frames'] == 1


def test_nonfinite_and_no_span_do_not_produce_an_estimate():
    points, priors = observations()
    points[:120, 2] = 58
    points[120:] = np.nan
    result = fit_blades(points, priors, np.ones(len(points)))
    assert all(row['tip_world'] is None for row in result['blades'])
    assert result['invalid'] == 240


def test_position_velocity_smoother_recovers_motion_and_missing_prefix_without_double_counting():
    times = np.arange(20)/30
    velocity = np.array([.7, -.2, .1])
    truth = [1., 2., 60.] + times[:, None]*velocity
    missing = {0, 1, 8, 9, 17, 18, 19}
    points = [None if i in missing else p for i, p in enumerate(truth)]
    covariances = [None if p is None else np.eye(3)*1e-5 for p in points]
    states, covariance = smooth_positions(times, points, covariances,
        acceleration_noise_m2_s3=.01)
    np.testing.assert_allclose(states[:, :3], truth, atol=.002)
    np.testing.assert_allclose(states[:, 3:], np.broadcast_to(velocity, (20, 3)), atol=.01)
    assert np.linalg.eigvalsh(covariance).min() > 0
    assert covariance[19, 0, 0] > covariance[18, 0, 0] > covariance[17, 0, 0]
    single = smooth_positions([0.], [[1., 2., 3.]], [np.eye(3)*.3])
    np.testing.assert_array_equal(single[1][0, :3, :3], np.eye(3)*.3)
    assert smooth_positions([0., 1.], [None, None], [None, None]) is None


@pytest.mark.parametrize('times,positions,covariances', [
    ([0., 0.], [None, None], [None, None]),
    ([1., 0.], [None, None], [None, None]),
    ([np.nan], [None], [None]),
    ([0.], [[1., 2., 3.]], [None]),
    ([0.], [[1., 2., 3.]], [np.diag([1., 1., -1.])]),
    ([0.], [[1., np.nan, 3.]], [np.eye(3)]),
])
def test_position_smoother_rejects_bad_clock_and_covariance(times, positions, covariances):
    with pytest.raises(ValueError):
        smooth_positions(times, positions, covariances)


def test_subunit_covariance_cannot_hide_material_asymmetry_in_absolute_tolerance():
    covariance = np.array([[1e-12, 1e-9, 0.], [0., 1e-12, 0.], [0., 0., 1e-12]])
    with pytest.raises(ValueError, match='covariance'):
        smooth_positions([0.], [[1., 2., 3.]], [covariance])


def test_tiny_time_steps_and_near_singular_covariance_remain_finite_or_are_rejected():
    times = np.arange(4)*1e-15
    positions = [[0., 0., 0.], None, [1e-15, 0., 0.], [2e-15, 0., 0.]]
    covariance = np.diag([1e-12, 1e-8, 1e-6])
    means, covariances = smooth_positions(times, positions, [covariance, None, covariance, covariance])
    assert np.isfinite(means).all() and np.isfinite(covariances).all()
    np.linalg.cholesky(covariances)
    singular = np.eye(3)*1e-100
    with pytest.raises(np.linalg.LinAlgError):
        smooth_positions(times, positions, [singular, None, singular, singular])


def test_final_only_observation_keeps_its_covariance_and_back_predicts_without_double_counting():
    position, covariance = np.array([1., 2., 3.]), np.diag([.1, .2, .3])
    means, covariances = smooth_positions([0., .1, .2], [None, None, position], [None, None, covariance])
    np.testing.assert_array_equal(means[:, :3], np.broadcast_to(position, (3, 3)))
    np.testing.assert_array_equal(means[:, 3:], np.zeros((3, 3)))
    np.testing.assert_array_equal(covariances[-1, :3, :3], covariance)
    assert covariances[0, 0, 0] > covariances[1, 0, 0] > covariances[2, 0, 0]
    np.linalg.cholesky(covariances)


@pytest.mark.parametrize('kwargs', [dict(bins=10.5), dict(minimum_bins=4.5), dict(minimum_points=8.5)])
def test_fit_counts_reject_fractional_values_before_fitting(kwargs):
    with pytest.raises(ValueError, match='fitting'):
        FitConfig(**kwargs)


@pytest.mark.parametrize('blade', [True, 1.0])
def test_blade_identity_requires_an_integer_identifier(blade):
    with pytest.raises(ValueError, match='prior'):
        BladePrior(blade, np.eye(4), 60.)
