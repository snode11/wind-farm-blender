#!/usr/bin/env python3
"""RGB-only dense annotation; no reconstruction imports or model inputs.

Run --build, inspect the saved native overlays, then --freeze. Rebuilding a
frozen observation is refused unless --verify is used (which writes nothing).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
NATIVE = ROOT / "outputs/nrel-video-single-blade/20261003-p3-center-scale/rgb/C1_000042_native_rgb.png"
OLD = ROOT / "outputs/nrel-video-single-blade/20261002-second/observations"
REVIEW = ROOT / "outputs/nrel-video-single-blade/20261003-p4-greville/rgb-review"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rectangle_neighborhood(shape, points, radius):
    mask = np.zeros(shape, bool)
    h, w = shape
    for x, y in points:
        xi, yi = int(round(float(x))), int(round(float(y)))
        mask[max(0, yi-radius):min(h, yi+radius+1), max(0, xi-radius):min(w, xi+radius+1)] = True
    return mask


def measure_rgb_columns(rgb, protocol):
    luma = np.asarray(rgb, dtype=np.float64) @ np.asarray(protocol["luminance_coefficients"])
    x0, x1 = protocol["search_x_inclusive"]
    y0, y1 = protocol["search_y_inclusive"]
    rows = []
    for x in range(x0, x1+1):
        profile = luma[:, x]
        differences = profile[y0:y1] - profile[y0+1:y1+1]
        j = y0 + int(np.argmax(differences))
        drop = float(profile[j]-profile[j+1])
        bright = float(np.median(profile[j-5:j-1]))
        dark = float(np.median(profile[j+3:j+7]))
        level = (bright+dark)/2
        crossings = [k for k in range(j-2, j+3) if profile[k] >= level > profile[k+1]]
        reason = None
        if bright < protocol["minimum_local_bright_luminance"]:
            reason = "insufficient_white_surface_brightness"
        elif dark > protocol["maximum_local_dark_luminance"]:
            reason = "insufficient_dark_background"
        elif bright-dark < protocol["minimum_local_bright_minus_dark"]:
            reason = "insufficient_local_contrast"
        elif drop < protocol["minimum_downward_one_row_drop"]:
            reason = "insufficient_one_row_drop"
        elif len(crossings) != 1:
            reason = "ambiguous_local_midpoint_crossing"
        y = None
        if reason is None:
            k = crossings[0]
            y = float(k + (profile[k]-level)/(profile[k]-profile[k+1]))
        rows.append({"x": x, "y_subpixel": y, "maximum_drop_row": j,
                     "one_row_drop": drop, "bright_luminance": bright,
                     "dark_luminance": dark, "local_contrast": bright-dark,
                     "rgb_reject_reason": reason})
    return rows


def extract_and_dedup(rgb, old_points, old_valid, protocol):
    rows = measure_rgb_columns(rgb, protocol)
    h, w = old_valid.shape
    radius = protocol["old_domain_guard_px"]
    domain_guard = binary_dilation(old_valid, structure=np.ones((2*radius+1, 2*radius+1), bool))
    point_guard = rectangle_neighborhood(old_valid.shape, old_points, protocol["old_point_dedup_chebyshev_radius_px"])
    guard = domain_guard | point_guard
    x0, x1 = protocol["search_x_inclusive"]
    y0, y1 = protocol["search_y_inclusive"]
    trim = protocol["endpoint_guard_px"]
    crop_guard = protocol["crop_edge_guard_px"]
    uncertainty_radius = protocol["uncertainty_vertical_halfwidth_px"]
    ribbon_radius = protocol["valid_ribbon_vertical_halfwidth_px"]
    accepted = []
    added = np.zeros_like(old_valid)
    uncertain = np.zeros_like(old_valid)
    for row in rows:
        x, y = row["x"], row["y_subpixel"]
        reason = row["rgb_reject_reason"]
        if reason is None and not (x0+trim <= x <= x1-trim):
            reason = "reviewed_segment_endpoint_guard"
        if reason is None and not (crop_guard <= x < w-crop_guard and crop_guard <= y < h-crop_guard):
            reason = "native_crop_guard"
        yi = int(round(y)) if y is not None else 0
        if reason is None and guard[yi, x]:
            reason = "old_trusted_domain_or_point_priority"
        # Retain only columns whose complete vertical uncertainty interval is
        # free from prior observations. A failed column is never interpolated.
        u0 = int(np.ceil(y-uncertainty_radius)) if y is not None else 0
        u1 = int(np.floor(y+uncertainty_radius)) if y is not None else -1
        if reason is None and np.any(guard[u0:u1+1, x]):
            reason = "uncertainty_touches_old_trusted_domain_or_point"
        row["release_reject_reason"] = reason
        row["accepted"] = reason is None
        if reason is None:
            accepted.append([x, y])
            lo, hi = max(y0, int(np.ceil(y-ribbon_radius))), min(y1, int(np.floor(y+ribbon_radius)))
            added[lo:hi+1, x] = ~guard[lo:hi+1, x]
            uncertain[u0:u1+1, x] = True
    dense = np.asarray(accepted, dtype=old_points.dtype).reshape((-1, 2))
    combined = np.concatenate([old_points, dense], axis=0)
    valid = old_valid | added
    assert np.array_equal(combined[:len(old_points)], old_points)
    assert not np.any(added & old_valid)
    assert np.all(valid[old_valid])
    assert not np.any(uncertain & old_valid)
    assert np.all(added[tuple(np.rint(dense[:, ::-1]).astype(int).T)])
    old_rounded = set(map(tuple, np.rint(old_points).astype(int)))
    dense_rounded = list(map(tuple, np.rint(dense).astype(int)))
    assert len(set(dense_rounded)) == len(dense_rounded)
    assert not (old_rounded & set(dense_rounded))
    return rows, dense, combined, added, uncertain, valid


def sources():
    return [NATIVE, OLD/"C1_000042_contour.npy", OLD/"C1_000042_contour_valid.png",
            OLD/"C1_000042_FBU.png", REVIEW/"README.md", REVIEW/"review.json",
            REVIEW/"root_lower_rgb.png", REVIEW/"lower_context_rgb.png",
            HERE/"protocol.json", HERE/"build_annotation.py"]


def generated_files():
    return [HERE/name for name in ["C1_000042_contour.npy", "C1_000042_contour_valid.png",
           "C1_000042_reviewed_boundary_mask.png", "C1_000042_rgb_dense_edge.npy",
           "C1_000042_uncertainty_band.png", "edge_measurements.csv",
           "root_lower_annotation.png", "root_lower_validity.png", "native_annotation.png"]]


def draw_review(rgb, dense, added, uncertain):
    rgb_image = Image.fromarray(rgb)
    annotated = rgb_image.copy()
    draw = ImageDraw.Draw(annotated)
    draw.rectangle((1050, 885, 1775, 935), outline=(235, 145, 0), width=1)
    for x, y in dense:
        draw.point((int(x), int(round(float(y)))), fill=(255, 35, 80))
    annotated.save(HERE/"native_annotation.png")
    annotated.crop((950, 850, 1850, 1020)).save(HERE/"root_lower_annotation.png")
    # The RGB pixels under overlays are shown separately in unaltered crops.
    validity = np.asarray(rgb, dtype=np.uint8).copy()
    validity[added] = (0.6*validity[added]+0.4*np.array([0, 190, 220])).astype(np.uint8)
    validity[uncertain] = (0.4*validity[uncertain]+0.6*np.array([255, 170, 0])).astype(np.uint8)
    Image.fromarray(validity).crop((950, 850, 1850, 1020)).save(HERE/"root_lower_validity.png")


def build():
    if (HERE/"annotation.json").exists():
        raise RuntimeError("Annotation frozen; use --verify; no rebuild after model forward")
    before = {str(p): sha(p) for p in sources()}
    protocol = json.loads((HERE/"protocol.json").read_text())
    rgb = np.asarray(Image.open(NATIVE).convert("RGB"))
    old_points = np.load(OLD/"C1_000042_contour.npy", allow_pickle=False)
    old_valid = np.asarray(Image.open(OLD/"C1_000042_contour_valid.png").convert("L")) > 0
    assert rgb.shape == (1080, 1920, 3) and old_valid.shape == (1080, 1920)
    assert hashlib.sha256(rgb.tobytes()).hexdigest() == "65a6724bdce4a56da579b2f51e752c81f478e1ef427bfec8e5602d3667e2ad8f"
    rows, dense, combined, added, uncertain, valid = extract_and_dedup(rgb, old_points, old_valid, protocol)
    np.save(HERE/"C1_000042_rgb_dense_edge.npy", dense, allow_pickle=False)
    np.save(HERE/"C1_000042_contour.npy", combined, allow_pickle=False)
    for name, mask in [("C1_000042_contour_valid.png", valid),
                       ("C1_000042_reviewed_boundary_mask.png", added),
                       ("C1_000042_uncertainty_band.png", uncertain)]:
        Image.fromarray(mask.astype(np.uint8)*255).save(HERE/name)
    with (HERE/"edge_measurements.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    draw_review(rgb, dense, added, uncertain)
    yy, xx = np.nonzero(added)
    final = {str(p): sha(p) for p in sources()}
    assert final == before
    stats = {
        "schema": "nrel.rgb_dense_edge_build.v1", "status": "AWAITING_NATIVE_OVERLAY_REVIEW",
        "source_sha256": before, "inputs_unchanged": True,
        "native_rgb_pixels_sha256": hashlib.sha256(rgb.tobytes()).hexdigest(),
        "old_point_count": len(old_points), "new_dense_point_count": len(dense),
        "combined_point_count": len(combined), "measured_column_count": len(rows),
        "rgb_failed_column_count": sum(r["rgb_reject_reason"] is not None for r in rows),
        "rejected_columns": [{"x": r["x"], "reason": r["release_reject_reason"]} for r in rows if not r["accepted"]],
        "new_point_x_range_inclusive": [int(dense[:,0].min()), int(dense[:,0].max())],
        "new_point_y_range_subpixel": [float(dense[:,1].min()), float(dense[:,1].max())],
        "domain_native_dimensions_wh": [1920, 1080],
        "new_domain_bbox_xyxy_half_open": [int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)],
        "old_valid_pixel_count": int(old_valid.sum()), "new_valid_pixel_count": int(added.sum()),
        "combined_valid_pixel_count": int(valid.sum()), "uncertainty_pixel_count": int(uncertain.sum()),
        "new_domain_overlap_old_domain_pixels": int(np.count_nonzero(added & old_valid)),
        "new_uncertainty_overlap_old_domain_pixels": int(np.count_nonzero(uncertain & old_valid)),
        "new_rounded_point_overlap_old_domain": 0,
        "new_rounded_point_coordinate_duplicates": 0,
        "new_rounded_point_coordinates_overlap_old_points": 0,
        "old_points_preserved_exact_order_and_values": True,
        "new_dense_points_are_per_column_rgb_measurements": True,
        "new_point_independence_is_claimed": False,
        "new_points_change_downstream_sampling_share": True,
        "fbu_read_mode": "raw-byte hash only; unchanged original referenced",
        "old_contour_read_purpose": "deduplication, preserving original observation prefix and values only",
        "old_valid_read_purpose": "old-domain preservation and overlap exclusion only",
        "projection_or_model_computed_or_used_for_boundary": False,
        "schema_exposure_disclosure": "After RGB-only visual boundary selection, observations.json was accidentally displayed while checking observation metadata schema; this exposed calibration/pose entries. No transform value was used, no model/projection was loaded or computed, no mesh/truth/fit/loss/score was read for edge extraction or selection.",
        "not_fully_blind_to_prior_rgb_region_review": True,
        "output_sha256": {str(p): sha(p) for p in generated_files()},
    }
    (HERE/"build_summary.json").write_text(json.dumps(stats, indent=2)+"\n")
    print(json.dumps({k: stats[k] for k in ["old_point_count", "new_dense_point_count", "combined_point_count", "rgb_failed_column_count", "new_domain_bbox_xyxy_half_open", "new_valid_pixel_count", "new_domain_overlap_old_domain_pixels"]}, indent=2))


def freeze():
    if (HERE/"annotation.json").exists():
        raise RuntimeError("Already frozen; use --verify")
    summary = json.loads((HERE/"build_summary.json").read_text())
    assert all(sha(Path(p)) == digest for p, digest in summary["source_sha256"].items())
    assert all(sha(Path(p)) == digest for p, digest in summary["output_sha256"].items())
    annotation = {
        "schema": "nrel-observation-boundary-review.v1",
        "status": "FROZEN_RGB_OBSERVATION_REVIEW_ONLY",
        "version": "C1-42-lower-R1-R4-rgb.v1",
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "camera_id": "C1", "frame_id": 42, "split": "fit",
        "override": {"camera_id": "C1", "frame_id": 42,
                     "contour_points_path": str(HERE/"C1_000042_contour.npy"),
                     "contour_valid_path": str(HERE/"C1_000042_contour_valid.png"),
                     "reviewed_boundary_mask_path": str(HERE/"C1_000042_reviewed_boundary_mask.png"),
                     "contour_uncertainty_px": 3.0},
        "sha256": summary["output_sha256"],
        "provenance": {
            "native_rgb_path": str(NATIVE), "native_rgb_sha256": sha(NATIVE),
            "native_rgb_pixels_sha256": summary["native_rgb_pixels_sha256"],
            "decoded_rgb_pixel_sha256": summary["native_rgb_pixels_sha256"],
            "source_contour_points_path": str(OLD/"C1_000042_contour.npy"),
            "source_contour_points_sha256": sha(OLD/"C1_000042_contour.npy"),
            "source_contour_valid_path": str(OLD/"C1_000042_contour_valid.png"),
            "source_contour_valid_sha256": sha(OLD/"C1_000042_contour_valid.png"),
            "source_labels_path": str(OLD/"C1_000042_FBU.png"),
            "source_labels_sha256": sha(OLD/"C1_000042_FBU.png"),
            "rgb_extraction_protocol_path": str(HERE/"protocol.json"),
            "rgb_extraction_protocol_sha256": sha(HERE/"protocol.json"),
            "build_script_path": str(HERE/"build_annotation.py"),
            "build_script_sha256": sha(HERE/"build_annotation.py"),
            "schema_exposure_disclosure": summary["schema_exposure_disclosure"],
            "old_contour_read_purpose": summary["old_contour_read_purpose"],
            "old_valid_read_purpose": summary["old_valid_read_purpose"],
            "all_other_camera_frames": "Reference original observations unchanged; only C1#42 contour paths override.",
            "fbu_unchanged": True,
            "boundary_selection_from_rgb_only": True,
            "native_full_rgb_and_unaltered_crops_visually_inspected": True,
            "native_generated_edge_and_local_validity_overlays_visually_inspected": True,
            "projection_or_model_computed_or_used_for_boundary": False,
            "new_annotation_frozen_before_model_forward": True,
        },
        "uncertainty": {"native_px": 3, "calibrated": False,
                        "meaning": "Uncalibrated native-pixel review convention; saved band is vertical +/-3px about per-column subpixel crossing; solver uses unchanged scalar 3px deadband."},
        "local_domain": {"reviewed_search_xyxy_half_open": [1050, 885, 1776, 936],
                         "endpoint_guard_px": 8, "accepted_x_inclusive": [1058, 1767],
                         "vertical_ribbon_halfwidth_px": 8, "crop_guard_px": 8,
                         "new_support_mask_includes_old_domain": False,
                         "old_trusted_domain_priority_guard_px": 3,
                         "old_trusted_point_dedup_chebyshev_radius_px": 6,
                         "joins_deferred": ["J1 x>1775"],
                         "occluder_and_false_edge_exclusion": ["E1", "U1", "E2", "E3"],
                         "no_unreviewed_rectangle_released": True},
        "measurements": {k: summary[k] for k in ["old_point_count", "new_dense_point_count", "combined_point_count", "measured_column_count", "rgb_failed_column_count", "new_point_x_range_inclusive", "new_point_y_range_subpixel", "domain_native_dimensions_wh", "new_domain_bbox_xyxy_half_open", "old_valid_pixel_count", "new_valid_pixel_count", "combined_valid_pixel_count", "uncertainty_pixel_count", "new_domain_overlap_old_domain_pixels", "new_uncertainty_overlap_old_domain_pixels", "new_rounded_point_overlap_old_domain", "new_rounded_point_coordinate_duplicates", "new_rounded_point_coordinates_overlap_old_points", "old_points_preserved_exact_order_and_values"]},
        "evidence_limits": ["Dense per-column samples are not independent new geometric information.",
                            "No RGB-to-3D root-band association is established.",
                            "Observation set revision changes downstream max600 sampling share.",
                            "No geometric accuracy, uniqueness, full surface or acceptance result is established.",
                            "No boundary selection or domain widening after any model result is allowed."]
    }
    (HERE/"annotation.json").write_text(json.dumps(annotation, indent=2)+"\n")
    print(json.dumps({"annotation_path": str(HERE/"annotation.json"), "annotation_sha256": sha(HERE/"annotation.json"), "frozen_utc": annotation["frozen_utc"]}, indent=2))


def verify():
    annotation = json.loads((HERE/"annotation.json").read_text())
    summary = json.loads((HERE/"build_summary.json").read_text())
    for p, digest in {**summary["source_sha256"], **annotation["sha256"]}.items():
        assert sha(Path(p)) == digest, p
    old = np.load(OLD/"C1_000042_contour.npy", allow_pickle=False)
    new = np.load(HERE/"C1_000042_contour.npy", allow_pickle=False)
    assert np.array_equal(new[:len(old)], old)
    rgb = np.asarray(Image.open(NATIVE).convert("RGB"))
    old_valid = np.asarray(Image.open(OLD/"C1_000042_contour_valid.png").convert("L")) > 0
    protocol = json.loads((HERE/"protocol.json").read_text())
    _, dense, combined, added, uncertain, valid = extract_and_dedup(rgb, old, old_valid, protocol)
    assert np.array_equal(dense, np.load(HERE/"C1_000042_rgb_dense_edge.npy", allow_pickle=False))
    assert np.array_equal(combined, new)
    for name, expected in [("C1_000042_contour_valid.png", valid), ("C1_000042_reviewed_boundary_mask.png", added), ("C1_000042_uncertainty_band.png", uncertain)]:
        assert np.array_equal(np.asarray(Image.open(HERE/name).convert("L")) > 0, expected)
    print("Frozen RGB annotation source/output hashes, exact old prefix, independent column measurements and local domain replay verified.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true")
    group.add_argument("--freeze", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.build:
        build()
    elif args.freeze:
        freeze()
    else:
        verify()
