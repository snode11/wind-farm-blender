"""Guard RGB per-column extraction and old-observation priority."""
import importlib.util
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "outputs/nrel-video-single-blade/stages/06-p5-observation/annotation"
SPEC = importlib.util.spec_from_file_location("rgb_annotation", DIRECTORY / "build_annotation.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PROTOCOL = json.loads((DIRECTORY / "protocol.json").read_text())


def independent_column_image():
    rgb = np.full((1080, 1920, 3), 30, dtype=np.uint8)
    # Deliberately non-smooth edges; an anchor interpolator cannot pass.
    for x in range(1050, 1776):
        edge = 910 + (x % 7)
        rgb[:edge, x] = 200
    return rgb


def test_each_native_column_is_measured_without_cross_column_interpolation():
    rgb = independent_column_image()
    rows = MODULE.measure_rgb_columns(rgb, PROTOCOL)
    assert len(rows) == 726
    assert all(r["rgb_reject_reason"] is None for r in rows)
    for row in rows:
        assert row["y_subpixel"] == 909.5 + (row["x"] % 7)
    rgb[:918, 1400] = 200
    rgb[918:, 1400] = 30
    changed = MODULE.measure_rgb_columns(rgb, PROTOCOL)
    assert changed[1400-1050]["y_subpixel"] == 917.5
    assert all(a == b for a, b in zip(rows, changed) if a["x"] != 1400)


def test_old_domain_points_and_crop_endpoint_guards_take_priority():
    rgb = independent_column_image()
    old = np.array([[1250, 912], [1600, 550]], dtype=np.float32)
    old_valid = np.zeros((1080, 1920), bool)
    old_valid[900:930, 1450:1453] = True
    rows, dense, combined, added, uncertain, valid = MODULE.extract_and_dedup(rgb, old, old_valid, PROTOCOL)
    assert np.array_equal(combined[:2], old)
    assert np.all(valid[old_valid])
    assert not np.any(added & old_valid)
    assert not np.any(uncertain & old_valid)
    assert not np.any(added[:, :1058])
    assert not np.any(added[:, 1768:])
    assert not any(1447 <= x <= 1455 for x in dense[:, 0])
    assert not any(abs(float(x)-1250) <= 6 and abs(float(y)-912) <= 6 for x, y in dense)
    assert any(r["release_reject_reason"] == "old_trusted_domain_or_point_priority" for r in rows)


def test_nonedge_rgb_column_is_rejected_not_inferred_from_neighbors():
    rgb = independent_column_image()
    rgb[:, 1500] = 50
    rows = MODULE.measure_rgb_columns(rgb, PROTOCOL)
    rejected = rows[1500-1050]
    assert rejected["y_subpixel"] is None
    assert rejected["rgb_reject_reason"] == "insufficient_white_surface_brightness"
    assert rows[1499-1050]["rgb_reject_reason"] is None
    assert rows[1501-1050]["rgb_reject_reason"] is None
