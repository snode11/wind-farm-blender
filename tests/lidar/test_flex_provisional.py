"""Provisional replay contracts use small synthetic files without a solver."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from wfrl.lidar import flex_provisional
from wfrl.lidar.replay import ReplayReader


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_payload(path, data):
    _write_json(path / "data.json", data)
    manifest = _read_json(path / "manifest.json")
    manifest["data_sha256"] = hashlib.sha256((path / "data.json").read_bytes()).hexdigest()
    _write_json(path / "manifest.json", manifest)


@pytest.fixture
def physical_run(tmp_path):
    run = tmp_path / "synthetic-run"
    run.mkdir()
    source = run / "input.fst"
    source.write_bytes(b"synthetic input, never executed\n")
    surface = run / "surface.vtp"
    surface.write_bytes(b"synthetic surface, never parsed\n")
    config = dict(
        inputs=[dict(path=source.name, sha256=hashlib.sha256(source.read_bytes()).hexdigest())],
        startup_discard_s=0.0, duration_s=2.0,
        wind_profile={"kind": "synthetic"}, fps=10, span_refined=False,
    )
    motion = [dict(time_s=t, azimuth_deg=48.0 * t, yaw_deg=0.0,
                   pitch_deg=[2.0] * 3, rotor_speed_rpm=8.0)
              for t in (0.0, 2.0)]
    measurements = [
        dict(time_s=0.0, blade_id=2, expected=True, passage_id="p1", truth_m=6.0,
             beams={"B2": dict(valid=True, estimate_m=6.5, error_m=0.5, slant_range_m=40.0),
                    "B1": dict(valid=True, estimate_m=100.0, error_m=94.0)}),
        dict(time_s=1.0, blade_id=2, expected=True, passage_id="p2", truth_m=6.0,
             beams={"B2": dict(valid=False, estimate_m=None, error_m=None)}),
    ]
    data = dict(evaluation_only=False, collision_excluded=True, motion=motion,
                measurements=measurements, calibration={"kind": "synthetic"})
    _write_json(run / "run_config.json", config)
    _write_json(run / "exit_status.json", {"exit_code": 0})
    (run / "solver.log").write_text("FAST.Farm terminated normally.\n", encoding="utf-8")
    _write_json(run / "processed.json", data)
    _write_json(run / "surface_hashes.json", [
        dict(path=surface.name, sha256=hashlib.sha256(surface.read_bytes()).hexdigest()),
    ])
    return SimpleNamespace(path=run, config=config, data=data,
                           destination=tmp_path / "nested" / "preview")


@pytest.fixture
def preview(physical_run):
    flex_provisional.build(physical_run.path, physical_run.destination)
    return physical_run.destination


def test_checked_bytes_returns_exact_binary_content(tmp_path):
    path = tmp_path / "source.bin"
    content = b"\x00\xff\nsource"
    path.write_bytes(content)
    assert flex_provisional.checked_bytes(path, hashlib.sha256(content).hexdigest()) == content


def test_checked_bytes_rejects_digest_mismatch(tmp_path):
    path = tmp_path / "source.bin"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError) as caught:
        flex_provisional.checked_bytes(path, hashlib.sha256(b"original").hexdigest())
    assert str(caught.value) == f"Preview integrity mismatch: {path}"


def test_checked_bytes_propagates_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        flex_provisional.checked_bytes(tmp_path / "absent", "0" * 64)


def test_build_writes_review_manifest_hashes_and_b2_statistics(physical_run):
    run = physical_run.path
    before = {path.name: path.read_bytes() for path in run.iterdir()}
    reader = flex_provisional.build(str(run), str(physical_run.destination))
    assert isinstance(reader, ReplayReader)
    manifest = reader.package.manifest
    assert manifest["schema"] == "wfrl.flex-review.v1"
    assert manifest["status"] == "REVIEW_ONLY"
    assert manifest["source"] == "FAST.Farm"
    assert manifest["source_run"] == str(run.resolve())
    assert manifest["run_id"] == run.name
    assert manifest["turbine_id"] == "T1"
    assert manifest["segment"] == {"start_s": 0.0, "end_s": 2.0}
    assert manifest["calibration"] == physical_run.data["calibration"]
    assert manifest["wind_profile"] == physical_run.config["wind_profile"]
    assert manifest["replay"] == dict(threshold_m=7.0, hysteresis_m=0.1,
                                      max_hold_s=5.0, passage_margin=1.25)
    assert manifest["raw_sources"] == {
        name: hashlib.sha256(before[name]).hexdigest()
        for name in ("run_config.json", "processed.json", "exit_status.json",
                     "solver.log", "surface_hashes.json")
    }
    payload = (physical_run.destination / "data.json").read_bytes()
    assert manifest["data_sha256"] == hashlib.sha256(payload).hexdigest()
    assert reader.package.motion == physical_run.data["motion"]
    assert reader.package.measurements == physical_run.data["measurements"]
    assert reader.package.statistics["expected_samples"] == 2
    assert reader.package.statistics["valid_samples"] == 1
    assert reader.package.statistics["valid_ratio"] == 0.5
    assert reader.package.statistics["mae_m"] == 0.5
    assert reader.package.statistics["missed_passage_count"] == 1
    assert reader.at(1.0)["measurement"]["estimate_m"] == 6.5
    assert reader.at(1.0)["status"] == "near_threshold"
    assert {path.name: path.read_bytes() for path in run.iterdir()} == before
    assert sorted(path.name for path in physical_run.destination.iterdir()) == ["data.json", "manifest.json"]


@pytest.mark.parametrize("is_directory", [False, True])
def test_build_rejects_existing_destination_without_overwriting(tmp_path, is_directory):
    destination = tmp_path / "occupied"
    if is_directory:
        destination.mkdir()
        sentinel = destination / "keep.txt"
    else:
        sentinel = destination
    sentinel.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        flex_provisional.build(tmp_path / "missing-run", destination)
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize(("exit_code", "log"), [
    (1, "FAST.Farm terminated normally."), (0, "solver interrupted"),
])
def test_build_rejects_unfinished_run(physical_run, exit_code, log):
    _write_json(physical_run.path / "exit_status.json", {"exit_code": exit_code})
    (physical_run.path / "solver.log").write_text(log, encoding="utf-8")
    with pytest.raises(ValueError, match="Physical run did not finish"):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()


@pytest.mark.parametrize(("field", "value"), [
    ("evaluation_only", True), ("collision_excluded", False),
    ("collision_excluded", None), ("collision_excluded", 1),
])
def test_build_requires_full_collision_checked_postprocessing(physical_run, field, value):
    physical_run.data[field] = value
    _write_json(physical_run.path / "processed.json", physical_run.data)
    with pytest.raises(ValueError, match="Full physical postprocessing required"):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()


def test_build_rejects_changed_input(physical_run):
    (physical_run.path / "input.fst").write_bytes(b"changed input")
    with pytest.raises(ValueError, match="Preview integrity mismatch: .*input.fst"):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()


@pytest.mark.parametrize("name", [
    "run_config.json", "exit_status.json", "solver.log", "input.fst",
    "processed.json", "surface_hashes.json",
])
def test_build_propagates_missing_source_files(physical_run, name):
    (physical_run.path / name).unlink()
    with pytest.raises(FileNotFoundError):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()


def test_build_propagates_invalid_json(physical_run):
    (physical_run.path / "processed.json").write_text("{invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()


def test_build_nonfinite_payload_leaves_destination_available_for_retry(physical_run):
    physical_run.data["motion"][0]["yaw_deg"] = float("inf")
    _write_json(physical_run.path / "processed.json", physical_run.data)
    with pytest.raises(ValueError, match="Out of range float values"):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()
    assert not physical_run.destination.parent.exists()
    physical_run.data["motion"][0]["yaw_deg"] = 0.0
    _write_json(physical_run.path / "processed.json", physical_run.data)
    reader = flex_provisional.build(physical_run.path, physical_run.destination)
    assert reader.package.motion == physical_run.data["motion"]
    assert isinstance(flex_provisional.load(physical_run.destination), ReplayReader)


def test_build_nonfinite_manifest_leaves_destination_available_for_retry(physical_run):
    physical_run.config["wind_profile"]["speed_mps"] = float("nan")
    _write_json(physical_run.path / "run_config.json", physical_run.config)
    with pytest.raises(ValueError, match="Out of range float values"):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()
    assert not physical_run.destination.parent.exists()
    physical_run.config["wind_profile"]["speed_mps"] = 8.0
    _write_json(physical_run.path / "run_config.json", physical_run.config)
    reader = flex_provisional.build(physical_run.path, physical_run.destination)
    assert reader.package.manifest["wind_profile"]["speed_mps"] == 8.0


@pytest.mark.parametrize("times", [[0.0, 0.0, 2.0], [0.0, 1.0, 0.5, 2.0], [1.0, 2.0], [0.0, 1.0]])
def test_build_invalid_motion_leaves_destination_available_for_retry(physical_run, times):
    motion = physical_run.data["motion"]
    physical_run.data["motion"] = [dict(motion[0], time_s=t) for t in times]
    _write_json(physical_run.path / "processed.json", physical_run.data)
    with pytest.raises(ValueError, match="Preview motion must cover its full interval"):
        flex_provisional.build(physical_run.path, physical_run.destination)
    assert not physical_run.destination.exists()
    assert not physical_run.destination.parent.exists()
    physical_run.data["motion"] = motion
    _write_json(physical_run.path / "processed.json", physical_run.data)
    reader = flex_provisional.build(physical_run.path, physical_run.destination)
    assert reader.package.motion == motion


def test_load_returns_seekable_replay_with_preserved_payload(preview):
    reader = flex_provisional.load(str(preview))
    assert isinstance(reader, ReplayReader)
    assert reader.start_s == 0.0
    assert reader.end_s == 2.0
    assert reader.at(1.0)["motion"]["azimuth_deg"] == 48.0
    assert reader.at(1.0)["measurement"]["slant_range_m"] == 40.0
    assert reader.package.statistics == _read_json(preview / "data.json")["statistics"]


@pytest.mark.parametrize(("field", "value"), [
    ("schema", "wfrl.flex-review.v2"), ("schema", None),
    ("status", "READY"), ("status", None),
])
def test_load_requires_explicit_provisional_manifest(preview, field, value):
    manifest = _read_json(preview / "manifest.json")
    manifest[field] = value
    _write_json(preview / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Expected an explicitly provisional flexibility preview"):
        flex_provisional.load(preview)


def test_load_rejects_payload_tampering(preview):
    with (preview / "data.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="Preview integrity mismatch: .*data.json"):
        flex_provisional.load(preview)


@pytest.mark.parametrize("times", [[], [0.0, 0.0, 2.0], [0.0, 1.0, 0.5, 2.0], [1.0, 2.0], [0.0, 1.0]])
def test_load_rejects_unordered_or_incomplete_motion(preview, times):
    data = _read_json(preview / "data.json")
    template = data["motion"][0]
    data["motion"] = [dict(template, time_s=t) for t in times]
    _replace_payload(preview, data)
    with pytest.raises(ValueError, match="Preview motion must cover its full interval"):
        flex_provisional.load(preview)


@pytest.mark.parametrize("time_s", [float("nan"), float("inf"), float("-inf")])
def test_load_rejects_nonfinite_interior_time_with_matching_digest(preview, time_s):
    data = _read_json(preview / "data.json")
    data["motion"].insert(1, dict(data["motion"][0], time_s=time_s))
    _replace_payload(preview, data)
    with pytest.raises(ValueError, match="Preview motion must cover its full interval"):
        flex_provisional.load(preview)


@pytest.mark.parametrize("name", ["manifest.json", "data.json"])
def test_load_propagates_missing_package_files(preview, name):
    (preview / name).unlink()
    with pytest.raises(FileNotFoundError):
        flex_provisional.load(preview)


@pytest.mark.parametrize("span_refined", [False, True])
def test_source_reader_exposes_verified_source_metadata(physical_run, span_refined):
    physical_run.config["span_refined"] = span_refined
    _write_json(physical_run.path / "run_config.json", physical_run.config)
    flex_provisional.build(physical_run.path, physical_run.destination)
    source = flex_provisional.source_reader(str(physical_run.destination))
    assert source.run == physical_run.path.resolve()
    assert source.fps == 10
    assert source.section_count == (37 if span_refined else 19)
    assert source.times == [0.0, 2.0]
    assert source.hashes == {
        row["path"]: row["sha256"] for row in _read_json(physical_run.path / "surface_hashes.json")
    }
    assert source.package.manifest["status"] == "REVIEW_ONLY"


@pytest.mark.parametrize("name", [
    "run_config.json", "processed.json", "exit_status.json", "solver.log", "surface_hashes.json",
])
def test_source_reader_rejects_changed_raw_sources(physical_run, preview, name):
    with (physical_run.path / name).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="Preview integrity mismatch") as caught:
        flex_provisional.source_reader(preview)
    assert str(physical_run.path / name) in str(caught.value)


def test_source_reader_propagates_missing_raw_source(physical_run, preview):
    (physical_run.path / "solver.log").unlink()
    with pytest.raises(FileNotFoundError):
        flex_provisional.source_reader(preview)


def test_source_reader_rejects_invalid_package_before_source_access(preview):
    manifest = _read_json(preview / "manifest.json")
    manifest["status"] = "READY"
    _write_json(preview / "manifest.json", manifest)
    with pytest.raises(ValueError, match="explicitly provisional"):
        flex_provisional.source_reader(preview)
