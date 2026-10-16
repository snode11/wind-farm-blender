import math
from types import SimpleNamespace

import pytest

from wfrl_blender import camera_projection as projection, nacelle_camera as camera


def test_nominal_sensor_and_independent_output_gate():
    assert camera.SENSOR_WIDTH_MM == pytest.approx(9.052364451004323)
    assert camera.SENSOR_HEIGHT_MM == pytest.approx(5.8235237482592375)
    for size in (640, 1920, 3840):
        width, height, pixel_x, pixel_y = camera.output_settings(size)
        assert max(width, height) == size
        assert width * pixel_x / (height * pixel_y) == pytest.approx(projection.aspect(59, 40))
    axis = projection.optical_axis(camera.YAW_DEG, camera.PITCH_DEG)
    assert axis == pytest.approx((-math.sin(math.radians(8)), 0, -math.cos(math.radians(8))))


def test_mount_uses_rotor_mechanical_origin_without_spin():
    rotor = SimpleNamespace(location=(-5.0, 0.0, 2.4), rotation_euler=(1.4, -.1, 0))
    assert camera.mount_location(rotor) == pytest.approx((-3.5, 0, .9))
    rotor.rotation_euler = (2.1, -.1, 0)
    assert camera.mount_location(rotor) == pytest.approx((-3.5, 0, .9))


class Scene(dict):
    def __init__(self):
        super().__init__()
        self.render = SimpleNamespace(resolution_x=1920, resolution_y=1080,
                                      pixel_aspect_x=1.0, pixel_aspect_y=1.0)


def test_camera_selection_applies_and_restores_exact_gate():
    scene = Scene()
    nominal = {'wfrl_nacelle_intrinsics': camera.INTRINSICS_ID}
    camera.select_output(scene, nominal)
    settings = vars(scene.render).copy()
    assert settings['resolution_x'] == 1920 and settings['resolution_y'] == 1235
    assert settings['pixel_aspect_x'] == 1.0
    camera.select_output(scene, nominal)
    assert vars(scene.render) == settings
    camera.select_output(scene, {})
    assert vars(scene.render) == dict(resolution_x=1920, resolution_y=1080,
                                     pixel_aspect_x=1.0, pixel_aspect_y=1.0)
    assert not scene
