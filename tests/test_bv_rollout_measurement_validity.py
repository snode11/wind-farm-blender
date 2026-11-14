from types import SimpleNamespace

import numpy as np
import pytest

from scripts.experiments import rollout_ckpt as script


@pytest.mark.parametrize('key', ['yaw', 'load', 'yaw_command', 'yaw_measured'])
@pytest.mark.parametrize('values, flags', [([1., np.nan, 3.], [True] * 3),
                                         ([1., 2., 3.], [True, False, True])])
def test_policy_rejects_nonfinite_or_flagged_observation(key, values, flags):
    measure = {key: values, f'{key}_valid': flags}
    with pytest.raises(ValueError, match=rf'{key}.*机组 \[2\].*不做插补'):
        script.pack_obs(measure, 3, [key], False)


def test_policy_preserves_degrees_and_does_not_impute_duty():
    measure = dict(yaw=[0., 10., -20.], yaw_valid=[True] * 3,
                   load=[np.nan] * 3, load_valid=[False] * 3)
    np.testing.assert_array_equal(script.pack_obs(measure, 3, ['yaw'], False)[:, 0],
                                  [0., 10., -20.])
    with pytest.raises(ValueError, match='duty.yaw'):
        script.pack_obs(measure, 3, ['yaw'], True)
    measure['duty'] = {'yaw': [0., np.nan, .1]}
    with pytest.raises(ValueError, match='duty.yaw'):
        script.pack_obs(measure, 3, ['yaw'], True)


def test_rollout_reports_masked_mean_extrema_counts_and_empty(capsys):
    yaw = np.array([[10., np.nan, 0.], [999., 99., 0.], [30., np.nan, 0.]])
    yaw_valid = np.array([[True, True, True], [False, False, True], [True, False, True]])
    power = np.array([[1., 2., 3.], [2., np.nan, 4.], [2., 3., 4.]])
    script.report_rollout(yaw, power, yaw_valid, np.isfinite(power))
    output = capsys.readouterr().out
    assert 'mean=20.00, min=10.00, max=30.00 （有效样本数=2）' in output
    assert 'T2 末50步偏航角(°): 无有效数据（有效样本数=0）' in output
    assert '末50步全场功率(MW): mean=7.5000' in output
    assert 'nan' not in output.lower()


def test_rollout_empty_window_has_no_numeric_statistics(capsys):
    values = np.full((3, 3), np.nan)
    script.report_rollout(values, values, np.ones((3, 3), bool), np.zeros((3, 3), bool))
    output = capsys.readouterr().out
    assert '无有效数据（有效样本数=0）' in output
    assert 'mean=' not in output
    assert 'nan' not in output.lower()


def test_rollout_main_saves_effective_validity_masks(monkeypatch, capsys):
    actor = script.Actor(3, 1)
    ck = dict(obs_keys=['yaw', 'wind_speed', 'wind_direction'], obs_dim=3, act_dim=1,
              obs_mean=np.zeros(3), obs_var=np.ones(3), actor=actor.state_dict())
    scene = SimpleNamespace(n=3, controls=['yaw'], to_dict=lambda: {})
    contract = SimpleNamespace(controls=['yaw'], low=[-5.], high=[5.], transform='clip',
                               validate_runtime=lambda *args: None,
                               commands=lambda *args: {'yaw': np.zeros(3)})

    class Runtime:
        closed = False
        driver = SimpleNamespace(act_bounds={'yaw': (-5., 5.)})
        last_measure = dict(yaw=[0.] * 3, yaw_valid=[True] * 3,
                            wind_speed=[8.] * 3, wind_direction=[270.] * 3)

        def reset(self):
            pass

        def step(self, **kwargs):
            self.last_measure = dict(yaw=[10., np.nan, 99.], yaw_valid=[True, True, False],
                                     power=[1., np.nan, 3.])

        def close(self, **kwargs):
            self.closed = True

    runtime = Runtime()
    saved = {}
    monkeypatch.setattr(script.torch, 'load', lambda *args, **kwargs: ck)
    monkeypatch.setattr(script, 'load_scene', lambda *args: scene)
    monkeypatch.setattr(script, 'load_action_contract', lambda *args, **kwargs: contract)
    monkeypatch.setattr(script, 'SceneRuntime', lambda *args, **kwargs: runtime)
    monkeypatch.setattr(script, 'WARMUP', 0)
    monkeypatch.setattr(script.np, 'savez', lambda path, **arrays: saved.update(arrays))
    monkeypatch.setattr('sys.argv', ['rollout_ckpt', '--ep-steps', '1'])
    assert script.main() == 0
    np.testing.assert_equal(saved['yaw_valid'], [[True, False, False]])
    np.testing.assert_equal(saved['power_valid'], [[True, False, True]])
    np.testing.assert_equal(saved['yaw'], [[10., np.nan, 99.]])
    assert runtime.closed
    assert '末50步全场功率(MW): 无有效数据（有效样本数=0）' in capsys.readouterr().out
