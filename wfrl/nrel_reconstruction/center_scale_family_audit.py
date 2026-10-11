"""Truth-free function-space audit of geometric-center/chord coordinates.

This module does not import the optimizer or read image data. The only least
squares calculation projects an analytically prescribed displacement onto the
existing rooted center-field space; it is not a reconstruction fit.
"""
from __future__ import annotations

from .artifact_paths import relocated_path

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import torch

from .model import BladeModel, bspline_weights


TARGET_Z_M = 9.225
AMPLITUDES_M = (-.05, -.02, -.01, -.005, .005, .01, .02, .05)
EXACTNESS_ATOL_M = 1e-10  # float64 algebra check, never a physical tolerance


def penalties(center: np.ndarray, chord_log: np.ndarray) -> dict[str, float]:
    """Original physical-control formulas, including the anchored root row."""
    center, chord_log = np.asarray(center), np.asarray(chord_log)
    return {
        "relative_parameter_change": float(np.mean((center / 5.) ** 2) + np.mean(chord_log ** 2)),
        "span_smoothness": float(np.mean((np.diff(center, n=2, axis=0) / 5.) ** 2)
                                 + np.mean(np.diff(chord_log, n=2) ** 2)),
    }


def chord_axis(twist: np.ndarray) -> np.ndarray:
    return np.stack((-np.sin(twist), np.cos(twist)), axis=-1)


def centroid_offset(base_chord, twist, basis, chord_log):
    return (base_chord * np.exp(basis @ chord_log))[:, None] * chord_axis(twist) / 4.


def rooted_projection(basis, required, center):
    """Equality-constrained least squares, with original box bounds checked.

    Eliminate deltaC[0]=0 exactly. When the resulting optimum lies strictly
    inside all inherited boxes it is also the box-constrained optimum. Refuse
    other cases instead of silently returning an infeasible projection.
    """
    delta_free, _, rank, singular = np.linalg.lstsq(basis[:, 1:], required, rcond=None)
    delta = np.vstack((np.zeros((1, 2)), delta_free))
    updated = center + delta
    if rank != basis.shape[1] - 1:
        raise ValueError("Rooted center basis is not full column rank")
    if not np.all(np.abs(updated) < 5.):
        raise ValueError("Projected controls violate original physical bounds")
    residual = basis @ delta - required
    return delta, residual, {"rank": int(rank), "singular_values": singular.tolist(),
                             "box_bounds_active": False,
                             "minimum_center_bound_margin_m": float(np.min(5. - np.abs(updated)))}


def vector_metrics(values):
    lengths = np.linalg.norm(values, axis=1)
    return {"max_vector_norm_m": float(lengths.max()),
            "rms_vector_norm_m": float(np.sqrt(np.mean(lengths ** 2))),
            "rms_coordinate_m": float(np.sqrt(np.mean(values ** 2)))}


def fields(model, samples):
    """Declared diagnostic extension between the existing mesh rings."""
    samples = np.asarray(samples, dtype=float)
    z = model.z.numpy().astype(float)
    return (bspline_weights(samples, len(model.control_z), model.span_m),
            np.interp(samples, z, model.base_chord.numpy()),
            np.interp(samples, z, model.twist.numpy()))


def perturb_log(model, chord_log, amplitude_m):
    basis, base, _ = fields(model, [TARGET_Z_M])
    local_chord = float((base * np.exp(basis @ chord_log))[0])
    ratio = 1. + 2. * amplitude_m / local_chord
    if ratio <= 0:
        raise ValueError("Perturbed chord must remain positive")
    changed = chord_log.copy()
    changed[1] += np.log(ratio) / basis[0, 1]
    if np.any(np.abs(changed) >= .35):
        raise ValueError("Perturbed chord controls exceed original bounds")
    return changed


