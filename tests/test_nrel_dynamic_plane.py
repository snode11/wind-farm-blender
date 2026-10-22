"""Rigid plane correctness and fail-closed input checks; no source truth reads."""
import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location("nrel_dynamic_plane", Path(__file__).parents[1] / "scripts/nrel_dynamic_plane.py")
plane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plane)


def rotation(axis, angle):
    c, s = np.cos(angle), np.sin(angle)
    return [np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
            np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])][axis]


def fixture_rows():
    frames, motions = [], []
    for i in range(100):
        time = 120 + (i + .5) / 20
        for camera in ("C1", "C2", "C3"):
            frames.append({"sample_index": i, "camera_id": camera, "time_s": time, "nacelle_pose": np.eye(4).tolist()})
        motions.append({"frame_id": i, "sim_time_s": time, "T_world_from_blade_root": np.eye(4).tolist(),
                        "units": "m", "source": "simulation ideal rigid running observation; no deformation"})
    return frames, motions


def test_pitched_coned_root_recovers_hub_and_shaft_not_blade_x():
    yaw = rotation(2, .4) @ rotation(0, .2)
    shaft = yaw @ rotation(1, np.deg2rad(5))
    blade = shaft @ rotation(0, .8) @ rotation(1, -.1) @ rotation(2, -.3)
    hub = np.array([12., -4., 90.])
    root, nacelle = np.eye(4), np.eye(4)
    root[:3, :3] = blade
    root[:3, 3] = hub + blade @ np.array([0., 0., 1.5])
    nacelle[:3, :3] = yaw
    center, normal = plane.derive_plane(root, nacelle, -5.)
    np.testing.assert_allclose(center, hub, atol=1e-12)
    np.testing.assert_allclose(normal, shaft[:, 0], atol=1e-12)
    assert not np.allclose(normal, blade[:, 0])
    point = np.array([10., 2., 88.])
    common = np.eye(4); common[:3, :3] = rotation(1, .7); common[:3, 3] = [4, 6, -2]
    c2, n2 = plane.derive_plane(common @ root, common @ nacelle, -5.)
    p2 = common[:3, :3] @ point + common[:3, 3]
    assert np.isclose(normal @ (point-center), n2 @ (p2-c2))


@pytest.mark.parametrize("mutation,match", [
    (lambda f, m: f.pop(), "Incomplete"),
    (lambda f, m: f.append(copy.deepcopy(f[0])), "Duplicate"),
    (lambda f, m: f[0].update(time_s=120.0), "sampling-center"),
    (lambda f, m: f[1]["nacelle_pose"][0].__setitem__(3, .01), "pose mismatch"),
    (lambda f, m: m[0]["T_world_from_blade_root"][0].__setitem__(0, -1), "proper rigid"),
    (lambda f, m: m[0].update(sim_time_s=float("nan")), "sampling-center"),
    (lambda f, m: m[0].update(units="cm"), "units mismatch"),
])
def test_mismatch_never_silently_passes(mutation, match):
    frames, motions = fixture_rows()
    mutation(frames, motions)
    with pytest.raises(ValueError, match=match):
        plane.validate_and_extract(frames, motions, -5)


def test_all_frames_are_required_and_normal_is_unit():
    frames, motions = fixture_rows()
    records, checks = plane.validate_and_extract(frames, motions, -5)
    assert len(records) == 100 and checks["record_count"] == 100
    assert records[0]["sim_time_s"] == 120.025
    assert records[-1]["sim_time_s"] == 124.975
    np.testing.assert_allclose(np.linalg.norm([r["n_world_unit"] for r in records], axis=1), 1)


def test_source_hash_or_path_change_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(plane, "REPOSITORY", tmp_path)
    monkeypatch.setattr(plane, "SOURCES", {"dataset": Path("data.json")})
    (tmp_path / "data.json").write_text("changed")
    policy = {"schema": "nrel-dynamic-plane-extract-policy.v1", "sampling": plane.SAMPLING,
              "script_sha256": plane.digest(Path(plane.__file__).read_bytes()), "hub_radius_m": 1.5,
              "tolerances": plane.TOLERANCES, "sources": {"dataset": {
                  "path": str(tmp_path / "data.json"), "sha256": "0" * 64,
                  "fields": plane.SOURCE_FIELDS["dataset"]}}}
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        plane.checked_sources(policy)
    policy["sources"]["dataset"]["path"] = str(tmp_path / "elsewhere.json")
    with pytest.raises(ValueError, match="whitelist mismatch"):
        plane.checked_sources(policy)
