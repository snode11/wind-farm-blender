#!/usr/bin/env python3
"""DY0: freeze and extract an explicitly permitted ideal rigid rotor plane.

This adapter never imports replay/geometry readers and never reads flexible
states or tip truth. The estimator receives only the signed rigid_plane.json.
The source JSON containers contain extra fields; only the policy's rigid-field
projection is used. Filesystem isolation cannot provide field-level isolation.
"""
from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from datetime import datetime, timezone

import numpy as np


REPOSITORY = Path(__file__).resolve().parents[1]
RUN = Path("outputs/nrel-video-dynamic/runs/20261004-integrated/inputs/baseline/plane")
FIRST = Path("outputs/nrel-video-single-blade/stages/01-first-run")
CAPTURE = FIRST / "internal-capture-formal/capture_20261002_213817_baf6978517"
SOURCES = {
    "dataset": FIRST / "formal/algorithm-input/dataset.json",
    "rigid_root": FIRST / "formal/algorithm-input/motion_observations.csv",
    "capture_rigid": CAPTURE / "frames.jsonl",
    "static_tilt": Path("blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json"),
}
SOURCE_FIELDS = {
    "dataset": ["schema", "target", "sampling", "files.motion_observations.csv"],
    "rigid_root": ["frame_id", "sim_time_s", "T_world_from_blade_root", "source", "units"],
    "capture_rigid": ["nacelle_pose", "sample_index", "time_s", "camera_id"],
    "static_tilt": ["scalars.ShftTilt"],
}
STATIC_TILT_SHA256 = "28c0753a125a53400c45e4d59b7ba2af91fba2c296adf0ba7a5ad4923d0a26b5"
SAMPLING = {"start_s": 120, "end_s_exclusive": 125, "fps": 20,
            "frame_count_per_camera": 100, "simulation_time": "start+(n+.5)/fps", "pts": "n/fps"}
PROBES = [CAPTURE / "motion_internal.jsonl",
          Path("blender_frontend/wfrl_blender/assets/mappo/geometry.npz"),
          FIRST / "formal/evaluation-only/scores.json"]
TOLERANCES = {"rigid": 1e-5, "three_camera_pose": 1e-7, "time_s": 1e-8,
              "unit_normal": 1e-10, "normal_adjacent_dot_min": 0.99}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def rigid_matrix(value, label):
    matrix = np.asarray(value, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError(f"Invalid finite 4x4 rigid matrix: {label}")
    rotation = matrix[:3, :3]
    if (not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-10, rtol=0)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=TOLERANCES["rigid"], rtol=0)
            or not np.isclose(np.linalg.det(rotation), 1, atol=TOLERANCES["rigid"], rtol=0)):
        raise ValueError(f"Matrix is not a proper rigid transform: {label}")
    return matrix


def derive_plane(root, nacelle, shaft_tilt_deg, hub_radius_m=1.5):
    root, nacelle = rigid_matrix(root, "blade root"), rigid_matrix(nacelle, "nacelle")
    if not math.isfinite(shaft_tilt_deg) or abs(shaft_tilt_deg) > 30 or hub_radius_m != 1.5:
        raise ValueError("Invalid frozen static tilt or root-origin offset")
    angle = math.radians(-shaft_tilt_deg)
    axis_in_nacelle = np.array([math.cos(angle), 0.0, -math.sin(angle)])
    center = root[:3, 3] - root[:3, :3] @ np.array([0.0, 0.0, hub_radius_m])
    normal = nacelle[:3, :3] @ axis_in_nacelle
    normal /= np.linalg.norm(normal)
    if not np.isfinite(center).all() or not np.isfinite(normal).all():
        raise ValueError("Nonfinite plane")
    return center, normal


