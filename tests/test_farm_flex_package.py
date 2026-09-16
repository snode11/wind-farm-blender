import hashlib
import json
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
