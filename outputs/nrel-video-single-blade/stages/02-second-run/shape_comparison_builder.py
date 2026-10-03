"""Rebuild shape-parameter inspection using frozen model metadata / PLY only.

No frame observations, truth surface or scoring/heldout data are used here.
Run with the existing wfrl-mac Python environment from any directory.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.interpolate import BSpline


OUTPUT = Path(__file__).resolve().parent
INPUTS = {
    "v1": OUTPUT.parent / "01-first-run/formal/reconstruction",
    "smooth_ablation": OUTPUT / "smooth-ablation",
    "contour_ablation": OUTPUT / "contour-ablation",
    "reconstruction": OUTPUT / "reconstruction",
}
LABELS = {"v1": "v1: linear", "smooth_ablation": "Smooth only",
          "contour_ablation": "Contour ablation", "reconstruction": "v2: final"}
COLORS = {"v1": "#777777", "smooth_ablation": "#0072B2",
          "contour_ablation": "#D55E00", "reconstruction": "#009E73"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_ascii_ply(path):
    path = Path(path).resolve()
    if any(part in {"evaluation-only", "internal-capture"} for part in path.parts):
        raise ValueError("Only independently reconstructed model PLY files are permitted")
    with path.open() as handle:
        if handle.readline().strip() != "ply" or handle.readline().strip() != "format ascii 1.0":
            raise ValueError("Expected ASCII model PLY")
        count = None
        for line in handle:
            fields = line.strip().split()
            if fields[:2] == ["element", "vertex"]:
                count = int(fields[2])
            if fields == ["end_header"]:
                break
        if count is None:
            raise ValueError("Missing vertex count")
        return np.array([[float(value) for value in handle.readline().split()[:3]] for _ in range(count)])


def parameter_curves(metadata, z):
    centres = np.asarray(metadata["center_offsets_m"], dtype=float)
    log_chord = np.asarray(metadata["control_chord_log_scale"], dtype=float)
    interpolation = metadata.get("interpolation", "linear")
    if interpolation == "bspline":
        knots = np.asarray(metadata["basis_knots_m"], dtype=float)
        spline = BSpline(knots, centres, 3, extrapolate=False)
        centre_curve = spline(z)
        chord_curve = np.exp(BSpline(knots, log_chord, 3, extrapolate=False)(z))
        derivative_jumps = []
        epsilon = 1e-6
        for knot in np.unique(knots)[1:-1]:
            derivative_jumps.append(float(np.linalg.norm(spline.derivative(1)(knot + epsilon)
                                                        - spline.derivative(1)(knot - epsilon))))
        jump_scope = "finite difference across simple knots at +/- 1e-6 m; analytically C2"
    elif interpolation == "linear":
        stations = np.asarray(metadata["control_z_m"], dtype=float)
        centre_curve = np.stack([np.interp(z, stations, centres[:, axis]) for axis in range(2)], axis=1)
        chord_curve = np.exp(np.interp(z, stations, log_chord))
        interval_slopes = np.diff(centres, axis=0) / np.diff(stations)[:, None]
        derivative_jumps = np.linalg.norm(np.diff(interval_slopes, axis=0), axis=1).tolist()
        jump_scope = "exact first-derivative jumps between linear control intervals"
    else:
        raise ValueError("Unknown frozen interpolation")
    # Common-grid finite differences deliberately treat every model identically.
    # Linear knot spikes are grid dependent, so this is a diagnostic proxy rather
    # than a physical curvature measurement or an engineering score.
    curve3d = np.column_stack((centre_curve, z))
    first = np.gradient(curve3d, z, axis=0, edge_order=2)
    second = np.gradient(first, z, axis=0, edge_order=2)
    curvature = np.linalg.norm(np.cross(first, second), axis=1) / np.linalg.norm(first, axis=1) ** 3
    return centre_curve, chord_curve, curvature, derivative_jumps, jump_scope


def main():
    z = np.linspace(0.0, 61.5, 1201)
    report = {
        "input_policy": "Frozen independently fitted model_state.json and model PLY only; no RGB, heldout or truth data",
        "coordinate_frame": "blade_root_local", "length_units": "m", "common_z_m": z.tolist(),
        "grid_spacing_m": float(z[1] - z[0]),
        "centerline_definition": "optimized x/y offsets relative to the fixed initial template pitch-axis parameterization; not the mesh vertex centroid",
        "chord_scale_definition": "exp(interpolated control_chord_log_scale), relative to identical fixed initial template",
        "curvature_definition": "common-grid central finite differences of r(z)=(offset_x,offset_y,z); kappa=norm(r' cross r'')/norm(r')^3",
        "curvature_limit": "Linear controls have undefined ordinary curvature at tangent discontinuities; plotted numerical spikes depend on grid spacing",
        "interpretation_limits": [
            "B-spline removes control-parameter kinks; it does not establish physical geometry accuracy",
            "Only optimized offset/log-chord fields are C2; the fixed initial dimension profile is unchanged",
            "Exported sampled meshes remain piecewise planar triangles, not mathematically C2 surfaces",
            "Different runs have different fit budgets/losses; this plot does not isolate a causal image or geometric improvement",
        ],
        "models": {},
    }
    curves = {}
    initial_vertices = {}
    before_signatures = {}
    for key, root in INPUTS.items():
        paths = {name: root / name for name in ("model_state.json", "T1_B1.ply", "initial_template.ply")}
        signatures = {name: sha(path) for name, path in paths.items()}
        before_signatures[key] = signatures
        metadata_all = json.loads(paths["model_state.json"].read_text())
        metadata = metadata_all["models"]["T1_B1"]
        if metadata["units"] != "m" or metadata["coordinate_frame"] != "blade_root_local" or metadata["span_m"] != 61.5:
            raise ValueError("Model coordinate/units differ")
        centre, chord, curvature, jumps, jump_scope = parameter_curves(metadata, z)
        curves[key] = centre, chord, curvature
        initial_vertices[key] = read_ascii_ply(paths["initial_template.ply"])
        vertices = read_ascii_ply(paths["T1_B1.ply"])
        ring_centroids = vertices.reshape(metadata["span_samples"], metadata["ring_samples"], 3).mean(axis=1)
        report["models"][key] = {
            "label": LABELS[key], "model_dir": str(root.resolve()), "input_sha256": signatures,
            "interpolation": metadata.get("interpolation", "linear"),
            "centerline_offsets_m": centre.tolist(), "chord_scale": chord.tolist(),
            "common_grid_curvature_proxy_per_m": curvature.tolist(),
            "curvature_proxy_summary": {"mean_per_m": float(curvature.mean()), "max_per_m": float(curvature.max())},
            "first_derivative_jumps": jumps, "derivative_jump_scope": jump_scope,
            "max_first_derivative_jump": max(jumps, default=0.0),
            "mesh_ring_centroids_m": ring_centroids.tolist(),
            "mesh_sampling": {"span_samples": metadata["span_samples"], "ring_samples": metadata["ring_samples"]},
        }
    baseline = initial_vertices["v1"]
    report["same_initial_identity"] = {
        "all_initial_ply_sha256_equal": len({item["initial_template.ply"] for item in before_signatures.values()}) == 1,
        "all_initial_vertex_arrays_equal": all(np.array_equal(baseline, vertices) for vertices in initial_vertices.values()),
        "initial_mesh_sha256": before_signatures["v1"]["initial_template.ply"],
        "initial_vertex_count": len(baseline),
    }
    if not all(report["same_initial_identity"][name] for name in ("all_initial_ply_sha256_equal", "all_initial_vertex_arrays_equal")):
        raise ValueError("Independent initial templates are not identical")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10.5, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold", "savefig.facecolor": "white"})
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for key, (centres, chord, curvature) in curves.items():
        style = {"color": COLORS[key], "linewidth": 2.2 if key == "reconstruction" else 1.5,
                 "label": LABELS[key], "alpha": 0.9}
        axes[0, 0].plot(z, centres[:, 0], **style)
        axes[0, 1].plot(z, centres[:, 1], **style)
        axes[1, 0].plot(z, chord, **style)
        axes[1, 1].plot(z, curvature, **style)
    for ax, baseline_value in zip(axes.ravel(), (0, 0, 1, 0)):
        ax.axhline(baseline_value, color="#222222", linewidth=1, linestyle="--", label="Initial template")
        ax.set_xlim(0, 61.5)
        ax.set_xlabel("Blade-root span z (m)")
        ax.grid(True, alpha=0.2)
    axes[0, 0].set_title("Centreline x offset")
    axes[0, 0].set_ylabel("Downstream / thickness-direction offset (m)")
    axes[0, 1].set_title("Centreline y offset")
    axes[0, 1].set_ylabel("Chordwise offset (m)")
    axes[1, 0].set_title("Chord scale relative to shared initial template")
    axes[1, 0].set_ylabel("Chord scale (ratio)")
    axes[1, 1].set_title("Common-grid centreline curvature proxy")
    axes[1, 1].set_ylabel("Numerical curvature proxy (1/m)")
    axes[1, 1].set_yscale("symlog", linthresh=0.005)
    axes[1, 1].set_ylim(0, max(values[2].max() for values in curves.values()) * 1.2)
    handles, legend_labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc="upper center", bbox_to_anchor=(0.5, 0.945), ncol=5, frameon=False)
    fig.suptitle("Shape-parameter comparison: identical independent initial mesh", fontsize=14, y=0.985)
    fig.text(0.05, 0.04,
             "B-spline offset fields remove control-knot kinks; sampled meshes remain piecewise planar.\n"
             "Curvature proxy uses a common 0.05125 m grid; v1 knot spikes are grid dependent. Smoothness does not establish physical accuracy.",
             fontsize=9.5, color="#444444", va="bottom")
    fig.tight_layout(rect=(0.02, 0.1, 0.98, 0.9))
    plot_path = OUTPUT / "shape_comparison.png"
    fig.savefig(plot_path, dpi=170)
    plt.close(fig)
    for key, root in INPUTS.items():
        if any(sha(root / name) != signature for name, signature in before_signatures[key].items()):
            raise RuntimeError("Frozen model files changed during plot generation")
    report["plot_path"] = str(plot_path.resolve())
    (OUTPUT / "shape_comparison.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"plot_path": str(plot_path), "same_initial_identity": report["same_initial_identity"],
                      "derivative_jumps": {key: value["max_first_derivative_jump"] for key, value in report["models"].items()}}, indent=2))


if __name__ == "__main__":
    main()
