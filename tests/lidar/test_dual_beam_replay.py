"""Stateless seeking, held groups and corruption checks for the new contract."""
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path

import numpy as np
import pytest
from wfrl.lidar.dual_beam import observe, PairLimits
from wfrl.lidar.dual_beam_replay import DualBeamReader, METHOD_ALGORITHM_VERSIONS, precompute
from wfrl.lidar.replay import ReplayReader
from wfrl.camera_video.data import SourceGeometry

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'blender_frontend/wfrl_blender/assets/mappo'


def make_reader():
    motion=[dict(time_s=t,azimuth_deg=180,yaw_deg=0,rotor_speed_rpm=10,pitch_deg=[0,0,0]) for t in (1.,1.025,1.05,2.5)]
    legacy=ReplayReader(SimpleNamespace(manifest={'segment':{'start_s':1.,'end_s':2.5}},motion=motion,
                                       measurements=[],cumulative=[]))
    rows=[]
    for i,m in enumerate(motion):
        obs={s:dict(time_s=m['time_s'],observed=True,valid=i in (1,2),first_object='blade' if i in (1,2) else 'ground',
                    blade_id=1 if i in (1,2) else None,reason='valid' if i in (1,2) else 'ground_first') for s in ('S1','S2','S3')}
        if i==2: obs['S1'].update(observed=False,valid=False,reason='invalid_calibration')
        pair_valid=i==2  # S1 fires one source frame before any pair is available
        rows.append(dict(time_s=m['time_s'],sample_kind='source',observations=obs,passage_id='b1',expected_blade_id=1,
                    reconstruction=dict(valid=pair_valid,blade_id=1 if pair_valid else None,
                                        clearance_estimate=20. if pair_valid else None,reason='valid' if pair_valid else 'no_pair'),
                    evaluation=dict(clearance_reference_m=19. if pair_valid else None,clearance_error_m=1. if pair_valid else None),
                    s1_observation_state=('not_triggered','triggered','unknown','not_triggered')[i]))
    config=dict(alarm_hold_s=.5,measurement_hold_s=1.,status='DIAGNOSTIC_INSTALLATION_UNCONFIRMED')
    cumulative,events=precompute(rows,config)
    overlay=dict(config=config,manifest={},results={'T1':dict(samples=rows,cumulative=cumulative,events=events)})
    return DualBeamReader(legacy,overlay,'T1'),legacy,overlay


def test_seeking_hold_group_expiry_and_no_future_alarm():
    reader,_,_=make_reader()
    hit=reader.at(1.025)
    assert hit['alarm']['active'] and hit['measurement'] is None
    unknown=reader.at(1.05)
    assert unknown['alarm']['observation_state']=='unknown' and unknown['alarm']['active']
    assert reader.at(1.6)['status']=='s1_unknown'
    assert reader.at(1.6)['measurement']['time_s']==1.05
    assert reader.at(2.5)['measurement'] is None
    expected=deepcopy(reader.at(1.05))
    for t in (2.5,1.,1.05,1.6,1.025,1.05): reader.at(t)
    assert reader.at(1.05)==expected
    assert not reader.at(1.)['alarm']['active']
    assert reader.at(1.)['alarm']['event_count']==0
    assert reader.beam_activity(1.)==(False,False,False)
    assert reader.beam_activity(1.025)==(True,True,True)


@pytest.mark.parametrize('mutation', ['invalid_number','mismatch_state','different_blade','future_time'])
def test_malformed_contract_is_rejected(mutation):
    _,legacy,overlay=make_reader()
    row=overlay['results']['T1']['samples'][2]
    if mutation=='invalid_number':row['reconstruction'].update(valid=False,clearance_estimate=0.)
    elif mutation=='mismatch_state':row['s1_observation_state']='not_triggered'
    elif mutation=='different_blade':row['observations']['S3']['blade_id']=2
    else:row['observations']['S2']['time_s']+=.025
    with pytest.raises(ValueError):DualBeamReader(legacy,overlay,'T1')


def declare_algorithm(overlay, method):
    version = METHOD_ALGORITHM_VERSIONS[method]
    for metadata in (overlay['config'], overlay['manifest']):
        metadata.update(reconstruction_method=method, algorithm_version=version)
    for turbine in overlay['results'].values():
        for row in turbine['samples']:
            row['reconstruction'].update(method=method, algorithm_version=version)


