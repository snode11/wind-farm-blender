"""The recorded installation contract must be usable without Blender."""
from dataclasses import replace
import math

import pytest

from wfrl_blender.custom_cameras import CameraParameters, validate_parameters, screen_direction_delta


@pytest.mark.parametrize("field", ["yaw", "pitch", "roll", "fov", "vfov", "focal_length_mm_record", "clip_near_m", "clip_far_m"])
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_angles_are_rejected(field, value):
    with pytest.raises(ValueError):
        validate_parameters(replace(CameraParameters(location=(1, 2, 3)), **{field: value}))


@pytest.mark.parametrize("coordinate", range(3))
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_lens_centres_are_rejected(coordinate, value):
    location = [1, 2, 3]
    location[coordinate] = value
    with pytest.raises(ValueError):
        validate_parameters(CameraParameters(location=tuple(location)))


@pytest.mark.parametrize("field,value", [("yaw", -0.001), ("yaw", 360),
    ("pitch", -90.001), ("pitch", 90.001), ("fov", .999), ("fov", 170.001)])
def test_out_of_range_input_is_rejected_without_clamping(field, value):
    params = replace(CameraParameters(location=(1, 2, 3)), **{field: value})
    with pytest.raises(ValueError):
        validate_parameters(params)
    assert getattr(params, field) == value


@pytest.mark.parametrize("pitch", [-90, 0, 90])
@pytest.mark.parametrize("fov", [10, 75, 120])
def test_valid_parameters_keep_full_precision_even_at_poles(pitch, fov):
    params = CameraParameters(location=(1.23456789123, -2.34567891234, 3.45678912345),
                              yaw=123.456789123, pitch=pitch, roll=47.123456789, fov=fov)
    assert validate_parameters(params) == params


@pytest.mark.parametrize("roll", [0, 37, 90, 180, 271])
def test_idle_direction_input_does_not_redecompose_angles(roll):
    for pitch in (-90, -15.4, 90):
        yaw, out_pitch = screen_direction_delta(123.456, pitch, roll, 0, 0)
        assert yaw == pytest.approx(123.456)
        assert out_pitch == pytest.approx(pitch)


def test_screen_input_roll_is_continuous_and_periodic():
    # An arbitrary roll must affect drag controls, not only special-case 180°.
    base = screen_direction_delta(180, 0, 0, 2, 1)
    rotated = screen_direction_delta(180, 0, 47, 2, 1)
    assert rotated != pytest.approx(base)
    assert screen_direction_delta(180, 0, 407, 2, 1) == pytest.approx(rotated)
    below = screen_direction_delta(180, 0, 89.999, 2, 1)
    above = screen_direction_delta(180, 0, 90.001, 2, 1)
    assert max(abs(a-b) for a, b in zip(below, above)) < 0.001
