import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.channels.sensors import VibrationSensor
from wfrl.channels.sensors import NacelleCamera, NacelleLidar
from wfrl.channels.base import Fidelity
from wfrl.scene.schema import Scene, Turbine, SensorSpec


def test_camera_full_farm_and_presliced_yaw_use_the_same_mount_pose():
    scene = Scene(turbines=[Turbine('T1', 0, 0), Turbine('T2', 500, 0), Turbine('T3', 1000, 0)])
    camera = NacelleCamera(scene, SensorSpec('camera'), [2, 0])
    for yaw in [[0, 40, 90], [30, 10, -90]]:
        for local, global_index in enumerate(camera.indices):
            full = camera.camera_pose({'yaw': yaw}, local)
            sliced = camera.camera_pose({'yaw': [yaw[global_index]]}, local)
            for a, b in zip(full, sliced):
                np.testing.assert_allclose(a, b)
    with pytest.raises(ValueError, match='yaw'):
        camera.camera_pose({'yaw': [10, 30]}, 0)


def test_lidar_statistics_are_explicitly_synthetic():
    scene = Scene(turbines=[Turbine('T1', 0, 0)])
    sensor = NacelleLidar(scene, SensorSpec('lidar'), [0])
    assert sensor.fidelity is Fidelity.SYNTH
    for stats in [None, SimpleNamespace(u_hub=8, ti=10)]:
        data = sensor.sample({'_source': SimpleNamespace(bts=stats), 't': 2})['lidar']
        assert data['source_kind'] == 'SYNTHETIC'
        assert data['has_turbulence'] is False
        assert data['bts_statistics_used'] is (stats is not None)
        assert data['bts_spatial_interpolation'] is False


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
