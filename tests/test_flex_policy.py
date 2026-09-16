import numpy as np
import pytest
import torch

from wfrl.blender_bridge.flex_policy import FlexPolicy
from wfrl.mappo_central import Actor


def checkpoint(tmp_path, **changes):
    ck = dict(env_id='Dec_Turb3_Row1_Fastfarm', obs_dim=3, act_dim=1,
              state_dim=9, obs_keys=['yaw', 'wind_speed', 'wind_direction'],
              obs_mean=np.array([0., 8., 270.]), obs_var=np.ones(3),
              actor=Actor(3, 1).state_dict())
    ck.update(changes)
    path=tmp_path/'policy.pt'
    torch.save(ck, path)
    return path


def test_normalization_and_deterministic_inference(tmp_path):
    policy=FlexPolicy(checkpoint(tmp_path))
    m=dict(yaw=np.array([0.,1.,2.]),wind_speed=np.array([8.,14.,19.]),
           wind_direction=np.full(3,270.))
    action, norm=policy.infer(m)
    assert np.allclose(norm,[[0,0,0],[1,6,0],[2,11,0]])
    assert np.array_equal(action,policy.infer(m)[0])
    assert len(policy.metadata['sha256'])==64
    m['yaw'][1]=np.nan
    with pytest.raises(ValueError,match='finite observations'):
        policy.infer(m)


@pytest.mark.parametrize('changes',[
    dict(state_dim=21), dict(obs_keys=['wind_speed','yaw','wind_direction']),
    dict(obs_var=[1,-1,1]),dict(recurrent=True),dict(controls=['pitch'])])
def test_reject_incompatible_checkpoint(tmp_path,changes):
    with pytest.raises(ValueError):
        FlexPolicy(checkpoint(tmp_path,**changes))