def validate_and_extract(frame_rows, motion_rows, shaft_tilt_deg):
    """Validate the complete three-camera Cartesian product before exporting."""
    n = SAMPLING["frame_count_per_camera"]
    frames, motions = {}, {}
    for raw in frame_rows:
        if not all(key in raw for key in SOURCE_FIELDS["capture_rigid"]):
            raise ValueError("Missing permitted capture fields")
        row = {key: raw[key] for key in SOURCE_FIELDS["capture_rigid"]}
        i, camera = row["sample_index"], row["camera_id"]
        if type(i) is not int or not 0 <= i < n or camera not in ("C1", "C2", "C3"):
            raise ValueError("Invalid frame identity")
        key = (camera, i)
        if key in frames:
            raise ValueError("Duplicate camera/frame record")
        expected = SAMPLING["start_s"] + (i + .5) / SAMPLING["fps"]
        if type(row["time_s"]) not in (int, float) or not math.isfinite(row["time_s"]) or abs(row["time_s"] - expected) > TOLERANCES["time_s"]:
            raise ValueError("Capture sampling-center mismatch")
        frames[key] = rigid_matrix(row["nacelle_pose"], str(key))
    for raw in motion_rows:
        if set(raw) != set(SOURCE_FIELDS["rigid_root"]):
            raise ValueError("Unexpected rigid-root fields")
        if str(raw["frame_id"]) != str(int(raw["frame_id"])):
            raise ValueError("Noninteger motion frame")
        i = int(raw["frame_id"])
        if not 0 <= i < n or i in motions:
            raise ValueError("Invalid or duplicate motion frame")
        expected = SAMPLING["start_s"] + (i + .5) / SAMPLING["fps"]
        actual = float(raw["sim_time_s"])
        if not math.isfinite(actual) or abs(actual - expected) > TOLERANCES["time_s"]:
            raise ValueError("Rigid-root sampling-center mismatch")
        if raw["units"] != "m" or raw["source"] != "simulation ideal rigid running observation; no deformation":
            raise ValueError("Rigid-root source or units mismatch")
        value = raw["T_world_from_blade_root"]
        motions[i] = rigid_matrix(json.loads(value) if isinstance(value, str) else value, f"motion {i}")
    if len(frames) != 3 * n or len(motions) != n:
        raise ValueError("Incomplete three-camera or rigid-root input")
    records, max_difference = [], 0.0
    for i in range(n):
        nacelle = frames[("C1", i)]
        for camera in ("C2", "C3"):
            difference = float(np.max(np.abs(frames[(camera, i)] - nacelle)))
            max_difference = max(max_difference, difference)
            if difference > TOLERANCES["three_camera_pose"]:
                raise ValueError(f"Three-camera nacelle pose mismatch at {i}")
        center, normal = derive_plane(motions[i], nacelle, shaft_tilt_deg)
        if abs(np.linalg.norm(normal) - 1) > TOLERANCES["unit_normal"]:
            raise ValueError("Nonunit normal")
        if records and float(np.dot(records[-1]["n_world_unit"], normal)) < TOLERANCES["normal_adjacent_dot_min"]:
            raise ValueError("Discontinuous normal/sign")
        records.append({"frame_id": i, "sim_time_s": SAMPLING["start_s"] + (i+.5)/SAMPLING["fps"],
                        "c_world_m": center.tolist(), "n_world_unit": normal.tolist()})
    return records, {"three_camera_pose_max_element_difference": max_difference,
                     "record_count": len(records), "proper_rigid_and_time_checks": "PASS"}


def policy_sources():
    return {key: {"path": str((REPOSITORY / path).resolve()),
                  "sha256": digest((REPOSITORY / path).read_bytes()), "fields": SOURCE_FIELDS[key]}
            for key, path in SOURCES.items()}