@pytest.mark.parametrize('method', list(METHOD_ALGORITHM_VERSIONS))
def test_explicit_method_and_version_are_exposed_by_replay(method):
    _, legacy, overlay = make_reader()
    declare_algorithm(overlay, method)
    reader = DualBeamReader(legacy, overlay, 'T1')
    assert reader.at(1.05)['reconstruction_method'] == method
    assert reader.at(1.05)['algorithm_version'] == METHOD_ALGORITHM_VERSIONS[method]


def test_legacy_absence_maps_only_to_old_algorithm():
    reader, legacy, overlay = make_reader()
    assert reader.at(1.05)['reconstruction_method'] == 'hub-axis.v1'
    assert reader.at(1.05)['algorithm_version'] == 'two-point-hub-extrapolation.v1'
    # Historical manifests already carried the old version without a method.
    overlay['manifest']['algorithm_version'] = 'two-point-hub-extrapolation.v1'
    assert DualBeamReader(legacy, overlay, 'T1').algorithm_version == 'two-point-hub-extrapolation.v1'
    overlay['manifest']['algorithm_version'] = 'hub-constrained-tls.v1'
    with pytest.raises(ValueError, match='version'):
        DualBeamReader(legacy, overlay, 'T1')


@pytest.mark.parametrize('location,field,value', [
    ('config', 'reconstruction_method', 'unknown.v1'),
    ('manifest', 'reconstruction_method', 'unknown.v1'),
    ('pair', 'method', 'unknown.v1'),
    ('config', 'algorithm_version', 'two-point-hub-extrapolation.v1'),
    ('manifest', 'algorithm_version', 'two-point-hub-extrapolation.v1'),
    ('pair', 'algorithm_version', 'two-point-hub-extrapolation.v1'),
    ('config', 'algorithm_version', 'unknown.v1'),
    ('manifest', 'reconstruction_method', 'hub-axis.v1'),
    ('pair', 'method', 'hub-axis.v1'),
])
def test_unknown_and_mixed_algorithm_identifiers_are_rejected(location, field, value):
    _, legacy, overlay = make_reader()
    declare_algorithm(overlay, 'hub-tls.v1')
    metadata = (overlay['results']['T1']['samples'][0]['reconstruction']
                if location == 'pair' else overlay[location])
    metadata[field] = value
    with pytest.raises(ValueError, match='method|version'):
        DualBeamReader(legacy, overlay, 'T1')


@pytest.mark.parametrize('location,field', [
    ('config', 'reconstruction_method'), ('manifest', 'reconstruction_method'),
    ('config', 'algorithm_version'), ('manifest', 'algorithm_version'), ('pair', 'method'),
])
def test_tls_identifiers_cannot_be_omitted(location, field):
    _, legacy, overlay = make_reader()
    declare_algorithm(overlay, 'hub-tls.v1')
    metadata = (overlay['results']['T1']['samples'][0]['reconstruction']
                if location == 'pair' else overlay[location])
    del metadata[field]
    with pytest.raises(ValueError, match='method|version'):
        DualBeamReader(legacy, overlay, 'T1')


def test_row_version_is_optional_but_every_turbine_method_is_checked():
    _, legacy, overlay = make_reader()
    declare_algorithm(overlay, 'hub-tls.v1')
    for row in overlay['results']['T1']['samples']:
        del row['reconstruction']['algorithm_version']
    DualBeamReader(legacy, overlay, 'T1')
    overlay['results']['T2'] = deepcopy(overlay['results']['T1'])
    overlay['results']['T2']['samples'][0]['reconstruction']['method'] = 'hub-axis.v1'
    with pytest.raises(ValueError, match='method'):
        DualBeamReader(legacy, overlay, 'T1')


