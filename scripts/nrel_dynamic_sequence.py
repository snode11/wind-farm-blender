#!/usr/bin/env python3
"""A03 offline kinematic sequence prototype using the bound A02 image-cost cache."""
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.nrel_dynamic_dy1 import digest, write_new
from scripts.nrel_dynamic_dy1_refine import ambiguity_summary, checked_planes

PROTOCOL_SCHEMA = "nrel-dynamic-sequence-protocol.v1"
BINDING_SCHEMA = "nrel-dynamic-sequence-input-binding.v1"
FRAMES = [42, 43, 44, 45]
SOURCE_DEPENDENCIES = (
    "scripts/nrel_dynamic_sequence.py", "scripts/nrel_dynamic_dy1.py", "scripts/nrel_dynamic_dy1_refine.py",
    "wfrl/__init__.py", "wfrl/paths.py", "wfrl/camera_video/__init__.py", "wfrl/camera_video/media.py",
    "wfrl/nrel_reconstruction/__init__.py", "wfrl/nrel_reconstruction/dynamic_sequence.py",
    "wfrl/nrel_reconstruction/dynamic_geometry.py", "wfrl/nrel_reconstruction/model.py",
    "wfrl/nrel_reconstruction/dataset.py", "wfrl/nrel_reconstruction/artifact_paths.py",
)


def record_status(group, frame):
    """Keep estimator role separate from reviewed-image availability."""
    if group not in ("G1", "G2", "G3") or frame not in FRAMES:
        raise ValueError("Unregistered group or frame")
    if group == "G1":
        return "PRIOR_DEFINED_BASELINE"
    if group == "G2":
        return "PRIOR_ONLY_AMBIGUOUS"
    return "PRIOR_ASSISTED_AMBIGUOUS" if frame in (42, 45) else "IMAGE_CONSTRAINED_AMBIGUOUS"


def validate_protocol(protocol):
    if (protocol.get("schema") != PROTOCOL_SCHEMA or protocol.get("status") != "FROZEN_BEFORE_COMPUTATION"
            or protocol.get("target") != "T1/B1" or protocol.get("frames") != FRAMES
            or any(type(frame) is not int for frame in protocol["frames"])):
        raise ValueError("Unregistered A03 protocol identity or frames")
    grid = protocol.get("q_grid_per_axis_m")
    expected = [round(-.5 + i * .05, 2) for i in range(21)]
    if not isinstance(grid, list) or len(grid) != 21 or any(type(q) not in (int, float) or not math.isfinite(q) for q in grid) or grid != expected:
        raise ValueError("A03 must reuse the exact 21-by-21 A02 candidate grid")
    fixed = {"anchor_frame_id": 43, "anchor_q_m": [0, 0], "initialization_q_m": [0, 0],
             "dt_s": .05, "temporal_weight": .05, "temporal_scale_m": .1,
             "node_weight": .01, "node_scale_m": .5, "component_speed_cap_m_s": 4.,
             "image_scale_native_px": 10., "equivalence_tolerance_native_px": 3.}
    for key, value in fixed.items():
        actual = protocol.get(key)
        if isinstance(value, list):
            good = isinstance(actual, list) and actual == value and all(type(v) in (int, float) for v in actual)
        else:
            good = type(actual) in (int, float) and math.isfinite(actual) and actual == value
        if not good or key == "anchor_frame_id" and type(actual) is not int:
            raise ValueError("Frozen sequence prior mismatch: " + key)
    budget = protocol.get("budget", {})
    for key, value in {"threads": 4, "optimizer_iterations": 0, "restarts": 0,
                       "max_dp_transition_evaluations_per_group": 3 * 441 ** 2}.items():
        if type(budget.get(key)) is not int or budget[key] != value:
            raise ValueError("Frozen DP budget mismatch: " + key)
    if type(budget.get("time_limit_s")) not in (int, float) or budget["time_limit_s"] != 300:
        raise ValueError("A03 wall-time budget must be 300 seconds")
    return list(itertools.product(grid, repeat=2))


