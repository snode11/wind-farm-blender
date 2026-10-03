"""RGB contour boundaries and fitting-entry provenance / pixel-unit checks."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("scipy")
pytest.importorskip("torch")

from wfrl.nrel_reconstruction import dataset, optimize
from wfrl.nrel_reconstruction.observations import segment_rgb
from test_nrel_dataset import bundle  # Reuse the signed allowlist fixture.


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nrel_single_blade.py"
spec = importlib.util.spec_from_file_location("nrel_single_blade_test_entry", SCRIPT)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def test_rgb_contour_crosses_generic_uncertainty_band_without_inventing_crop_edges():
    rgb = np.zeros((140, 240, 3), dtype=np.uint8)
    rgb[45:95, :220] = 210  # Actual threshold foreground touches the crop.
    labels, annotation, points, valid = segment_rgb(rgb, "C2", with_contours=True)
    xy = points.astype(int)
    assert len(points) > 100 and points.dtype == np.float32
    assert np.all(valid[xy[:, 1], xy[:, 0]])
    assert np.all(labels[xy[:, 1], xy[:, 0]] == 2)  # Real RGB edge retained across FBU U-band.
    assert np.all(xy[:, 0] >= 8) and np.all(xy[:, 1] >= 8)
    assert not valid[:, :8].any() and not valid[:8, :].any()
    assert (np.isclose(points[:, 1], 45)).any() and (np.isclose(points[:, 1], 94)).any()
    assert annotation["trusted_contour"]["point_correspondence"] == "none; per-frame silhouette only"
    assert annotation["truth_segmentation_used"] is False


def test_c1_manual_occlusion_boundaries_are_excluded_but_visible_rgb_edge_remains():
    rgb = np.zeros((140, 240, 3), dtype=np.uint8)
    rgb[45:95, 10:220] = 210
    labels, annotation, points, valid = segment_rgb(rgb, "C1", with_contours=True)
    xy = points.astype(int)
    # C1's left quarter is true occlusion: neither the artificial x=60 cut
    # nor its eight-pixel exclusion neighbourhood may be a measured contour.
    assert np.all(xy[:, 0] >= 68)
    assert not valid[:, :68].any()
    assert np.all(labels[xy[:, 1], xy[:, 0]] == 2)
    assert np.any((xy[:, 1] == 45) & (xy[:, 0] > 80))
    assert annotation["trusted_contour"]["occlusion_and_crop_exclusion_native_px"] == 8


@pytest.fixture
def fitting_case(bundle, tmp_path, monkeypatch):
    """Signed schema plus synthetic decode pixels; the optimizer is stubbed.

    This tests entry validation only and does not claim a video experiment or
    actual target segmentation. The standalone segmentation tests exercise RGB.
    """
    data = json.loads((bundle / "dataset.json").read_text())
    reference = {"t_ref_sim_time_s": 117.025, "frame_id": 0,
                 "selection_basis": "Synthetic entry-validation fixture",
                 "fit_frame_ids": [0], "heldout_frame_ids": [1]}
    data["reference_state"] = reference
    dataset.write_json(bundle / "dataset.json", data)
    _, calibration, motion = dataset.load_bundle(bundle)
    observation_root = tmp_path / "observations"
    observation_root.mkdir()
    rgb = np.zeros((1080, 1920, 3), dtype=np.uint8)
    rgb[400:600, 800:1200] = 210
    labels = np.zeros((1080, 1920), dtype=np.uint8)
    labels[402:598, 802:1198] = 1
    labels[398:402, 798:1202] = 2
    points = np.stack((np.arange(802, 1198), np.full(396, 400)), axis=1).astype(np.float32)
    valid = np.ones(labels.shape, dtype=np.uint8) * 255
    valid[:8] = 0; valid[-8:] = 0; valid[:, :8] = 0; valid[:, -8:] = 0
    observations, annotations = [], []
    for camera in ("C1", "C2", "C3"):
        for frame in (0, 1):
            key = f"{camera}_{frame:06d}"
            label_path = observation_root / f"{key}_FBU.png"
            contour_path = observation_root / f"{key}_contour.npy"
            valid_path = observation_root / f"{key}_contour_valid.png"
            Image.fromarray(labels).save(label_path)
            np.save(contour_path, points, allow_pickle=False)
            Image.fromarray(valid).save(valid_path)
            c, m = calibration[(camera, frame)], motion[frame]
            observations.append({"camera_id": camera, "frame_id": frame, "sim_time_s": m["sim_time_s"],
                                 "K": c["K"], "T_camera_cv_from_world": c["T_camera_cv_from_world"],
                                 "T_world_from_blade_root": m["T_world_from_blade_root"],
                                 "labels_path": str(label_path), "split": "fit" if frame == 0 else "heldout",
                                 "contour_points_path": str(contour_path), "contour_valid_path": str(valid_path),
                                 "contour_uncertainty_px": 3.0})
            annotations.append({"camera_id": camera, "frame_id": frame, "sim_time_s": m["sim_time_s"],
                                "source_video_sha256": data["files"][f"{camera}.mp4"],
                                "rgb_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(), "truth_segmentation_used": False,
                                "trusted_contour": {"source": "thresholded MP4 RGB component before FBU uncertainty band",
                                                    "point_correspondence": "none; per-frame silhouette only", "points": len(points),
                                                    "uncertainty_native_px": 3, "occlusion_and_crop_exclusion_native_px": 8}})
    annotation_doc = {"annotations": annotations, "reference_state": reference}
    config = tmp_path / "config.json"
    dataset.write_json(config, {})
    monkeypatch.setattr(dataset, "iter_rgb", lambda path, width, height: iter([rgb, rgb]))
    calls = []
    monkeypatch.setattr(optimize, "reconstruct", lambda *args: calls.append(args) or {"status": "TEST_ENTRY_ONLY"})
    def save():
        dataset.write_json(observation_root / "observations.json", observations)
        dataset.write_json(observation_root / "annotations.json", annotation_doc)
    def fit():
        save()
        return entry.fit_bundle(bundle, observation_root, tmp_path / "result", config)
    return {"observations": observations, "annotations": annotations, "annotation_doc": annotation_doc,
            "fit": fit, "calls": calls, "root": observation_root, "bundle": bundle}


def test_entry_accepts_native_contours_and_verifies_actual_selected_rgb_hashes(fitting_case):
    case = fitting_case
    assert case["fit"]()["status"] == "TEST_ENTRY_ONLY"
    assert len(case["calls"]) == 1
    verification = json.loads((case["root"].parent / "result" / "fit_decode_verification.json").read_text())
    assert verification["selected_rgb_hashes_verified"] is True
    assert all(item["frames_decoded"] == 2 for item in verification["videos"].values())


def test_entry_keeps_legacy_observations_without_all_optional_contour_keys(fitting_case):
    for observation in fitting_case["observations"]:
        for key in ("contour_points_path", "contour_valid_path", "contour_uncertainty_px"):
            observation.pop(key)
    assert fitting_case["fit"]()["status"] == "TEST_ENTRY_ONLY"


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "missing_camera", "partial_contour", "unknown_field", "wrong_split"])
def test_entry_rejects_non_cartesian_or_invalid_observation_schema(fitting_case, mutation):
    observations = fitting_case["observations"]
    if mutation == "duplicate": observations.append(dict(observations[0]))
    elif mutation == "missing": observations.pop()
    elif mutation == "missing_camera": observations[:] = [item for item in observations if item["camera_id"] != "C3"]
    elif mutation == "partial_contour": observations[0].pop("contour_valid_path")
    elif mutation == "unknown_field": observations[0]["exact_reference_mesh"] = "disallowed"
    elif mutation == "wrong_split": observations[0]["split"] = "heldout"
    with pytest.raises(ValueError): fitting_case["fit"]()
    assert not fitting_case["calls"]


@pytest.mark.parametrize("mutation", ["duplicate", "truth_used", "rgb_hash", "video_hash", "contour_source", "uncertainty_units"])
def test_entry_rejects_inconsistent_rgb_or_contour_provenance(fitting_case, mutation):
    annotations = fitting_case["annotations"]
    if mutation == "duplicate": annotations.append(dict(annotations[0]))
    elif mutation == "truth_used": annotations[0]["truth_segmentation_used"] = True
    elif mutation == "rgb_hash": annotations[0]["rgb_sha256"] = "0" * 64
    elif mutation == "video_hash": annotations[0]["source_video_sha256"] = "0" * 64
    elif mutation == "contour_source": annotations[0]["trusted_contour"]["source"] = "renderer truth"
    elif mutation == "uncertainty_units": annotations[0]["trusted_contour"]["uncertainty_native_px"] = 3 / 4
    with pytest.raises(ValueError): fitting_case["fit"]()
    assert not fitting_case["calls"]


@pytest.mark.parametrize("mutation", ["label_size", "label_values", "valid_size", "valid_values", "excluded_point", "point_bounds", "point_nan", "point_shape"])
def test_entry_rejects_wrong_native_image_units_or_invalid_contour_cache(fitting_case, mutation):
    observation = fitting_case["observations"][0]
    if mutation == "label_size": Image.fromarray(np.zeros((270, 480), dtype=np.uint8)).save(observation["labels_path"])
    elif mutation == "label_values": Image.fromarray(np.full((1080, 1920), 255, dtype=np.uint8)).save(observation["labels_path"])
    elif mutation == "valid_size": Image.fromarray(np.ones((270, 480), dtype=np.uint8) * 255).save(observation["contour_valid_path"])
    elif mutation == "valid_values": Image.fromarray(np.full((1080, 1920), 2, dtype=np.uint8)).save(observation["contour_valid_path"])
    elif mutation == "excluded_point": Image.fromarray(np.zeros((1080, 1920), dtype=np.uint8)).save(observation["contour_valid_path"])
    else:
        points = np.load(observation["contour_points_path"], allow_pickle=False)
        if mutation == "point_bounds": points[0, 0] = 1920
        elif mutation == "point_nan": points[0, 0] = np.nan
        elif mutation == "point_shape": points = np.ones((10, 3))
        np.save(observation["contour_points_path"], points, allow_pickle=False)
    with pytest.raises(ValueError): fitting_case["fit"]()
    assert not fitting_case["calls"]


def test_entry_rejects_contour_cache_path_outside_observation_directory(fitting_case, tmp_path):
    path = tmp_path / "external_contour.npy"
    np.save(path, np.array([[100, 100]], dtype=float), allow_pickle=False)
    fitting_case["observations"][0]["contour_points_path"] = str(path)
    with pytest.raises(ValueError, match="within the observation"):
        fitting_case["fit"]()


def test_saved_second_round_contours_preserve_native_pixels_and_exclude_true_occlusion():
    """Check real allowed artifacts when present; never open truth/source mesh."""
    root = Path(__file__).resolve().parents[1] / "outputs/nrel-video-single-blade/20261002-second/observations"
    path = root / "observations.json"
    if not path.exists():
        pytest.skip("Second-round RGB observation artifact is not present in this checkout")
    entries = json.loads(path.read_text())
    annotations = json.loads((root / "annotations.json").read_text())["annotations"]
    evidence = {(row["camera_id"], row["frame_id"]): row for row in annotations}
    assert len(entries) == len({(item["camera_id"], item["frame_id"]) for item in entries})
    for item in entries:
        points = np.load(item["contour_points_path"], allow_pickle=False)
        labels = np.array(Image.open(item["labels_path"]), copy=True)
        valid = np.array(Image.open(item["contour_valid_path"]), copy=True)
        assert labels.shape == valid.shape == (1080, 1920)
        assert points.dtype == np.float32 and points.ndim == 2 and points.shape[1] == 2
        assert np.isfinite(points).all()
        xy = np.rint(points).astype(int)
        assert np.all(valid[xy[:, 1], xy[:, 0]] != 0)
        assert np.all(labels[xy[:, 1], xy[:, 0]] == 2)
        assert xy[:, 0].min() >= 8 and xy[:, 1].min() >= 8
        assert xy[:, 0].max() < 1920 - 8 and xy[:, 1].max() < 1080 - 8
        if item["camera_id"] == "C1":
            assert xy[:, 0].min() >= 480 + 8
        annotation = evidence[(item["camera_id"], item["frame_id"])]
        assert annotation["truth_segmentation_used"] is False
        assert annotation["trusted_contour"]["points"] == len(points)
