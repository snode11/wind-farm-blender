"""Frozen-model local forward perturbations; no fitting or truth input.

The retained contour backend supplies visibility, masks and sample selection.
Span attribution is diagnostic nearest-edge attribution, not correspondence
truth. Observed-point span bins are assigned once from the frozen baseline.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch

from .contour import _limit, _silhouette_edges, projected_contour_candidates
from .diagnostics import _permitted, read_mesh_ply
from .model import BladeModel, bspline_weights, linear_weights
from .optimize import _prepare


TARGET_Z_M = 9.225
ROOT_BAND_M = (6.15, 12.30)
AMPLITUDES_M = (0.02, 0.05)
PARAMETERS = ("center_x", "center_y", "chord_halfwidth")


def sha256(path):
    return hashlib.sha256(_permitted(path).read_bytes()).hexdigest()


def restore_model(metadata):
    """Invert the exported bounded physical controls, preserving float32."""
    model = BladeModel(metadata["span_samples"], metadata["ring_samples"],
                       len(metadata["control_z_m"]), interpolation=metadata["interpolation"])
    centers = torch.tensor(metadata["center_offsets_m"], dtype=torch.float32)
    chord = torch.tensor(metadata["control_chord_log_scale"], dtype=torch.float32)
    if not torch.equal(centers[0], torch.zeros(2)):
        raise ValueError("Frozen model violates anchored root")
    if bool((centers.abs() >= 5).any()) or bool((chord.abs() >= .35).any()):
        raise ValueError("Frozen controls cannot be inverted within declared bounds")
    with torch.no_grad():
        model.center_raw.copy_(torch.atanh(centers[1:] / 5))
        model.chord_raw.copy_(torch.atanh(chord / .35))
    model.requires_grad_(False)
    return model


def local_basis(model, z=TARGET_Z_M):
    if model.interpolation == "bspline":
        return bspline_weights(np.array([z]), len(model.control_z), model.span_m)[0]
    return linear_weights(np.array([z]), model.control_z.numpy())[0]


def normalization(model, z=TARGET_Z_M, control_index=1):
    basis = local_basis(model, z)
    _, chord_log = model.control_values()
    # Fixed template chord is piecewise linear at its public stations. The
    # model's mesh rings interpolate that template; report this ring-interpolated
    # section value because z=9.225 is between existing rings.
    base = float(np.interp(z, model.z.numpy(), model.base_chord.numpy()))
    chord = base * np.exp(basis @ chord_log.numpy())
    return {"target_z_m": z, "control_index": control_index,
            "control_greville_z_m": float(model.control_z[control_index]),
            "basis_at_target": float(basis[control_index]),
            "ring_interpolated_template_chord_at_target_m": base,
            "continuous_field_chord_at_target_m": float(chord)}


def perturb_model(model, parameter, amplitude_m, norm=None):
    """Physical control perturbation, anchored root and fixed t/c retained.

    Center amplitude is the continuous B-spline reference-axis offset at target z.
    Chord amplitude changes its half-width by amplitude_m at target z. These
    amplitudes normalize a diagnostic continuous section, not an added vertex.
    The model's chord/4 section-center convention also moves its geometric
    centroid by half the half-width amplitude along the rotated chord axis.
    """
    if parameter not in PARAMETERS:
        raise ValueError("Unknown perturbation parameter")
    norm = normalization(model) if norm is None else norm
    result = copy.deepcopy(model)
    center, chord_log = model.control_values()
    center, chord_log = center.clone(), chord_log.clone()
    index, weight = norm["control_index"], norm["basis_at_target"]
    if weight <= 0:
        raise ValueError("Selected control has no support at target")
    if parameter.startswith("center_"):
        center[index, 0 if parameter == "center_x" else 1] += amplitude_m / weight
    else:
        ratio = 1 + 2 * amplitude_m / norm["continuous_field_chord_at_target_m"]
        if ratio <= 0:
            raise ValueError("Chord perturbation must retain positive chord")
        chord_log[index] += np.log(ratio) / weight
    if bool((center.abs() >= 5).any()) or bool((chord_log.abs() >= .35).any()):
        raise ValueError("Perturbation exceeds frozen model physical bounds")
    with torch.no_grad():
        result.center_raw.copy_(torch.atanh(center[1:] / 5))
        result.chord_raw.copy_(torch.atanh(chord_log / .35))
    return result


def perturb_combination(model, direction, amplitude_m, norm=None):
    """Apply a fixed unit direction in the same physical normalization."""
    direction = np.asarray(direction, dtype=float)
    if direction.shape != (3,) or not np.isfinite(direction).all():
        raise ValueError("Combination direction must have three finite components")
    if not np.isclose(np.linalg.norm(direction), 1., rtol=0, atol=1e-6):
        raise ValueError("Combination direction must have unit Euclidean norm")
    if not np.isfinite(amplitude_m):
        raise ValueError("Combination amplitude must be finite")
    norm = normalization(model) if norm is None else norm
    result = model
    for parameter, component in zip(PARAMETERS, direction):
        result = perturb_model(result, parameter, float(component * amplitude_m), norm)
    return result


def nearest_segments(points, segments):
    if not len(segments):
        raise ValueError("No contour segments")
    delta = segments[:, 1] - segments[:, 0]
    relative = points[:, None] - segments[None, :, 0]
    fraction = ((relative * delta[None]).sum(-1) /
                delta.square().sum(-1).clamp_min(1e-16)[None]).clamp(0, 1)
    foot = segments[None, :, 0] + fraction[..., None] * delta[None]
    distances = torch.linalg.vector_norm(points[:, None] - foot, dim=-1)
    value, index = distances.min(dim=1)
    return value, index, fraction[torch.arange(len(points)), index]


def perspective_attribute(fraction, start_z, end_z, start_depth, end_depth):
    inverse_depth = (1 - fraction) / start_depth + fraction / end_depth
    local_z = ((1 - fraction) * start_z / start_depth + fraction * end_z / end_depth) / inverse_depth
    return local_z, 1 / inverse_depth


def edge_attributes(points, vertices, faces, observation):
    """Assign projected silhouette points to nearest original projected edge."""
    transform = observation["camera"] @ observation["root"]
    camera = vertices @ transform[:3, :3].T + transform[:3, 3]
    edges = torch.tensor(_silhouette_edges(camera, faces), dtype=torch.long)
    endpoints = camera[edges]
    if bool((endpoints[:, :, 2] <= .01).any()):
        raise ValueError("Diagnostic span attribution does not certify near-clipped edges")
    homogeneous = endpoints @ observation["K"].T
    projected = homogeneous[:, :, :2] / homogeneous[:, :, 2:3]
    distances, index, fraction = nearest_segments(points, projected)
    chosen = edges[index]
    z, depth = perspective_attribute(fraction, vertices[chosen[:, 0], 2],
                                     vertices[chosen[:, 1], 2],
                                     camera[chosen[:, 0], 2], camera[chosen[:, 1], 2])
    return z, depth, distances


def robust_values(distance, uncertainty_px):
    residual = torch.relu(distance - uncertainty_px) / 10.
    return torch.where(residual < .2, .5 * residual.square() / .2, residual - .1)


def distance_stats(distance, uncertainty, scale):
    if not len(distance):
        return {"count": 0, "mean_native_px": None, "p90_native_px": None,
                "robust_mean": None, "beyond_deadband_fraction": None}
    return {"count": len(distance), "mean_native_px": float(distance.mean() / scale),
            "p90_native_px": float(torch.quantile(distance, .9) / scale),
            "robust_mean": float(robust_values(distance, uncertainty).mean()),
            "beyond_deadband_fraction": float((distance > uncertainty).float().mean())}


def contour_diagnostic(model, observation, size, baseline_z=None, dense=False):
    vertices = model()
    candidates, segments, counts = projected_contour_candidates(
        vertices, model.faces, observation["K"], observation["camera"], observation["root"],
        size, observation["labels"], observation["contour_valid"],
        max_candidates=100000 if dense else 300)
    all_observed = observation["contour_points"]
    inside = ((all_observed[:, 0] >= 0) & (all_observed[:, 0] <= size[0] - 1) &
              (all_observed[:, 1] >= 0) & (all_observed[:, 1] <= size[1] - 1))
    observed = _limit(all_observed[inside], 100000 if dense else 600)
    counts.update({"observed_source_count": len(all_observed), "observed_inside_count": int(inside.sum()),
                   "observed_count": len(observed), "observed_sampling_truncated": int(int(inside.sum()) > len(observed))})
    if not len(candidates) or not len(segments) or not len(observed):
        raise ValueError("Missing trusted contour terms; do not report zero loss")
    c2o = torch.linalg.vector_norm(candidates[:, None] - observed[None], dim=-1).min(dim=1).values
    o2c, index, fraction = nearest_segments(observed, segments)
    candidate_z, _, point_errors = edge_attributes(candidates, vertices, model.faces, observation)
    segment_z, segment_depth, segment_errors = edge_attributes(segments.reshape(-1, 2), vertices, model.faces, observation)
    segment_z, segment_depth = segment_z.reshape(-1, 2), segment_depth.reshape(-1, 2)
    inferred_z, _ = perspective_attribute(fraction, segment_z[index, 0], segment_z[index, 1],
                                          segment_depth[index, 0], segment_depth[index, 1])
    max_error = max(float(point_errors.max()), float(segment_errors.max()))
    if max_error > .005:
        raise ValueError(f"Unreliable projected-edge attribution: {max_error}px")
    observed_z = inferred_z if baseline_z is None else baseline_z
    if len(observed_z) != len(observed):
        raise ValueError("Baseline observed-point assignment mismatch")
    scale = size[0] / 1920.
    uncertainty = observation["contour_uncertainty_px"]
    rows = []
    for region in ("all", "root_band", "outside_root_band"):
        cmask = (candidate_z >= ROOT_BAND_M[0]) & (candidate_z <= ROOT_BAND_M[1])
        omask = (observed_z >= ROOT_BAND_M[0]) & (observed_z <= ROOT_BAND_M[1])
        if region == "outside_root_band":
            cmask, omask = ~cmask, ~omask
        elif region == "all":
            cmask, omask = torch.ones_like(cmask), torch.ones_like(omask)
        cs, os = distance_stats(c2o[cmask], uncertainty, scale), distance_stats(o2c[omask], uncertainty, scale)
        rows.append({"region": region, "c2o": cs, "o2c": os,
                     "image_loss": .5 * (cs["robust_mean"] + os["robust_mean"])
                     if cs["count"] and os["count"] else None})
    return {"counts": counts, "regions": rows, "attribution_max_projection_error_px": max_error,
            "observed_z": observed_z, "observed_native_pixels": (observed + .5) / scale - .5,
            "o2c_native_px": o2c / scale}


def response_analysis(records, vectors):
    """Local finite-difference response of fixed trusted observed-point sets."""
    result = []
    for width in (320, 640):
        for split in ("fit", "nonblind_diagnostic"):
            for region in ("all", "root_band", "outside_root_band"):
                by_amplitude = []
                reference_indices = [i for i, r in enumerate(records) if r["variant"] == "baseline"
                                     and r["width"] == width and r["split"] == split and r["sampling"] == "default"]
                for amplitude in AMPLITUDES_M:
                    columns = []
                    for parameter in PARAMETERS:
                        values = []
                        for i in reference_indices:
                            base = records[i]
                            z = np.asarray(vectors[i]["baseline_observed_z_m"])
                            mask = (z >= ROOT_BAND_M[0]) & (z <= ROOT_BAND_M[1])
                            if region == "all": mask = np.ones(len(z), dtype=bool)
                            if region == "outside_root_band": mask = ~mask
                            pair = []
                            for sign in (-1, 1):
                                j = next(j for j, r in enumerate(records) if r["width"] == width
                                         and r["sampling"] == "default" and r["camera_id"] == base["camera_id"]
                                         and r["frame_id"] == base["frame_id"] and r["parameter"] == parameter
                                         and r["amplitude_m"] == sign * amplitude)
                                pair.append(np.asarray(vectors[j]["o2c_native_px"])[mask])
                            values.extend((pair[1] - pair[0]) / (2 * amplitude))
                        columns.append(values)
                    matrix = np.asarray(columns).T
                    if not len(matrix): continue
                    _, singular, right = np.linalg.svd(matrix / np.sqrt(len(matrix)), full_matrices=False)
                    norms = np.linalg.norm(matrix, axis=0)
                    denominator = norms[:, None] * norms[None, :]
                    cosine = np.divide(matrix.T @ matrix, denominator, out=np.zeros((3, 3)), where=denominator > 0)
                    by_amplitude.append({"amplitude_m": amplitude, "point_count": len(matrix),
                                         "rms_response_native_px_per_m": np.sqrt(np.mean(matrix ** 2, axis=0)).tolist(),
                                         "column_cosine": cosine.tolist(),
                                         "singular_values_native_px_per_m": singular.tolist(),
                                         "weakest_right_singular_direction": right[-1].tolist(),
                                         "response_matrix": matrix.tolist()})
                stability = None
                if len(by_amplitude) == 2:
                    a, b = [np.asarray(x["response_matrix"]) for x in by_amplitude]
                    stability = (np.linalg.norm(a-b, axis=0) / np.maximum(np.linalg.norm(a, axis=0), 1e-12)).tolist()
                result.append({"width": width, "split": split, "region": region,
                               "parameter_order": list(PARAMETERS), "amplitudes": by_amplitude,
                               "relative_response_change_002_to_005": stability,
                               "scope": "unsigned nearest-contour distances with baseline-fixed observed bins; local response only"})
    return result


def run_audit(source_dir, output_dir):
    source, output = _permitted(source_dir), Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use an empty diagnostic output directory")
    output.mkdir(parents=True, exist_ok=True)
    paths = [source / "reconstruction" / name for name in ("model_state.json", "T1_B1.ply", "frozen_config.json")]
    paths += [source / "observations/observations.json", source / "model_freeze.json"]
    state = json.loads(paths[0].read_text())
    observations = json.loads(paths[3].read_text())
    if len(observations) != 12 or {(x["camera_id"], x["frame_id"]) for x in observations} != {
            (c, f) for c in ("C1", "C2", "C3") for f in (42, 43, 44, 45)}:
        raise ValueError("Expected exactly the frozen twelve observations")
    paths += [_permitted(x[key]) for x in observations for key in ("labels_path", "contour_points_path", "contour_valid_path")]
    hashes = {str(path): sha256(path) for path in paths}
    freeze = json.loads(paths[4].read_text())
    if sha256(paths[1]) != freeze["sha256"]["reconstruction/T1_B1.ply"]:
        raise ValueError("Formal frozen PLY hash mismatch")
    model = restore_model(state["models"]["T1_B1"])
    reference_v, reference_f = read_mesh_ply(paths[1])
    difference = np.abs(model().numpy() - reference_v)
    if difference.max() > 2e-6 or not np.array_equal(model.faces.numpy(), reference_f):
        raise ValueError("Reconstructed frozen model does not match formal PLY")
    norm = normalization(model)
    variants = [("baseline", None, 0., model)]
    for parameter in PARAMETERS:
        for amplitude in AMPLITUDES_M:
            for sign in (-1, 1):
                value = sign * amplitude
                variants.append((f"{parameter}_{value:+.3f}m", parameter, value,
                                 perturb_model(model, parameter, value, norm)))
    torch.set_num_threads(2)
    records, vectors = [], []
    started = time.perf_counter()
    # 320/640 perturbations use the production default sampling budget. Native
    # and 160 baseline plus dense 640/native checks expose scale/sample effects.
    suites = [(320, False, True), (640, False, True), (160, False, False),
              (1920, False, False), (640, True, False), (1920, True, False)]
    for width, dense, perturb in suites:
        size = (width, width * 9 // 16)
        prepared = _prepare(observations, size, "cpu", require_contours=True)
        for observation in prepared:
            baseline_z = None
            for variant, parameter, amplitude, candidate in (variants if perturb else variants[:1]):
                diagnostic = contour_diagnostic(candidate, observation, size, baseline_z, dense)
                if baseline_z is None:
                    baseline_z = diagnostic["observed_z"].clone()
                record = {"camera_id": observation["camera_id"], "frame_id": observation["frame_id"],
                          "original_split": observation["split"],
                          "split": "fit" if observation["split"] == "fit" else "nonblind_diagnostic",
                          "width": width, "height": size[1], "sampling": "expanded" if dense else "default",
                          "variant": variant, "parameter": parameter, "amplitude_m": amplitude,
                          "counts": diagnostic["counts"], "regions": diagnostic["regions"],
                          "attribution_max_projection_error_px": diagnostic["attribution_max_projection_error_px"]}
                records.append(record)
                vectors.append({"record_index": len(records)-1,
                                "baseline_observed_z_m": baseline_z.tolist(),
                                "observed_native_pixels": diagnostic["observed_native_pixels"].tolist(),
                                "o2c_native_px": diagnostic["o2c_native_px"].tolist()})
            print(json.dumps({"width": width, "expanded_sampling": dense,
                              "camera": observation["camera_id"], "frame": observation["frame_id"],
                              "completed_records": len(records), "elapsed_s": round(time.perf_counter()-started, 1)}), flush=True)
    coupling = response_analysis(records, vectors)
    changed = [str(path) for path in paths if sha256(path) != hashes[str(path)]]
    if changed:
        raise RuntimeError(f"Frozen inputs changed during audit: {changed}")
    report = {"status": "LOCAL_FORWARD_DIAGNOSTIC_ONLY", "source": str(source),
              "no_optimizer_executed": True, "no_scoring_truth_read": True,
              "input_sha256_before_and_after_equal": True, "input_sha256": hashes,
              "frozen_mesh_check": {"max_abs_coordinate_error_m": float(difference.max()),
                                    "rms_coordinate_error_m": float(np.sqrt(np.mean(difference**2))),
                                    "vertex_count": len(reference_v), "face_count": len(reference_f), "faces_exact": True},
              "normalization": norm, "root_band_m": list(ROOT_BAND_M),
              "root_band_scope": "diagnostic band around 9.225m; not the original 0-20.5m scoring region",
              "amplitudes_m": list(AMPLITUDES_M), "records": records,
              "local_response": coupling, "wall_s": time.perf_counter()-started,
              "limitations": [
                  "No optimization, acceptance change, default change, or geometric ground truth comparison.",
                  "Frames 44-45 are previously viewed nonblind diagnostics, not new held-out validation.",
                  "Center and chord coefficients share broad first-free-control B-spline support; this is not isolated section editing.",
                  "Chord and absolute thickness change together under the frozen t/c prior; no independent thickness estimate.",
                  "Nearest-edge span attribution is baseline-dependent and not observed 3D correspondence.",
                  "Observed bins are frozen; candidate bins and topology/mask/sample selection can change under perturbation.",
                  "Expanded sampling removes global 300/600 limits, but retains one-pixel working spacing and the backend 512 samples/edge cap.",
                  "Cross-resolution changes mix mask resampling, scale, deadband, working-pixel robust scales, visibility probes and sampling budgets.",
                  "Raw O2C response SVD does not prove global identifiability, measurement confidence or a unique cause.",
                  "Local finite differences may cross nearest-segment, visibility, mask and sample cases."]}
    (output / "parameter_audit.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    (output / "observed_response_vectors.json").write_text(json.dumps(vectors, separators=(",", ":"), allow_nan=False)+"\n")
    flat = []
    for i, record in enumerate(records):
        for region in record["regions"]:
            row = {key: value for key, value in record.items() if key not in {"counts", "regions"}}
            row.update(record_index=i, **record["counts"], region=region["region"], image_loss=region["image_loss"])
            for direction in ("c2o", "o2c"):
                row.update({f"{direction}_{key}": value for key, value in region[direction].items()})
            flat.append(row)
    with (output / "per_observation.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0])); writer.writeheader(); writer.writerows(flat)
    return report


def run_combination(source_dir, output_dir):
    """Verify the preselected 640/fit/root weak direction by actual forwards.

    The direction is derived once from the already-saved 0.02m single-variable
    response. It is not selected by objective improvement and is never fitted.
    All earlier 360 records are read-only; this writes additional evidence.
    """
    source, output = _permitted(source_dir), Path(output_dir).resolve()
    destinations = [output / name for name in (
        "combination_audit.json", "combination_response_vectors.json", "combination_per_observation.csv")]
    if any(path.exists() for path in destinations):
        raise FileExistsError("Combination evidence already exists; do not overwrite")
    baseline_path = output / "parameter_audit.json"
    vectors_path = output / "observed_response_vectors.json"
    preserved = {str(path): sha256(path) for path in (baseline_path, vectors_path, output / "per_observation.csv")}
    baseline = json.loads(baseline_path.read_text())
    old_vectors = json.loads(vectors_path.read_text())
    for path, digest in baseline["input_sha256"].items():
        if sha256(path) != digest:
            raise ValueError(f"Frozen source differs from initial audit: {path}")
    response = next(item for item in baseline["local_response"] if item["width"] == 640
                    and item["split"] == "fit" and item["region"] == "root_band")
    direction = np.array(next(item for item in response["amplitudes"]
                              if item["amplitude_m"] == .02)["weakest_right_singular_direction"])
    state = json.loads((source / "reconstruction/model_state.json").read_text())
    model = restore_model(state["models"]["T1_B1"])
    norm = normalization(model)
    observations = json.loads((source / "observations/observations.json").read_text())
    prepared = _prepare(observations, (640, 360), "cpu", require_contours=True)
    records, vectors = [], []
    started = time.perf_counter()
    torch.set_num_threads(2)
    old = baseline["records"]
    for observation in prepared:
        original_index = next(i for i, item in enumerate(old) if item["variant"] == "baseline"
                              and item["width"] == 640 and item["sampling"] == "default"
                              and item["camera_id"] == observation["camera_id"] and item["frame_id"] == observation["frame_id"])
        baseline_z = torch.tensor(old_vectors[original_index]["baseline_observed_z_m"], dtype=torch.float32)
        for amplitude in AMPLITUDES_M:
            for sign in (-1, 1):
                signed = sign * amplitude
                candidate = perturb_combination(model, direction, signed, norm)
                diagnostic = contour_diagnostic(candidate, observation, (640, 360), baseline_z)
                records.append({"camera_id": observation["camera_id"], "frame_id": observation["frame_id"],
                                "split": "fit" if observation["split"] == "fit" else "nonblind_diagnostic",
                                "width": 640, "height": 360, "baseline_record_index": original_index,
                                "amplitude_m": signed, "parameter_components_m": (signed*direction).tolist(),
                                "counts": diagnostic["counts"], "regions": diagnostic["regions"],
                                "attribution_max_projection_error_px": diagnostic["attribution_max_projection_error_px"]})
                vectors.append({"record_index": len(records)-1, "baseline_record_index": original_index,
                                "o2c_native_px": diagnostic["o2c_native_px"].tolist()})
    comparisons = []
    for split in ("fit", "nonblind_diagnostic"):
        for camera in ("all", "C1", "C2", "C3"):
            for region in ("all", "root_band", "outside_root_band"):
                for amplitude in AMPLITUDES_M:
                    actual, predicted, plus_change, minus_change, axis_signals = [], [], [], [], []
                    for base_i, base in enumerate(old):
                        if not (base["width"] == 640 and base["sampling"] == "default" and base["variant"] == "baseline"
                                and base["split"] == split and (camera == "all" or base["camera_id"] == camera)):
                            continue
                        z = np.asarray(old_vectors[base_i]["baseline_observed_z_m"])
                        mask = (z >= ROOT_BAND_M[0]) & (z <= ROOT_BAND_M[1])
                        if region == "all": mask = np.ones(len(z), dtype=bool)
                        if region == "outside_root_band": mask = ~mask
                        combo = []
                        for sign in (-1, 1):
                            j = next(j for j, item in enumerate(records) if item["baseline_record_index"] == base_i
                                     and item["amplitude_m"] == sign * amplitude)
                            combo.append(np.asarray(vectors[j]["o2c_native_px"])[mask])
                        base_values = np.asarray(old_vectors[base_i]["o2c_native_px"])[mask]
                        actual.extend((combo[1]-combo[0])/2)
                        plus_change.extend(combo[1]-base_values); minus_change.extend(combo[0]-base_values)
                        single_columns = []
                        for parameter in PARAMETERS:
                            pair = []
                            for sign in (-1, 1):
                                j = next(j for j, item in enumerate(old) if item["width"] == 640
                                         and item["sampling"] == "default" and item["camera_id"] == base["camera_id"]
                                         and item["frame_id"] == base["frame_id"] and item["parameter"] == parameter
                                         and item["amplitude_m"] == sign * amplitude)
                                pair.append(np.asarray(old_vectors[j]["o2c_native_px"])[mask])
                            single_columns.append((pair[1]-pair[0])/2)
                        columns = np.asarray(single_columns).T
                        predicted.extend(columns @ direction); axis_signals.extend(columns)
                    actual, predicted, axis_signals = np.asarray(actual), np.asarray(predicted), np.asarray(axis_signals)
                    row = {"split": split, "camera_id": camera, "region": region, "amplitude_m": amplitude,
                           "observed_point_count": len(actual)}
                    if len(actual):
                        row.update({"actual_symmetric_rms_native_px": float(np.sqrt(np.mean(actual**2))),
                                    "linear_prediction_symmetric_rms_native_px": float(np.sqrt(np.mean(predicted**2))),
                                    "actual_plus_vs_baseline_rms_native_px": float(np.sqrt(np.mean(np.asarray(plus_change)**2))),
                                    "actual_minus_vs_baseline_rms_native_px": float(np.sqrt(np.mean(np.asarray(minus_change)**2))),
                                    "single_axis_symmetric_rms_native_px": np.sqrt(np.mean(axis_signals**2, axis=0)).tolist(),
                                    "relative_prediction_vector_error": float(np.linalg.norm(actual-predicted)/max(np.linalg.norm(actual), 1e-12))})
                    comparisons.append(row)
    unchanged = {str(path): sha256(path) == digest for path, digest in preserved.items()}
    source_unchanged = all(sha256(path) == digest for path, digest in baseline["input_sha256"].items())
    if not all(unchanged.values()) or not source_unchanged:
        raise RuntimeError("Input or earlier evidence changed during combination audit")
    report = {"status": "FIXED_DIRECTION_FORWARD_VERIFICATION_ONLY", "source": str(source),
              "direction_source": "640px fit/root_band O2C raw-distance finite differences at 0.02m; weakest right singular vector",
              "parameter_order": list(PARAMETERS), "fixed_unit_direction": direction.tolist(),
              "amplitudes_m": list(AMPLITUDES_M), "normalization": norm,
              "no_optimizer_executed": True, "no_scoring_truth_read": True,
              "direction_not_selected_by_loss_improvement": True, "candidate_model_not_adopted": True,
              "earlier_360_evidence_unchanged": unchanged, "source_inputs_unchanged": source_unchanged,
              "records": records, "response_comparisons": comparisons,
              "wall_s": time.perf_counter()-started,
              "limitations": baseline["limitations"] + [
                  "center_x/y are reference-axis offsets; chord/4 section-center convention moves geometric centroid by half the chord-halfwidth amplitude along rotated chord axis.",
                  "The direction was generated from previously inspected fit image responses, then fixed for all forward checks.",
                  "Actual plus/minus distances can change asymmetrically; a small symmetric difference alone does not mean both models equal baseline.",
                  "The same 640 production mask/sampling/visibility cases remain active; disagreement with the linear prediction is retained."]}
    destinations[0].write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    destinations[1].write_text(json.dumps(vectors, separators=(",", ":"), allow_nan=False)+"\n")
    flat = []
    for i, record in enumerate(records):
        for region in record["regions"]:
            row = {key: value for key, value in record.items() if key not in {"counts", "regions", "parameter_components_m"}}
            row.update(record_index=i, **record["counts"], region=region["region"], image_loss=region["image_loss"])
            for parameter, component in zip(PARAMETERS, record["parameter_components_m"]): row[parameter+"_m"] = component
            for d in ("c2o", "o2c"): row.update({d+"_"+key: value for key, value in region[d].items()})
            flat.append(row)
    with destinations[2].open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0])); writer.writeheader(); writer.writerows(flat)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--combination-only", action="store_true", help="Add fixed-SVD-direction verification without replacing prior evidence")
    args = parser.parse_args()
    report = (run_combination if args.combination_only else run_audit)(args.source_dir, args.output_dir)
    print(json.dumps({"status": report["status"], "records": len(report["records"]),
                      "wall_s": report["wall_s"], "frozen_mesh_check": report.get("frozen_mesh_check")}))


if __name__ == "__main__":
    main()
