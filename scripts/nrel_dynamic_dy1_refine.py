#!/usr/bin/env python3
"""Frozen finite-grid DY1 refinement and camera ablations, without a dynamic fit.

Every camera contour is evaluated once per candidate/frame. The original
three-camera equal mean is primary; the five camera subsets only reuse those
same image terms. Candidate axis-to-plane distances have an unverified
structural-point mapping and cannot establish engineering acceptance.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.nrel_dynamic_dy1 import checked_observations, digest, write_new

PROTOCOL_SCHEMA = "nrel-dynamic-dy1-refine-protocol.v1"
BINDING_SCHEMA = "nrel-dy1-refine-input-binding.v1"
FRAMES = [42, 43, 44, 45]
CAMERAS = ("C1", "C2", "C3")
GROUPS = {"all3": CAMERAS, "C1C2": ("C1", "C2"), "C2C3": ("C2", "C3"),
          "C1": ("C1",), "C2": ("C2",), "C3": ("C3",)}
SOURCE_DEPENDENCIES = (
    "scripts/nrel_dynamic_dy1_refine.py", "scripts/nrel_dynamic_dy1.py",
    "wfrl/__init__.py", "wfrl/paths.py", "wfrl/camera_video/__init__.py",
    "wfrl/nrel_reconstruction/__init__.py", "wfrl/nrel_reconstruction/dynamic_geometry.py",
    "wfrl/nrel_reconstruction/contour.py", "wfrl/nrel_reconstruction/optimize.py",
    "wfrl/nrel_reconstruction/dataset.py", "wfrl/nrel_reconstruction/model.py",
    "wfrl/nrel_reconstruction/renderer.py", "wfrl/nrel_reconstruction/artifact_paths.py",
    "wfrl/nrel_reconstruction/observations.py", "wfrl/camera_video/media.py",
)


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_protocol(protocol):
    """Reject unregistered frames, coercions and an inconsistent frozen budget."""
    if not isinstance(protocol, dict) or (
            protocol.get("schema") != PROTOCOL_SCHEMA
            or protocol.get("status") != "FROZEN_BEFORE_COMPUTATION"
            or protocol.get("target") != "T1/B1"):
        raise ValueError("Unregistered DY1 refine protocol identity")
    frames = protocol.get("frames")
    if not isinstance(frames, list) or any(type(f) is not int for f in frames) or frames != FRAMES:
        raise ValueError("Exactly the reviewed frames 42 through 45 are required")
    if (protocol.get("primary_group") != "all3"
            or protocol.get("secondary_groups") != list(GROUPS)[1:]):
        raise ValueError("The registered primary and secondary camera groups are required")
    model = protocol.get("candidate_model", {})
    if not isinstance(model, dict):
        raise ValueError("Missing candidate model contract")
    grid = model.get("q_grid_per_axis_m")
    if (not isinstance(grid, list) or len(grid) < 2 or any(not _number(q) for q in grid)
            or any(abs(q) > .5 for q in grid)
            or any(a >= b for a, b in zip(grid, grid[1:]))
            or grid[0] != -.5 or grid[-1] != .5):
        raise ValueError("q grid must increase finitely from exactly -0.5 to 0.5 m")
    if (model.get("q_units") != "m"
            or model.get("q_directions") != ["blade_root_x", "blade_root_y"]
            or not _number(model.get("maximum_tip_q_component_m"))
            or model["maximum_tip_q_component_m"] != .5):
        raise ValueError("Candidate coordinates or original DY1 bounds changed")
    states = len(frames) * len(grid) ** 2
    evaluations = states * len(CAMERAS)
    if "state_count" in model and (type(model["state_count"]) is not int
                                   or model["state_count"] != len(grid) ** 2):
        raise ValueError("Candidate per-frame state count differs from grid")
    budget = protocol.get("budget", {})
    if not isinstance(budget, dict):
        raise ValueError("Missing explicit frozen budget")
    for key, expected in (("threads", 4), ("optimizer_iterations", 0),
                          ("max_candidate_states", states), ("max_image_evaluations", evaluations)):
        if type(budget.get(key)) is not int or budget[key] != expected:
            raise ValueError("Frozen budget mismatch: " + key)
    for key, expected in (("restarts", 0), ("total_candidate_image_evaluations", evaluations)):
        if key in budget and (type(budget[key]) is not int or budget[key] != expected):
            raise ValueError("Frozen budget mismatch: " + key)
    size = budget.get("image_size")
    if (not isinstance(size, list) or len(size) != 2 or any(type(v) is not int for v in size)
            or any(v <= 1 for v in size) or size[0] > 1920 or size[1] > 1080
            or size[0] * 9 != size[1] * 16):
        raise ValueError("Image size must be an integer 16:9 resolution within native dimensions")
    if not _number(budget.get("time_limit_s")) or budget["time_limit_s"] != 900:
        raise ValueError("The frozen wall-time limit must be 900 seconds")
    tolerance = protocol.get("equivalence_tolerance_native_px")
    if not _number(tolerance) or tolerance != 3:
        raise ValueError("Registered historical tolerance must be 3 native pixels")
    if (not isinstance(protocol.get("candidate_structural_point"), dict)
            or not isinstance(protocol.get("calibration_uncertainty"), str)
            or not protocol["calibration_uncertainty"]):
        raise ValueError("Structural mapping and calibration limitations must be explicit")
    return {"grid": list(itertools.product(grid, repeat=2)), "size": tuple(size),
            "candidate_states": states, "image_evaluations": evaluations,
            "tolerance_native_px": float(tolerance)}


def aggregate_camera_metrics(camera_rows, camera_ids):
    """Equal camera mean, requiring every named camera exactly once and finite."""
    if not camera_ids or len(set(camera_ids)) != len(camera_ids) or any(c not in CAMERAS for c in camera_ids):
        raise ValueError("Invalid camera subset")
    identities = [r.get("camera_id") for r in camera_rows]
    if len(identities) != len(set(identities)) or any(c not in CAMERAS for c in identities):
        raise ValueError("Duplicate or unexpected camera metrics")
    index = {r["camera_id"]: r for r in camera_rows}
    invalid = [c for c in camera_ids if c not in index or index[c].get("valid") is not True
               or not _number(index[c].get("native_mean_distance_px"))
               or not _number(index[c].get("loss"))
               or index[c]["native_mean_distance_px"] < 0 or index[c]["loss"] < 0]
    valid = not invalid
    return {"camera_ids": list(camera_ids), "valid_image_terms": valid,
            "missing_or_invalid_camera_ids": invalid,
            "native_mean_distance_px": sum(index[c]["native_mean_distance_px"] for c in camera_ids) / len(camera_ids) if valid else None,
            "loss": sum(index[c]["loss"] for c in camera_ids) / len(camera_ids) if valid else None}


def _boundary(q):
    return any(value == -.5 or value == .5 for value in q)


def ambiguity_summary(rows, tolerance_px):
    """Finite grid extrema and an explicit pair; neither CI nor acceptance."""
    if not _number(tolerance_px) or tolerance_px < 0:
        raise ValueError("Invalid equivalence tolerance")
    valid = [r for r in rows if r.get("valid_image_terms") is True
             and _number(r.get("native_mean_distance_px")) and r["native_mean_distance_px"] >= 0
             and _number(r.get("candidate_d_m"))]
    for row in valid:
        q = row.get("q_m")
        if (not isinstance(q, list) or len(q) != 2 or any(not _number(v) or abs(v) > .5 for v in q)):
            raise ValueError("Summary candidate q violates original finite bounds")
    if not valid:
        return {"status": "UNAVAILABLE", "reason": "no complete finite candidate image terms",
                "d_range_m": None, "d_span_m": None, "extreme_equivalent_pair": None,
                "equivalent_state_count": 0, "valid_candidate_count": 0,
                "invalid_candidate_count": len(rows), "not_a_confidence_interval": True}
    best = min(valid, key=lambda r: (r["native_mean_distance_px"], tuple(r["q_m"])))
    cutoff = best["native_mean_distance_px"] + tolerance_px
    equivalent = [r for r in valid if r["native_mean_distance_px"] <= cutoff]
    lower = min(equivalent, key=lambda r: (r["candidate_d_m"], tuple(r["q_m"])))
    upper = max(equivalent, key=lambda r: (r["candidate_d_m"], tuple(r["q_m"])))
    def state(row):
        return {"q_m": row["q_m"], "candidate_d_m": row["candidate_d_m"],
                "native_mean_distance_px": row["native_mean_distance_px"],
                "search_boundary": _boundary(row["q_m"]),
                **{key: row[key] for key in ("candidate_axis_tip_world_m", "camera_metrics") if key in row}}
    return {"status": "FINITE_GRID_DIAGNOSTIC_ONLY", "best_q_m": best["q_m"],
            "best_native_mean_distance_px": best["native_mean_distance_px"],
            "equivalence_tolerance_native_px": float(tolerance_px), "equivalence_cutoff_native_px": cutoff,
            "equivalent_state_count": len(equivalent), "equivalent_q_m": [r["q_m"] for r in equivalent],
            "equivalent_q_range_m": [[min(r["q_m"][i] for r in equivalent), max(r["q_m"][i] for r in equivalent)] for i in range(2)],
            "d_range_m": [lower["candidate_d_m"], upper["candidate_d_m"]],
            "d_span_m": upper["candidate_d_m"] - lower["candidate_d_m"],
            "search_boundary_best": _boundary(best["q_m"]),
            "search_boundary_equivalent": any(_boundary(r["q_m"]) for r in equivalent),
            "equivalent_boundary_state_count": sum(_boundary(r["q_m"]) for r in equivalent),
            "extreme_equivalent_pair": {"minimum_d": state(lower), "maximum_d": state(upper)},
            "valid_candidate_count": len(valid), "invalid_candidate_count": len(rows) - len(valid),
            "not_a_confidence_interval": True, "structural_acceptance": False}


def _bound_inputs(args, protocol_path):
    binding_path = Path(args.binding).resolve()
    binding = json.loads(binding_path.read_text())
    if (binding.get("schema") != BINDING_SCHEMA
            or binding.get("status") != "FROZEN_BEFORE_COMPUTATION"
            or binding.get("protocol_sha256") != digest(protocol_path)):
        raise ValueError("Input binding identity or frozen protocol SHA256 mismatch")
    paths = binding.get("paths")
    files = binding.get("files")
    if not isinstance(paths, dict) or set(paths) != {"input", "observations", "native", "model", "plane"} or not isinstance(files, dict) or not files:
        raise ValueError("Incomplete input path or file binding")
    for name, registered in paths.items():
        if not isinstance(registered, str) or str(Path(getattr(args, name)).resolve()) != registered:
            raise ValueError("Input path differs from pre-computation binding: " + name)
    for path, signature in files.items():
        if (not isinstance(path, str) or str(Path(path).resolve()) != path
                or not isinstance(signature, str) or len(signature) != 64
                or any(c not in "0123456789abcdef" for c in signature)):
            raise ValueError("Invalid absolute file/SHA256 binding")
        if digest(path) != signature:
            raise ValueError("Input changed after pre-computation binding: " + path)
    required = [paths["model"], paths["plane"], *(str((ROOT / p).resolve()) for p in SOURCE_DEPENDENCIES)]
    if any(path not in files for path in required):
        raise ValueError("Binding omits model, plane, runner or source dependency")
    return binding_path, binding


def checked_planes(payload, data, moves):
    """Verify signature, complete sampled times and proper unit-normal geometry."""
    import numpy as np
    supplied = payload.get("payload_sha256")
    canonical = {k: v for k, v in payload.items() if k != "payload_sha256"}
    actual = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    if (payload.get("schema") != "nrel-dynamic-rigid-plane.v1"
            or payload.get("target") != "T1/B1" or payload.get("coordinate_frame") != "world"
            or payload.get("sampling") != data["sampling"]
            or payload.get("condition") != "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE"
            or payload.get("structural_tip_or_flexible_truth_included") is not False
            or supplied != actual):
        raise ValueError("Ideal plane identity or payload signature mismatch")
    records = payload.get("records")
    count = data["sampling"]["frame_count_per_camera"]
    if not isinstance(records, list) or len(records) != count:
        raise ValueError("Incomplete ideal plane sequence")
    planes = {}
    for record in records:
        frame = record.get("frame_id")
        if type(frame) is not int or frame not in moves or frame in planes:
            raise ValueError("Invalid or duplicate ideal plane frame")
        if (not _number(record.get("sim_time_s"))
                or abs(record["sim_time_s"] - moves[frame]["sim_time_s"]) > 1e-9):
            raise ValueError("Plane and image sampling time mismatch; interpolation is forbidden")
        c, n = np.asarray(record.get("c_world_m"), dtype=float), np.asarray(record.get("n_world_unit"), dtype=float)
        if c.shape != (3,) or n.shape != (3,) or not np.isfinite(c).all() or not np.isfinite(n).all() or abs(np.linalg.norm(n) - 1) > 1e-10:
            raise ValueError("Nonfinite or nonunit ideal plane geometry")
        planes[frame] = record
    if set(planes) != set(range(count)):
        raise ValueError("Ideal plane sequence does not cover the complete sampled frame grid")
    return planes


def _camera_metric(observation, loss, terms, native_scale):
    raw = {key: float(value) for key, value in terms.items()}
    if any(not math.isfinite(value) for value in raw.values()) or not math.isfinite(float(loss)):
        raise ValueError("Nonfinite contour calculation")
    valid = raw.get("contour_terms_available") == 1 and all(raw.get(key, 0) > 0 for key in ("candidate_count", "candidate_segment_count", "observed_count"))
    result = {"camera_id": observation["camera_id"], "valid": valid,
              "loss": float(loss) if valid else None,
              "native_mean_distance_px": .5 * (raw["candidate_to_observed_mean_px"] + raw["observed_to_candidate_mean_px"]) * native_scale if valid else None,
              "contour_terms": {key: (value if valid or "mean_px" not in key and key not in ("candidate_to_observed", "observed_to_candidate") else None) for key, value in raw.items()}}
    return result


def _counterexamples(summaries, tolerance):
    lines = ["# Finite-grid candidate counterexamples", "",
             "These are model-axis readouts with an unverified structural mapping. Each group uses its own minimum image distance; camera subsets are secondary diagnostics. The registered equivalence band is historical boundary uncertainty, not an acceptance threshold or confidence interval.", "",
             "| Frame | Group | Minimum-d q (m) | Minimum d (m) | Image mean (native px) | Maximum-d q (m) | Maximum d (m) | Image mean (native px) | d span (m) | Equivalent states | Boundary touch |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for frame, groups in summaries.items():
        for group, summary in groups.items():
            pair = summary["extreme_equivalent_pair"]
            if pair is None:
                lines.append(f"| {frame} | {group} | unavailable | — | — | unavailable | — | — | — | 0 | unavailable |")
                continue
            low, high = pair["minimum_d"], pair["maximum_d"]
            lines.append(f"| {frame} | {group} | {low['q_m']} | {low['candidate_d_m']:.9f} | {low['native_mean_distance_px']:.6f} | {high['q_m']} | {high['candidate_d_m']:.9f} | {high['native_mean_distance_px']:.6f} | {summary['d_span_m']:.9f} | {summary['equivalent_state_count']} | {summary['search_boundary_equivalent']} |")
    lines += ["", f"Every listed pair satisfies mean distance ≤ that group's finite-grid minimum + {tolerance:g} native px. Boundary-touching states warn that the finite search has stopped at the registered q bounds. A zero span or narrow interval is not proof of structural accuracy.", ""]
    return "\n".join(lines)


def run(args):
    import numpy as np
    import torch
    from wfrl.nrel_reconstruction.dynamic_geometry import DynamicBladeReference, transform_points, plane_readout
    from wfrl.nrel_reconstruction.optimize import _prepare
    from wfrl.nrel_reconstruction.contour import projected_contour_loss

    start = time.monotonic()
    protocol_path = Path(args.protocol).resolve()
    protocol = json.loads(protocol_path.read_text())
    contract = validate_protocol(protocol)
    binding_path, binding = _bound_inputs(args, protocol_path)
    # All dependency hashes are frozen before any candidate loop or data preparation.
    signatures = dict(binding["files"])
    signatures.update({str(protocol_path): digest(protocol_path), str(binding_path): digest(binding_path)})
    torch.set_num_threads(protocol["budget"]["threads"])
    observations, data, calibration, moves, checked_hashes = checked_observations(
        Path(args.input).resolve(), Path(args.observations).resolve(), Path(args.native).resolve(), FRAMES)
    if any(binding["files"].get(path) != signature for path, signature in checked_hashes.items()):
        raise ValueError("Binding omits or differs from a verified input/cache/native file")
    model = DynamicBladeReference.from_model_state(args.model, expected_sha256=signatures[binding["paths"]["model"]])
    planes = checked_planes(json.loads(Path(args.plane).read_text()), data, moves)
    for frame in FRAMES:
        for camera in CAMERAS:
            cal = calibration[(camera, frame)]
            if (cal["width"], cal["height"]) != (1920, 1080):
                raise ValueError("Registered native-pixel metric requires the original 1920x1080 images")
    size = contract["size"]
    prepared = _prepare(observations, size, "cpu", require_contours=True)
    if sorted((p["camera_id"], p["frame_id"]) for p in prepared) != sorted(itertools.product(CAMERAS, FRAMES)):
        raise ValueError("Prepared observations lost or duplicated a registered image")
    base = model.evaluate([0., 0.])
    if not np.array_equal(base["vertices"], model.reference_vertices):
        raise ValueError("Zero-q geometry changed from the frozen static model")
    changed = [path for path, signature in signatures.items() if digest(path) != signature]
    if changed:
        raise ValueError("Inputs changed before candidate computation: " + ", ".join(changed))
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    write_new(out / "input_freeze.json", {"schema": "nrel-dy1-refine-input-freeze.v1", "files": signatures,
              "budget": protocol["budget"], "truth_read": False, "optimizer_iterations": 0})
    rows, invariants, evaluations = [], [], 0
    def check_time():
        if time.monotonic() - start > protocol["budget"]["time_limit_s"]:
            write_new(out / "failure.json", {"status": "BUDGET_STOP", "completed_states": len(rows),
                      "image_evaluations": evaluations, "elapsed_s": time.monotonic() - start})
            raise RuntimeError("Registered DY1 refine wall-time budget exceeded")
    for frame in FRAMES:
        plane = planes[frame]
        root = np.asarray(moves[frame]["T_world_from_blade_root"])
        frame_obs = sorted((p for p in prepared if p["frame_id"] == frame), key=lambda p: p["camera_id"])
        # Check the identical q=0 surface under the one declared root transform.
        world = transform_points(base["vertices"], root)
        max_chain, max_pixel = 0., 0.
        for camera in CAMERAS:
            cal = calibration[(camera, frame)]
            C, K = np.asarray(cal["T_camera_cv_from_world"]), np.asarray(cal["K"])
            direct, composed = transform_points(base["vertices"], C @ root), transform_points(world, C)
            max_chain = max(max_chain, float(np.max(np.abs(direct - composed))))
            a, b = direct @ K.T, composed @ K.T
            good = (a[:, 2] > .01) & (b[:, 2] > .01)
            if good.any():
                error = float(np.max(np.abs(a[good, :2] / a[good, 2:] - b[good, :2] / b[good, 2:])))
                max_pixel = max(max_pixel, error)
                if error > 1e-6:
                    raise ValueError("Zero-deformation projection chain mismatch")
        invariants.append({"frame_id": frame, "max_chain_difference_m": max_chain,
                           "max_projection_chain_difference_native_px": max_pixel,
                           "q0_local_geometry_unchanged": True, "root_pose_applied_once": True,
                           "sampling_time_interpolated": False})
        for q in contract["grid"]:
            check_time()
            geometry = model.evaluate(q)
            vertices = torch.as_tensor(geometry["vertices"], dtype=torch.float32)
            faces = torch.as_tensor(geometry["faces"], dtype=torch.long)
            camera_rows = []
            with torch.no_grad():
                for obs in frame_obs:
                    check_time()
                    loss, terms = projected_contour_loss(vertices, faces, obs["K"], obs["camera"], obs["root"],
                        size, obs["labels"], obs["contour_points"], candidate_valid_mask=obs["contour_valid"],
                        uncertainty_px=obs["contour_uncertainty_px"])
                    evaluations += 1
                    camera_rows.append(_camera_metric(obs, loss, terms, 1920 / size[0]))
            tip = transform_points(geometry["axis_tip"], root)
            reading = plane_readout(tip, plane["c_world_m"], plane["n_world_unit"])
            group_metrics = {name: aggregate_camera_metrics(camera_rows, cameras) for name, cameras in GROUPS.items()}
            primary = group_metrics["all3"]
            rows.append({"frame_id": frame, "sim_time_s": moves[frame]["sim_time_s"], "q_m": list(q),
                         "valid_image_terms": primary["valid_image_terms"],
                         "native_mean_distance_px": primary["native_mean_distance_px"], "loss": primary["loss"],
                         "candidate_d_m": float(reading["d"]), "candidate_abs_d_m": float(reading["abs_d"]),
                         "candidate_axis_tip_local_m": geometry["axis_tip"].tolist(),
                         "candidate_axis_tip_world_m": tip.tolist(), "candidate_plane_foot_world_m": reading["foot"].tolist(),
                         "structural_mapping_status": geometry["mapping_status"], "camera_metrics": camera_rows,
                         "group_metrics": group_metrics})
            if len(rows) > contract["candidate_states"] or evaluations > contract["image_evaluations"]:
                raise ValueError("Actual computation exceeds frozen evaluation budget")
        print(f"DY1 refine frame {frame}: {len(contract['grid'])} candidates x 3 cameras complete; {evaluations} image evaluations", flush=True)
    check_time()
    if len(rows) != contract["candidate_states"] or evaluations != contract["image_evaluations"]:
        raise ValueError("Actual evaluations do not equal the frozen budget")
    summaries, whole_grid = {}, {}
    for frame in FRAMES:
        frame_rows = [row for row in rows if row["frame_id"] == frame]
        summaries[str(frame)] = {group: ambiguity_summary([{**row, **row["group_metrics"][group]} for row in frame_rows], contract["tolerance_native_px"]) for group in GROUPS}
        lower = min(frame_rows, key=lambda row: (row["candidate_d_m"], tuple(row["q_m"])))
        upper = max(frame_rows, key=lambda row: (row["candidate_d_m"], tuple(row["q_m"])))
        whole_grid[str(frame)] = {"status": "IMAGE_FREE_FINITE_GRID_CONTRAST_ONLY_NOT_G2",
            "candidate_state_count": len(frame_rows), "d_range_m": [lower["candidate_d_m"], upper["candidate_d_m"]],
            "d_span_m": upper["candidate_d_m"] - lower["candidate_d_m"],
            "minimum_d_q_m": lower["q_m"], "maximum_d_q_m": upper["q_m"],
            "image_constraint_used": False, "extra_geometry_evaluations": 0,
            "structural_mapping_verified": False, "engineering_acceptance": False}
    changed = [path for path, signature in signatures.items() if digest(path) != signature]
    integrity = {"unchanged": not changed, "changed": changed, "frozen_file_count": len(signatures),
                 "candidate_states": len(rows), "image_evaluations": evaluations,
                 "evaluation_budget_matches": True}
    write_new(out / "post_run_integrity.json", integrity)
    if changed:
        raise ValueError("Inputs changed during DY1 refine")
    result = {"schema": "nrel-dynamic-dy1-refine-grid.v1", "status": "DIAGNOSTIC_COMPLETE_NOT_STRUCTURAL_ACCEPTANCE",
              "target": "T1/B1", "structural_mapping": protocol["candidate_structural_point"],
              "calibration_uncertainty": protocol["calibration_uncertainty"], "static_model_provenance": model.provenance,
              "primary_group": "all3", "secondary_groups": list(GROUPS)[1:],
              "group_roles": {group: "PRIMARY_ALL_THREE_EQUAL_WEIGHT" if group == "all3" else "SECONDARY_IMAGE_GEOMETRY_ABLATION_ONLY" for group in GROUPS},
              "rows": rows, "frame_summary": {frame: groups["all3"] for frame, groups in summaries.items()},
              "group_frame_summary": summaries, "whole_grid_frame_d_summary": whole_grid, "coordinate_invariants": invariants,
              "candidate_states": len(rows), "image_evaluations": evaluations, "budget": protocol["budget"],
              "elapsed_s": time.monotonic() - start, "engineering_acceptance": "PENDING_USE_CASE_TARGETS",
              "truth_read": False, "optimizer_iterations": 0, "time_interpolation": False,
              "finite_grid_only": True, "post_run_integrity": integrity}
    write_new(out / "sensitivity.json", result)
    with (out / "counterexamples.md").open("x", encoding="utf-8") as stream:
        stream.write(_counterexamples(summaries, contract["tolerance_native_px"]))
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"],
                      "candidate_states": len(rows), "image_evaluations": evaluations,
                      "primary_frames": result["frame_summary"]}, indent=2, allow_nan=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "observations", "native", "model", "plane", "protocol", "binding", "output"):
        parser.add_argument("--" + name, required=True)
    run(parser.parse_args())
