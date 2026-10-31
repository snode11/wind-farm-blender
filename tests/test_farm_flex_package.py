import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import numpy as np
import pytest

from wfrl.lidar.replay import precompute
from wfrl_blender.farm_flex import read_package


def fixture(tmp_path,pose_error=False):
    times=np.array([0.,.025]);transforms=np.zeros((2,3,3,19,3,4),np.float32)
    transforms[...,:3]=np.eye(3)
    poses=np.zeros((2,3,6),np.float32);poses[:,:,1]=[[359],[361]];poses[:,:,2]=10
    settings=dict(threshold_m=7.,hysteresis_m=.1,max_hold_s=5.,passage_margin=1.25)
    data={}
    for i,tid in enumerate(('T1','T2','T3')):
        motion=[dict(time_s=float(t),yaw_deg=0.,azimuth_deg=359.+2*j,
                     rotor_speed_rpm=10.,pitch_deg=[0.,0.,0.]) for j,t in enumerate(times)]
        cumulative,statistics=precompute([],motion,settings)
        data[tid]=dict(motion=motion,measurements=[],cumulative=cumulative,statistics=statistics)
    if pose_error:poses[1,2,1]=0
    np.savez_compressed(tmp_path/'geometry.npz',times=times,transforms=transforms,poses=poses)
    (tmp_path/'data.json').write_text(json.dumps(data))
    (tmp_path/'source-surfaces.json').write_text('{}')
    m=dict(schema='wfrl.farm-flex-review.v1',status='REVIEW_ONLY',turbine_ids=['T1','T2','T3'],
           segment=dict(start_s=0.,end_s=.025),replay=settings,
           files={n:hashlib.sha256((tmp_path/n).read_bytes()).hexdigest()
                  for n in ('geometry.npz','data.json','source-surfaces.json')})
    (tmp_path/'manifest.json').write_text(json.dumps(m))
    return tmp_path


def test_common_clock_and_azimuth_wrap(tmp_path):
    *_,readers=read_package(fixture(tmp_path))
    assert all(r.at(.0125)['motion']['azimuth_deg']==360 for r in readers.values())
    assert all(r.at(.0125)['measurement'] is None for r in readers.values())
    (tmp_path/'geometry.npz').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='integrity'):
        read_package(tmp_path)


def test_reject_cross_file_pose_mismatch(tmp_path):
    with pytest.raises(ValueError,match='poses differ'):
        read_package(fixture(tmp_path,pose_error=True))


def flexible_fixture(tmp_path):
    path=fixture(tmp_path)
    m=json.loads((path/'manifest.json').read_text())
    m.update(schema='wfrl.farm-flex-review.v2',tower_model='elastodyn-flexible',layout_m=[[0,0,0],[504,0,0],[1008,0,0]])
    times=np.array([0.,.025]);nacelle=np.zeros((2,3,3,4));nacelle[...,:3]=np.eye(3)
    nacelle[1,:,0,3]=.1
    tower=np.zeros((2,3,2,3,4));tower[...,:3]=np.eye(3);tower[1,:,1,0,3]=.1
    np.savez_compressed(path/'tower-motion.npz',times=times,heights=[0.,87.6],transforms=tower,nacelle=nacelle)
    data=json.loads((path/'data.json').read_text())
    for k,tid in enumerate(m['turbine_ids']):
        for i,row in enumerate(data[tid]['motion']):
            row['nacelle_transform']=nacelle[i,k].tolist()
            row['nacelle_position_m']=(np.array(m['layout_m'][k])+[i*.1,0,87.6]).tolist()
    (path/'data.json').write_text(json.dumps(data))
    for name in ('tower-motion.npz','data.json'):
        m['files'][name]=hashlib.sha256((path/name).read_bytes()).hexdigest()
    (path/'manifest.json').write_text(json.dumps(m))
    return path,m


def test_flexible_support_clock_and_corruption(tmp_path):
    path,m=flexible_fixture(tmp_path)
    read_package(path)
    with np.load(path/'tower-motion.npz') as a: content=dict(a)
    content['times']=np.array([0.,.05])
    np.savez_compressed(path/'tower-motion.npz',**content)
    m['files']['tower-motion.npz']=hashlib.sha256((path/'tower-motion.npz').read_bytes()).hexdigest()
    (path/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='tower motion'):read_package(path)


def test_reject_flexible_reference_mismatch(tmp_path):
    path,m=flexible_fixture(tmp_path)
    data=json.loads((path/'data.json').read_text())
    data['T2']['motion'][1]['nacelle_position_m'][0]+=.5
    (path/'data.json').write_text(json.dumps(data))
    m['files']['data.json']=hashlib.sha256((path/'data.json').read_bytes()).hexdigest()
    (path/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='Nacelle position'):read_package(path)


def rewrite_data(path, data):
    (path/'data.json').write_text(json.dumps(data))
    manifest = json.loads((path/'manifest.json').read_text())
    manifest['files']['data.json'] = hashlib.sha256((path/'data.json').read_bytes()).hexdigest()
    (path/'manifest.json').write_text(json.dumps(manifest))


