"""Analytic blade observations, target ambiguity and causal failure boundaries."""
from dataclasses import replace
import numpy as np
from wfrl.radar_x100.tracking import BladePrior, BladeTracker, fit_blades


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
