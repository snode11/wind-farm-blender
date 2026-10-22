"""Comparison denominators, independent geometry and historical reproduction."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.lidar.dual_beam_replay import precompute
from wfrl.lidar.moving_tower import horizontal_clearance

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('compare_dual_beam_methods', ROOT/'scripts/lidar/compare_dual_beam_methods.py')
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def square_tower():
    points = np.array([[-1,-1,0], [1,-1,0], [1,1,0], [-1,1,0],
                       [-2,-2,10], [2,-2,10], [2,2,10], [-2,2,10]], float)
    triangles = np.array([[i,(i+1)%4,i+4] for i in range(4)]
                         + [[(i+1)%4,(i+1)%4+4,i+4] for i in range(4)])
    return points, triangles


@pytest.mark.parametrize('point', [[5,0,2], [5,0,8], [0,0,5], [5,3,0], [5,3,10]])
def test_independent_tower_score_own_height_and_penetration(point):
    points, triangles = square_tower()
    independent = comparison.independent_clearance(point, points, triangles)
    assert independent == pytest.approx(horizontal_clearance(point, points, triangles)[0], abs=1e-8)
    translation = np.array([12., -6., 5.])
    assert comparison.independent_clearance(np.array(point)+translation, points+translation, triangles) == pytest.approx(independent, abs=1e-8)
    if point[0] == 0:
        assert independent < 0


def test_statistics_nearest_rank_pooled_and_signed_tail():
    s = comparison.statistics([-4., -2., 1., 3.])
    assert s['mae_m'] == 2.5
    assert s['bias_m'] == -.5
    assert s['p95_abs_error_m'] == 4.
    assert s['min_signed_error_m'] == -4.
    assert s['max_underestimate_m'] == 4.
    assert s['max_overestimate_m'] == 3.
    assert comparison.statistics([])['mae_m'] is None


def test_historical_reproduction_exact_discrete_and_frozen_tolerance():
    historical = dict(time_s=1., valid=True, blade_id=1, reason='valid', point=[1.,2.,3.], count=2)
    actual = {**deepcopy(historical), 'method':'hub-axis.v1'}
    actual['point'][0] += 9e-9
    assert comparison.numerical_reproduction(actual, historical)['passed']
    actual['point'][0] += 2e-9
    assert not comparison.numerical_reproduction(actual, historical)['passed']
    for field, value in [('time_s', 1.+1e-9), ('valid', False), ('count', 2.0), ('reason', 'changed')]:
        actual = deepcopy(historical); actual[field] = value
        assert not comparison.numerical_reproduction(actual, historical)['passed']


def make_packages(tmp_path):
    source_path = tmp_path/'source'; source_path.mkdir()
    (source_path/'blade-reference.json').write_text(json.dumps({'tip_local_m':[0,0,2]}))
    times = np.arange(5, dtype=float)
    poses = np.zeros((5,6)); poses[:,1] = [160,170,180,190,200]
    transforms = np.zeros((5,3,19,3,4)); transforms[..., :3] = np.eye(3)
    transforms[:,0,-1,0,3] = [4,3,2,3,4]
    points, triangles = square_tower()
    tower_transforms = np.zeros((5,2,3,4)); tower_transforms[..., :3] = np.eye(3)
    source = SimpleNamespace(path=source_path, times=times, poses=poses, transforms=transforms,
                             scalars={'ShftTilt':0., 'OverHang':0., 'TowerHt':5., 'Twr2Shft':0., 'PreCone(1)':0.},
                             layout=np.zeros(3), tower_reference=points, tower_triangles=triangles,
                             tower_station=np.array([0,0,0,0,1,1,1,1]), tower_transforms=tower_transforms,
                             manifest={'source_fps':1.})
    references = comparison.structural_reference_points(source)
    packages = []
    for name, method in [('baseline','hub-axis.v1'), ('candidate','hub-tls.v1')]:
        path=tmp_path/name; path.mkdir(); (path/'manifest.json').write_text('{}')
        rows=[]
        for i,time in enumerate(times):
            blade, passage = comparison.expected_window(poses[i],15.)
            observations = {s:dict(time_s=float(time),observed=True,valid=i in (1,3),
                                    first_object='blade' if i in (1,3) else 'none',
                                    blade_id=1 if i in (1,3) else None,
                                    reason='valid' if i in (1,3) else 'miss', slant_range_m=5. if s=='S3' else 20.)
                            for s in ('S1','S2','S3')}
            valid = i in (1,3) and (name=='baseline' or i==1)
            estimate_point = [7.,0,6.] if name=='baseline' else [5.,0,8.]
            reference = comparison.independent_clearance(references[i,0], points, triangles) if valid else None
            estimate = comparison.independent_clearance(estimate_point, points, triangles) if valid else None
            rows.append(dict(time_s=float(time),observations=observations,expected_blade_id=blade,passage_id=passage,
                             reconstruction=dict(valid=valid,blade_id=1 if valid else None,method=method,
                                                 clearance_estimate=estimate,tip_estimate_m=estimate_point if valid else None,
                                                 reason='valid' if valid else 'tls_nonunique_direction' if i==3 else 'miss'),
                             evaluation=dict(tip_reference_m=references[i,0].tolist() if valid else None,
                                             clearance_reference_m=reference, clearance_error_m=999. if valid else None),
                             s1_observation_state='triggered' if i in (1,3) else 'not_triggered'))
        config=dict(reconstruction_method=method,expected_window_half_angle_deg=15.,alarm_hold_s=1.,measurement_hold_s=1.)
        cumulative,events=precompute(rows,config)
        manifest=dict(turbine_ids=['T1'],source_hashes={},segment={'start_s':0.,'end_s':4.},source_fps=1.,
                      reference_definition='structural',clearance_definition='horizontal')
        packages.append(dict(path=path,overlay=dict(config=config,manifest=manifest,
                              results={'T1':dict(samples=rows,cumulative=cumulative,events=events)}),sources={'T1':source}))
    return packages


def test_whole_window_minimum_common_own_sets_and_polluted_stored_error(tmp_path):
    baseline,candidate=make_packages(tmp_path)
    report=comparison.compare_packages(baseline,candidate)
    assert report['integrity']['checks']['reference_window_samples_recomputed'] == 3
    assert report['metrics']['common']['baseline']['error_samples'] == 1
    assert report['metrics']['own_valid']['baseline']['error_samples'] == 2
    assert report['metrics']['own_valid']['candidate']['error_samples'] == 1
    assert report['metrics']['own_valid']['baseline']['mae_m'] != 999.
    window=report['complete_passage_minima'][0]
    assert window['reference_min_time_s'] == 2.
    assert window['reference_samples'] == 3
    assert window['candidate']['predicted_min_time_s'] == 1.
    assert window['candidate']['error_m'] == pytest.approx(window['candidate']['predicted_min_m']-window['reference_min_m'])
    assert window['candidate']['error_m'] > report['metrics']['own_valid']['candidate']['mae_m']
    coverage=report['coverage']['pooled']['candidate']
    assert coverage['expected_samples'] == 3
    assert coverage['fresh_valid_samples'] == 1
    assert coverage['fresh_coverage'] == 1/3
    assert coverage['unknown_samples'] == 2
    assert coverage['longest_missing_grid_duration_s'] == 2.
    assert coverage['covered_complete_passages'] == coverage['complete_passages'] == 1


@pytest.mark.parametrize('change', ['raw', 'alarm', 'config', 'window', 'reference'])
def test_mismatch_rejected(tmp_path, change):
    baseline,candidate=make_packages(tmp_path)
    data=candidate['overlay']['results']['T1']
    if change=='raw':data['samples'][1]['observations']['S3']['slant_range_m'] += .1
    elif change=='alarm':data['cumulative'][1]['alarm']['active'] = False
    elif change=='config':candidate['overlay']['config']['alarm_hold_s'] = 2.
    elif change=='window':data['samples'][1]['expected_blade_id'] = 2
    else:data['samples'][1]['evaluation']['clearance_reference_m'] += .1
    with pytest.raises(ValueError):comparison.compare_packages(baseline,candidate)


def test_boundary_truncated_passage_is_separate(tmp_path):
    baseline,candidate=make_packages(tmp_path)
    for package in (baseline,candidate):
        source=package['sources']['T1']; source.poses[0,1] = 170.
        for name in ('baseline','candidate'):
            row=package['overlay']['results']['T1']['samples'][0]
            row['expected_blade_id'],row['passage_id']=comparison.expected_window(source.poses[0],15.)
    report=comparison.compare_packages(baseline,candidate)
    assert report['complete_passage_minima'] == []
    assert report['coverage']['pooled']['baseline']['boundary_truncated_passages'] == 1
    assert report['coverage']['pooled']['baseline']['complete_passages'] == 0