def greville_audit(model, center, chord_log):
    """Audit an exact coordinate transform, not a new forward/fitting model."""
    a, base, twist = fields(model, model.control_z.numpy())
    offset = centroid_offset(base, twist, a, chord_log)
    initial_offset = centroid_offset(base, twist, a, np.zeros_like(chord_log))
    reference_samples = a @ center
    geometric = reference_samples + offset
    relative = geometric - initial_offset
    recovered = np.linalg.solve(a, geometric - offset)
    old, pullback = penalties(center, chord_log), penalties(recovered, chord_log)
    mesh_basis = model.weights.numpy().astype(float)
    mesh_offset = centroid_offset(model.base_chord.numpy().astype(float),
                                  model.twist.numpy().astype(float), mesh_basis, chord_log)
    true_mesh_centers = mesh_basis @ center + mesh_offset
    recovered_mesh_centers = mesh_basis @ recovered + mesh_offset
    naive_mesh_centers = mesh_basis @ geometric
    # These two alternatives correctly interpolate the Greville samples, but
    # still change the family unless the offset correction is retained.
    absolute_coefficients = np.linalg.solve(a, geometric)
    relative_coefficients = np.linalg.solve(a, relative)
    mesh_initial_offset = centroid_offset(model.base_chord.numpy().astype(float),
                                          model.twist.numpy().astype(float), mesh_basis,
                                          np.zeros_like(chord_log))
    return {
        "basis_rank": int(np.linalg.matrix_rank(a)), "basis_condition_number_2": float(np.linalg.cond(a)),
        "greville_basis": a.tolist(), "geometric_centers_at_greville_m": geometric.tolist(),
        "template_relative_centers_at_greville_m": relative.tolist(),
        "maximum_recovered_control_error_m": float(np.max(np.abs(recovered - center))),
        "maximum_recovered_ring_center_error_m": float(np.max(np.abs(recovered_mesh_centers - true_mesh_centers))),
        "naive_interpolated_geometric_center_error": vector_metrics(naive_mesh_centers - true_mesh_centers),
        "independent_absolute_center_spline_error": vector_metrics(mesh_basis @ absolute_coefficients - true_mesh_centers),
        "independent_template_relative_center_spline_error": vector_metrics(mesh_initial_offset + mesh_basis @ relative_coefficients - true_mesh_centers),
        "root_reference_axis_anchor_m": center[0].tolist(),
        "root_geometric_center_m": geometric[0].tolist(),
        "root_template_relative_geometric_center_m": relative[0].tolist(),
        "regularization": {
            "original_control_coefficients": old,
            "pullback_from_geometric_greville_coordinates": pullback,
            "naive_absolute_geometric_samples_as_controls": penalties(geometric, chord_log),
            "naive_template_relative_geometric_samples_as_controls": penalties(relative, chord_log),
            "naive_reference_axis_samples_as_controls": penalties(reference_samples, chord_log),
            "naive_absolute_geometric_spline_coefficients": penalties(absolute_coefficients, chord_log),
            "naive_template_relative_geometric_spline_coefficients": penalties(relative_coefficients, chord_log),
        },
        "max_regularization_pullback_error": max(abs(old[key] - pullback[key]) for key in old),
    }


