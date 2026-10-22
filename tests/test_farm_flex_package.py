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


@pytest.mark.parametrize('entrypoint', ['attach', 'constructor'])
@pytest.mark.parametrize('source', ['farm', 'dual_beam'])
def test_runtime_reuses_one_validated_package(monkeypatch, entrypoint, source):
    from wfrl_blender import clearance_replay, deflection, farm_flex, prebend, tower_motion

    path = (farm_flex.default_package() if source == 'farm'
            else farm_flex.default_dual_package())
    calls = dict(archives=[], resolved=[], references=[], towers=[], comparison_paths=[])
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
    monkeypatch.setattr(farm_flex, 'detach', lambda scene: None)
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
    assert runtime.reference is calls['references'][0]
    assert runtime.tower_motion is calls['towers'][0]
    assert calls['comparison_paths'] == [calls['resolved'][0][0]]
    if entrypoint == 'attach':
        assert scene['wfrl_flex_active'] is False
