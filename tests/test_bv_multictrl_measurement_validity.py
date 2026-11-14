import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.experiments import probe_multictrl_ff as script


@pytest.mark.parametrize('key', ['yaw', 'load', 'yaw_command', 'yaw_measured'])
def test_multictrl_uses_flags_and_finite_values_for_statistics(key):
    readings = [{key: [value, 0.], f'{key}_valid': [valid, True]}
                for value, valid in [(10., True), (np.nan, True), (999., False), (30., True)]]
    summary = script.channel_summary(readings, key)
    assert summary['count'] == 2
    assert summary['mean'] == 20.
    assert summary['minimum'] == 10.
    assert summary['maximum'] == 30.
    assert summary['std'] == 10.
    assert '有效样本数=2' in script.describe(summary)


@pytest.mark.parametrize('key', ['yaw', 'load', 'torque', 'rotor_speed', 'pitch_meas',
                               'yaw_command', 'yaw_measured'])
def test_empty_multictrl_channel_never_computes_nan_statistics(key):
    readings = [{key: [np.nan], f'{key}_valid': [True]},
                {key: [999.], f'{key}_valid': [False]}]
    with warnings.catch_warnings():
        warnings.simplefilter('error', RuntimeWarning)
        summary = script.channel_summary(readings, key)
    assert summary['count'] == 0
    assert summary['mean'] is summary['std'] is summary['minimum'] is summary['maximum'] is None
    assert script.describe(summary) == '无有效数据（有效样本数=0）'


def test_uncontrolled_yaw_command_none_is_missing():
    summary = script.channel_summary([dict(yaw_command=None, yaw_command_valid=[False] * 3)],
                                     'yaw_command')
    assert summary['count'] == 0


def test_multictrl_main_reports_missing_snapshot_channels(monkeypatch, capsys):
    class Trainer:
        running = False
        error = None
        step = 0
        obs_keys = ['yaw', 'pitch', 'torque', 'wind_speed', 'wind_direction']
        actor = SimpleNamespace(mu=[SimpleNamespace(in_features=5)],
                                log_std=SimpleNamespace(numel=lambda: 3))
        limiter = SimpleNamespace(counts={'yaw': 1}, summary=lambda: {'yaw': 1},
                                  events=[SimpleNamespace(control=k) for k in ('yaw', 'torque')])

        def start(self):
            self.running = True

        def latest(self):
            self.step += 1
            return SimpleNamespace(step=self.step, measure=dict(
                torque=[self.step * 10.] * 3, pitch_meas=[0.] * 3, rotor_speed=[8.] * 3,
                yaw=[np.nan, 10., 20.], yaw_valid=[True] * 3,
                load=[np.nan] * 3, load_valid=[False] * 3,
                yaw_command=None, yaw_command_valid=[False] * 3,
                m_flap=np.full((3, 3), np.nan)))

        def stop(self, **kwargs):
            self.running = False

    monkeypatch.setattr(script, 'Trainer', lambda *args, **kwargs: Trainer())
    monkeypatch.setattr(script, 'wait_step', lambda *args, **kwargs: (True, 0.))
    monkeypatch.setattr(script.time, 'sleep', lambda _: None)
    monkeypatch.setattr(script, 'FAILS', [])
    assert script.main() == 0
    output = capsys.readouterr().out
    assert 'T1 yaw: 无有效数据（有效样本数=0）' in output
    assert 'T1 load: 无有效数据（有效样本数=0）' in output
    assert 'T1 yaw_command: 无有效数据（有效样本数=0）' in output
    assert 'nan' not in output.lower()
