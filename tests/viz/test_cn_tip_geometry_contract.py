"""Finite geometry, rotation conventions and integration state contracts."""

import math

import numpy as np
import pytest

from wfrl.viz.tip_geometry import (
    CameraModel, RpmAzimuthIntegrator, TurbineGeometry, finite_scalar,
    finite_vector, rotation_matrix, tip_3d_from_pixel,
)


def geometry():
    return TurbineGeometry(110, 5, 5, 68, 2)


@pytest.mark.parametrize("value", [
    [True, 2, 3], ["invalid", 2, 3], np.array([1 + 2j, 2, 3]),
    np.ma.array([1., 2., 3.], mask=[False, True, False]),
    [10**400, 2, 3],
])
def test_invalid_vector_values_cannot_become_valid_coordinates(value):
    with pytest.raises(ValueError):
        finite_vector(value)


@pytest.mark.parametrize("value", [None, 10**400, np.ma.array(3., mask=True)])
def test_invalid_scalars_raise_value_error(value):
    with pytest.raises(ValueError):
        finite_scalar(value)


def test_numeric_strings_used_by_csv_readers_remain_supported():
    assert finite_scalar("3.5") == 3.5
    np.testing.assert_array_equal(finite_vector(["12.5", "-2.25"], 2), [12.5, -2.25])


def test_masked_calibration_cannot_become_valid_pose_or_intrinsics():
    masked_rotation = np.ma.array(np.eye(3), mask=np.eye(3, dtype=bool))
    with pytest.raises(ValueError):
        rotation_matrix(masked_rotation)
    with pytest.raises(ValueError):
        CameraModel.from_calibration(640, 480, masked_rotation, None, [0, 0, 0], np.eye(3))
    with pytest.raises(ValueError):
        CameraModel(640, 480, 60, 40, [0, 0, 0], np.eye(3),
                    distortion=np.ma.array(np.zeros(5), mask=[True, False, False, False, False]))


def test_camera_owns_distortion_coefficients():
    distortion = np.zeros(5)
    camera = CameraModel(640, 480, 60, 40, [0, 0, 0], np.eye(3), distortion=distortion)
    distortion[0] = np.nan
    np.testing.assert_array_equal(camera.distortion, np.zeros(5))


def test_masked_pixel_is_not_an_estimated_observation():
    geom = geometry()
    camera = CameraModel.look_at(640, 480, 60, 40, geom.hub, geom.tip_position(180), geom.e_lat)
    assert tip_3d_from_pixel(camera, geom, np.ma.array(320., mask=True), 240) is None


@pytest.mark.parametrize("direction", [float("nan"), float("inf"), True])
def test_integrator_rejects_invalid_direction(direction):
    with pytest.raises(ValueError):
        RpmAzimuthIntegrator(geometry(), direction=direction)


@pytest.mark.parametrize("failure", ["overflow", "geometry", "decompose"])
def test_failed_integration_preserves_phase_and_time(monkeypatch, failure):
    geom = geometry()
    integrator = RpmAzimuthIntegrator(geom)
    integrator.seed(15, 0)
    with monkeypatch.context() as patch:
        if failure != "overflow":
            def fail(*args):
                raise ValueError("invalid geometry")
            patch.setattr(geom, "tip_position" if failure == "geometry" else "decompose", fail)
        with pytest.raises(ValueError):
            integrator.step(1, 1e308 if failure == "overflow" else 10)
    assert (integrator.phi, integrator.t) == (15, 0)
    assert integrator.step(1, 10)["azimuth_deg_integrated"] == 75


@pytest.mark.parametrize("yaw", [0, 90, -90, 360])
@pytest.mark.parametrize("azimuth", [0, 90, 180, 360, -90])
def test_tip_coordinates_follow_metre_degree_and_rotation_conventions(yaw, azimuth):
    geom = TurbineGeometry(110, 5, 5, 68, 2, origin_w=(10, -20, 7), yaw_deg=yaw)
    phi, psi, tilt, heading = map(math.radians, [azimuth, 3, 5, yaw])
    local = np.array([
        5 - 68 * (math.cos(psi) * math.cos(phi) * math.sin(tilt) + math.sin(psi) * math.cos(tilt)),
        68 * math.cos(psi) * math.sin(phi),
        110 + 68 * (math.cos(psi) * math.cos(phi) * math.cos(tilt) - math.sin(psi) * math.sin(tilt)),
    ])
    expected = [10 + math.cos(heading)*local[0] - math.sin(heading)*local[1],
                -20 + math.sin(heading)*local[0] + math.cos(heading)*local[1], 7 + local[2]]
    point = geom.tip_position(azimuth, 3)
    np.testing.assert_allclose(point, expected, atol=1e-12)
    derived = geom.decompose(point)
    assert derived["radius_m"] == pytest.approx(68)
    assert derived["out_of_plane_m"] == pytest.approx(68 * math.sin(psi))
    assert derived["out_of_plane_deg"] == pytest.approx(3)
    assert abs((derived["azimuth_deg"] - azimuth + 180) % 360 - 180) < 1e-10


@pytest.mark.parametrize("direction", [-1, 1])
def test_rpm_integration_uses_seconds_and_wraps_angles(direction):
    integrator = RpmAzimuthIntegrator(geometry(), direction=direction)
    assert integrator.step(0, 10) is None
    integrator.seed(-10, 2)
    expected = (350 + direction * 120) % 360
    assert integrator.step(4, 10)["azimuth_deg_integrated"] == expected
    with pytest.raises(ValueError, match="Rewind"):
        integrator.step(3, 10)
    assert (integrator.phi, integrator.t) == (expected, 4)