def audit(metadata):
    if metadata["interpolation"] != "bspline" or metadata["span_samples"] != 25 or len(metadata["control_z_m"]) != 7:
        raise ValueError("This frozen protocol requires the existing 25-ring, 7-control B-spline model")
    model = BladeModel(metadata["span_samples"], metadata["ring_samples"],
                       len(metadata["control_z_m"]), interpolation="bspline")
    center = np.asarray(metadata["center_offsets_m"], dtype=float)
    chord_log = np.asarray(metadata["control_chord_log_scale"], dtype=float)
    if not np.array_equal(center[0], [0., 0.]):
        raise ValueError("Root reference-axis coefficient must remain anchored")
    if np.any(np.abs(center) >= 5.) or np.any(np.abs(chord_log) >= .35):
        raise ValueError("Frozen physical controls must lie within original bounds")
    z = model.z.numpy().astype(float)
    basis = model.weights.numpy().astype(float)
    base = model.base_chord.numpy().astype(float)
    twist = model.twist.numpy().astype(float)
    initial_offset = centroid_offset(base, twist, basis, chord_log)
    root_z = np.linspace(6.15, 12.3, 247)
    root_basis, root_base, root_twist = fields(model, root_z)
    target_basis, target_base, target_twist = fields(model, [TARGET_Z_M])
    ring_rows, cases = [], []
    for amplitude in AMPLITUDES_M:
        changed = perturb_log(model, chord_log, amplitude)
        exact_required = initial_offset - centroid_offset(base, twist, basis, changed)
        delta, residual, info = rooted_projection(basis, exact_required, center)
        root_required = (centroid_offset(root_base, root_twist, root_basis, chord_log)
                         - centroid_offset(root_base, root_twist, root_basis, changed))
        root_residual = root_basis @ delta - root_required
        target_required = (centroid_offset(target_base, target_twist, target_basis, chord_log)
                           - centroid_offset(target_base, target_twist, target_basis, changed))
        target_residual = target_basis @ delta - target_required
        compensation_metrics = vector_metrics(exact_required)
        old_penalties, projected_penalties = penalties(center, chord_log), penalties(center + delta, changed)
        row = {
            "halfwidth_amplitude_m": amplitude,
            "control1_chord_log_increment": float(changed[1] - chord_log[1]),
            "chord_control_min_bound_margin": float(np.min(.35 - np.abs(changed))),
            "center_projection": info,
            "center_control_deltas_m": delta.tolist(),
            "projected_center_controls_m": (center + delta).tolist(),
            "required_compensation_metrics": compensation_metrics,
            "all_ring_residual": vector_metrics(residual),
            "root_band_continuous_residual": vector_metrics(root_residual),
            "target_residual_xy_m": target_residual[0].tolist(),
            "target_residual_norm_m": float(np.linalg.norm(target_residual[0])),
            "maximum_residual_ring_z_m": float(z[np.linalg.norm(residual, axis=1).argmax()]),
            "relative_projection_residual_l2": float(np.linalg.norm(residual) / np.linalg.norm(exact_required)),
            "exact_in_original_ring_space_at_float64_tolerance": bool(np.max(np.linalg.norm(residual, axis=1)) <= EXACTNESS_ATOL_M),
            "original_regularization": old_penalties,
            "projected_original_family_regularization": projected_penalties,
            "projected_greville_audit": greville_audit(model, center + delta, changed),
        }
        cases.append(row)
        for index, position in enumerate(z):
            ring_rows.append({"halfwidth_amplitude_m": amplitude, "ring_index": index, "z_m": float(position),
                              "required_compensation_x_m": float(exact_required[index, 0]),
                              "required_compensation_y_m": float(exact_required[index, 1]),
                              "projected_compensation_x_m": float((basis @ delta)[index, 0]),
                              "projected_compensation_y_m": float((basis @ delta)[index, 1]),
                              "residual_x_m": float(residual[index, 0]),
                              "residual_y_m": float(residual[index, 1]),
                              "residual_norm_m": float(np.linalg.norm(residual[index]))})
    return {
        "status": "FUNCTION_SPACE_DIAGNOSTIC_ONLY_NO_TRUTH_NO_IMAGE_FIT",
        "source_model": "frozen 20261002-second reconstruction T1_B1 physical controls",
        "precision": "float64 algebra over existing frozen float32 template/basis buffers; no float32 inverse-tanh round trip",
        "numerical_exactness_atol_m": EXACTNESS_ATOL_M,
        "numerical_exactness_scope": "arithmetic check only; not metrology or acceptance tolerance",
        "original_basis_storage_vs_exact_float64_max": float(np.max(np.abs(basis - bspline_weights(z, 7)))),
        "baseline_greville_audit": greville_audit(model, center, chord_log),
        "cases": cases,
        "conclusions": {
            "center_preserving_ring_chord_direction_is_exact_original_family": all(case["exact_in_original_ring_space_at_float64_tolerance"] for case in cases),
            "original_family_has_invertible_greville_coordinate_map": True,
            "greville_map_preserves_objective_only_with_inverse_map_and_inherited_constraints": True,
            "independent_bspline_geometric_centers_or_naive_penalties_change_shape_prior": True,
            "root_reference_anchor_is_not_root_geometric_anchor": True,
            "new_fit_executed": False,
        },
    }, ring_rows


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(root, output, protocol_path=None, addendum_path=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    protocol_path = relocated_path(protocol_path, root=root) if protocol_path else output / "protocol.json"
    protocol = json.loads(protocol_path.read_text())
    allowed_paths = {name: relocated_path(root / name, root=root) for name in protocol["inputs"]}
    for name, path in allowed_paths.items():
        if "truth" in path.parts or _hash(path) != protocol["inputs"][name]:
            raise ValueError(f"Frozen input changed or forbidden: {path}")
    for name in ("family_audit.json", "ring_residuals.csv", "run_record.json"):
        if (output / name).exists():
            raise FileExistsError(f"Refusing to overwrite audit result: {name}")
    source_hash = _hash(Path(__file__).resolve())
    run_record = {"started_at_utc": datetime.now(timezone.utc).isoformat(),
                  "source_path": str(Path(__file__).resolve()), "source_sha256": source_hash,
                  "protocol_path": str(protocol_path), "protocol_sha256": _hash(protocol_path),
                  "output_dir": str(output)}
    if addendum_path:
        addendum_path = Path(addendum_path).resolve()
        addendum = json.loads(addendum_path.read_text())
        if addendum["extends_protocol_sha256"] != _hash(protocol_path):
            raise ValueError("Addendum does not extend this frozen protocol")
        run_record.update({"addendum_path": str(addendum_path), "addendum_sha256": _hash(addendum_path)})
    output.mkdir(parents=True, exist_ok=True)
    with (output / "run_record.json").open("x") as handle:
        json.dump(run_record, handle, indent=2); handle.write("\n")
    state_path = root / "outputs/nrel-video-single-blade/stages/02-second-run/reconstruction/model_state.json"
    state = json.loads(state_path.read_text())
    result, rows = audit(state["models"]["T1_B1"])
    result["protocol_sha256"] = _hash(protocol_path)
    result["run_record"] = run_record
    result["source_unchanged_since_run_started"] = source_hash == _hash(Path(__file__).resolve())
    result["input_hashes_before"] = protocol["inputs"]
    result["input_hashes_after"] = {name: _hash(path) for name, path in allowed_paths.items()}
    result["inputs_unchanged"] = result["input_hashes_before"] == result["input_hashes_after"]
    if not result["inputs_unchanged"] or not result["source_unchanged_since_run_started"]:
        raise ValueError("Frozen inputs or audit source changed during audit")
    (output / "family_audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    with (output / "ring_residuals.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps({"output": str(output), "cases": len(result["cases"]), "ring_rows": len(rows),
                      "inputs_unchanged": result["inputs_unchanged"], "conclusions": result["conclusions"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, help="Reuse the frozen protocol while writing to a fresh output directory")
    parser.add_argument("--addendum", type=Path, help="Optional frozen Greville-interpolation extension protocol")
    args = parser.parse_args()
    run(args.repo_root, args.output_dir, args.protocol, args.addendum)
