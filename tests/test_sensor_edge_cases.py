import warnings
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.channels.sensors import (VibrationSensor, PowerSensor, ActuatorSensor,
                                  BladeLoadSensor, RotorSpeedSensor, AcousticSensor)
from wfrl.channels.sensors import NacelleCamera, NacelleLidar
from wfrl.channels.base import Fidelity, registry
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


def sensor_scene():
    return Scene(turbines=[Turbine('T1', 0, 0), Turbine('T2', 500, 0), Turbine('T3', 1000, 0)])


@pytest.mark.parametrize("sensor_type,key", [(PowerSensor, "power"), (ActuatorSensor, "yaw"),
                                           (RotorSpeedSensor, "torque")])
def test_scalar_sensor_requires_full_farm_shape_before_subselection(sensor_type, key):
    scene = sensor_scene()
    sensor = sensor_type(scene, SensorSpec(sensor_type.type_name), [0])
    for value in ([1.], [[1., 2., 3.]], [1., 2., 3., 4.], object()):
        with pytest.raises(ValueError, match=key):
            sensor.sample({key: value})
    error = sensor.step({key: [1.]})
    assert all("error" in reading for reading in error.values())


@pytest.mark.parametrize("sensor_type", [BladeLoadSensor, VibrationSensor, AcousticSensor])
def test_blade_sensors_require_three_loads_per_turbine(sensor_type):
    sensor = sensor_type(sensor_scene(), SensorSpec(sensor_type.type_name), [0])
    for value in (np.ones((1, 3)), np.ones((3, 2)), np.ones(3), object()):
        with pytest.raises(ValueError, match="m_flap"):
            sensor.sample({"m_flap": value})
    if isinstance(sensor, VibrationSensor):
        assert sensor._hist == []


@pytest.mark.parametrize("params", [dict(delay=0), dict(delay=-1), dict(delay=np.nan),
                                   dict(noise_std=0), dict(noise_std=-1), dict(noise_std=np.nan)])
def test_unimplemented_delay_and_noise_are_not_silently_accepted(params):
    scene = sensor_scene()
    scene.sensors = [SensorSpec("power", params=params)]
    with pytest.raises(ValueError, match="不支持参数"):
        scene.validate()
    with pytest.raises(ValueError, match="不支持参数"):
        registry.build(scene)


def test_unknown_sensor_and_invalid_parameter_mapping_raise_value_error():
    for name in ("missing", []):
        with pytest.raises(ValueError, match="传感器"):
            registry.get(name)
    for params in ([], 1, True):
        with pytest.raises(ValueError, match="params"):
            PowerSensor(sensor_scene(), SensorSpec("power", params=params), [0])


@pytest.mark.parametrize("value", [0., -1., np.nan, np.inf, True])
def test_sensor_sampling_rate_and_step_duration_are_validated(value):
    sensor = AcousticSensor(sensor_scene(), SensorSpec("acoustic"), [0])
    sensor.rate_hz = value
    with pytest.raises(ValueError):
        sensor.decimate()
    assert sensor._k == 0
    sensor.rate_hz = .2
    sensor.scene.dt = value
    with pytest.raises(ValueError):
        sensor.decimate()
    assert sensor._k == 0


def test_sampling_period_is_in_seconds_and_empty_mounts_keep_xyz_shape():
    sensor = AcousticSensor(sensor_scene(), SensorSpec("acoustic"), [])
    assert [sensor.decimate() for _ in range(5)] == [True, False, True, False, True]
    assert sensor.period_steps == 2
    assert sensor.poses({}).shape == (0, 3)
    lidar = NacelleLidar(sensor.scene, SensorSpec("lidar"), [])
    assert lidar.endpoints({}).shape == (0, 15, 3)


def test_lidar_beams_follow_each_nacelle_yaw_with_metre_ranges():
    scene = sensor_scene()
    lidar = NacelleLidar(scene, SensorSpec("lidar"), [2, 0, 1])
    points = lidar.endpoints({"yaw": [0., 90., -90.]})
    offsets = points - lidar.poses({})[:, None, :]
    np.testing.assert_allclose(offsets[:, 0, :], [[0., 50., 0.], [-50., 0., 0.], [0., -50., 0.]], atol=1e-12)
    for turbine in offsets:
        np.testing.assert_allclose(np.linalg.norm(turbine, axis=1), np.repeat([50., 100., 160.], 5))


@pytest.mark.parametrize("params", [dict(fov=np.nan), dict(fov=0), dict(fov=180),
    dict(pitch=None), dict(yaw_off=np.inf), dict(range=-1),
    dict(mount="custom", offset=[0., np.nan, 0.]), dict(mount="custom", dx_down=True)])
def test_camera_rejects_nonfinite_and_invalid_geometry_parameters(params):
    with pytest.raises(ValueError, match="camera"):
        NacelleCamera(sensor_scene(), SensorSpec("camera", params=params), [0])


def test_camera_rejects_malformed_yaw_without_a_finite_looking_pose():
    camera = NacelleCamera(sensor_scene(), SensorSpec("camera"), [0])
    for yaw in (object(), [[0., 10., 20.]], [np.nan, 0., 0.]):
        with pytest.raises(ValueError, match="yaw"):
            camera.camera_pose({"yaw": yaw}, 0)


def test_acoustic_missing_or_nonfinite_loads_stay_unknown_without_warning():
    sensor = AcousticSensor(sensor_scene(), SensorSpec("acoustic"), [0, 1, 2])
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        assert np.isnan(sensor.sample({})["acoustic"]).all()
        values = sensor.sample({"m_flap": [[np.nan, np.nan, np.inf],
                                            [2., 2., 2.], [4., np.nan, 4.]]})["acoustic"]
    assert np.isnan(values[0])
    np.testing.assert_allclose(values[1:], [1., 1.])


def test_sensor_units_preserve_newton_metres_and_convert_power_to_rotor_rpm():
    scene = sensor_scene()
    loads = np.arange(9, dtype=float).reshape(3, 3) * 1e6
    blade = BladeLoadSensor(scene, SensorSpec("bladeload"), [2, 0])
    np.testing.assert_array_equal(blade.sample({"m_flap": loads})["m_flap"], loads[[2, 0]])
    rotor = RotorSpeedSensor(scene, SensorSpec("rotorspeed"), [0, 1, 2])
    rpm = rotor.sample({"power": [.944, 2., np.nan], "torque": [10000., 0., 10000.]})["rotorspeed"]
    assert rpm[0] == pytest.approx(100. * 60. / (2 * np.pi * 97.))
    assert np.isnan(rpm[1:]).all()
