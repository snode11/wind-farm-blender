import numpy as np
import pytest

from scripts.experiments import probe_gain_ff as script


def test_run_one_counts_valid_turbines_and_complete_power_steps(monkeypatch):
    readings = [
        dict(yaw=[10., np.nan, 90.], yaw_valid=[True, True, False], power=[1., 2., 3.]),
        dict(yaw=[20., 40., 100.], yaw_valid=[True, True, False], power=[2., 3., 4.]),
        dict(yaw=[np.nan] * 3, yaw_valid=[False] * 3, power=[2., np.nan, 4.]),
    ]

    class Driver:
        n = 3
        xcoords = np.zeros(3)
        closed = False

        def reset(self, **kwargs):
            pass

        def step(self, push):
            return readings.pop(0)

        def close(self, **kwargs):
            self.closed = True

    driver = Driver()
    monkeypatch.setattr(script, 'FastFarmDriver', lambda *args, **kwargs: driver)
    for name in ('WARMUP', 'PUSH', 'SETTLE'):
        monkeypatch.setattr(script, name, 0)
    monkeypatch.setattr(script, 'READ', 3)
    yaw, power, up, counts = script.run_one(1.)
    assert yaw == pytest.approx(70 / 3)
    assert power == 7.5
    assert up == [0, 1, 2]
    assert counts == {'yaw': 3, 'power': 2}
    assert driver.closed


def test_gain_empty_baseline_reports_unavailable(monkeypatch, capsys):
    readings = [dict(yaw=[np.nan, 99.], yaw_valid=[True, False], power=[np.nan, 2.])]
    yaw, power, counts = script.summarize_readings(readings, [0, 1])
    assert yaw is power is None
    assert counts == {'yaw': 0, 'power': 0}
    monkeypatch.setattr(script, 'run_one', lambda *args, **kwargs: (yaw, power, [0, 1], counts))
    monkeypatch.setattr('sys.argv', ['probe_gain_ff'])
    assert script.main() == 0
    output = capsys.readouterr().out
    assert '无有效数据（有效样本数=0）' in output
    assert 'GAIN_FF_UNAVAILABLE' in output
    assert 'GAIN_FF_NONE' not in output
    assert 'nan' not in output.lower()


def test_flagged_finite_power_is_not_a_partial_farm_total():
    readings = [dict(yaw=[0., np.nan], yaw_valid=[True, False],
                     power=[2., 99.], power_valid=[True, False])]
    yaw, power, counts = script.summarize_readings(readings, [0, 1])
    assert yaw == 0.
    assert power is None
    assert counts == {'yaw': 1, 'power': 0}
