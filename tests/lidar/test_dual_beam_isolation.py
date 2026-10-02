"""Small full-path checks for the runnable deletion/pollution audit."""
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

import scripts.lidar.verify_dual_beam_isolation as isolation
from wfrl.lidar.dual_beam import validate_calibration
from wfrl.lidar.dual_beam_replay import METHOD_ALGORITHM_VERSIONS, precompute


def observation(point, time):
    point = np.asarray(point, float)
    return dict(time_s=time, origin_m=(point + [0., 0., 10.]).tolist(),
                direction=[0., 0., -1.], slant_range_m=10.,
                first_object='blade', blade_id=1, valid=True, observed=True,
                reason='valid', point_m=point.tolist())


def fixture_source_and_rows(method):
    times = np.array([1., 1.025])
    # A square moving-tower model, static in this analytic fixture, with its
    # side surface triangulated between two known section heights.
    tower = np.array([[-2., -2., 0.], [2., -2., 0.], [2., 2., 0.], [-2., 2., 0.],
                      [-2., -2., 110.], [2., -2., 110.], [2., 2., 110.], [-2., 2., 110.]])
    triangles = np.array([[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                          [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    transform = np.column_stack((np.eye(3), np.zeros(3)))
    source = SimpleNamespace(
        times=times, scalars=dict(ShftTilt=0., OverHang=0., TowerHt=100.,
                                Twr2Shft=0., **{'PreCone(1)': 0.}),
        poses=np.zeros((2, 6)), nacelles=np.tile(transform, (2, 1, 1)),
        layout=np.zeros(3), tower_transforms=np.tile(transform, (2, 2, 1, 1)),
        tower_station=np.array([0] * 4 + [1] * 4), tower_reference=tower,
        tower_triangles=triangles,
        transforms='forbidden-terminal-flexible-transform',
        source_tips='forbidden-source-tip', blade_reference='forbidden-blade-reference')
    config = dict(schema='wfrl.dual-beam-calibration.v1', status='SIMULATION_SELECTED',
                  origins_m=[[0., 0., 90.]] * 3, common_origin_approximation=True,
                  angles_deg=[10., 12., 14.], range_m=[5., 100.],
                  min_point_separation_m=.25, min_orientation_projection_m=.01,
                  effective_length_m=63., measurement_hold_s=1., alarm_hold_s=1.,
                  expected_window_half_angle_deg=15., reconstruction_method=method)
    rows = []
    _, _, limits = validate_calibration(config)
    for index, time in enumerate(times):
        row = dict(time_s=float(time), observations={
            'S1': observation((0., 0., 70.), float(time)),
            'S2': observation((10., 0., 80.), float(time)),
            'S3': observation((30., 0., 60.), float(time))},
            evaluation=dict(tip_reference_m=[0., 0., 27.], clearance_reference_m=-100.,
                            clearance_error_m=1000.))
        if index == 1:
            row['observations']['S3'].update(valid=False, reason='no_return')
        row['reconstruction'] = isolation.recompute_row(row, source, index, config, limits, Counter())
        rows.append(row)
    return source, dict(samples=rows), config


@pytest.mark.parametrize('method', ['hub-axis.v1', 'hub-tls.v1'])
def test_all_rows_recomputed_with_deleted_and_polluted_truth(method):
    source, data, config = fixture_source_and_rows(method)
    report = isolation.verify_turbine(source, data, config)
    assert report['passed'] and report['rows'] == 2
    assert report['reconstruction_calls'] == 6
    assert report['maximum_absolute_prediction_difference'] == {'original': 0., 'deleted': 0., 'polluted': 0.}
    assert report['recomputed_reasons'] == {'valid': 1, 's3_no_return': 1}
    eigen = report['independent_tls']
    assert eigen['valid_pairs_recomputed'] == eigen['scored_pairs'] == 1
    assert eigen['comparison_to_package'] == (method == 'hub-tls.v1')
    if method == 'hub-tls.v1':
        assert eigen['maximum_tip_coordinate_absolute_difference_m'] < 1e-12
        assert eigen['maximum_clearance_absolute_difference_m'] < 1e-12
    for reads in report['recorded_reads'].values():
        assert reads['source.nacelles'] == 2
        assert reads['fixed_scalars.OverHang'] == 4
        assert not any('point_m' in key or 'evaluation' in key or 'source.transforms' in key for key in reads)


def test_deletion_and_pollution_preserve_only_permitted_measurements():
    _, data, _ = fixture_source_and_rows('hub-tls.v1')
    row = data['samples'][0]
    deleted = isolation.observation_variant(row, 'deleted')
    assert set(deleted) == {'time_s', 'observations'}
    assert set(deleted['observations']) == {'S2', 'S3'}
    polluted = isolation.observation_variant(row, 'polluted')
    assert polluted['evaluation'] != row['evaluation']
    assert polluted['reconstruction'] != row['reconstruction']
    for name in ('S2', 'S3'):
        assert 'point_m' not in deleted['observations'][name]
        assert polluted['observations'][name]['point_m'] != row['observations'][name]['point_m']
        for key in isolation.OBSERVATION_FIELDS & row['observations'][name].keys():
            assert deleted['observations'][name][key] == polluted['observations'][name][key] == row['observations'][name][key]


def test_forbidden_read_guard_fails_even_for_absent_values():
    reads = Counter()
    guard = isolation.GuardedMapping({'slant_range_m': 10.}, isolation.OBSERVATION_FIELDS, 'S2', reads)
    assert guard.get('time_support', 'instantaneous_source_geometry') == 'instantaneous_source_geometry'
    with pytest.raises(isolation.ForbiddenRead, match='point_m'):
        guard.get('point_m')
    assert reads['S2.point_m'] == 1
    source, _, _ = fixture_source_and_rows('hub-tls.v1')
    with pytest.raises(isolation.ForbiddenRead, match='source.transforms'):
        _ = isolation.GuardedSource(source, reads).transforms


def test_independent_eigensolver_does_not_call_production_reconstruct(monkeypatch):
    source, data, config = fixture_source_and_rows('hub-tls.v1')
    expected = data['samples'][0]['reconstruction']

    def fail(*args, **kwargs):
        raise AssertionError('Production solver must not be called')

    monkeypatch.setattr(isolation, 'reconstruct', fail)
    independent = isolation.independent_eigen_tls(data['samples'][0], source, 0, config, Counter())
    for key, value in independent.items():
        assert value == pytest.approx(expected[key], abs=1e-12)


def test_saved_prediction_change_is_rejected_and_forbidden_dependency_is_detected(monkeypatch):
    source, data, config = fixture_source_and_rows('hub-tls.v1')
    changed = deepcopy(data)
    changed['samples'][0]['reconstruction']['clearance_estimate'] += .001
    report = isolation.verify_turbine(source, changed, config)
    assert not report['passed'] and report['failed_rows'] == {'original': 1}
    estimator = isolation.reconstruct

    def bad_estimator(s2, *args, **kwargs):
        s2.get('point_m')
        return estimator(s2, *args, **kwargs)

    monkeypatch.setattr(isolation, 'reconstruct', bad_estimator)
    report = isolation.verify_turbine(source, data, config)
    assert not report['passed']
    assert report['failed_rows'] == {'original': 2, 'deleted': 2, 'polluted': 2}
    assert all('Forbidden estimator input read' in failure['error'] for failure in report['failures'])


def test_comparison_distinguishes_null_categories_and_reproduction_tolerance():
    saved = dict(valid=False, reason='missing', tip_estimate_m=None, clearance_estimate=None)
    recomputed = dict(saved, method='hub-axis.v1')
    assert isolation.prediction_difference(saved, recomputed) == ([], 0.)
    recomputed['valid'] = True
    assert isolation.prediction_difference(saved, recomputed)[0]
    assert not isolation.prediction_difference({'clearance_estimate': 1.}, {'clearance_estimate': 1. + 5e-9})[0]
    assert isolation.prediction_difference({'clearance_estimate': 1.}, {'clearance_estimate': 1. + 2e-8})[0]


@pytest.mark.parametrize('malformed', [1.000000001, 1., True, '1'])
def test_blade_identity_requires_exact_type_and_value(malformed):
    assert isolation.prediction_difference({'blade_id': 1}, {'blade_id': malformed})[0]
    source, data, config = fixture_source_and_rows('hub-tls.v1')
    data['samples'][0]['reconstruction']['blade_id'] = malformed
    report = isolation.verify_turbine(source, data, config)
    assert not report['passed'] and report['failed_rows'] == {'original': 1}
    assert 'discrete type/value differs' in report['failures'][0]['error']


def test_real_reader_rejects_asynchronous_rows_before_isolation_recomputation():
    source, data, config = fixture_source_and_rows('hub-tls.v1')
    version = METHOD_ALGORITHM_VERSIONS['hub-tls.v1']
    config['algorithm_version'] = version
    source.manifest = {'turbine_ids': ['T1'], 'segment': {'start_s': 1., 'end_s': 1.025}}
    source.motion = [{'time_s': float(t)} for t in source.times]
    for row in data['samples']:
        row.update(sample_kind='source', expected_blade_id=1, passage_id='b1',
                   s1_observation_state='triggered')
    data['cumulative'], data['events'] = precompute(data['samples'], config)
    overlay = dict(config=config,
                   manifest=dict(reconstruction_method='hub-tls.v1', algorithm_version=version),
                   results={'T1': data})
    isolation.validate_replay_contract(source, overlay, 'T1')
    data['samples'][1]['observations']['S2']['time_s'] += .025
    with pytest.raises(ValueError, match='not simultaneous'):
        isolation.validate_replay_contract(source, overlay, 'T1')


def test_ast_checks_real_boundary_and_rejects_truth_in_hub_provenance(tmp_path):
    report = isolation.audit_postprocess_boundary()
    assert report['passed'], report['failures']
    path = tmp_path / 'postprocess.py'
    text = (isolation.ROOT / 'scripts/lidar/postprocess_dual_beam.py').read_text()
    path.write_text(text.replace('source.poses[index],1,nacelle', 'source.transforms[index],1,nacelle'))
    changed = isolation.audit_postprocess_boundary(path)
    assert not changed['passed']
    assert any('Hub reads source fields' in failure for failure in changed['failures'])
