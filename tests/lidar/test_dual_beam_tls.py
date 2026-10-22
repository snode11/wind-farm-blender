"""Hub-constrained TLS from permitted observations, with independent checks."""
from copy import deepcopy
import inspect
import math

import numpy as np
import pytest

from wfrl.lidar.dual_beam import METHOD_VERSIONS, PairLimits, reconstruct, s1_state
from wfrl.lidar.dual_beam_replay import precompute


def echo(point, origin=None, blade=1, time=1.):
    point = np.asarray(point, float)
    origin = point + [0., 0., 10.] if origin is None else np.asarray(origin, float)
    distance = float(np.linalg.norm(point - origin))
    return dict(time_s=time, origin_m=origin.tolist(),
                direction=((point - origin) / distance).tolist(),
                slant_range_m=distance, first_object='blade', blade_id=blade,
                observed=True, valid=True, reason='valid')


def estimate(a=None, b=None, hub=(0., 0., 100.), method='hub-tls.v1', **kwargs):
    return reconstruct(echo((10., 0., 80.)) if a is None else a,
                       echo((30., 0., 60.)) if b is None else b,
                       hub, 63., lambda p: float(p[0] + .1 * p[2]),
                       time_s=1., method=method, **kwargs)


def test_methods_and_default_are_explicit():
    assert METHOD_VERSIONS == {
        'hub-axis.v1': 'two-point-hub-extrapolation.v1',
        'hub-tls.v1': 'hub-constrained-tls.v1',
    }
    assert inspect.signature(reconstruct).parameters['method'].default == 'hub-axis.v1'
    legacy = estimate(method='hub-axis.v1')
    tls = estimate()
    assert legacy['valid'] and tls['valid']
    assert legacy['method'] == 'hub-axis.v1' and tls['method'] == 'hub-tls.v1'
    assert not np.allclose(legacy['direction'], tls['direction'])


def test_analytic_tls_and_independent_eigen_crosscheck():
    result = estimate()
    assert result['valid']
    # For q2=(10,0,-20), q3=(30,0,-40), A.T A has x/z block
    # 100 * [[10,-14],[-14,20]]. Its principal eigenvalue is 15+sqrt(221).
    eigenvalue = 15 + math.sqrt(221)
    analytic = np.array([14., 0., 10 - eigenvalue])
    analytic /= np.linalg.norm(analytic)
    assert result['direction'] == pytest.approx(analytic, abs=2e-15)
    relative = np.array([[10., 0., -20.], [30., 0., -40.]])
    values, axes = np.linalg.eigh(relative.T @ relative)
    eigen = axes[:, np.argmax(values)]
    if eigen @ relative.sum(axis=0) < 0:
        eigen = -eigen
    assert result['direction'] == pytest.approx(eigen, abs=2e-15)
    assert result['tip_estimate_m'] == pytest.approx(np.array([0., 0., 100.]) + 63 * analytic)
    assert np.linalg.norm(np.array(result['tip_estimate_m']) - result['hub_m']) == pytest.approx(63.)
    # Equal orthogonal-distance weighting must not normalize each row first.
    radial_mean = (relative / np.linalg.norm(relative, axis=1)[:, None]).sum(axis=0)
    radial_mean /= np.linalg.norm(radial_mean)
    assert not np.allclose(result['direction'], radial_mean, atol=1e-3)
    # Centering two rows recovers the old secant and loses the hub constraint.
    _, _, centered_axes = np.linalg.svd(relative - relative.mean(axis=0))
    assert abs(centered_axes[0] @ analytic) < .999


def test_order_and_full_rotation_translation_covariance():
    a, b = echo((10., 0., 80.)), echo((30., 0., 60.))
    base = estimate(a, b)
    assert estimate(b, a)['tip_estimate_m'] == pytest.approx(base['tip_estimate_m'])
    axis = np.array([-2., 1., 3.]); axis /= np.linalg.norm(axis)
    skew = np.array([[0., -axis[2], axis[1]], [axis[2], 0., -axis[0]],
                     [-axis[1], axis[0], 0.]])
    theta = .71
    rotation = (math.cos(theta) * np.eye(3) + math.sin(theta) * skew
                + (1 - math.cos(theta)) * np.outer(axis, axis))
    shift = np.array([500., -100., 8.])
    transform = lambda p: rotation @ np.asarray(p) + shift
    transformed = reconstruct(
        echo(transform((10., 0., 80.)), transform(a['origin_m'])),
        echo(transform((30., 0., 60.)), transform(b['origin_m'])),
        transform((0., 0., 100.)), 63.,
        lambda p: float((rotation.T @ (p - shift)) @ np.array([1., 0., .1])),
        time_s=1., method='hub-tls.v1')
    assert transformed['valid']
    assert transformed['direction'] == pytest.approx(rotation @ base['direction'])
    assert transformed['tip_estimate_m'] == pytest.approx(transform(base['tip_estimate_m']))
    assert transformed['clearance_estimate'] == pytest.approx(base['clearance_estimate'])


