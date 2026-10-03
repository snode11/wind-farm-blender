"""Independent P5 geometry scoring, permitted only after both models freeze.

Run from the repository root with PYTHONPATH=. and the wfrl-mac Python.
No fitter is imported. Every model, source and input signature is checked before
truth access. Existing scoring artifacts are never overwritten.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

_REPOSITORY = next(path for path in Path(__file__).resolve().parents
                   if (path / "pyproject.toml").is_file() and (path / "wfrl/nrel_reconstruction").is_dir())
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))
from wfrl.nrel_reconstruction.artifact_paths import frozen_path, relocated_path, repository_root

REPO = repository_root(__file__)

BRANCHES = ("original_raw", "revised_boundary")
KINDS = ("initial", "prior_only", "final")
MESH_FILES = {"initial": "initial_template.ply", "prior_only": "prior_only.ply", "final": "T1_B1.ply"}
EXPECTED_CONFIG = {"span_bounds_m": [0, 61.5], "section_positions_m": [9.225, 30.75, 52.275],
                   "sample_count": 8192, "regional_sample_count": 2048, "seed": 20261002}
DIRECTIONS = ("reconstruction_to_truth", "truth_to_reconstruction")
REGIONS = ("root", "middle", "tip")
BASE = Path("outputs/nrel-video-single-blade")
HISTORY = BASE / "20261002-second"
TRUTH_ROOT = BASE / "20261002-first/formal/evaluation-only/truth-at-tref"
SCORER_SNAPSHOT = BASE / "20261003-p4-greville/implementation/wfrl/nrel_reconstruction/evaluate.py"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def verify(mapping, base):
    signatures = {}
    for name, expected in mapping.items():
        digest = expected["sha256"] if isinstance(expected, dict) else expected
        path = frozen_path(base / name, digest, root=REPO)
        actual = sha(path)
        if actual != digest or (isinstance(expected, dict) and "bytes" in expected and path.stat().st_size != expected["bytes"]):
            raise RuntimeError(f"Signature mismatch: {path}")
        signatures[str(path)] = actual
    return signatures


def gate(experiment):
    fit = experiment / "fit"
    freeze_path = fit / "model_freeze.json"
    if not freeze_path.is_file():
        raise RuntimeError("Both-branch model freeze absent; truth access prohibited")
    freeze = load(freeze_path)
    if (freeze["status"] != "ALL_BRANCHES_FROZEN_BEFORE_NONBLIND_AND_TRUTH_EVALUATION"
            or freeze["inputs_and_source_unchanged"] is not True or freeze["further_tuning"] is not False):
        raise RuntimeError("Model freeze contract failed; truth access prohibited")
    for branch in BRANCHES:
        for name in ("initial_template.ply", "prior_only.ply", "T1_B1.ply", "model_state.json",
                     "initial_template.json", "prior_only.json", "T1_B1.json", "optimization.json", "surface_evidence.json"):
            if f"{branch}/{name}" not in freeze["sha256"]:
                raise RuntimeError(f"Missing frozen branch artifact: {branch}/{name}")
    signatures = verify(freeze["sha256"], fit)
    if freeze.get("cross_residuals_after_geometry_freeze") is not True:
        raise RuntimeError("Cross-target residuals were not declared post-geometry-freeze")
    for name in ("geometry_freeze.json", "cross_observation_residuals.json", "frozen_config.json"):
        if name not in freeze["sha256"]:
            raise RuntimeError(f"Required control evidence is not frozen: {name}")
    geometry_freeze = load(fit / "geometry_freeze.json")
    if (geometry_freeze["further_tuning"] is not False
            or geometry_freeze["utc"] != freeze["geometry_freeze_utc"]
            or datetime.fromisoformat(geometry_freeze["utc"]) > datetime.fromisoformat(freeze["utc"])):
        raise RuntimeError("Geometry freeze contract differs from final model freeze")
    signatures.update(verify(geometry_freeze["sha256"], fit))
    for name in ("implementation_at_launch.json", "fit_entry_inputs.json"):
        path = fit / name
        if name not in freeze["sha256"]:
            raise RuntimeError(f"Signature manifest itself is not frozen: {name}")
        signatures.update(verify(load(path), Path("/")))
    signatures[str(freeze_path.resolve())] = sha(freeze_path)
    return freeze, signatures


def baseline_identity_before_truth(experiment, root, historical):
    signatures, identity = {}, {}
    for kind, filename in MESH_FILES.items():
        old = root / HISTORY / "reconstruction" / filename
        new = experiment / "fit/original_raw" / filename
        signatures.update(verify({str(old.relative_to(root)): historical[str(old.relative_to(root))]}, root))
        old = relocated_path(old, root=root)
        identical = new.read_bytes() == old.read_bytes()
        identity[kind] = {"historical_path": str(old.relative_to(root)), "byte_identical": identical,
                          "sha256": sha(new), "bytes": new.stat().st_size}
        if not identical:
            raise RuntimeError(f"original_raw historical mesh differs before truth access: {kind}")
    return signatures, identity


def cross_observation_summary(path):
    report = load(path)
    if set(report["results"]) != set(BRANCHES):
        raise RuntimeError("Cross-target residual observation versions differ")
    targets = {}
    expected_images = {(camera, frame) for camera in ("C1", "C2", "C3") for frame in (42, 43)}
    for target, models in report["results"].items():
        if set(models) != set((*BRANCHES, "shared_prior_only", "initial_template")):
            raise RuntimeError(f"Cross-target residual model set differs: {target}")
        targets[target] = {}
        for model, rows in models.items():
            if len(rows) != 6 or {(row["camera_id"], row["frame_id"]) for row in rows} != expected_images:
                raise RuntimeError(f"Cross-target residual fitting images differ: {target}/{model}")
            if any(row["split"] != "fit" or row["observation_version"] != target
                   or row["fitted_model"] != model or row["image_loss"] is None for row in rows):
                raise RuntimeError(f"Cross-target residual labels/availability differ: {target}/{model}")
            term_names = set.intersection(*(set(row["terms"]) for row in rows))
            terms = {name: sum(row["terms"][name] for row in rows) / len(rows) for name in sorted(term_names)
                     if all(isinstance(row["terms"][name], (float, int)) for row in rows)}
            targets[target][model] = {"image_loss_mean": sum(row["image_loss"] for row in rows) / len(rows),
                                     "term_means": terms, "fitting_images": len(rows)}
    differences = {}
    for target, models in targets.items():
        a, b = models["original_raw"], models["revised_boundary"]
        differences[target] = {"image_loss_mean_delta": b["image_loss_mean"] - a["image_loss_mean"],
                               "term_mean_deltas": {name: b["term_means"][name] - a["term_means"][name]
                                                    for name in a["term_means"] if name in b["term_means"]}}
    return {"path": str(path), "comparison_contract": report["comparison_contract"], "targets": targets,
            "revised_boundary_minus_original_raw_under_same_target": differences,
            "units_note": "Image loss is dimensionless; term units follow original residual term names, including pixels."}


def score_branch(arguments):
    # The parent has completed the gate before any child imports the scorer.
    from wfrl.nrel_reconstruction import evaluate
    branch, fit, truth, metadata, config, output, expected_scorer, scorer_digest = arguments
    if Path(evaluate.__file__).resolve() != expected_scorer.resolve() or sha(evaluate.__file__) != scorer_digest:
        raise RuntimeError("Imported scorer differs from the verified unchanged implementation")
    start = time.perf_counter()
    score = evaluate.evaluate_run(fit / branch, truth, metadata, scoring_config=config, output_dir=output / branch)
    return branch, score, time.perf_counter() - start


def checked_sections(score):
    rows = score["sections"]
    positions = [row["truth"]["z_m"] for row in rows]
    if positions != EXPECTED_CONFIG["section_positions_m"]:
        raise RuntimeError(f"Fixed scoring sections differ: {positions}")
    if any(row["reconstruction"]["z_m"] != row["truth"]["z_m"] for row in rows):
        raise RuntimeError("Reconstruction and truth sections do not share fixed z")
    return rows


def model_summary(score):
    rows = []
    for row in checked_sections(score):
        rec, truth = row["reconstruction"], row["truth"]
        rows.append({"z_m": truth["z_m"],
                     "long_side_abs_error_m": abs(rec["chord_length_m"] - truth["chord_length_m"]),
                     "short_side_abs_error_m": abs(rec["body_thickness_m"] - truth["body_thickness_m"]),
                     "reconstruction_long_side_m": rec["chord_length_m"], "truth_long_side_m": truth["chord_length_m"],
                     "reconstruction_short_side_m": rec["body_thickness_m"], "truth_short_side_m": truth["body_thickness_m"],
                     "section_closed": rec["section_closed"], "self_intersections": rec["self_intersections"]})
    return {"global": score["global"], "regions": score["regions"], "sections": rows,
            "surface": score["surface"], "sampling": score["sampling"]}


def pair_comparison(a, b):
    """B minus A: negative means smaller error. Assert section alignment first."""
    positions_a, positions_b = [x["z_m"] for x in a["sections"]], [x["z_m"] for x in b["sections"]]
    if positions_a != positions_b or positions_a != EXPECTED_CONFIG["section_positions_m"]:
        raise RuntimeError("Cannot compare unmatched fixed sections")
    regional = {region: {direction: {metric: b["regions"][region][direction][metric] - a["regions"][region][direction][metric]
                                      for metric in ("mean_m", "p95_m")} for direction in DIRECTIONS} for region in REGIONS}
    return {"global_delta_m": {direction: {metric: b["global"][direction][metric] - a["global"][direction][metric]
                                           for metric in ("mean_m", "p95_m")} for direction in DIRECTIONS},
            "regional_delta_m": regional,
            "section_abs_error_delta_m": [{"z_m": sb["z_m"], **{key: sb[key] - sa[key] for key in
                                           ("long_side_abs_error_m", "short_side_abs_error_m")}}
                                          for sa, sb in zip(a["sections"], b["sections"])]}


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(experiment, root, workers=2):
    output = experiment / "evaluation-only"
    # Leave an interrupted scoring run visible; never quietly rescore its files.
    if (output / "evaluation_record.json").exists() or (output / "evaluator_at_launch.json").exists() or any(
            (output / branch / "scores.json").exists() for branch in BRANCHES):
        raise FileExistsError("Refuse to overwrite prior evaluation artifacts")
    freeze, signatures = gate(experiment)
    gate_utc = datetime.now(timezone.utc).isoformat()
    protocol_path, historical_path = experiment / "protocol.json", experiment / "historical_before.json"
    protocol, historical = load(protocol_path), load(historical_path)["files"]
    if protocol["branches"] != list(BRANCHES):
        raise RuntimeError("Branch protocol mismatch")
    config_path = experiment / "scoring_config.json"
    if load(config_path) != EXPECTED_CONFIG:
        raise RuntimeError("Scoring config differs from frozen original parameters")
    signatures.update({str(path.resolve()): sha(path) for path in (protocol_path, historical_path, config_path, Path(__file__).resolve())})
    original_signatures, original_identity = baseline_identity_before_truth(experiment, root, historical)
    signatures.update(original_signatures)
    scorer_path = root / "wfrl/nrel_reconstruction/evaluate.py"
    scorer_snapshot = relocated_path(root / SCORER_SNAPSHOT, root=root)
    signatures.update(verify({str(SCORER_SNAPSHOT): historical[str(SCORER_SNAPSHOT)]}, root))
    if scorer_path.read_bytes() != scorer_snapshot.read_bytes():
        raise RuntimeError("Geometry scorer changed relative to the frozen P4 implementation")
    scorer_digest = sha(scorer_path)
    if str(scorer_path.resolve()) not in signatures:
        raise RuntimeError("Scorer was not included in the fit implementation signature gate")
    cross_targets = cross_observation_summary(experiment / "fit/cross_observation_residuals.json")
    frozen_config = load(experiment / "fit/frozen_config.json")
    if sum(frozen_config["iterations_per_stage"]) != 100:
        raise RuntimeError("Frozen optimization budget differs from the declared 100 steps")
    optimizations = {branch: load(experiment / "fit" / branch / "optimization.json") for branch in BRANCHES}
    for branch, optimization in optimizations.items():
        if optimization["config"] != frozen_config or len(optimization["video_history"]) != 100 or len(optimization["prior_only_history"]) != 100:
            raise RuntimeError(f"Frozen branch optimizer config/budget differs: {branch}")
    if optimizations["original_raw"]["prior_only_history"] != optimizations["revised_boundary"]["prior_only_history"]:
        raise RuntimeError("Branches do not preserve the shared prior-only trajectory")
    for filename in ("initial_template.ply", "prior_only.ply"):
        if (experiment / "fit/original_raw" / filename).read_bytes() != (experiment / "fit/revised_boundary" / filename).read_bytes():
            raise RuntimeError(f"Branch initial/shared prior meshes differ: {filename}")
    nonblind_path = experiment / "fit/nonblind_residuals.json"
    nonblind = None
    if nonblind_path.exists():
        nonblind = load(nonblind_path)
        if nonblind["status"] != "NONBLIND_DIAGNOSTIC_ONLY" or nonblind["independent_validation"] is not False:
            raise RuntimeError("Nonblind diagnostics do not preserve their declared evidence status")
        signatures[str(nonblind_path.resolve())] = sha(nonblind_path)
    output.mkdir(parents=True, exist_ok=True)
    write(output / "evaluator_at_launch.json", {"utc": gate_utc, "truth_read": False,
          "evaluator_sha256": sha(__file__), "scoring_config_sha256": sha(config_path),
          "unchanged_scorer_sha256": scorer_digest, "verified_before_truth": signatures,
          "historical_verification_note": "Signature-map paths identify relocated artifacts or preserved byte-identical snapshots; snapshot matches do not establish current live source byte identity.",
          "original_raw_historical_mesh_identity_before_truth": original_identity})
    signatures[str((output / "evaluator_at_launch.json").resolve())] = sha(output / "evaluator_at_launch.json")

    # All frozen outputs/source/input signatures and historical mesh equality
    # have passed. This is the first read of evaluation truth or old scores.
    truth, metadata = root / TRUTH_ROOT / "T1_B1_truth.ply", root / TRUTH_ROOT / "truth_manifest.json"
    old_score_path, old_config_path = root / HISTORY / "evaluation-only/reconstruction/scores.json", root / HISTORY / "scoring_config.json"
    signatures.update(verify({str(path.relative_to(root)): historical[str(path.relative_to(root))]
                              for path in (truth, metadata, old_score_path, old_config_path)}, root))
    truth, metadata, old_score_path, old_config_path = [relocated_path(path, root=root)
                                                       for path in (truth, metadata, old_score_path, old_config_path)]
    old_score = load(old_score_path)
    if old_score["scoring_config"] != EXPECTED_CONFIG or load(old_config_path) != EXPECTED_CONFIG:
        raise RuntimeError("Historical scoring configuration differs")
    from wfrl.nrel_reconstruction.evaluate import _metadata_contract
    truth_meta, contracts = load(metadata), {}
    for branch in BRANCHES:
        contracts[branch] = {}
        for filename in ("model_state.json", "initial_template.json", "prior_only.json", "T1_B1.json"):
            model_meta = load(experiment / "fit" / branch / filename)
            errors = _metadata_contract(model_meta.get("reference_state", model_meta), truth_meta)
            contracts[branch][filename] = errors
            if errors:
                raise RuntimeError(f"Metadata contract mismatch: {branch}/{filename}: {errors}")
    write(output / "evaluation_record.json", {"status": "SCORING_STARTED_AFTER_VERIFIED_FREEZE",
          "gate_passed_utc": gate_utc, "model_freeze_utc": freeze["utc"], "protected_signatures_before": signatures,
          "metadata_contract_errors": contracts, "scoring_config": EXPECTED_CONFIG,
          "parallel_evaluation_workers": workers, "fitting_parameters_changed": False,
          "original_raw_historical_mesh_identity_before_truth": original_identity})
    print(json.dumps({"gate": "PASSED", "protected_files": len(signatures),
                      "metadata_contracts": "8/8 PASS", "truth_access": "after gate"}), flush=True)
    args = [(branch, experiment / "fit", truth, metadata, config_path, output, scorer_path, scorer_digest) for branch in BRANCHES]
    results, elapsed = {}, {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for branch, result, seconds in executor.map(score_branch, args):
            if result["status"] != "SCORED" or result["metadata_errors"]:
                raise RuntimeError(f"Branch did not score cleanly: {branch}")
            if result["comparison_provenance"]["status"] != "DECLARED_SAME_CONFIGURATION":
                raise RuntimeError(f"Missing same-configuration no-image control: {branch}")
            results[branch], elapsed[branch] = result, seconds
            print(json.dumps({"branch": branch, "status": result["status"],
                              "image_contribution": result["comparison"]["image_contribution"], "seconds": seconds}), flush=True)
    historical_models = {kind: model_summary(old_score["models"][kind]) for kind in KINDS}
    equality = {kind: results["original_raw"]["models"][kind] == old_score["models"][kind] for kind in KINDS}
    if not all(equality.values()):
        raise RuntimeError(f"Byte-identical original_raw historical meshes did not reproduce scoring metrics: {equality}")
    summary = {"status": "BOTH_FROZEN_BRANCHES_SCORED", "units": "m", "scoring_config": EXPECTED_CONFIG,
               "engineering_acceptance": "PENDING_USE_CASE_TARGETS", "truth_feedback_used": False,
               "section_convention": old_score["section_convention"], "branches": {},
               "historical_formal_v2": {"path": str(old_score_path.relative_to(root)), "models": historical_models,
                                        "image_contribution": old_score["comparison"]["image_contribution"]},
               "original_raw_historical_mesh_identity_before_truth": original_identity,
               "original_raw_historical_scores_exactly_equal": equality,
               "cross_observation_residuals": cross_targets,
               "nonblind_diagnostics": {"status": "NONBLIND_DIAGNOSTIC_ONLY", "independent_validation": False,
                                       "path": str(nonblind_path), "results": nonblind["results"]} if nonblind else None}
    global_rows, regional_rows, section_rows = [], [], []
    for branch in BRANCHES:
        result = results[branch]
        optimization = optimizations[branch]
        models = {kind: model_summary(result["models"][kind]) for kind in KINDS}
        own_objective = optimization["final_objective_under_training_observation_version"]
        summary["branches"][branch] = {"models": models, "comparison": result["comparison"],
              "image_contribution": result["comparison"]["image_contribution"],
              "comparison_provenance": result["comparison_provenance"],
              "coverage_conclusion": result["surface_evidence"]["coverage_conclusion"],
              "surface_evidence": result["surface_evidence"],
              "final_640_total_objective_on_own_target": own_objective["total_objective"],
              "final_fit_image_loss_on_own_target": own_objective["image_loss_mean"],
              "final_regularization_on_own_target": own_objective["regularization"],
              "optimizer_config": optimization["config"], "video_steps": len(optimization["video_history"]),
              "prior_only_steps": len(optimization["prior_only_history"]),
              "coordinate_branch": optimization["coordinate_branch"],
              "own_target_loss_interpretation": "Target observations differ between branches; own-target objective differences are not a common-objective improvement.",
              "scoring_wall_s": elapsed[branch],
              "final_minus_initial": pair_comparison(models["initial"], models["final"]),
              "final_minus_prior_only": pair_comparison(models["prior_only"], models["final"])}
        for kind, score in models.items():
            for direction in DIRECTIONS:
                global_rows.append({"branch": branch, "model": kind, "direction": direction,
                                    **{key: score["global"][direction][key] for key in ("mean_m", "p95_m", "standard_error_mean_m")}})
                for region in REGIONS:
                    regional_rows.append({"branch": branch, "model": kind, "region": region, "direction": direction,
                                          **{key: score["regions"][region][direction][key] for key in ("mean_m", "p95_m", "standard_error_mean_m")}})
            section_rows.extend({"branch": branch, "model": kind, **row} for row in score["sections"])
    finals = {branch: summary["branches"][branch]["models"]["final"] for branch in BRANCHES}
    summary["cross_branch_deltas"] = {
        "revised_boundary_minus_original_raw": pair_comparison(finals["original_raw"], finals["revised_boundary"]),
        "original_raw_minus_historical_formal_v2": pair_comparison(historical_models["final"], finals["original_raw"]),
        "revised_boundary_minus_historical_formal_v2": pair_comparison(historical_models["final"], finals["revised_boundary"])}
    summary["limitations"] = ["Two predeclared finite-budget runs; same blade model and optimizer, different observation targets.",
        "Own-target objective reductions do not establish improvement on a common observation target.",
        "Previously inspected frames remain nonblind diagnostics, never an independent validation set.",
        "Monte Carlo triangle-surface estimates are not certified metrology or use-case acceptance.",
        "Complete surface recovery, hidden surfaces, thickness and twist remain unestablished or prior-dependent."]
    write(output / "cross_branch_summary.json", summary)
    for name, rows in (("global_distance.csv", global_rows), ("regional_distance.csv", regional_rows), ("section_errors.csv", section_rows)):
        write_csv(output / name, rows)
    gate(experiment)
    after = verify(signatures, Path("/"))
    record = load(output / "evaluation_record.json")
    record.update({"status": "COMPLETE_SIGNATURES_UNCHANGED", "completed_utc": datetime.now(timezone.utc).isoformat(),
                   "protected_signatures_after": after, "signatures_unchanged": after == signatures,
                   "original_raw_historical_scores_exactly_equal": equality,
                   "summary_sha256": sha(output / "cross_branch_summary.json")})
    write(output / "evaluation_record.json", record)
    print(json.dumps({"status": record["status"], "summary": str(output / "cross_branch_summary.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be positive")
    run(Path(__file__).resolve().parent.parent, args.root.resolve(), args.workers)