def checked_cache(cache, grid, model_hash, moves):
    """Require the original full all3 image metric; preserve missingness as inf."""
    import numpy as np
    if (cache.get("schema") != "nrel-dynamic-dy1-refine-grid.v1"
            or cache.get("status") != "DIAGNOSTIC_COMPLETE_NOT_STRUCTURAL_ACCEPTANCE"
            or cache.get("target") != "T1/B1" or cache.get("primary_group") != "all3"
            or cache.get("truth_read") is not False or cache.get("finite_grid_only") is not True
            or cache.get("optimizer_iterations") != 0 or cache.get("time_interpolation") is not False
            or cache.get("candidate_states") != 1764 or cache.get("image_evaluations") != 5292
            or cache.get("static_model_provenance", {}).get("model_state_sha256") != model_hash
            or cache.get("budget", {}).get("image_size") != [320, 180]
            or cache.get("post_run_integrity", {}).get("unchanged") is not True):
        raise ValueError("Unsupported, changed or incomplete A02 cache identity")
    rows = cache.get("rows", [])
    if len(rows) != 1764:
        raise ValueError("Incomplete A02 candidate cache")
    index = {}
    for row in rows:
        frame, q = row.get("frame_id"), row.get("q_m")
        if (type(frame) is not int or frame not in FRAMES or not isinstance(q, list) or len(q) != 2
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in q)):
            raise ValueError("Invalid cached frame or q identity")
        key = frame, tuple(q)
        if key in index or tuple(q) not in grid or not math.isclose(row.get("sim_time_s", math.nan), moves[frame]["sim_time_s"], abs_tol=1e-9, rel_tol=0):
            raise ValueError("Duplicate or mistimed A02 candidate")
        index[key] = row
    if set(index) != set(itertools.product(FRAMES, grid)):
        raise ValueError("Cache omits a reviewed frame/state")
    image = np.full((4, 441), np.inf)
    for i, frame in enumerate(FRAMES):
        if cache.get("frame_summary", {}).get(str(frame), {}).get("equivalence_tolerance_native_px") != 3:
            raise ValueError("A02 historical 3-native-pixel assumption changed")
        for j, q in enumerate(grid):
            row = index[(frame, q)]
            metrics = row.get("camera_metrics", [])
            camera_index = {r.get("camera_id"): r for r in metrics}
            valid = (len(metrics) == 3 and set(camera_index) == {"C1", "C2", "C3"}
                     and all(r.get("valid") is True and type(r.get("native_mean_distance_px")) in (int, float)
                             and math.isfinite(r["native_mean_distance_px"]) and r["native_mean_distance_px"] >= 0 for r in metrics))
            if row.get("valid_image_terms") is True:
                mean = row.get("native_mean_distance_px")
                if (not valid or type(mean) not in (int, float) or not math.isfinite(mean)
                        or not math.isclose(mean, sum(r["native_mean_distance_px"] for r in metrics) / 3, abs_tol=1e-10, rel_tol=0)):
                    raise ValueError("A02 primary image cost is not the complete equal camera mean")
                image[i, j] = mean
            elif row.get("valid_image_terms") is not False or row.get("native_mean_distance_px") is not None:
                raise ValueError("Missing image terms must remain unavailable, never zero")
    return image, index