@pytest.mark.parametrize('change,reason', [
    ({'valid': False, 'reason': 'no_return'}, 's3_no_return'),
    ({'time_s': .975}, 'not_same_time'),
    ({'time_support': 'held'}, 'not_instantaneous_observations'),
    ({'blade_id': 2}, 'different_blades'),
    ({'slant_range_m': float('nan')}, 'invalid_range_or_direction'),
    ({'slant_range_m': float('inf')}, 'invalid_range_or_direction'),
    ({'direction': [0., 0., -2.]}, 'invalid_range_or_direction'),
    ({'origin_m': [float('nan'), 0., 0.]}, 'invalid_input'),
])
def test_invalid_observations_retain_method(change, reason):
    b = echo((30., 0., 60.)); b.update(change)
    result = estimate(b=b)
    assert not result['valid'] and result['reason'] == reason
    assert result['method'] == 'hub-tls.v1'
    assert result['clearance_estimate'] is None


@pytest.mark.parametrize('method', ['unknown.v1', None, ['hub-tls.v1']])
def test_unknown_method_explicitly_rejected(method):
    result = estimate(method=method)
    assert result['reason'] == 'unsupported_reconstruction_method'
    assert result['method'] == method
    assert result['tip_estimate_m'] is None


def test_numerical_nonuniqueness_and_unchanged_geometric_limits():
    a = echo((10., 0., 0.))
    # Equal-radius orthogonal points already fail the original secant guard;
    # the candidate must retain that rejection instead of redefining validity.
    assert estimate(a, echo((0., 10., 0.)), hub=(0., 0., 0.))['reason'] == 'ambiguous_root_to_tip'
    # At a large hub-relative radius, a tiny radial asymmetry clears the
    # original 0.01 m projection guard but is below numerical TLS uniqueness.
    result = estimate(echo((1e14, 0., 0.)),
                      echo((0., 1e14 * (1 + 1e-15), 0.)), hub=(0., 0., 0.))
    assert result['reason'] == 'tls_direction_not_unique'
    assert result['method'] == 'hub-tls.v1'
    # Once the leading direction is numerically resolvable, no extra quality
    # gate based on this fragment's error or angular spread is imposed.
    assert estimate(echo((1e14, 0., 0.)),
                    echo((0., 1e14 * (1 + 1e-9), 0.)), hub=(0., 0., 0.))['valid']
    assert estimate(a, echo((-10., 0., 0.)), hub=(0., 0., 0.))['reason'] == 'ambiguous_root_to_tip'
    assert estimate(a, echo((10.01, 0., 0.)), hub=(0., 0., 0.))['reason'] == 'points_too_close'
    assert estimate(limits=PairLimits(min_separation_m=0))['reason'] == 'invalid_calibration'
    assert estimate(hub=(float('inf'), 0., 100.))['reason'] == 'invalid_input'


def test_failed_or_nonfinite_svd_explicitly_rejected(monkeypatch):
    def fail(*args, **kwargs):
        raise np.linalg.LinAlgError('deliberate solver failure')
    monkeypatch.setattr(np.linalg, 'svd', fail)
    assert estimate()['reason'] == 'tls_numerical_failure'
    monkeypatch.setattr(np.linalg, 'svd', lambda *args, **kwargs:
                        (None, np.array([float('nan'), 1.]), np.zeros((2, 3))))
    assert estimate()['reason'] == 'tls_numerical_failure'


def test_truth_and_saved_hit_pollution_is_ignored():
    a, b = echo((10., 0., 80.)), echo((30., 0., 60.))
    base = estimate(a, b)
    polluted_a, polluted_b = deepcopy(a), deepcopy(b)
    for row in (polluted_a, polluted_b):
        row.update(point_m=[1e8, 1e8, 1e8], truth_tip=[0., 0., 0.],
                   axis=[1., 0., 0.], evaluation={'clearance_reference_m': -1e8},
                   clearance_error_m=1e8, tip_estimate_m=[-1e8, 0., 0.],
                   span_station=1e8, turbine_id='truth-derived-name',
                   terminal_flexible_transform=[1e8] * 16)
    assert estimate(polluted_a, polluted_b) == base
    assert not {'truth', 'tip_reference', 'surface_clearance', 'axis', 'turbine_id'} & set(inspect.signature(reconstruct).parameters)


@pytest.mark.parametrize('failure', ['s2', 's3', 'time', 'blade', 'separation', 'tls'])
def test_s1_alarm_independent_when_pair_rejects(failure):
    a, b, hub = echo((10., 0., 80.)), echo((30., 0., 60.)), (0., 0., 100.)
    if failure in ('s2', 's3'):
        (a if failure == 's2' else b).update(valid=False, reason='no_return')
    elif failure == 'time':
        b['time_s'] = .975
    elif failure == 'blade':
        b['blade_id'] = 2
    elif failure == 'separation':
        b = echo((10.01, 0., 80.))
    else:
        a, b, hub = (echo((1e14, 0., 0.)),
                     echo((0., 1e14 * (1 + 1e-15), 0.)), (0., 0., 0.))
    pair = estimate(a, b, hub=hub)
    assert not pair['valid']
    s1 = echo((0., 0., 70.))
    alarm = s1_state(s1)
    assert alarm == 'triggered'
    rows = []
    for time, state in ((1., alarm), (1.5, 'unknown'), (2.01, 'unknown')):
        rows.append(dict(time_s=time, expected_blade_id=1, passage_id='b1',
                         reconstruction=deepcopy(pair), evaluation={},
                         s1_observation_state=state, observations={'S1': s1}))
    cumulative, events = precompute(rows, dict(alarm_hold_s=1., measurement_hold_s=.5))
    assert cumulative[0]['alarm']['active']
    assert cumulative[1]['alarm']['active']
    assert not cumulative[2]['alarm']['active']
    assert all(row['measurement'] is None for row in cumulative)
    assert events[0]['start_s'] == 1.
