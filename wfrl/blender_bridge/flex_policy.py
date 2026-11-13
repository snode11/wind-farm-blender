"""Strict deterministic yaw-policy adapter for recorded three-turbine runs."""
from pathlib import Path
import hashlib

import numpy as np
import torch

from wfrl.mappo_central import Actor
from wfrl.policy_actions import duty_headroom, load_action_contract, load_obs_duty_limit


class FlexPolicy:
    def __init__(self, checkpoint):
        path = Path(checkpoint)
        ck = torch.load(path, map_location='cpu', weights_only=False)
        if 'scene' in ck:
            raise ValueError('FlexPolicy probe supports only the legacy Row1 environment, without a saved scene')
        contract = load_action_contract(ck, controls=['yaw'], n_agents=3,
                                        env_id='Dec_Turb3_Row1_Fastfarm')
        contract.validate_runtime(['yaw'], {'yaw': (-5., 5.)})
        if contract.transform != 'clip':
            raise ValueError('FlexPolicy probe requires the legacy Row1 clip action contract')
        self.obs_duty = ck.get('obs_duty', False)
        self.obs_duty_limit = load_obs_duty_limit(ck)
        obs_dim = 3 + int(self.obs_duty)
        if (ck.get('env_id') != 'Dec_Turb3_Row1_Fastfarm'
                or ck.get('obs_dim') != obs_dim or ck.get('act_dim') != 1
                or ck.get('state_dim') != 3 * obs_dim or ck.get('recurrent', False)
                or ck.get('obs_keys') != ['yaw', 'wind_speed', 'wind_direction']
                or ck.get('controls', ['yaw']) != ['yaw']):
            raise ValueError('Checkpoint does not match the three-turbine yaw contract')
        self.mean = np.asarray(ck['obs_mean'], dtype=np.float32)
        var = np.asarray(ck['obs_var'], dtype=np.float32)
        if (self.mean.shape != (obs_dim,) or var.shape != (obs_dim,)
                or not np.isfinite(self.mean).all()
                or not np.isfinite(var).all() or (var < 0).any()):
            raise ValueError('Invalid checkpoint normalization')
        self.std = np.sqrt(var + 1e-8)
        self.actor = Actor(obs_dim, 1).eval()
        self.actor.load_state_dict(ck['actor'], strict=True)
        if not all(torch.isfinite(v).all() for v in self.actor.parameters()):
            raise ValueError('Nonfinite policy weights')
        self.metadata = dict(checkpoint=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            environment=ck['env_id'], turbine_order=['T1', 'T2', 'T3'],
            obs_keys=ck['obs_keys'], obs_mean=self.mean.tolist(),
            obs_duty=self.obs_duty, obs_duty_limit=self.obs_duty_limit,
            obs_var=var.tolist(), controls=['yaw'],
            inference='deterministic Normal.mean; stored normalization, no refit',
            action_unit='yaw increment degrees per control step')

    def infer(self, measure):
        cols = [np.asarray(measure[k], dtype=np.float32)
                for k in self.metadata['obs_keys']]
        if any(v.shape != (3,) or not np.isfinite(v).all() for v in cols):
            raise ValueError('Policy requires finite observations for all three turbines')
        if self.obs_duty:
            duty = np.asarray((measure.get('duty') or {}).get('yaw', np.full(3, np.nan)),
                              dtype=np.float32)
            if duty.shape != (3,) or not np.isfinite(duty).all():
                raise ValueError('Policy requires finite duty.yaw for all three turbines')
            cols.append(duty_headroom(duty, self.obs_duty_limit))
        obs = np.stack(cols, axis=1)
        norm = (obs - self.mean) / self.std
        with torch.no_grad():
            action = self.actor.dist(torch.from_numpy(norm)).mean.numpy().ravel()
        if not np.isfinite(action).all():
            raise ValueError('Nonfinite policy action')
        return action, norm