def measured_fixture(tmp_path):
    path = fixture(tmp_path)
    data = json.loads((path/'data.json').read_text())
    invalid = dict(valid=False, slant_range_m=None, estimate_m=None, error_m=None,
                   reason='no_hit', hit_point_m=None)
    row = dict(time_s=0., blade_id=1, expected=True, passage_id='b1-p0', truth_m=3.,
               truth_tip_point_m=[3., 0., 0.], truth_wall_point_m=[0., 0., 0.],
               beams=dict(B1=invalid.copy(), B3=invalid.copy(),
                          B2=dict(valid=True, slant_range_m=4., estimate_m=3.25,
                                  error_m=.25, reason='valid', hit_point_m=[0., 0., 4.])))
    payload = data['T1']
    payload['measurements'] = [row]
    replay = json.loads((path/'manifest.json').read_text())['replay']
    payload['cumulative'], payload['statistics'] = precompute([row], payload['motion'], replay)
    rewrite_data(path, data)
    return path, data


def test_review_b2_keeps_held_reading_and_empty_turbine_statistics(tmp_path):
    path, _ = measured_fixture(tmp_path)
    *_, readers = read_package(path)
    sample = readers['T1'].at(.0125)
    assert sample['measurement']['estimate_m'] == 3.25
    assert sample['measurement']['slant_range_m'] == 4.
    assert sample['status'] == 'near_threshold'
    assert sample['statistics'] == dict(expected_samples=1, valid_samples=1, valid_ratio=1.,
        mae_m=.25, max_abs_error_m=.25, p95_abs_error_m=.25, max_positive_bias_m=.25,
        passage_count=1, missed_passage_count=0)
    for tid in ('T2', 'T3'):
        empty = readers[tid].at(.0125)
        assert empty['measurement'] is None and empty['status'] == 'waiting'
        assert empty['statistics'] == dict(expected_samples=0, valid_samples=0, valid_ratio=None,
            mae_m=None, max_abs_error_m=None, p95_abs_error_m=None, max_positive_bias_m=None,
            passage_count=0, missed_passage_count=0)


@pytest.mark.parametrize('corruption', [
    'unobserved_reading', 'wrong_total', 'wrong_count_type', 'wrong_status',
    'changed_reading', 'changed_expiry', 'future_prefix', 'nonfinite_statistics',
    'nonfinite_audit', 'invalid_time', 'duplicate_observation', 'unordered_observations',
    'signed_error', 'invalid_estimate', 'unexpected_valid', 'boolean_blade',
])
def test_reject_inconsistent_review_measurements_and_prefixes(tmp_path, corruption):
    path, data = measured_fixture(tmp_path)
    payload = data['T1']
    row, state = payload['measurements'][0], payload['cumulative'][0]
    if corruption == 'unobserved_reading':
        payload['measurements'] = []
    elif corruption == 'wrong_total':
        payload['statistics']['mae_m'] += 1e-8
    elif corruption == 'wrong_count_type':
        state['statistics']['valid_samples'] = True
    elif corruption == 'wrong_status':
        state['status'] = 'above_threshold'
    elif corruption == 'changed_reading':
        state['measurement']['estimate_m'] += 5e-13
    elif corruption == 'changed_expiry':
        state['measurement']['expires_at_s'] += 5e-13
    elif corruption == 'future_prefix':
        state['time_s'] = .02
    elif corruption == 'nonfinite_statistics':
        payload['statistics']['mae_m'] = float('nan')
    elif corruption == 'nonfinite_audit':
        row['truth_tip_point_m'][0] = float('inf')
    elif corruption == 'invalid_time':
        row['time_s'] = -.001
    elif corruption == 'duplicate_observation':
        payload['measurements'].append(row.copy())
    elif corruption == 'unordered_observations':
        later = dict(row, time_s=.025)
        payload['measurements'].insert(0, later)
    elif corruption == 'signed_error':
        row['beams']['B2']['error_m'] = -.25
    elif corruption == 'invalid_estimate':
        row['beams']['B2'].update(valid=False, reason='no_hit')
    elif corruption == 'unexpected_valid':
        row['expected'] = False
    elif corruption == 'boolean_blade':
        row['blade_id'] = True
    if corruption in ('invalid_time', 'duplicate_observation', 'unordered_observations',
                      'signed_error', 'invalid_estimate', 'unexpected_valid', 'boolean_blade'):
        replay = json.loads((path/'manifest.json').read_text())['replay']
        payload['cumulative'], payload['statistics'] = precompute(
            payload['measurements'], payload['motion'], replay)
    rewrite_data(path, data)
    with pytest.raises(ValueError):
        read_package(path)


def test_review_statistics_allow_only_aggregate_roundoff(tmp_path):
    path, data = measured_fixture(tmp_path)
    data['T1']['cumulative'][0]['statistics']['mae_m'] += 5e-13
    data['T1']['statistics']['mae_m'] += 5e-13
    rewrite_data(path, data)
    *_, readers = read_package(path)
    assert readers['T1'].at(.0125)['statistics']['mae_m'] == .25
    assert readers['T1'].package.statistics['mae_m'] == .25


