"""Prospectively frozen forward-only center/scale decomposition of formal v2.

A is the unchanged chord control operation; B subtracts its analytic ring-center
translation; C applies that translation to the baseline shape. B and C are mesh
interventions, not a claimed reparameterization of the seven-control model.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch

from .contour import _limit, _silhouette_edges, projected_contour_candidates, projected_contour_loss
from .diagnostics import _permitted, read_mesh_ply
from .optimize import _prepare
from .parameter_audit import (ROOT_BAND_M, TARGET_Z_M, distance_stats, edge_attributes,
                              nearest_segments, normalization, perspective_attribute,
                              perturb_model, restore_model, robust_values, sha256)

AMPLITUDES_M = (.005, .01, .02, .05)
MODES = ("X", "Y", "A", "B", "C")
GEOMETRY_ATOL_M = 2e-6


class FrozenMesh:
    def __init__(self, vertices, faces):
        self.vertices, self.faces = vertices.detach().clone(), faces.detach().clone()

    def __call__(self):
        return self.vertices


def decompose_chord(model, amplitude_m, norm=None):
    """Preserve original float32 A and analytically split translation/scale."""
    if not np.isfinite(amplitude_m):
        raise ValueError("Amplitude must be finite")
    changed = perturb_model(model, "chord_halfwidth", amplitude_m, norm)
    original_v, changed_v = model().detach(), changed().detach()
    _, original_log = model.control_values()
    _, changed_log = changed.control_values()
    original_chord = model.base_chord * torch.exp(model.weights @ original_log)
    changed_chord = model.base_chord * torch.exp(model.weights @ changed_log)
    delta_chord = changed_chord - original_chord
    shift = torch.stack((-torch.sin(model.twist) * delta_chord / 4,
                          torch.cos(model.twist) * delta_chord / 4,
                          torch.zeros_like(delta_chord)), dim=1)
    per_vertex = shift[:, None, :].expand(-1, model.ring_samples, -1).reshape(-1, 3)
    meshes = {"A": FrozenMesh(changed_v, model.faces),
              "B": FrozenMesh(changed_v - per_vertex, model.faces),
              "C": FrozenMesh(original_v + per_vertex, model.faces)}
    # Double precision reductions measure the float32 output residual, rather
    # than hiding that residual behind float32 mean cancellation.
    base = original_v.double().reshape(model.span_samples, model.ring_samples, 3)
    rings = {key: mesh().double().reshape_as(base) for key, mesh in meshes.items()}
    centered = lambda ring: ring - ring.mean(1, keepdim=True)
    base_pair = base[:, :, None, :] - base[:, None, :, :]
    c_pair = rings["C"][:, :, None, :] - rings["C"][:, None, :, :]
    scale = (changed_chord / original_chord).double()[:, None, None]
    unsupported = model.weights[:, 1] == 0
    checks = {
        "vertex_A_minus_base_equals_B_plus_C_minus_2base_max_error_m":
            float(((rings["A"]-base)-(rings["B"]-base)-(rings["C"]-base)).abs().max()),
        "B_geometric_ring_center_max_error_m": float((rings["B"].mean(1)-base.mean(1)).abs().max()),
        "C_centered_ring_vertices_max_error_m": float((centered(rings["C"])-centered(base)).abs().max()),
        "C_pairwise_ring_distance_max_error_m": float((torch.linalg.vector_norm(c_pair, dim=-1)-torch.linalg.vector_norm(base_pair, dim=-1)).abs().max()),
        "B_isotropic_centered_ring_scale_max_error_m": float((centered(rings["B"])-centered(base)*scale).abs().max()),
        "A_ring_center_vs_analytic_shift_max_error_m": float((rings["A"].mean(1)-base.mean(1)-shift.double()).abs().max()),
        "root_vertices_max_error_m": max(float((ring[0]-base[0]).abs().max()) for ring in rings.values()),
        "tip_vertices_max_error_m": max(float((ring[-1]-base[-1]).abs().max()) for ring in rings.values()),
        "outside_control_support_max_error_m": max(float((ring[unsupported]-base[unsupported]).abs().max()) for ring in rings.values()),
        "z_coordinates_max_error_m": max(float((ring[:, :, 2]-base[:, :, 2]).abs().max()) for ring in rings.values()),
    }
    if max(checks.values()) > GEOMETRY_ATOL_M:
        raise ValueError(f"Center/scale geometry identity failed: {checks}")
    checks.update({"amplitude_m": amplitude_m, "geometry_tolerance_m": GEOMETRY_ATOL_M,
                   "faces_exact": all(torch.equal(mesh.faces, model.faces) for mesh in meshes.values()),
                   "fixed_thickness_to_chord": True, "fixed_twist": True,
                   "ring_z_m": model.z.tolist(), "ring_delta_chord_m": delta_chord.tolist(),
                   "ring_center_translation_m": shift.tolist(),
                   "max_analytic_ring_center_translation_m": float(torch.linalg.vector_norm(shift, dim=1).max())})
    return meshes, checks


def region_mask(z, region):
    root = (np.asarray(z) >= ROOT_BAND_M[0]) & (np.asarray(z) <= ROOT_BAND_M[1])
    return root if region == "root_band" else ~root if region == "outside_root_band" else np.ones(len(z), dtype=bool)


def evaluate_mesh(mesh, observation, size, baseline_z=None):
    """Retain the production backend and explicitly expose active branches."""
    vertices = mesh()
    candidates, segments, counts = projected_contour_candidates(
        vertices, mesh.faces, observation["K"], observation["camera"], observation["root"],
        size, observation["labels"], observation["contour_valid"])
    all_observed = observation["contour_points"]
    inside = (torch.isfinite(all_observed).all(-1) & (all_observed[:, 0] >= 0) &
              (all_observed[:, 0] <= size[0]-1) & (all_observed[:, 1] >= 0) & (all_observed[:, 1] <= size[1]-1))
    observed = _limit(all_observed[inside], 600)
    counts.update(observed_source_count=len(all_observed), observed_inside_count=int(inside.sum()),
                  observed_count=len(observed), observed_sampling_truncated=int(int(inside.sum()) > len(observed)))
    if not len(candidates) or not len(segments) or not len(observed):
        raise ValueError("Missing trusted contour term cannot be reported as zero")
    c2o = torch.linalg.vector_norm(candidates[:, None]-observed[None], dim=-1).min(1).values
    o2c, nearest, fraction = nearest_segments(observed, segments)
    candidate_z, _, ce = edge_attributes(candidates, vertices, mesh.faces, observation)
    segment_z, segment_depth, se = edge_attributes(segments.reshape(-1, 2), vertices, mesh.faces, observation)
    segment_z, segment_depth = segment_z.reshape(-1, 2), segment_depth.reshape(-1, 2)
    inferred_z, _ = perspective_attribute(fraction, segment_z[nearest, 0], segment_z[nearest, 1],
                                          segment_depth[nearest, 0], segment_depth[nearest, 1])
    observed_z = inferred_z if baseline_z is None else baseline_z
    if len(observed_z) != len(observed):
        raise ValueError("Frozen observed index set mismatch")
    error = max(float(ce.max()), float(se.max()))
    if error > .005:
        raise ValueError(f"Unreliable projected-edge attribution {error}")
    scale = size[0]/1920.
    uncertainty = observation["contour_uncertainty_px"]
    regions = []
    for name in ("all", "root_band", "outside_root_band"):
        cmask, omask = region_mask(candidate_z, name), region_mask(observed_z, name)
        cs, os = distance_stats(c2o[cmask], uncertainty, scale), distance_stats(o2c[omask], uncertainty, scale)
        regions.append(dict(region=name, c2o=cs, o2c=os,
                            image_loss=.5*(cs["robust_mean"]+os["robust_mean"]) if cs["count"] and os["count"] else None))
    # Nearest original silhouette edge is diagnostic provenance, not a physical
    # correspondence. Segment IDs alone can shift when other pieces disappear.
    transform = observation["camera"] @ observation["root"]
    camera = vertices @ transform[:3, :3].T + transform[:3, 3]
    edges = torch.tensor(_silhouette_edges(camera, mesh.faces), dtype=torch.long)
    endpoints = camera[edges] @ observation["K"].T
    projected = endpoints[:, :, :2]/endpoints[:, :, 2:3]
    _, owner, _ = nearest_segments(segments.mean(1), projected)
    owners = edges[owner[nearest]].numpy()
    raw = (o2c/scale).numpy()
    robust = robust_values(o2c, uncertainty).numpy()
    branches = dict(silhouette_edges=edges.tolist(), nearest_original_edge=owners,
                    o2c_deadband_active=(o2c > uncertainty).numpy())
    return dict(counts=counts, regions=regions, observed_z=observed_z,
                observed_native_pixels=((observed+.5)/scale-.5).numpy(), o2c_native_px=raw,
                o2c_robust=robust, branches=branches, attribution_max_projection_error_px=error)


def rms(value):
    return float(np.sqrt(np.mean(np.asarray(value)**2))) if len(value) else None


def response_summary(records, vectors):
    lookup = {(r["width"], r["camera_id"], r["frame_id"], r["mode"], r["amplitude_m"]): i
              for i, r in enumerate(records)}
    summaries = []
    for width in (640, 320):
        for split in ("fit", "nonblind_diagnostic"):
            base_indices = [i for i, r in enumerate(records) if r["width"] == width and r["split"] == split and r["mode"] == "baseline"]
            for region in ("all", "root_band", "outside_root_band"):
                previous = {}
                for amplitude in AMPLITUDES_M:
                    columns, mode_rows = {}, {}
                    for mode in MODES:
                        derivative, plus, minus, robust_plus, robust_minus, robust_base = [], [], [], [], [], []
                        global_losses = {-1: [], 0: [], 1: []}
                        for i in base_indices:
                            base = records[i]; bv = vectors[i]
                            mask = region_mask(bv["observed_z"], region)
                            pair = []
                            for sign in (-1, 1):
                                j = lookup[(width, base["camera_id"], base["frame_id"], mode, sign*amplitude)]
                                pair.append(vectors[j]["o2c_native_px"][mask])
                                (robust_minus if sign < 0 else robust_plus).extend(vectors[j]["o2c_robust"][mask])
                                global_losses[sign].append(records[j]["regions"][0]["image_loss"])
                            b = bv["o2c_native_px"][mask]
                            derivative.extend((pair[1]-pair[0])/(2*amplitude))
                            plus.extend(pair[1]-b); minus.extend(pair[0]-b)
                            robust_base.extend(bv["o2c_robust"][mask])
                            global_losses[0].append(base["regions"][0]["image_loss"])
                        d = np.asarray(derivative); columns[mode] = d
                        base_loss = float(np.mean(global_losses[0]))
                        mode_rows[mode] = dict(point_count=len(d), rms_derivative_native_px_per_m=rms(d),
                            symmetric_response_rms_native_px=rms(d*amplitude),
                            plus_vs_baseline_rms_native_px=rms(plus), minus_vs_baseline_rms_native_px=rms(minus),
                            fixed_observed_robust_mean={"baseline": float(np.mean(robust_base)) if robust_base else None,
                                "plus": float(np.mean(robust_plus)) if robust_plus else None,
                                "minus": float(np.mean(robust_minus)) if robust_minus else None},
                            full_bidirectional_loss_equal_observation_mean={"baseline": base_loss,
                                "plus": float(np.mean(global_losses[1])), "minus": float(np.mean(global_losses[-1])),
                                "plus_delta": float(np.mean(global_losses[1]))-base_loss,
                                "minus_delta": float(np.mean(global_losses[-1]))-base_loss},
                            relative_derivative_change_from_previous_amplitude=(float(np.linalg.norm(d-previous[mode])/max(np.linalg.norm(previous[mode]), 1e-12)) if mode in previous else None))
                    matrices = {}
                    for last in ("A", "B"):
                        matrix = np.column_stack([columns[k] for k in ("X", "Y", last)])
                        if not len(matrix):
                            matrices[last] = None; continue
                        _, singular, right = np.linalg.svd(matrix/np.sqrt(len(matrix)), full_matrices=False)
                        norms = np.linalg.norm(matrix, axis=0)
                        matrices[last] = dict(mode_order=["X", "Y", last], singular_values_native_px_per_m=singular.tolist(),
                            weakest_right_singular_direction=right[-1].tolist(),
                            column_cosine=np.divide(matrix.T@matrix, norms[:, None]*norms[None, :],
                                out=np.zeros((3, 3)), where=norms[:, None]*norms[None, :] > 0).tolist())
                    denom = np.linalg.norm(columns["A"])
                    summaries.append(dict(width=width, split=split, region=region, amplitude_m=amplitude,
                        previous_amplitude_m=None if not previous else AMPLITUDES_M[AMPLITUDES_M.index(amplitude)-1],
                        modes=mode_rows, response_matrices=matrices,
                        image_derivative_A_minus_B_minus_C_rms_native_px_per_m=rms(columns["A"]-columns["B"]-columns["C"]),
                        image_derivative_decomposition_relative_error=float(np.linalg.norm(columns["A"]-columns["B"]-columns["C"])/max(denom, 1e-12)) if len(columns["A"]) else None))
                    previous = columns
    return summaries


def run_audit(source_dir, output_dir):
    source, output = _permitted(source_dir), Path(output_dir).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new empty output directory; frozen evidence is append-only")
    output.mkdir(parents=True, exist_ok=True)
    paths = [source/"reconstruction"/n for n in ("model_state.json", "T1_B1.ply", "frozen_config.json")]
    paths += [source/"observations/observations.json", source/"model_freeze.json"]
    state, observations = json.loads(paths[0].read_text()), json.loads(paths[3].read_text())
    expected = {(c, f) for c in ("C1", "C2", "C3") for f in (42, 43, 44, 45)}
    if len(observations) != 12 or {(o["camera_id"], o["frame_id"]) for o in observations} != expected:
        raise ValueError("Expected the fixed 12 observations")
    if any((o["split"] == "fit") != (o["frame_id"] in (42, 43)) for o in observations):
        raise ValueError("Expected frames 42/43 fit and 44/45 nonblind")
    paths += [_permitted(o[k]) for o in observations for k in ("labels_path", "contour_points_path", "contour_valid_path")]
    code_paths = [Path(__file__).resolve().parent/name for name in
                  ("center_scale_audit.py", "parameter_audit.py", "model.py", "contour.py", "optimize.py", "renderer.py", "diagnostics.py")]
    hashes = {str(p): sha256(p) for p in paths+code_paths}
    freeze = json.loads(paths[4].read_text())
    if hashes[str(paths[1])] != freeze["sha256"]["reconstruction/T1_B1.ply"]:
        raise ValueError("Frozen formal mesh signature mismatch")
    model = restore_model(state["models"]["T1_B1"])
    reference_v, reference_f = read_mesh_ply(paths[1])
    mesh_error = float(np.abs(model().numpy()-reference_v).max())
    if mesh_error > GEOMETRY_ATOL_M or not np.array_equal(model.faces.numpy(), reference_f):
        raise ValueError("Formal model restore mismatch")
    norm = normalization(model)
    variants = [("baseline", 0., FrozenMesh(model(), model.faces))]
    geometry_checks = []
    for amplitude in AMPLITUDES_M:
        for sign in (-1, 1):
            signed = amplitude*sign
            split_mesh, checks = decompose_chord(model, signed, norm)
            geometry_checks.append(checks)
            for mode, parameter in (("X", "center_x"), ("Y", "center_y")):
                changed = perturb_model(model, parameter, signed, norm)
                variants.append((mode, signed, FrozenMesh(changed(), model.faces)))
            variants.extend((mode, signed, split_mesh[mode]) for mode in ("A", "B", "C"))
    protocol = dict(status="FROZEN_BEFORE_ANY_REAL_IMAGE_EVALUATION", created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
        source=str(source), input_and_source_sha256=hashes, formal_mesh_max_coordinate_error_m=mesh_error,
        normalization=norm, root_band_m=list(ROOT_BAND_M), amplitudes_m=list(AMPLITUDES_M),
        modes={"X": "reference-axis center_x", "Y": "reference-axis center_y",
            "A": "original chord_halfwidth perturbation; includes quarter-chord geometric-center shift",
            "B": "A vertices minus analytic delta_chord/4 times rotated chord axis at every ring; fixed geometric center",
            "C": "baseline vertices plus the same analytic ring translation; fixed section shape/size"},
        mode_family_limit="B/C mesh interventions are not certified members of the original seven-control model family",
        widths_in_execution_order=[640, 320], primary_width=640, verification_width=320,
        sampling=dict(candidate_limit=300, observed_limit=600, working_spacing_px=1., per_edge_limit=512),
        observation_split="42/43 fit; previously viewed 44/45 nonblind_diagnostic; never tune on either",
        expected_image_configurations=984, geometry_float32_tolerance_m=GEOMETRY_ATOL_M,
        target_halfwidth_normalization="continuous field at 9.225m, not an inserted vertex; control_index=1",
        observed_z_policy="assign once per width/camera/frame from baseline nearest edge, then retain same indices and z for all modes",
        metrics=["fixed-observed unsigned O2C native-pixel response", "pointwise true deadband+robust O2C",
            "production bidirectional robust contour loss, equal observation average", "[X,Y,A] versus [X,Y,B] SVD and column cosine",
            "adjacent amplitude derivative stability", "A-B-C image derivative nonadditivity",
            "branch count, silhouette edge, nearest original edge and O2C deadband activity changes"],
        forbidden=["optimizer", "ground-truth scoring read", "new acquisition or rendering", "best-mode selection", "acceptance or default change"],
        geometry_checks=geometry_checks,
        predeclared_interpretation="Persistent weak response after removing center drift means the parameter convention alone is insufficient; it does not identify the unique cause or establish confidence intervals.")
    protocol_path = output/"frozen_protocol.json"
    protocol_path.write_text(json.dumps(protocol, indent=2, allow_nan=False)+"\n")
    protocol_hash = sha256(protocol_path)
    (output/"frozen_protocol.sha256").write_text(protocol_hash+"  frozen_protocol.json\n")
    # This is saved before _prepare and before a single real image evaluation.
    np.savez_compressed(output/"geometry_variants.npz", vertices=np.stack([m().numpy() for _, _, m in variants]),
                        faces=model.faces.numpy(), mode=np.array([x[0] for x in variants]),
                        amplitude_m=np.array([x[1] for x in variants]))
    torch.set_num_threads(2)
    started = time.perf_counter(); records, vectors, parity = [], [], []
    for width in (640, 320):
        size = (width, width*9//16)
        prepared = _prepare(observations, size, "cpu", require_contours=True)
        for observation in prepared:
            baseline_z, base_diagnostic, baseline_i = None, None, len(records)
            for mode, signed, mesh in variants:
                d = evaluate_mesh(mesh, observation, size, baseline_z)
                if baseline_z is None:
                    baseline_z = d["observed_z"].clone(); base_diagnostic = d
                    exact, _ = projected_contour_loss(mesh(), mesh.faces, observation["K"], observation["camera"], observation["root"],
                        size, observation["labels"], observation["contour_points"], observation["contour_valid"],
                        uncertainty_px=observation["contour_uncertainty_px"])
                    error = abs(float(exact)-d["regions"][0]["image_loss"])
                    if error > 1e-7: raise ValueError("Diagnostic/production objective mismatch")
                    parity.append(dict(width=width, camera_id=observation["camera_id"], frame_id=observation["frame_id"], error=error))
                if not np.array_equal(d["observed_native_pixels"], base_diagnostic["observed_native_pixels"]):
                    raise ValueError("Observed samples shifted across variants")
                changed_counts = {k: [base_diagnostic["counts"][k], v] for k, v in d["counts"].items() if v != base_diagnostic["counts"][k]}
                edge_change = np.any(d["branches"]["nearest_original_edge"] != base_diagnostic["branches"]["nearest_original_edge"], axis=1)
                dead_change = d["branches"]["o2c_deadband_active"] != base_diagnostic["branches"]["o2c_deadband_active"]
                silhouette_changed = d["branches"]["silhouette_edges"] != base_diagnostic["branches"]["silhouette_edges"]
                region_changes = {region: dict(observed_count=int(region_mask(baseline_z, region).sum()),
                    nearest_original_edge_changed_count=int(edge_change[region_mask(baseline_z, region)].sum()),
                    deadband_active_changed_count=int(dead_change[region_mask(baseline_z, region)].sum()))
                    for region in ("all", "root_band", "outside_root_band")}
                record = dict(record_index=len(records), baseline_record_index=baseline_i,
                    width=width, height=size[1], camera_id=observation["camera_id"], frame_id=observation["frame_id"],
                    split="fit" if observation["split"] == "fit" else "nonblind_diagnostic", mode=mode, amplitude_m=signed,
                    counts=d["counts"], regions=d["regions"], changed_counts=changed_counts,
                    silhouette_edge_set_changed=silhouette_changed, branch_changes_by_region=region_changes,
                    any_detected_branch_change=bool(changed_counts or silhouette_changed or edge_change.any() or dead_change.any()),
                    attribution_max_projection_error_px=d["attribution_max_projection_error_px"])
                records.append(record)
                vectors.append(dict(observed_z=baseline_z.numpy(), observed_native_pixels=d["observed_native_pixels"],
                    o2c_native_px=d["o2c_native_px"], o2c_robust=d["o2c_robust"],
                    nearest_original_edge=d["branches"]["nearest_original_edge"], deadband_active=d["branches"]["o2c_deadband_active"]))
            print(json.dumps(dict(width=width, camera=observation["camera_id"], frame=observation["frame_id"],
                                  completed=len(records), elapsed_s=round(time.perf_counter()-started, 2))), flush=True)
    if len(records) != 984: raise AssertionError("Incomplete planned suite")
    if any(sha256(p) != digest for p, digest in hashes.items()) or sha256(protocol_path) != protocol_hash:
        raise RuntimeError("Frozen input/source/protocol changed during run")
    summaries = response_summary(records, vectors)
    report = dict(status="CONTROLLED_FORWARD_ONLY_NO_REFIT", protocol_sha256=protocol_hash, records=records,
        response_summary=summaries, geometry_checks=geometry_checks,
        production_loss_parity=parity, frozen_input_and_source_hashes_unchanged=True,
        no_optimizer_executed=True, no_scoring_truth_read=True, no_mode_selected=True,
        actual_image_configurations=len(records), extra_production_objective_baseline_checks=len(parity),
        wall_s=time.perf_counter()-started,
        limitations=["Float32 ring-center and decomposition equalities are checked to the prospectively fixed 2e-6m tolerance, not exact arithmetic.",
            "B/C are ring-wise mesh interventions and need not belong to the seven-control model family; no regularization equivalence is claimed.",
            "Same counts do not certify the same complete mask/visibility/sample branch; nearest-edge ownership is diagnostic and can be ambiguous at overlap.",
            "Observed z remains baseline-derived, not known 3D correspondence; candidate attribution is recomputed.",
            "Cross-resolution comparisons also change mask resampling, sampling, probes and working-pixel robust scale.",
            "Raw unsigned O2C derivatives/SVD are local diagnostic responses, not uncertainty or global identifiability.",
            "Fit/nonblind are always separate; 44/45 are previously viewed and cannot become blind validation.",
            "No geometric accuracy, acceptable application tolerance or unique degeneration cause is established by this forward suite."])
    (output/"center_scale_audit.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    arrays = {}
    for i, vector in enumerate(vectors):
        for key, value in vector.items(): arrays[f"r{i:04d}_{key}"] = value
    np.savez_compressed(output/"observed_response_vectors.npz", **arrays)
    flat = []
    for r in records:
        for region in r["regions"]:
            row = {k: r[k] for k in ("record_index", "baseline_record_index", "width", "height", "camera_id", "frame_id", "split", "mode", "amplitude_m", "any_detected_branch_change", "silhouette_edge_set_changed")}
            row.update(r["counts"]); row.update(region=region["region"], image_loss=region["image_loss"])
            row.update(r["branch_changes_by_region"][region["region"]])
            for direction in ("c2o", "o2c"):
                row.update({f"{direction}_{k}": v for k, v in region[direction].items()})
            flat.append(row)
    with (output/"per_observation.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat[0])); writer.writeheader(); writer.writerows(flat)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True); parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(); result = run_audit(args.source_dir, args.output_dir)
    print(json.dumps({key: result[key] for key in ("status", "actual_image_configurations", "wall_s", "protocol_sha256")}))


if __name__ == "__main__":
    main()
