"""Contour input units and objective dispatch, independent of NREL truth."""
import numpy as np
import pytest
from PIL import Image

torch = pytest.importorskip("torch")
from wfrl.nrel_reconstruction.optimize import _prepare


def observation(tmp_path):
    labels = np.zeros((12, 20), dtype=np.uint8)
    labels[:, 5:10] = 1
    # The validity domain is independent of the deliberately uncertain edge.
    labels[:, 5] = 2
    Image.fromarray(labels).save(tmp_path / "labels.png")
    Image.fromarray(np.full_like(labels, 255)).save(tmp_path / "valid.png")
    np.save(tmp_path / "points.npy", np.array([[5., 3.], [5., 8.]], dtype=np.float32))
    return {"camera_id": "C1", "frame_id": 0, "labels_path": str(tmp_path / "labels.png"),
            "K": [[20., 0, 9.5], [0, 20., 5.5], [0, 0, 1]],
            "T_camera_cv_from_world": np.eye(4), "T_world_from_blade_root": np.eye(4),
            "contour_points_path": str(tmp_path / "points.npy"),
            "contour_valid_path": str(tmp_path / "valid.png"), "contour_uncertainty_px": 3.}


def test_contour_resize_matches_calibration_half_pixel_and_independent_validity(tmp_path):
    item = _prepare([observation(tmp_path)], (10, 6), "cpu", True)[0]
    assert np.allclose(item["contour_points"], [[2.25, 1.25], [2.25, 3.75]])
    assert np.allclose(item["K"], [[10, 0, 4.5], [0, 10, 2.5], [0, 0, 1]])
    assert item["contour_uncertainty_px"] == 1.5
    assert bool(item["contour_valid"].all())


@pytest.mark.parametrize("bad", [np.array([[np.nan, 2], [1, 2]]), np.array([[20., 2], [1, 2]])])
def test_contour_rejects_nonfinite_or_outside_points(tmp_path, bad):
    item = observation(tmp_path)
    np.save(tmp_path / "points.npy", bad)
    with pytest.raises(ValueError):
        _prepare([item], (10, 6), "cpu", True)


def test_contour_rejects_mismatched_validity_size_and_anisotropic_resize(tmp_path):
    item = observation(tmp_path)
    with pytest.raises(ValueError, match="aspect-preserving"):
        _prepare([item], (10, 5), "cpu", True)
    Image.fromarray(np.zeros((5, 5), dtype=np.uint8)).save(tmp_path / "valid.png")
    with pytest.raises(ValueError, match="matching binary"):
        _prepare([item], (10, 6), "cpu", True)