@pytest.mark.parametrize('failure', ['invalid_s2', 'invalid_s3', 'different_blade', 'tls_degenerate'])
def test_same_time_pair_failure_preserves_s1_hold_and_seek_statistics(failure):
    _, legacy, overlay = make_reader()
    declare_algorithm(overlay, 'hub-tls.v1')
    rows = overlay['results']['T1']['samples']
    # One fresh reading precedes S1's independent hit on a failed-pair frame.
    for name in ('S2', 'S3'):
        rows[0]['observations'][name].update(valid=True, first_object='blade', blade_id=1, reason='valid')
    rows[0]['reconstruction'].update(valid=True, blade_id=1, clearance_estimate=20., reason='valid')
    rows[0]['evaluation'].update(clearance_reference_m=19., clearance_error_m=1.)
    failed = rows[1]
    if failure in ('invalid_s2', 'invalid_s3'):
        name = 'S2' if failure == 'invalid_s2' else 'S3'
        failed['observations'][name].update(valid=False, first_object='ground', blade_id=None,
                                          reason='ground_first')
    elif failure == 'different_blade':
        failed['observations']['S3']['blade_id'] = 2
    failed['reconstruction']['reason'] = failure
    rows[2]['reconstruction'].update(valid=False, blade_id=None, clearance_estimate=None,
                                     reason='no_pair')
    rows[2]['evaluation'].update(clearance_reference_m=None, clearance_error_m=None)
    cumulative, events = precompute(rows, overlay['config'])
    overlay['results']['T1'].update(cumulative=cumulative, events=events)
    reader = DualBeamReader(legacy, overlay, 'T1')
    fired = reader.at(1.025)
    assert fired['alarm']['active'] and fired['alarm']['observation_state'] == 'triggered'
    assert fired['measurement']['time_s'] == 1.
    assert fired['statistics']['expected_samples'] == 2
    assert fired['statistics']['valid_samples'] == fired['statistics']['all_valid_samples'] == 1
    assert fired['statistics']['error_samples'] == 1
    assert fired['alarm']['event_count'] == 1
    assert fired['alarm']['last_hit_s'] == 1.025
    assert reader.at(1.)['alarm']['event_count'] == 0
    assert reader.at(1.525)['alarm']['active']
    assert not reader.at(1.525001)['alarm']['active']
    assert reader.at(2.)['measurement']['time_s'] == 1.
    assert reader.at(2.000001)['measurement'] is None
    expected = deepcopy(reader.at(1.05))
    for t in (2.5, 1.025, 1.6, 1., 1.05):
        reader.at(t)
    assert reader.at(1.05) == expected
    assert reader.at(1.)['statistics']['expected_samples'] == 1
    assert reader.at(2.5)['statistics']['valid_samples'] == 1
    assert reader.at(2.5)['statistics']['error_samples'] == 1


def test_reconstructed_source_surface_matches_old_saved_ray():
    source=SourceGeometry(SOURCE,'T1')
    row=next(r for r in source.measurements if r['beams']['B2']['valid'])
    snapshot=source.sample(row['time_s'],include_blades=True)
    transform=snapshot['nacelle_world_transform'][:3]
    config=source.manifest['calibration']
    origin=transform[:,:3]@config['origin_m']+transform[:,3]
    direction=transform[:,:3]@config['beam_directions'][1]
    direction/=np.linalg.norm(direction)
    hit=observe(row['time_s'],origin,direction,snapshot['blade_points_world_m'],source.blade_triangles,
                snapshot['tower_points_world_m'],source.tower_triangles)
    assert hit['valid'] and hit['blade_id']==row['blade_id']
    assert hit['slant_range_m']==pytest.approx(row['beams']['B2']['slant_range_m'],abs=.005)


def test_prefix_statistics_accept_only_cross_python_sum_roundoff():
    _, legacy, overlay = make_reader()
    statistics = overlay['results']['T1']['cumulative'][2]['statistics']
    statistics['bias_m'] += 1e-15
    statistics['mae_m'] -= 1e-15
    reader = DualBeamReader(legacy, overlay, 'T1')
    # Display uses recomputed statistics, not the perturbed saved values.
    assert reader.at(1.05)['statistics']['bias_m'] == 1.
    assert reader.at(1.05)['statistics']['mae_m'] == 1.
    statistics['mae_m'] += 1e-6
    with pytest.raises(ValueError, match='precomputed'):
        DualBeamReader(legacy, overlay, 'T1')


@pytest.mark.parametrize('location,field,value', [
    ('statistics', 'expected_samples', 3.0),
    ('statistics', 'valid_samples', True),
    ('statistics', 'valid_samples', 2),
    ('state', 'time_s', 1.05+1e-15),
    ('alarm', 'active', 1),
    ('alarm', 'expires_at_s', 1.525+1e-15),
    ('measurement', 'estimate_m', 20.+1e-15*4),
    ('measurement', 'error_m', 1.+1e-15),
])
def test_prefix_roundoff_exception_does_not_weaken_states_or_counts(location, field, value):
    _, legacy, overlay = make_reader()
    state = overlay['results']['T1']['cumulative'][2]
    target = state if location=='state' else state[location]
    target[field] = value
    with pytest.raises(ValueError, match='precomputed'):
        DualBeamReader(legacy, overlay, 'T1')


def test_event_times_remain_exact_with_statistics_roundoff_exception():
    _, legacy, overlay = make_reader()
    overlay['results']['T1']['events'][0]['hit_times_s'][0] += 1e-15
    with pytest.raises(ValueError, match='precomputed'):
        DualBeamReader(legacy, overlay, 'T1')
