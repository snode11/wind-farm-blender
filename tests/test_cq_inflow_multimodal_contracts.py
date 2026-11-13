"""Invalid inflow and multimodal inputs cannot become usable observations."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl import inflow, multimodal
from wfrl.windcond import make_wind_schedule


@pytest.mark.parametrize("speed", ["nan", "inf", "-inf", "0", "-2", "1e999"])
@pytest.mark.parametrize("kind", ["pin", "cycle"])
def test_schedule_rejects_nonpositive_or_nonfinite_speed(kind, speed):
    spec = f"pin:{speed}" if kind == "pin" else f"cycle:8,{speed},10"
    with pytest.raises(ValueError, match="风速"):
        make_wind_schedule(spec)


def test_explicit_positive_speeds_are_not_clipped_to_sampling_bounds():
    assert make_wind_schedule("pin:1")(0) == (1., None)
    assert make_wind_schedule("cycle:1,40")(1) == (40., None)


@pytest.fixture
def bts_source(tmp_path, monkeypatch):
    import openfast_toolbox.io

    path = tmp_path / "wind.bts"
    path.write_bytes(b"first")
    u = np.zeros((3, 3, 2, 2))
    u[0] = [[[6., 10.], [8., 12.]], [[8., 12.], [10., 14.]],
            [[10., 14.], [12., 16.]]]
    data = {"u": u, "t": np.array([0., .5, 1.]), "y": np.array([-5., 5.]),
            "z": np.array([80., 100.])}
    reads = []

    def read(filename):
        reads.append(filename)
        return data

    monkeypatch.setattr(openfast_toolbox.io, "TurbSimFile", read)
    monkeypatch.setattr(inflow, "_CACHE", {})
    return NS(path=path, data=data, reads=reads)


@pytest.mark.parametrize("height", [np.nan, np.inf, -np.inf, 79., 101., True])
def test_bts_hub_height_must_be_finite_and_inside_grid(bts_source, height):
    with pytest.raises(ValueError, match="高度|z_hub"):
        inflow.bts_info(str(bts_source.path), z_hub=height)


@pytest.mark.parametrize("field,value", [
    ("t", [0., 0., 1.]), ("t", [0., 1., .5]), ("t", [0., np.nan, 1.]),
    ("t", [0.]), ("z", [100., 80.]), ("z", [80., 80.]),
    ("z", [80., np.nan]), ("y", [-5.]), ("y", [-5., np.inf]),
    ("u", np.full((3, 3, 2, 2), np.nan)),
    ("u", np.zeros((3, 3, 2, 2))),
    ("u", np.full((3, 3, 2, 2), -1.)),
])
def test_bts_invalid_axes_or_reference_speed_are_rejected(bts_source, field, value):
    bts_source.data[field] = np.asarray(value)
    with pytest.raises(ValueError, match="BTS"):
        inflow.bts_info(str(bts_source.path))


def test_bts_valid_statistics_keep_interpolation_and_nearest_layer_ti(bts_source):
    info = inflow.bts_info(str(bts_source.path))
    assert info.u_hub == 11.
    assert info.z_used == 80.
    assert info.ti == 100. * np.std([6., 8., 8., 10., 10., 12.]) / 9.
    assert info.dt == .5 and info.t_span == 1.
    assert inflow.bts_info(str(bts_source.path)) is info
    assert len(bts_source.reads) == 1


def test_bts_cache_refreshes_when_the_file_is_replaced(bts_source):
    before = inflow.bts_info(str(bts_source.path))
    replacement = bts_source.path.with_suffix(".new")
    replacement.write_bytes(b"replacement")
    replacement.replace(bts_source.path)
    bts_source.data["u"][0] += 2.
    after = inflow.bts_info(str(bts_source.path))
    assert after.u_hub == before.u_hub + 2.
    assert len(bts_source.reads) == 2


def test_bts_cached_statistics_do_not_hide_a_missing_file(bts_source):
    inflow.bts_info(str(bts_source.path))
    bts_source.path.unlink()
    with pytest.raises(FileNotFoundError):
        inflow.bts_info(str(bts_source.path))


def test_truncated_bts_reports_its_path_and_format_error(tmp_path):
    path = tmp_path / "truncated.bts"
    path.write_bytes(b"short")
    with pytest.raises(ValueError, match=r"BTS.*truncated\.bts"):
        inflow.bts_info(str(path))


def feature_interface():
    data = {"ti": np.array([.1, .2]), "a": np.array([.2, .3]),
            "power": np.array([2.5e6, 5.e6]), "yaw": np.array([0., 30.])}
    fi = NS(layout_x=np.array([0., 500.]), layout_y=np.array([0., 0.]),
            get_turbine_TIs=lambda: data["ti"], get_turbine_ais=lambda: data["a"],
            get_turbine_powers=lambda: data["power"],
            floris=NS(farm=NS(yaw_angles=data["yaw"]),
                      flow_field=NS(wind_speeds=np.array([8.]))))
    return fi, data


@pytest.mark.parametrize("field", ["ti", "a", "power", "yaw"])
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_vibration_rejects_nonfinite_inputs_before_clipping(field, value):
    fi, data = feature_interface()
    data[field][0] = value
    with pytest.raises(ValueError, match="finite|有限"):
        multimodal.vibration_features(fi)


def test_vibration_rejects_negative_turbulence_intensity():
    fi, data = feature_interface()
    data["ti"][0] = -.1
    with pytest.raises(ValueError, match="TI"):
        multimodal.vibration_features(fi)


def test_vibration_rejects_overflow_when_converting_observation_to_float32():
    fi, data = feature_interface()
    data["yaw"][0] = 1.e50
    with pytest.raises(ValueError, match="finite|有限"):
        multimodal.vibration_features(fi)


def test_vibration_rejects_multiple_conditions_flattened_as_turbines():
    fi, data = feature_interface()
    for key, value in data.items():
        data[key] = np.tile(value, (2, 1))
    fi.floris.farm.yaw_angles = data["yaw"]
    with pytest.raises(ValueError, match="turbine|机组"):
        multimodal.vibration_features(fi)


def test_vibration_feature_order_units_and_dtype_are_unchanged():
    fi, _ = feature_interface()
    expected = [[.1, .64, 0., .5, .064, .064],
                [.2, .84, .75, 1., .588, .168]]
    features = multimodal.vibration_features(fi)
    assert features.shape == (2, 6) and features.dtype == np.float32
    np.testing.assert_allclose(features, np.asarray(expected, np.float32), rtol=1e-6)


@pytest.mark.parametrize("speed", [0., -8., np.nan, np.inf, -np.inf])
def test_wake_images_reject_invalid_free_stream_speed(monkeypatch, speed):
    fi, _ = feature_interface()
    fi.floris.flow_field.wind_speeds[0] = speed
    monkeypatch.setattr(multimodal, "farm_wake_plane",
                        lambda *a, **kw: (np.full((3, 4), 8., np.float32),
                                         -500., 1000., -500., 500.))
    with pytest.raises(ValueError, match="wind|风速"):
        multimodal.wake_images(fi, 90., out_hw=(2, 2))


@pytest.mark.parametrize("velocity", [np.nan, np.inf, -np.inf, 1.e50])
def test_wake_plane_rejects_nonfinite_velocity_before_observation_cast(velocity):
    fi, _ = feature_interface()
    fi.calculate_horizontal_plane = lambda **kw: NS(
        df={"u": NS(values=np.full(12, velocity))})
    with pytest.raises(ValueError, match="finite|有限"):
        multimodal.farm_wake_plane(fi, 90., res=(4, 3))


def test_wake_image_valid_normalization_and_order_are_unchanged(monkeypatch):
    fi, _ = feature_interface()
    plane = np.array([[0., 4., 8., 12.]] * 3, dtype=np.float32)
    monkeypatch.setattr(multimodal, "farm_wake_plane",
                        lambda *a, **kw: (plane, -500., 1000., -500., 500.))
    images = multimodal.wake_images(fi, 90., out_hw=(3, 3))
    assert images.shape == (2, 1, 3, 3) and images.dtype == np.float32
    np.testing.assert_array_equal(images[0, 0], [[0., .5, 1.]] * 3)
    np.testing.assert_array_equal(images[1, 0], [[.5, 1., 1.5]] * 3)


def test_mismatched_layout_cannot_fill_an_unobserved_turbine_image_with_zeros():
    fi, _ = feature_interface()
    fi.layout_y = np.array([0.])
    fi.calculate_horizontal_plane = lambda **kw: NS(
        df={"u": NS(values=np.full(12, 8.))})
    with pytest.raises(ValueError, match="layout|坐标"):
        multimodal.wake_images(fi, 90., res=(4, 3))


@pytest.mark.parametrize("fs", [0., -50., np.nan, np.inf, -np.inf])
def test_waveform_rejects_nonpositive_or_nonfinite_sample_rate(fs):
    fi, _ = feature_interface()
    with pytest.raises(ValueError, match="fs"):
        multimodal.vibration_waveform(fi, 0, fs=fs)


@pytest.mark.parametrize("rotor_hz", [np.nan, np.inf, -np.inf])
def test_waveform_rejects_nonfinite_rotor_frequency(rotor_hz):
    fi, _ = feature_interface()
    with pytest.raises(ValueError, match="rotor_hz"):
        multimodal.vibration_waveform(fi, 0, rotor_hz=rotor_hz)


@pytest.mark.parametrize("n", [-1, 2.5, True])
def test_waveform_sample_count_is_a_nonnegative_integer(n):
    fi, _ = feature_interface()
    with pytest.raises(ValueError, match="n"):
        multimodal.vibration_waveform(fi, 0, n=n)