def test_review_with_no_valid_b2_keeps_missing_reading_and_null_errors(tmp_path):
    path, data = measured_fixture(tmp_path)
    payload = data['T1']
    beams = payload['measurements'][0]['beams']
    beams['B1'], beams['B2'] = beams['B2'], beams['B1']
    settings = json.loads((path/'manifest.json').read_text())['replay']
    payload['cumulative'], payload['statistics'] = precompute(
        payload['measurements'], payload['motion'], settings)
    rewrite_data(path, data)
    *_, readers = read_package(path)
    sample = readers['T1'].at(.0125)
    assert sample['measurement'] is None and sample['status'] == 'waiting'
    assert sample['statistics'] == dict(expected_samples=1, valid_samples=0, valid_ratio=0.,
        mae_m=None, max_abs_error_m=None, p95_abs_error_m=None, max_positive_bias_m=None,
        passage_count=1, missed_passage_count=1)


@pytest.fixture
def cold_package_cache():
    from wfrl_blender import farm_flex

    farm_flex.clear_package_cache()
    yield
    farm_flex.clear_package_cache()


@pytest.mark.parametrize('entrypoint', ['attach', 'constructor'])
@pytest.mark.parametrize('source', ['farm', 'dual_beam'])
def test_runtime_reuses_one_validated_package(monkeypatch, cold_package_cache, entrypoint, source):
    from wfrl_blender import clearance_replay, deflection, farm_flex, prebend, tower_motion

    path = (farm_flex.default_package() if source == 'farm'
            else farm_flex.default_dual_package())
    calls = dict(archives=[], resolved=[], references=[], towers=[], comparison_paths=[], comparison_inputs=[])
    original_load = np.load
    original_resolve = farm_flex.dual_beam_api().resolve_package
    original_reference = prebend.read_reference
    original_tower = tower_motion.read_tower
    original_comparison = deflection.read_comparison
    runtime_type = farm_flex.FarmFlex
    runtime = runtime_type.__new__(runtime_type)

    def load_archive(path, **kwargs):
        calls['archives'].append(Path(path).name)
        return original_load(path, **kwargs)

    def resolve(path):
        result = original_resolve(path)
        calls['resolved'].append(result)
        return result

    def reference(*args):
        result = original_reference(*args)
        calls['references'].append(result)
        return result

    def tower(*args):
        result = original_tower(*args)
        calls['towers'].append(result)
        return result

    class BeforeMeshBinding(Exception):
        pass

    def comparison(path, *args, **kwargs):
        calls['comparison_paths'].append(path)
        calls['comparison_inputs'].append(kwargs)
        original_comparison(path, *args, **kwargs)
        raise BeforeMeshBinding

    def construct(scene, path, **kwargs):
        runtime_type.__init__(runtime, scene, path, **kwargs)
        return runtime

    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    monkeypatch.setattr(np, 'load', load_archive)
    monkeypatch.setattr(farm_flex.dual_beam_api(), 'resolve_package', resolve)
    monkeypatch.setattr(prebend, 'read_reference', reference)
    monkeypatch.setattr(tower_motion, 'read_tower', tower)
    monkeypatch.setattr(deflection, 'read_comparison', comparison)
    monkeypatch.setattr(farm_flex, 'FarmFlex', construct)
    monkeypatch.setattr(farm_flex, '_ACTIVES', {})
    monkeypatch.setattr(farm_flex, 'detach', lambda scene=None, *, restore=True: None)
    monkeypatch.setattr(clearance_replay, 'clear', lambda scene, reason: None)

    class Scene(dict):
        objects = {f'WFRL.Turbine.{tid}.{suffix}': object()
                   for tid in ('T1', 'T2', 'T3')
                   for suffix in ('YawRoot', 'Rotor', 'Tower', 'Blade1', 'Blade2', 'Blade3')}

    scene = Scene()
    with pytest.raises(BeforeMeshBinding):
        if entrypoint == 'attach':
            farm_flex.attach(scene, path)
        else:
            construct(scene, path)
    assert calls['archives'].count('geometry.npz') == 1
    assert calls['archives'].count('tower-motion.npz') == 1
    assert len(calls['resolved']) == len(calls['references']) == len(calls['towers']) == 1
    assert runtime.reference is not calls['references'][0]
    assert runtime.reference == calls['references'][0]
    assert runtime.reference['curve'] is not calls['references'][0]['curve']
    assert runtime.tower_motion is not calls['towers'][0]
    assert runtime.tower_motion.keys() == calls['towers'][0].keys()
    for key, values in runtime.tower_motion.items():
        assert values is calls['towers'][0][key]
        assert not values.flags.writeable
    assert calls['comparison_inputs'][0]['blade_reference'] is runtime.reference
    assert calls['comparison_inputs'][0]['tower_motion'] is runtime.tower_motion
    assert calls['comparison_paths'] == [calls['resolved'][0][0]]
    if entrypoint == 'attach':
        assert scene['wfrl_flex_active'] is False