def run(args):
    import numpy as np
    import torch
    from wfrl.nrel_reconstruction.dataset import load_bundle
    from wfrl.nrel_reconstruction.dynamic_geometry import transform_points, plane_readout
    from wfrl.nrel_reconstruction.dynamic_sequence import StructuralPointPrior, solve_sequence

    started = time.monotonic()
    protocol_path, binding_path = Path(args.protocol).resolve(), Path(args.binding).resolve()
    protocol = json.loads(protocol_path.read_text())
    grid = validate_protocol(protocol)
    binding = json.loads(binding_path.read_text())
    names = ("model", "prior", "cache", "plane", "input")
    if (binding.get("schema") != BINDING_SCHEMA or binding.get("status") != "FROZEN_BEFORE_COMPUTATION"
            or binding.get("protocol_sha256") != digest(protocol_path) or set(binding.get("paths", {})) != set(names)):
        raise ValueError("Binding identity or protocol hash mismatch")
    for name in names:
        if binding["paths"][name] != str(Path(getattr(args, name)).resolve()):
            raise ValueError("Pre-frozen input path changed: " + name)
    signatures = dict(binding.get("files", {}))
    cache_freeze = Path(args.cache).resolve().parent / "input_freeze.json"
    required = [binding["paths"][name] for name in names if name != "input"] + [str(cache_freeze)]
    required += [str((ROOT / p).resolve()) for p in SOURCE_DEPENDENCIES]
    if any(path not in signatures for path in required):
        raise ValueError("Binding omits an allowed input, A02 freeze or executed source")
    for path, signature in signatures.items():
        if str(Path(path).resolve()) != path or digest(path) != signature:
            raise ValueError("Bound input/source changed: " + path)
    signatures.update({str(protocol_path): digest(protocol_path), str(binding_path): digest(binding_path)})
    torch.set_num_threads(4)
    data, calibration, moves = load_bundle(args.input)
    for name in ("dataset.json", *data["files"]):
        path = str((Path(args.input).resolve() / name).resolve())
        if binding["files"].get(path) != digest(path):
            raise ValueError("Binding omits a signed video/root/calibration input")
    times = [moves[frame]["sim_time_s"] for frame in FRAMES]
    if not np.allclose(np.diff(times), .05, atol=1e-9, rtol=0):
        raise ValueError("Actual sampled times differ from the frozen 0.05 s interval")
    planes = checked_planes(json.loads(Path(args.plane).read_text()), data, moves)
    model_hash = signatures[binding["paths"]["model"]]
    baseline = json.loads(cache_freeze.read_text())
    for relative in ("wfrl/nrel_reconstruction/dynamic_geometry.py", "wfrl/nrel_reconstruction/model.py"):
        path = str((ROOT / relative).resolve())
        if baseline.get("files", {}).get(path) != signatures[path]:
            raise ValueError("Surface-producing code differs from the A02 cache baseline")
    if baseline.get("files", {}).get(binding["paths"]["model"]) != model_hash:
        raise ValueError("Static model differs from the A02 cache baseline")
    image, cached_rows = checked_cache(json.loads(Path(args.cache).read_text()), grid, model_hash, moves)
    prior = StructuralPointPrior.from_files(args.model, args.prior, model_sha256=model_hash,
                                           prior_sha256=signatures[binding["paths"]["prior"]])
    geometries = [prior.evaluate(q) for q in grid]
    maximum_identity_error = max(geometry["surface_identity_max_error_m"] for geometry in geometries)
    zero = grid.index((0., 0.))
    g1 = {"status": "FROZEN_Q0_BASELINE", "q_m": [[0., 0.] for _ in FRAMES], "state_indices": [zero] * 4,
          "image_cost_used": False, "transition_evaluations": 0, "total_cost": 0.}
    options = dict(anchor_index=1, temporal_weight=protocol["temporal_weight"], temporal_scale_m=protocol["temporal_scale_m"],
                   node_weight=protocol["node_weight"], node_scale_m=protocol["node_scale_m"],
                   speed_cap_m_s=protocol["component_speed_cap_m_s"], image_scale_px=protocol["image_scale_native_px"])
    solutions = {"G1": g1, "G2": solve_sequence(grid, times, **options),
                 "G3": solve_sequence(grid, times, image, **options)}
    for group in ("G2", "G3"):
        solution = solutions[group]
        if solution["transition_evaluations"] != protocol["budget"]["max_dp_transition_evaluations_per_group"]:
            raise ValueError("Actual DP state-pair count differs from frozen budget")
        if solution["status"] == "UNAVAILABLE":
            raise ValueError(group + ": " + solution["reason"])
    ranges = {}
    for frame in FRAMES:
        root, plane = moves[frame]["T_world_from_blade_root"], planes[frame]
        candidate_rows = []
        for q, geometry in zip(grid, geometries):
            row = cached_rows[(frame, q)]
            point = transform_points(geometry["structural_tip"], root)
            d = plane_readout(point, plane["c_world_m"], plane["n_world_unit"])["d"]
            candidate_rows.append({"q_m": list(q), "candidate_d_m": d,
                "valid_image_terms": row["valid_image_terms"], "native_mean_distance_px": row["native_mean_distance_px"]})
        ranges[str(frame)] = ambiguity_summary(candidate_rows, 3.)
    arrays = {"faces": geometries[zero]["faces"], "material_ids": geometries[zero]["material_ids"]}
    groups = {}
    for group, solution in solutions.items():
        records, world_vertices = [], []
        for index, frame in enumerate(FRAMES):
            state = solution["state_indices"][index]
            geometry, plane = geometries[state], planes[frame]
            root = moves[frame]["T_world_from_blade_root"]
            point = transform_points(geometry["structural_tip"], root)
            reading = plane_readout(point, plane["c_world_m"], plane["n_world_unit"])
            world_vertices.append(transform_points(geometry["vertices"], root))
            records.append({"frame_id": frame, "sim_time_s": moves[frame]["sim_time_s"], "q_m": solution["q_m"][index],
                "status": record_status(group, frame),
                "observation_availability": "PRIOR_ASSISTED_AMBIGUOUS" if frame in (42, 45) else "IMAGE_CONSTRAINED_AMBIGUOUS",
                "point_world_m": point.tolist(), "center_world_m": plane["c_world_m"],
                "normal_world_unit": plane["n_world_unit"], "foot_world_m": reading["foot"].tolist(),
                "d_m": reading["d"], "abs_d_m": reading["abs_d"], "range_m": ranges[str(frame)]["d_range_m"],
                "range_scope": "independent_per_frame_image_equivalent_grid_not_joint_posterior",
                "point_scope": "PRIOR_DEFINED_STRUCTURAL_POINT", "T_world_from_blade_root": root,
                "image_mean_native_px": float(image[index, state]) if np.isfinite(image[index, state]) else None,
                "image_cost_used": solution["image_cost_used"], "hypothetical_candidate_state": True})
        arrays[group + "_vertices_world"] = np.stack(world_vertices)
        groups[group] = {"records": records, "solution": solution}
    if time.monotonic() - started > 300:
        raise RuntimeError("Frozen A03 wall-time budget exceeded")
    changed = [path for path, signature in signatures.items() if digest(path) != signature]
    if changed:
        raise ValueError("Input changed during A03: " + ", ".join(changed))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    package = output / "review-package"
    package.mkdir()
    sequence = {"schema": "nrel-dynamic-review-sequence.v1", "target": "T1/B1", "frames": FRAMES,
        "groups": groups, "independent_image_equivalent_ranges": ranges, "outside_registered_centers": "UNAVAILABLE",
        "outside_frame_ids": [frame for frame in range(data["sampling"]["frame_count_per_camera"]) if frame not in FRAMES],
        "point_scope": "PRIOR_DEFINED_STRUCTURAL_POINT", "kinematic_prior_only": True,
        "offline_future_frames_used": True, "interpolation_allowed": False,
        "surface_cache_identity": {"all_441_q_states_verified": True, "max_error_m": maximum_identity_error,
                                   "q0_surface_exact": True, "new_image_evaluations": 0},
        "structural_mapping": prior.provenance, "engineering_acceptance": "PENDING_USE_CASE_TARGETS"}
    write_new(package / "sequence.json", sequence)
    with (package / "geometry.npz").open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    write_new(package / "model-state.json", json.loads(Path(args.model).read_text()))
    write_new(package / "static-prior.json", json.loads(Path(args.prior).read_text()))
    write_new(package / "estimator-config.json", protocol)
    write_new(package / "input-provenance.json", {"paths": binding["paths"], "files": signatures,
              "surface_cache_path": binding["paths"]["cache"], "truth_read": False, "independent_score_read": False})
    file_names = ("sequence.json", "geometry.npz", "model-state.json", "static-prior.json", "estimator-config.json", "input-provenance.json")
    manifest = {"schema": "nrel-dynamic-review-package.v1", "target": "T1/B1", "status": "REVIEW_ONLY", "units": "m",
        "condition": "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE", "point_scope": "PRIOR_DEFINED_STRUCTURAL_POINT",
        "files": {name: digest(package / name) for name in file_names}, "sampling": data["sampling"],
        "videos": {camera: {"path": str((Path(args.input).resolve() / (camera + ".mp4")).resolve()),
                             "sha256": data["files"][camera + ".mp4"]} for camera in ("C1", "C2", "C3")},
        "frames": FRAMES, "groups": ["G1", "G2", "G3"], "interpolation_allowed": False,
        "hypothetical_candidate_states": True, "structural_mapping_verified": False,
        "kinematic_prior_only": True, "physics_status": "NO_ELASTIC_DYNAMICS_OR_BEAMDYN_SOLUTION",
        "elapsed_s": time.monotonic() - started, "new_image_evaluations": 0,
        "engineering_acceptance": "PENDING_USE_CASE_TARGETS"}
    write_new(package / "manifest.json", manifest)
    write_new(output / "post_run_integrity.json", {"unchanged": True, "changed": [], "files": signatures})
    freeze = {"schema": "nrel-dynamic-estimator-freeze.v1", "status": "FROZEN_BEFORE_INDEPENDENT_SCORE",
        "package_path": str(package), "manifest_sha256": digest(package / "manifest.json"),
        "files": {str(package / name): digest(package / name) for name in (*file_names, "manifest.json")},
        "input_files": signatures, "truth_read": False, "independent_score_read": False,
        "protocol_sha256": digest(protocol_path), "binding_sha256": digest(binding_path)}
    write_new(output / "estimator_freeze.json", freeze)
    print(json.dumps({"status": freeze["status"], "package": str(package), "groups": {g: s["q_m"] for g, s in solutions.items()},
                      "elapsed_s": manifest["elapsed_s"], "new_image_evaluations": 0}, indent=2, allow_nan=False))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model", "prior", "cache", "plane", "input", "protocol", "binding", "output"):
        parser.add_argument("--" + name, required=True)
    run(parser.parse_args())
