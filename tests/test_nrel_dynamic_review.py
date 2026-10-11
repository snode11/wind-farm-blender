"""Review-package safety using synthetic data, with no source or scoring truth."""
import copy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("standalone_review_test", ROOT / "wfrl/nrel_reconstruction/dynamic_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def build(tmp_path, edit_sequence=None, edit_geometry=None, edit_manifest=None):
    package = tmp_path / "package"
    package.mkdir()
    sampling = {"start_s": 120, "end_s_exclusive": 125, "fps": 20, "frame_count_per_camera": 100,
                "simulation_time": "start+(n+.5)/fps", "pts": "n/fps"}
    groups = {}
    for group in review.GROUPS:
        records = [{"frame_id": f, "sim_time_s": review.sample_time(f), "q_m": [0., 0.],
            "status": "HYPOTHESIS_REVIEW_ONLY", "point_world_m": [2., 3., 4.],
            "center_world_m": [0., 0., 0.], "normal_world_unit": [1., 0., 0.],
            "foot_world_m": [0., 3., 4.], "d_m": 2., "abs_d_m": 2.,
            "range_m": [-1., 1.], "range_scope": review.RANGE_SCOPE} for f in review.FRAMES]
        groups[group] = {"records": records}
    sequence = {"schema": "nrel-dynamic-review-sequence.v1", "target": "T1/B1", "frames": list(review.FRAMES), "groups": groups}
    geometry = {"faces": np.asarray([[0, 1, 2]], dtype=np.int64),
                "material_ids": np.asarray([[0,0], [0,1], [0,2]], dtype=np.int64)}
    for group in review.GROUPS:
        geometry[group + "_vertices_world"] = np.repeat(np.asarray([[[0.,0.,0.], [1.,0.,0.], [0.,1.,0.]]]), 4, axis=0)
    if edit_sequence:
        edit_sequence(sequence)
    if edit_geometry:
        edit_geometry(geometry)
    (package / "sequence.json").write_text(json.dumps(sequence))
    np.savez(package / "geometry.npz", **geometry)
    videos = {}
    for camera in ("C1", "C2", "C3"):
        path = tmp_path / (camera + ".mp4")
        path.write_bytes(("synthetic signed bytes " + camera).encode())
        videos[camera] = {"path": str(path), "sha256": review.sha256(path)}
    manifest = {"schema": "nrel-dynamic-review-package.v1", "target": "T1/B1", "status": "REVIEW_ONLY",
        "units": "m", "condition": "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE", "point_scope": review.POINT_SCOPE,
        "sampling": sampling, "files": {name: review.sha256(package/name) for name in ("sequence.json", "geometry.npz")}, "videos": videos}
    if edit_manifest:
        edit_manifest(manifest)
    (package / "manifest.json").write_text(json.dumps(manifest))
    return package


def test_exact_timeline_group_and_independent_range(tmp_path):
    package = review.ReviewPackage.load(build(tmp_path))
    assert package.at_scene_frame(43)["record"]["frame_id"] == 42
    assert package.at_scene_frame(46, "G1")["record"]["sim_time_s"] == 122.275
    assert package.at_scene_frame(44)["record"]["range_m"] == [-1, 1]  # Does not contain prior d.
    for frame in (1, 42, 47, 100):
        assert package.at_scene_frame(frame) is None
    for frame in (0, 101, 44., True):
        with pytest.raises(ValueError):
            package.at_scene_frame(frame)
    with pytest.raises(ValueError):
        package.at_scene_frame(44, "G4")
    assert package.geometry["G3_vertices_world"].flags.writeable is False


@pytest.mark.parametrize("name", ["sequence.json", "geometry.npz", "C2"])
def test_changed_signed_data_rejected(tmp_path, name):
    path = build(tmp_path)
    target = path/name if name.endswith(("json", "npz")) else tmp_path/(name + ".mp4")
    with target.open("ab") as stream:
        stream.write(b"corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        review.ReviewPackage.load(path)


@pytest.mark.parametrize("field,value", [("point_world_m", None), ("d_m", None), ("d_m", float("nan")),
    ("sim_time_s", 122.18), ("normal_world_unit", [2,0,0]), ("foot_world_m", [1,3,4]),
    ("abs_d_m", -2), ("q_m", [.51,0]), ("range_m", [1,-1]), ("range_scope", "joint_posterior")])
def test_signed_but_inconsistent_measurement_rejected(tmp_path, field, value):
    path = build(tmp_path, edit_sequence=lambda s: s["groups"]["G3"]["records"][1].update({field: value}))
    with pytest.raises(ValueError):
        review.ReviewPackage.load(path)


@pytest.mark.parametrize("change", ["badface", "duplicatematerial", "nanvertex", "missingframe", "degenerate"])
def test_geometry_and_record_contract(tmp_path, change):
    def geometry(g):
        if change == "badface": g["faces"][0,2] = 9
        if change == "duplicatematerial": g["material_ids"][1] = g["material_ids"][0]
        if change == "nanvertex": g["G1_vertices_world"][0,0,0] = float("nan")
        if change == "degenerate": g["G1_vertices_world"][:,2] = g["G1_vertices_world"][:,1]
    def sequence(s):
        if change == "missingframe": s["groups"]["G2"]["records"].pop()
    with pytest.raises(ValueError):
        review.ReviewPackage.load(build(tmp_path, edit_geometry=geometry, edit_sequence=sequence))


def test_path_escape_and_status_do_not_load(tmp_path):
    path = build(tmp_path, edit_manifest=lambda m: m["files"].update({"../outside": "0" * 64}))
    with pytest.raises(ValueError, match="Unsafe"):
        review.ReviewPackage.load(path)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["status"] = "ACCEPTED"
    (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="conditional status"):
        review.ReviewPackage.load(path)