def freeze(output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a fresh extraction directory")
    output.mkdir(parents=True, exist_ok=True)
    sources = policy_sources()
    if sources["static_tilt"]["sha256"] != STATIC_TILT_SHA256:
        raise ValueError("Static tilt container differs from audited fixed archive")
    policy = {"schema": "nrel-dynamic-plane-extract-policy.v1", "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
              "run_id": "20261004-a01", "target": "NREL5MW/T1/B1", "sampling": SAMPLING,
              "sources": sources, "script_path": str(Path(__file__).resolve()),
              "script_sha256": digest(Path(__file__).read_bytes()), "tolerances": TOLERANCES,
              "hub_radius_m": 1.5,
              "root_offset_basis": "capture_nrel_three_camera.py:116 and rigid_motion_validation.json:9; root=T_world_from_blade_local @ Translation(0,0,1.5)",
              "normal_definition": "normalize(R_world_from_YawRoot @ Ry(-ShftTilt) @ [1,0,0]); rotor local +x",
              "center_definition": "root_translation - root_rotation @ [0,0,1.5]",
              "condition": "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE",
              "authorization_record": "Execution-stage A-route auxiliary-input choice; not a claim of item-by-item user confirmation",
              "field_isolation": "Adapter projects only enumerated fields; OS isolates files, not fields within source containers",
              "forbidden": ["motion_internal.jsonl", "flexible source arrays", "truth tip coordinates", "truth meshes", "truth masks"],
              "probe_paths": [str((REPOSITORY / path).resolve()) for path in PROBES],
              "downstream_file": "rigid_plane.json", "elastic_delta_reference": None}
    write_new(output / "extract_policy.json", policy)
    allowed = [str(Path(__file__).resolve()), *(entry["path"] for entry in sources.values())]
    quote = json.dumps
    exceptions = " ".join(f"(require-not (literal {quote(path)}))" for path in allowed)
    read_rule = f"(deny file-read-data (require-all (subpath {quote(str(REPOSITORY))}) {exceptions} (require-not (subpath {quote(str(output))}))))"
    write_rule = f"(deny file-write* (require-all (subpath {quote(str(REPOSITORY))}) (require-not (subpath {quote(str(output))}))))"
    (output / "extract_isolation.sb").write_text("(version 1)\n(allow default)\n(deny network*)\n" + read_rule + "\n" + write_rule + "\n")
    print(json.dumps({"status": "POLICY_FROZEN_BEFORE_EXTRACTION", "policy": str(output / "extract_policy.json")}))


def checked_sources(policy):
    if policy["schema"] != "nrel-dynamic-plane-extract-policy.v1" or policy["sampling"] != SAMPLING:
        raise ValueError("Policy schema/sampling mismatch")
    if policy["script_sha256"] != digest(Path(__file__).read_bytes()):
        raise ValueError("Extractor changed after policy freeze")
    if policy["hub_radius_m"] != 1.5 or policy["tolerances"] != TOLERANCES or set(policy["sources"]) != set(SOURCES):
        raise ValueError("Frozen policy contract mismatch")
    raw = {}
    for key, expected_path in SOURCES.items():
        entry = policy["sources"][key]
        path = Path(entry["path"])
        expected = (REPOSITORY / expected_path).resolve()
        if path != expected or path.resolve() != path or entry["fields"] != SOURCE_FIELDS[key]:
            raise ValueError(f"Source path/field whitelist mismatch: {key}")
        value = path.read_bytes()
        if digest(value) != entry["sha256"]:
            raise ValueError(f"Source SHA256 mismatch: {key}")
        raw[key] = value
    if digest(raw["static_tilt"]) != STATIC_TILT_SHA256:
        raise ValueError("Static tilt container mismatch")
    return raw


def denied_probes(policy):
    expected = [str((REPOSITORY / path).resolve()) for path in PROBES]
    if policy["probe_paths"] != expected:
        raise ValueError("Probe paths differ from frozen extractor")
    result = []
    for path in expected:
        try:
            # No read call: a failure is required even when a forbidden file exists.
            with open(path, "rb"):
                pass
        except OSError as error:
            if error.errno not in (errno.EPERM, errno.EACCES):
                raise RuntimeError(f"Probe not denied by OS policy: {path}: {error}") from error
            result.append({"path": path, "denied": True, "errno": error.errno, "bytes_read": 0})
        else:
            raise RuntimeError(f"Forbidden source was readable: {path}")
    return result


def extract(policy_path):
    policy_path = Path(policy_path).resolve()
    output = policy_path.parent
    if (output / "rigid_plane.json").exists():
        raise FileExistsError("Frozen plane already exists; no overwrite")
    policy_bytes = policy_path.read_bytes()
    policy = json.loads(policy_bytes)
    probes = denied_probes(policy)
    raw = checked_sources(policy)
    dataset = json.loads(raw["dataset"])
    if dataset["schema"] != "nrel-single-blade-video.v1" or dataset["target"] != "T1/B1" or dataset["sampling"] != SAMPLING:
        raise ValueError("Signed video dataset identity or sampling mismatch")
    if dataset["files"]["motion_observations.csv"] != digest(raw["rigid_root"]):
        raise ValueError("Root observations do not match video handoff signature")
    frames = (json.loads(line) for line in raw["capture_rigid"].splitlines())
    motions = csv.DictReader(io.StringIO(raw["rigid_root"].decode()))
    tilt = json.loads(raw["static_tilt"])["scalars"]["ShftTilt"]
    if type(tilt) not in (int, float):
        raise ValueError("Invalid static tilt type")
    records, checks = validate_and_extract(frames, motions, float(tilt))
    plane = {"schema": "nrel-dynamic-rigid-plane.v1", "run_id": policy["run_id"], "target": "T1/B1",
             "coordinate_frame": "world", "units": {"center": "m", "time": "s", "normal": "unit vector"},
             "condition": "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE", "normal_positive": "rotor local +x",
             "sampling": SAMPLING, "extract_policy_sha256": digest(policy_bytes),
             "extractor_sha256": policy["script_sha256"],
             "source_sha256": {key: item["sha256"] for key, item in policy["sources"].items()},
             "structural_tip_or_flexible_truth_included": False, "records": records}
    plane["payload_sha256"] = digest(canonical(plane))
    write_new(output / "rigid_plane.json", plane)
    plane_hash = digest((output / "rigid_plane.json").read_bytes())
    (output / "rigid_plane.json.sha256").write_text(plane_hash + "  rigid_plane.json\n")
    report = {"status": "RIGID_PLANE_EXTRACTED_VALIDATED", "checks": checks,
              "plane_sha256": plane_hash, "payload_sha256": plane["payload_sha256"],
              "extract_policy_sha256": digest(policy_bytes), "extractor_sha256": policy["script_sha256"],
              "source_hashes_verified": True, "probe_results": probes,
              "dynamic_truth_read": False, "physics_run": False,
              "limitations": ["Ideal plane assistance, not an estimated/independently measured hub axis",
                              "Source container identity frozen at this execution, not a retrospective capture signature",
                              "Only sampled-time planes exported; no interpolation contract implied"]}
    write_new(output / "extraction_report.json", report)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("freeze")
    prepare.add_argument("--output", type=Path, default=REPOSITORY / RUN)
    run = sub.add_parser("extract")
    run.add_argument("--policy", type=Path, default=REPOSITORY / RUN / "extract_policy.json")
    args = parser.parse_args()
    freeze(args.output) if args.command == "freeze" else extract(args.policy)


if __name__ == "__main__":
    main()
