"""Actual first-hit changes, frozen windows and fresh pressure denominators."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.lidar import stress_dual_beam_pointing as pointing
from wfrl.lidar.dual_beam import METHOD_VERSIONS, PairLimits


def toy_geometry():
    # Nominal downward rays hit a narrow opaque blade at z=5. A 0.2 degree
    # pointing change misses it and reaches ground, changing the measured range.
    blade = np.array([[-.003, -1., 5.], [.003, -1., 5.], [0., 1., 5.]])
    blades = np.stack([blade, blade+[100., 0., 0.], blade+[200., 0., 0.]])
    triangles = np.array([[0, 1, 2]])
    tower = np.array([[100., -1., 0.], [100., 1., 0.], [100., 0., 10.]])
    return dict(time_s=1., nacelle=np.column_stack((np.eye(3), np.zeros(3))),
                layout=np.zeros(3), origins=np.array([[0., 0., 10.]]*3),
                directions=np.array([[0., 0., -1.]]*3), blades=blades,
                blade_triangles=triangles, tower=tower, tower_triangles=triangles,
                limits=PairLimits(min_range_m=.1))


@pytest.mark.parametrize('signs', pointing.POINTING_COMBINATIONS)
def test_actual_pointing_rehits_geometry_and_changes_membership(signs):
    geometry = toy_geometry()
    nominal = pointing.forward_observations(**geometry, signs=(0, 0))
    assert nominal['S2']['first_object'] == 'blade' and nominal['S2']['valid']
    assert nominal['S2']['slant_range_m'] == pytest.approx(5.)
    actual = pointing.forward_observations(**geometry, signs=signs, nominal_s1=nominal['S1'])
    assert actual['S1'] == nominal['S1']
    for beam, sign in zip(('S2', 'S3'), signs):
        assert actual[beam]['first_object'] == 'ground'
        assert not actual[beam]['valid']
        assert actual[beam]['slant_range_m'] > 10.
        expected = pointing.rotate_about_y(geometry['directions'][1], sign*.2)
        assert actual[beam]['direction'] == pytest.approx(expected)
        # Fixed-hit calibration probes retain the old first object and range;
        # they are a different experiment from actual pointing re-intersection.
        fixed_hit = deepcopy(nominal[beam])
        fixed_hit['direction'] = expected.tolist()
        assert fixed_hit['valid'] and fixed_hit['slant_range_m'] == nominal[beam]['slant_range_m']
        changes = pointing.observation_changes(nominal[beam], actual[beam])
        assert changes['first_object_changed'] and changes['blade_id_changed']
        assert changes['valid_changed'] and changes['range_branch_changed']


def test_rotation_uses_source_nacelle_material_y_before_world_transport(monkeypatch):
    geometry = toy_geometry()
    geometry['nacelle'] = np.column_stack((np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]),
                                         np.array([3., 4., 5.])))
    geometry['layout'] = np.array([20., 30., 0.])
    observed = []
    def capture(time_s, origin, direction, *args):
        observed.append((origin.copy(), direction.copy()))
        return dict(time_s=time_s, direction=direction.tolist())
    monkeypatch.setattr(pointing, 'observe', capture)
    preserved = {'nominal_s1': True}
    actual = pointing.forward_observations(**geometry, signs=(-1, 1), nominal_s1=preserved)
    assert actual['S1'] == preserved and len(observed) == 2
    rotation = geometry['nacelle'][:, :3]
    for (origin, direction), sign in zip(observed, (-1, 1)):
        assert origin == pytest.approx(rotation@geometry['origins'][1]+geometry['nacelle'][:, 3]+geometry['layout'])
        expected = rotation@pointing.rotate_about_y(geometry['directions'][1], sign*.2)
        assert direction == pytest.approx(expected)
        assert direction[0] == pytest.approx(0.)
        assert abs(direction[1]) > 0


def source_and_overlay(phases):
    poses = np.zeros((len(phases), 6))
    poses[:, 1] = phases
    source = SimpleNamespace(times=1.+np.arange(len(phases))*.025, poses=poses)
    rows = []
    for pose in poses:
        blade, passage = pointing.expected_window(pose, 15.)
        rows.append(dict(expected_blade_id=blade, passage_id=passage))
    return {'T1': source}, {'results': {'T1': {'samples': rows}}}


def test_windows_freeze_source_phase_and_preserve_boundary_truncation():
    sources, overlay = source_and_overlay([150., 165., 180., 195., 210.])
    windows = pointing.freeze_windows(sources, overlay, {'expected_window_half_angle_deg': 15.})['T1']
    assert [r['source_index'] for r in windows] == [1, 2, 3]
    assert all(r['expected_blade_id'] == 1 for r in windows)
    assert not any(r['boundary_truncated'] for r in windows)
    sources, overlay = source_and_overlay([165., 180., 195., 210.])
    windows = pointing.freeze_windows(sources, overlay, {'expected_window_half_angle_deg': 15.})['T1']
    assert all(r['boundary_truncated'] for r in windows)
    overlay['results']['T1']['samples'][1]['expected_blade_id'] = 2
    with pytest.raises(ValueError, match='fixed windows'):
        pointing.freeze_windows(sources, overlay, {'expected_window_half_angle_deg': 15.})


def test_all_four_sign_combinations_are_declared_without_range_changes():
    assert pointing.POINTING_COMBINATIONS == ((-1, -1), (-1, 1), (1, -1), (1, 1))
    assert len({pointing.combination_id(s) for s in pointing.POINTING_COMBINATIONS}) == 4
    assert pointing.ANGLE_AMPLITUDE_DEG == .2


def test_pressure_summary_keeps_missing_samples_and_wrong_blades_in_denominator():
    combo = pointing.combination_id((-1, -1))
    rows = []
    observation = dict(first_object='blade', slant_range_m=8., valid=True, observed=True,
                       blade_id=1, reason='valid')
    changes = {s: pointing.observation_changes(observation, observation) for s in ('S1', 'S2', 'S3')}
    for i in range(3):
        methods = {}
        for method in METHOD_VERSIONS:
            valid = i != 2
            methods[method] = dict(reconstruction=dict(valid=valid, blade_id=1 if i == 0 else 2 if i == 1 else None,
                                                       reason='valid' if valid else 's2_ground_first'),
                                   evaluation=dict(error_m=.25 if valid else None))
        rows.append(dict(time_s=1.+i*.025, expected_blade_id=1, passage_id='blade1-cycle0',
                         boundary_truncated=False, nominal_observations={'S1': observation},
                         combinations={combo: dict(methods=methods, beam_changes=changes,
                                                   observations={'S2': observation, 'S3': observation})}))
    summary = pointing.summarize_samples(rows, combo, .025)
    for method in METHOD_VERSIONS:
        value = summary['methods'][method]
        assert value['valid_pairs'] == 2 and value['rejected_pairs'] == 1
        assert value['own_valid_error_statistics']['error_samples'] == 2
        assert value['correct_expected_blade_error_statistics']['error_samples'] == 1
        coverage = value['coverage']
        assert coverage['expected_samples'] == 3 and coverage['fresh_valid_samples'] == 1
        assert coverage['fresh_coverage'] == pytest.approx(1/3)
        assert coverage['unknown_samples'] == 2
        assert coverage['unknown_reasons'] == {'paired_wrong_blade': 1, 's2_ground_first': 1}
        assert coverage['complete_passages'] == coverage['covered_complete_passages'] == 1
        assert coverage['longest_missing_samples'] == 2
        assert coverage['longest_missing_grid_duration_s'] == .05
    assert summary['beam_changes']['S1']['changes'] == {}
    assert summary['common_scored_samples'] == 2


def test_nominal_reproduction_rejects_categorical_and_numeric_drift():
    nominal = pointing.forward_observations(**toy_geometry(), signs=(0, 0))['S2']
    assert pointing.verify_nominal_observation(nominal, deepcopy(nominal)) == 0.
    bad = deepcopy(nominal)
    bad['blade_id'] = 2
    with pytest.raises(ValueError, match='identity'):
        pointing.verify_nominal_observation(nominal, bad)
    bad = deepcopy(nominal)
    bad['slant_range_m'] += 1e-6
    with pytest.raises(ValueError, match='numeric'):
        pointing.verify_nominal_observation(nominal, bad)
