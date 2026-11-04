"""Check preserved inputs, run the bounded P3 regression suite, and record hashes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

# Locate the live repository without assuming a fixed stage nesting depth.
_REPOSITORY = next(path for path in Path(__file__).resolve().parents
                   if (path / "pyproject.toml").is_file() and (path / "wfrl/nrel_reconstruction").is_dir())
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))
from wfrl.nrel_reconstruction.artifact_paths import frozen_path, repository_root

HERE = Path(__file__).resolve().parent
REPO = repository_root(__file__)
VERIFIED_FROZEN_PATHS = {}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def compare_hashes(mapping):
    changed = []
    for name, expected in mapping.items():
        try:
            resolved = frozen_path(name, expected, root=REPO)
            VERIFIED_FROZEN_PATHS[name] = str(resolved)
        except RuntimeError:
            changed.append(name)
    return changed


def main():
    protocol = json.loads((HERE / "protocol.json").read_text())
    expected = {name: item["sha256"] for name, item in protocol["inputs"].items()}
    assert not compare_hashes(expected), "Frozen baseline input changed before validation"
    modules = ["center_scale_audit", "center_scale_family_audit", "root_rgb_audit"]
    new_files = [f"wfrl/nrel_reconstruction/{name}.py" for name in modules]
    new_files += [f"tests/test_nrel_{name}.py" for name in modules]
    before = {name: digest(REPO / name) for name in new_files}
    tests = [f"tests/test_nrel_{name}.py" for name in modules]
    tests += ["tests/test_nrel_parameter_audit.py", "tests/test_nrel_contour.py", "tests/test_nrel_model_v2.py"]
    command = [sys.executable, "-m", "pytest", "-q", *tests]
    run = subprocess.run(command, cwd=REPO, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (HERE / "tests.txt").write_text("Command: " + " ".join(command) + "\n\n" + run.stdout)
    print(run.stdout)
    assert run.returncode == 0, "P3 regression suite failed"
    after = {name: digest(REPO / name) for name in new_files}
    assert before == after, "P3 implementation changed during validation"
    assert not compare_hashes(expected), "Frozen baseline input changed after validation"
    geometry_protocol = json.loads((HERE / "geometry/frozen_protocol.json").read_text())
    geometry = json.loads((HERE / "geometry/center_scale_audit.json").read_text())
    family = json.loads((HERE / "family/verified/family_audit.json").read_text())
    rgb = json.loads((HERE / "rgb/root_rgb_audit.json").read_text())
    assert not compare_hashes(geometry_protocol["input_and_source_sha256"])
    assert digest(HERE / "geometry/frozen_protocol.json") == geometry["protocol_sha256"]
    assert not compare_hashes({family["run_record"]["source_path"]: family["run_record"]["source_sha256"]})
    assert not compare_hashes(rgb["source_sha256"])
    assert not compare_hashes(rgb["input_sha256"])
    result = {
        "schema": "nrel-p3-delivery-validation.v1",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "status": "P3_CONTROLLED_DIAGNOSTICS_COMPLETE_GEOMETRY_ACCEPTANCE_PENDING",
        "protocol_sha256": digest(HERE / "protocol.json"),
        "preserved_input_count": len(expected),
        "preserved_input_mismatch_count": 0,
        "tests": {"command": command, "exit_code": run.returncode,
                  "summary": run.stdout.strip().splitlines()[-1]},
        "new_source_and_test_sha256_before_tests": before,
        "new_source_and_test_sha256_after_tests": after,
        "source_and_frozen_protocol_checks_passed": True,
        "historical_verification_sources": VERIFIED_FROZEN_PATHS,
        "historical_verification_note": "Frozen signatures may resolve to preserved originals or implementation snapshots; this does not establish byte identity of current live source.",
        "geometry_actual_configurations": geometry["actual_image_configurations"],
        "geometry_production_baseline_checks": len(geometry["production_loss_parity"]),
        "family_cases": len(family["cases"]),
        "rgb_frames_matching_annotation": sum(x["matches_annotation"] for x in rgb["rgb_frames"]),
        "historical_backend_crosschecks": len(rgb["historical_backend"]),
        "new_fit_executed": False,
        "new_capture_or_encoding_executed": False,
        "scoring_truth_read_by_P3_algorithms": False,
        "baseline_model_or_masks_changed": False,
        "figure_and_rgb_visual_review": "Root reviewed summary figure and selected native crop/overlay; RGB agent reviewed all selected frame evidence.",
        "independent_review_record": "independent_review.md",
    }
    (HERE / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"inputs_preserved": len(expected), "status": result["status"]}))


if __name__ == "__main__":
    main()
