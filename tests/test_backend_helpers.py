"""Backend numeric helpers preserve physical dimensions and field point ordering."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from wfrl.floris_field import extract_wake_plane, get_floris
from wfrl.turbines.dimensions import load_turbine_dimensions


def write_dimensions(root, *, include_offset=True):
    path = root / "FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat"
    path.parent.mkdir()
    source = """63.0 TipRad
1.5 HubRad
-5.0191D+00 OverHang
-5.0 ShftTilt
87.6 TowerHt
"""
    if include_offset:
        source += "1.96256 Twr2Shft\n"
    path.write_text(source, encoding="latin-1")


def test_camera_dimensions_preserve_shaft_offset_and_tilt(tmp_path):
    write_dimensions(tmp_path)
    dimensions = load_turbine_dimensions(tmp_path)
    assert dimensions.tip_rad == 63.0
    assert dimensions.hub_rad == 1.5
    assert dimensions.overhang == -5.0191
    assert dimensions.hub_height == pytest.approx(90.00000338842476, abs=1e-12)


def test_missing_mount_dimension_is_not_silently_zero(tmp_path):
    write_dimensions(tmp_path, include_offset=False)
    with pytest.raises(ValueError, match="Twr2Shft"):
        load_turbine_dimensions(tmp_path)


def test_floris_plane_keeps_xy_order_and_requested_yaw():
    columns = {"x1": [0, 1, 0, 1], "x2": [0, 0, 2, 2], "u": [8, 7, 6, 5]}

    class PlaneData:
        def __len__(self):
            return 4

        def __getitem__(self, key):
            return NS(values=np.array(columns[key]))

    calls = []

    def calculate(**kwargs):
        calls.append(kwargs)
        return NS(df=PlaneData())

    default_yaw = np.array([[[12.0]]])
    fi = NS(floris=NS(farm=NS(yaw_angles=default_yaw)),
            calculate_horizontal_plane=calculate)
    env = NS(env=NS(mdp=NS(interface=NS(fi=fi))))
    assert get_floris(env) is fi
    x, y, z, u = extract_wake_plane(fi, 90.0)
    np.testing.assert_array_equal(x[:, :, 0], [[0, 0], [1, 1]])
    np.testing.assert_array_equal(y[:, :, 0], [[0, 2], [0, 2]])
    np.testing.assert_array_equal(u[:, :, 0], [[8, 6], [7, 5]])
    np.testing.assert_array_equal(z, np.full((2, 2, 1), 90.0))
    assert calls[-1]["yaw_angles"] is default_yaw
    explicit_yaw = np.array([[[25.0]]])
    extract_wake_plane(fi, 100.0, explicit_yaw)
    assert calls[-1]["height"] == 100.0
    assert calls[-1]["yaw_angles"] is explicit_yaw


def test_backend_imports_do_not_attempt_desktop_dependencies():
    source = '''
import sys
attempted = []
class NoDesktop:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'pyvista', 'pyvistaqt', 'qtpy', 'PyQt5', 'PyQt6', 'PySide6', 'vtk', 'vtkmodules'
        }:
            attempted.append(fullname)
            raise ImportError(fullname)
sys.meta_path.insert(0, NoDesktop())
from wfrl.studio import Trainer, Snapshot
from wfrl.scene.runtime import SceneRuntime
from wfrl.channels.sensors import NacelleCamera
from wfrl.viz import wakevtk
from wfrl.floris_field import get_floris, extract_wake_plane
from wfrl.turbines.dimensions import load_turbine_dimensions
import wfrl.studio
assert set(wfrl.studio.__all__) == {'Trainer', 'Snapshot'}
assert NacelleCamera.HUB_H == load_turbine_dimensions().hub_height
assert not attempted, attempted
'''
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", source], cwd=root, env=environment,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def plane_interface(columns):
    class PlaneData:
        def __len__(self):
            return len(columns["x1"])

        def __getitem__(self, key):
            return NS(values=np.asarray(columns[key]))

    return NS(calculate_horizontal_plane=lambda **kwargs: NS(df=PlaneData()))


def test_rectangular_shuffled_wake_plane_keeps_coordinate_velocity_pairs():
    columns = {"x1": [2., 0., 1., 2., 1., 0.], "x2": [4., 0., 4., 0., 0., 4.],
               "u": [24., 0., 14., 20., 10., np.nan]}
    x, y, z, u = extract_wake_plane(plane_interface(columns), 90., [[[0.]]])
    assert x.shape == y.shape == z.shape == u.shape == (3, 2, 1)
    np.testing.assert_array_equal(x[:, :, 0], [[0., 0.], [1., 1.], [2., 2.]])
    np.testing.assert_array_equal(y[:, :, 0], [[0., 4.], [0., 4.], [0., 4.]])
    np.testing.assert_allclose(u[:, :, 0], [[0., np.nan], [10., 14.], [20., 24.]], equal_nan=True)


@pytest.mark.parametrize("columns", [
    {"x1": [0.], "x2": [0.]},
    {"x1": [], "x2": [], "u": []},
    {"x1": [0., 1.], "x2": [0.], "u": [8., 8.]},
    {"x1": [0., 1., 0.], "x2": [0., 0., 1.], "u": [8., 8., 8.]},
    {"x1": [0., 1., 0., 0.], "x2": [0., 0., 1., 1.], "u": [8., 8., 8., 8.]},
    {"x1": [np.nan], "x2": [0.], "u": [8.]},
])
def test_malformed_plane_is_rejected_as_value_error(columns):
    with pytest.raises(ValueError, match="FLORIS"):
        extract_wake_plane(plane_interface(columns), 90., [[[0.]]])


def test_floris_lookup_handles_deep_wrappers_and_rejects_cycles_or_missing_fields():
    fi = object()
    env = NS(mdp=NS(interface=NS(fi=fi)))
    for _ in range(8):
        env = NS(env=env)
    assert get_floris(env) is fi
    for missing in (NS(), NS(mdp=NS()), NS(mdp=NS(interface=NS(fi=None)))):
        with pytest.raises(ValueError, match="FLORIS"):
            get_floris(missing)
    cycle = NS()
    cycle.env = cycle
    with pytest.raises(ValueError, match="FLORIS"):
        get_floris(cycle)


def test_invalid_height_and_missing_yaw_fail_before_plane_calculation():
    fi = NS(calculate_horizontal_plane=lambda **kwargs: pytest.fail("invalid input reached FLORIS"))
    for height in (np.nan, np.inf, True, [], "90"):
        with pytest.raises(ValueError, match="hub_h"):
            extract_wake_plane(fi, height, [[[0.]]])
    with pytest.raises(ValueError, match="yaw_angles"):
        extract_wake_plane(fi, 90.)


def test_dimension_parser_ignores_numbers_in_comments_and_other_fields(tmp_path):
    write_dimensions(tmp_path)
    path = tmp_path / "FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat"
    source = path.read_text(encoding="latin-1")
    prefix = "# 7 missing TipRad\n# TipRad reference\n! TipRad reference\n9 OtherField - TipRad reference\n"
    path.write_text(prefix + source, encoding="latin-1")
    assert load_turbine_dimensions(tmp_path).tip_rad == 63.
    path.write_text(prefix + source.replace("63.0 TipRad\n", ""), encoding="latin-1")
    with pytest.raises(ValueError, match="TipRad"):
        load_turbine_dimensions(tmp_path)


@pytest.mark.parametrize("original,replacement", [("63.0 TipRad", "1e999 TipRad"),
    ("63.0 TipRad", "invalid63 TipRad"), ("1.5 HubRad", "-1 HubRad"),
    ("63.0 TipRad", "1 TipRad"), ("87.6 TowerHt", "0 TowerHt"),
    ("-5.0 ShftTilt", "nan ShftTilt")])
def test_dimension_parser_rejects_nonfinite_tokens_and_impossible_sizes(tmp_path, original, replacement):
    write_dimensions(tmp_path)
    path = tmp_path / "FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat"
    path.write_text(path.read_text(encoding="latin-1").replace(original, replacement), encoding="latin-1")
    with pytest.raises(ValueError):
        load_turbine_dimensions(tmp_path)
