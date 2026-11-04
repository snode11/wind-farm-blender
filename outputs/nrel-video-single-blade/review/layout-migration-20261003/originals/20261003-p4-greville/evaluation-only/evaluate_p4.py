"""Independent post-freeze P4 scoring; never imports or changes a fitter.

Run from the repository root with PYTHONPATH=. and the wfrl-mac Python.
All model/code/input signatures pass before the first evaluation-only read.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import csv
import hashlib
import json
from pathlib import Path
import time


BRANCHES = ("original_raw", "physical_reference_center", "greville_normalized")
EXPECTED_CONFIG = {"span_bounds_m": [0, 61.5], "section_positions_m": [9.225, 30.75, 52.275],
                   "sample_count": 8192, "regional_sample_count": 2048, "seed": 20261002}
DIRECTIONS = ("reconstruction_to_truth", "truth_to_reconstruction")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def verify(mapping, base):
    result = {}
    for name, expected in mapping.items():
        path = (base / name).resolve()
        actual = sha(path)
        if actual != expected:
            raise RuntimeError(f"Signature mismatch: {path}")
        result[str(path)] = actual
    return result


def gate(experiment):
    fit = experiment / "fit"
    freeze_path = fit / "model_freeze.json"
    if not freeze_path.is_file():
        raise RuntimeError("All-branch model freeze is absent; truth access prohibited")
    freeze = load(freeze_path)
    if (freeze["status"] != "ALL_BRANCHES_FROZEN_BEFORE_NONBLIND_AND_TRUTH_EVALUATION"
            or freeze["inputs_and_source_unchanged"] is not True
            or freeze["further_tuning"] is not False):
        raise RuntimeError("Model freeze contract failed; truth access prohibited")
    for name in BRANCHES:
        for file in ("initial_template.ply", "prior_only.ply", "T1_B1.ply", "model_state.json", "optimization.json"):
            if f"{name}/{file}" not in freeze["sha256"]:
                raise RuntimeError(f"Missing frozen branch artifact: {name}/{file}")
    signatures = verify(freeze["sha256"], fit)
    for filename in ("implementation_at_launch.json", "fit_entry_inputs.json"):
        signatures.update(verify(load(fit / filename), Path("/")))
    signatures[str(freeze_path.resolve())] = sha(freeze_path)
    return freeze, signatures


def score_branch(arguments):
    # Imported only after main's verified gate and copied to a fresh process.
    from wfrl.nrel_reconstruction.evaluate import evaluate_run
    name, fit, truth, metadata, config, output = arguments
    started = time.perf_counter()
    result = evaluate_run(fit / name, truth, metadata, scoring_config=config, output_dir=output / name)
    return name, result, time.perf_counter() - started


def model_summary(score):
    sections = []
    for row in score["sections"]:
        rec, truth = row["reconstruction"], row["truth"]
        sections.append({"z_m": truth["z_m"],
                         "long_side_abs_error_m": abs(rec["chord_length_m"] - truth["chord_length_m"]),
                         "short_side_abs_error_m": abs(rec["body_thickness_m"] - truth["body_thickness_m"]),
                         "reconstruction_long_side_m": rec["chord_length_m"],
                         "truth_long_side_m": truth["chord_length_m"],
                         "reconstruction_short_side_m": rec["body_thickness_m"],
                         "truth_short_side_m": truth["body_thickness_m"],
                         "section_closed": rec["section_closed"],
                         "self_intersections": rec["self_intersections"]})
    return {"global": score["global"], "regions": score["regions"], "sections": sections}


def pair_comparison(a, b):
    """Report B minus A; negative means a smaller geometric error."""
    return {"global_delta_m": {direction: {metric: b["global"][direction][metric] - a["global"][direction][metric]
                                          for metric in ("mean_m", "p95_m")} for direction in DIRECTIONS},
            "section_abs_error_delta_m": [
                {"z_m": sb["z_m"], **{key: sb[key] - sa[key]
                  for key in ("long_side_abs_error_m", "short_side_abs_error_m")}}
                for sa, sb in zip(a["sections"], b["sections"])],
            "regional_mean_delta_m": {region: {direction: b["regions"][region][direction]["mean_m"] - a["regions"][region][direction]["mean_m"]
                                                for direction in DIRECTIONS} for region in ("root", "middle", "tip")}}


def plot_summary(summary, output):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    import numpy as np
    labels = ["Original raw", "Physical center", "Greville center"]
    colors = ["#56667A", "#D38A23", "#176F8A"]
    fig, axes = plt.subplots(2, 2, figsize=(11.8, 8.3), constrained_layout=True)
    for i, direction in enumerate(DIRECTIONS):
        for index, name in enumerate(BRANCHES):
            values = summary["branches"][name]["models"]["final"]["global"][direction]
            axes[0, i].bar(np.arange(2) + (index-1)*.24, [values["mean_m"], values["p95_m"]], .23,
                           color=colors[index], label=labels[index])
        axes[0, i].set_xticks([0, 1], ["Mean", "P95"])
        axes[0, i].set_ylabel("Surface distance (m); lower is better")
        axes[0, i].set_title(direction.replace("_", " ").capitalize())
        axes[0, i].legend(fontsize=8)
    for index, name in enumerate(BRANCHES):
        sections = summary["branches"][name]["models"]["final"]["sections"]
        for ax, key, title in ((axes[1, 0], "long_side_abs_error_m", "Fixed-section long-side absolute error"),
                               (axes[1, 1], "short_side_abs_error_m", "Fixed-section short-side absolute error")):
            ax.plot([s["z_m"] for s in sections], [s[key] for s in sections], marker="o", color=colors[index], label=labels[index])
            ax.set_title(title)
            ax.set_xlabel("Fixed section z (m)")
            ax.set_ylabel("Absolute error (m); lower is better")
            ax.set_xticks([9.225, 30.75, 52.275])
    for ax in axes.flat:
        ax.grid(axis="y", alpha=.2)
        ax.set_axisbelow(True)
    fig.suptitle("P4 frozen models: unchanged geometry scoring\nSame image data and finite objective budget; no truth feedback", fontsize=14)
    fig.savefig(output / "cross_branch_geometry.png", dpi=180)
    fig.savefig(output / "cross_branch_geometry.pdf")
    plt.close(fig)


def run(experiment, root, workers=3):
    output = experiment / "evaluation-only"
    if (output / "evaluation_record.json").exists() or any((output / branch / "scores.json").exists() for branch in BRANCHES):
        raise FileExistsError("Refuse to overwrite prior evaluation artifacts")
    freeze, signatures = gate(experiment)
    gate_utc = datetime.now(timezone.utc).isoformat()
    protocol, addendum = load(experiment / "protocol.json"), load(experiment / "protocol_addendum.json")
    if addendum["branches"] != list(BRANCHES):
        raise RuntimeError("Branch protocol mismatch")
    config_path = experiment / "scoring_config.json"
    if load(config_path) != EXPECTED_CONFIG:
        raise RuntimeError("Scoring config differs from fixed original parameters")

    # This line is reached only after every frozen model, source and fitting
    # input passed its hash check. No earlier operation reads evaluation truth.
    truth_root = root / "outputs/nrel-video-single-blade/20261002-first/formal/evaluation-only/truth-at-tref"
    truth, metadata = truth_root / "T1_B1_truth.ply", truth_root / "truth_manifest.json"
    history_root = root / "outputs/nrel-video-single-blade/20261002-second"
    history_path = history_root / "evaluation-only/reconstruction/scores.json"
    protected = [truth, metadata, history_path, history_root / "scoring_config.json"]
    signatures.update(verify({str(p): protocol["historical_inputs"][str(p.relative_to(root))]["sha256"] for p in protected}, Path("/")))
    signatures[str(config_path)] = sha(config_path)
    signatures[str(Path(__file__).resolve())] = sha(__file__)
    historical = load(history_path)
    if historical["scoring_config"] != EXPECTED_CONFIG or load(history_root / "scoring_config.json") != EXPECTED_CONFIG:
        raise RuntimeError("Historical scoring configuration mismatch")
    from wfrl.nrel_reconstruction.evaluate import _metadata_contract
    truth_meta = load(metadata)
    contracts = {}
    for branch in BRANCHES:
        contracts[branch] = {}
        for filename in ("model_state.json", "initial_template.json", "prior_only.json", "T1_B1.json"):
            model_meta = load(experiment / "fit" / branch / filename)
            errors = _metadata_contract(model_meta.get("reference_state", model_meta), truth_meta)
            contracts[branch][filename] = errors
            if errors:
                raise RuntimeError(f"Metadata contract mismatch: {branch}/{filename}: {errors}")
    write(output / "evaluation_record.json", {"status": "SCORING_STARTED_AFTER_VERIFIED_FREEZE", "gate_passed_utc": gate_utc,
            "model_freeze_utc": freeze["utc"], "protected_signatures_before": signatures,
            "metadata_contract_errors": contracts, "scoring_config": EXPECTED_CONFIG,
            "parallel_evaluation_workers": workers, "fitting_parameters_changed": False})
    print(json.dumps({"gate": "PASSED", "protected_files": len(signatures), "metadata_contracts": "12/12 PASS", "truth_access": "after gate"}), flush=True)
    args = [(branch, experiment / "fit", truth, metadata, config_path, output) for branch in BRANCHES]
    results, elapsed = {}, {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for branch, result, seconds in executor.map(score_branch, args):
            if result["status"] != "SCORED" or result["metadata_errors"]:
                raise RuntimeError(f"Branch did not score cleanly: {branch}")
            results[branch], elapsed[branch] = result, seconds
            print(json.dumps({"branch": branch, "status": result["status"], "image_contribution": result["comparison"]["image_contribution"], "seconds": seconds}), flush=True)
    summary = {"status": "ALL_THREE_FROZEN_BRANCHES_SCORED", "units": "m", "scoring_config": EXPECTED_CONFIG,
               "engineering_acceptance": "PENDING_USE_CASE_TARGETS", "truth_feedback_used": False,
               "section_convention": historical["section_convention"], "branches": {},
               "historical_formal_v2": {"path": str(history_path.relative_to(root)), "models": {kind: model_summary(value) for kind, value in historical["models"].items()}, "image_contribution": historical["comparison"]["image_contribution"]}}
    global_rows, section_rows = [], []
    for branch in BRANCHES:
        result = results[branch]
        optimization = load(experiment / "fit" / branch / "optimization.json")
        summary["branches"][branch] = {"models": {kind: model_summary(value) for kind, value in result["models"].items()},
                                     "image_contribution": result["comparison"]["image_contribution"],
                                     "comparison_provenance": result["comparison_provenance"]["status"],
                                     "coverage_conclusion": result["surface_evidence"]["coverage_conclusion"],
                                     "final_640_total_objective": optimization["final_640_total_objective"],
                                     "final_fit_image_loss": sum(r["image_loss"] for r in optimization["fit_residuals"]["T1_B1"]) / 6,
                                     "scoring_wall_s": elapsed[branch]}
        for kind, score in summary["branches"][branch]["models"].items():
            for direction in DIRECTIONS:
                global_rows.append({"branch": branch, "model": kind, "direction": direction,
                                    **{key: score["global"][direction][key] for key in ("mean_m", "p95_m", "standard_error_mean_m")}})
            section_rows.extend({"branch": branch, "model": kind, **row} for row in score["sections"])
    finals = {name: summary["branches"][name]["models"]["final"] for name in BRANCHES}
    summary["cross_branch_deltas"] = {
        "primary_greville_minus_physical_center": pair_comparison(finals["physical_reference_center"], finals["greville_normalized"]),
        "greville_minus_original_raw": pair_comparison(finals["original_raw"], finals["greville_normalized"]),
        "physical_center_minus_original_raw": pair_comparison(finals["original_raw"], finals["physical_reference_center"]),
        "original_raw_minus_historical_formal_v2": pair_comparison(summary["historical_formal_v2"]["models"]["final"], finals["original_raw"])}
    summary["limitations"] = ["Three predeclared finite-budget coordinate runs; no new observations or physical information.",
                              "Monte Carlo source-area-weighted triangle-surface statistics, not certified metrology.",
                              "Image objective reduction alone does not establish geometric improvement.",
                              "No complete-surface or use-case accuracy acceptance is implied."]
    write(output / "cross_branch_summary.json", summary)
    for filename, rows in (("global_distance.csv", global_rows), ("section_errors.csv", section_rows)):
        with (output / filename).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    plot_summary(summary, output)
    gate(experiment)
    after = verify(signatures, Path("/"))
    record = load(output / "evaluation_record.json")
    record.update({"status": "COMPLETE_SIGNATURES_UNCHANGED", "completed_utc": datetime.now(timezone.utc).isoformat(),
                   "protected_signatures_after": after, "signatures_unchanged": after == signatures,
                   "summary_sha256": sha(output / "cross_branch_summary.json")})
    write(output / "evaluation_record.json", record)
    print(json.dumps({"status": record["status"], "summary": str(output / "cross_branch_summary.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=3)
    arguments = parser.parse_args()
    run(Path(__file__).resolve().parent.parent, arguments.root.resolve(), arguments.workers)
