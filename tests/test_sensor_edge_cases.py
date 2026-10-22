import warnings
from types import SimpleNamespace

import numpy as np

from wfrl.channels.sensors import VibrationSensor


def test_vibration_sensor_accepts_missing_blade_loads_without_warning():
    sensor = object.__new__(VibrationSensor)
    sensor.scene = SimpleNamespace(n=3)
    sensor.indices = np.arange(3)
    sensor._hist = []

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        missing = sensor.sample({"m_flap": np.full((3, 3), np.nan)})
        partial = sensor.sample({
            "m_flap": np.array([[1.0, np.nan, 3.0],
                                [np.nan, np.nan, np.nan],
                                [2.0, 4.0, 6.0]])
        })

    assert np.isnan(missing["vibration"]).all()
    assert np.isfinite(partial["vibration"][[0, 2]]).all()
    assert np.isnan(partial["vibration"][1]).all()
