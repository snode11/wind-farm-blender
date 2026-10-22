"""Geometric regression tests: actual surfaces, source-area weights and truth boundary."""
import json
from pathlib import Path
import struct

import numpy as np
import pytest

from wfrl.nrel_reconstruction.evaluate import (Mesh, clip_mesh_z, evaluate_mesh_pair,
    evaluate_run, point_to_surface_distances, read_ply, sample_surface, section_metrics,
    triangle_areas, write_ply)


def prism(z0=0.0, z1=3.0, chord=2.0, thickness=0.2):
    vertices = np.array([[-thickness/2,-chord/2,z0],[-thickness/2,chord/2,z0],
                         [thickness/2,chord/2,z0],[thickness/2,-chord/2,z0],
                         [-thickness/2,-chord/2,z1],[-thickness/2,chord/2,z1],
                         [thickness/2,chord/2,z1],[thickness/2,-chord/2,z1]])
    faces = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                      [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
    return Mesh(vertices, faces)


def test_surface_distance_is_not_nearest_vertex():
    mesh = Mesh([[0,0,0],[10,0,0],[0,10,0]], [[0,1,2]])
    points = np.array([[2,2,3],[5,5,0],[-1,-1,0],[8,8,0]])
    actual = point_to_surface_distances(points, mesh, point_batch=1, triangle_batch=1)
    assert actual == pytest.approx([3,0,np.sqrt(2),np.sqrt(18)], abs=1e-12)


def test_surface_sampler_weights_area_not_triangle_count():
    mesh = Mesh([[0,0,0],[1,0,0],[0,1,0],[0,0,1],[3,0,1],[0,3,1]], [[0,1,2],[3,4,5]])
    points, face_ids = sample_surface(mesh, 20000, 11)
    assert np.mean(face_ids == 1) == pytest.approx(0.9, abs=0.008)
    assert np.isfinite(points).all()


def test_degenerate_and_empty_models_return_null_not_zero():
    truth = prism()
    empty = Mesh([], [])
    scores = evaluate_mesh_pair(empty, truth, span_bounds_m=[0,3], sample_count=50, regional_sample_count=25)
    assert scores["global"]["truth_to_reconstruction"]["mean_m"] is None
    assert scores["global"]["truth_to_reconstruction"]["null_reason"] == "TARGET_HAS_NO_NONDEGENERATE_SURFACE"
    degenerate = Mesh([[0,0,0],[0,0,1],[0,0,2]], [[0,1,2]])
    assert np.isnan(point_to_surface_distances([[1,0,0]], degenerate)).all()


def test_identity_and_missing_tip_do_not_use_only_success_region():
    truth = prism()
    identity = evaluate_mesh_pair(truth, truth, span_bounds_m=[0,3], sample_count=500, regional_sample_count=100)
    assert identity["global"]["truth_to_reconstruction"]["mean_m"] < 1e-12
    shorter = clip_mesh_z(truth, 0, 1.9)
    missing = evaluate_mesh_pair(shorter, truth, span_bounds_m=[0,3], sample_count=2000, regional_sample_count=500)
    assert missing["regions"]["tip"]["truth_to_reconstruction"]["mean_m"] > 0.4
    assert missing["global"]["truth_to_reconstruction"]["source_area_m2"] == pytest.approx(triangle_areas(truth).sum())
    assert missing["regions"]["tip"]["reconstruction_to_truth"]["mean_m"] is None


def test_clip_area_and_twisted_sections():
    mesh = prism()
    body_area = 2 * (2 + 0.2) * 1
    # Middle third has no artificial caps.
    assert triangle_areas(clip_mesh_z(mesh,1,2)).sum() == pytest.approx(body_area)
    angle = np.deg2rad(32)
    rot = np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
    points = mesh.vertices.copy()
    points[:,:2] = points[:,:2] @ rot.T
    original, twisted = section_metrics(mesh,1.5), section_metrics(Mesh(points,mesh.faces),1.5)
    assert original["chord_length_m"] == pytest.approx(2)
    assert original["body_thickness_m"] == pytest.approx(0.2)
    assert original["section_closed"]
    assert original["self_intersections"] == 0
    assert twisted["chord_length_m"] == pytest.approx(original["chord_length_m"])
    assert twisted["body_thickness_m"] == pytest.approx(original["body_thickness_m"])
    assert abs(twisted["chord_angle_from_y_deg"] - original["chord_angle_from_y_deg"]) == pytest.approx(32)


def test_ply_roundtrip_and_binary_polygon(tmp_path):
    path = tmp_path / "shape.ply"
    mesh = prism()
    write_ply(path, mesh)
    actual = read_ply(path)
    np.testing.assert_array_equal(actual.vertices, mesh.vertices)
    np.testing.assert_array_equal(actual.faces, mesh.faces)
    binary = tmp_path / "binary.ply"
    header = b"ply\nformat binary_little_endian 1.0\nelement vertex 4\nproperty float x\nproperty float y\nproperty float z\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n"
    binary.write_bytes(header + b"".join(struct.pack("<fff",*point) for point in [[0,0,0],[1,0,0],[1,1,0],[0,1,0]]) + struct.pack("<Biiii",4,0,1,2,3))
    assert len(read_ply(binary).faces) == 2


def test_reference_contract_refuses_truth_before_geometry_read(tmp_path):
    reconstruction = tmp_path / "reconstruction"
    reconstruction.mkdir()
    metadata = {"target":"T1/B1", "units":"m", "coordinate_frame":"blade_root_local", "surface_scope":"whole_blade", "t_ref_sim_time_s":120.0}
    (reconstruction / "model_state.json").write_text(json.dumps(metadata))
    truth_meta = {**metadata,"t_ref_sim_time_s":120.1}
    result = evaluate_run(reconstruction,tmp_path/"does-not-exist.ply",truth_meta,output_dir=tmp_path/"evaluation-only")
    assert result["status"] == "NOT_COMPARABLE"
    assert "REFERENCE_TIME_MISMATCH" in result["metadata_errors"]


def test_same_prior_only_cannot_establish_image_contribution(tmp_path):
    reconstruction = tmp_path / "reconstruction"
    reconstruction.mkdir()
    metadata = {"target":"T1/B1", "units":"m", "coordinate_frame":"blade_root_local", "surface_scope":"whole_blade", "t_ref_sim_time_s":120.0}
    (reconstruction / "model_state.json").write_text(json.dumps(metadata))
    (reconstruction / "surface_evidence.json").write_text(json.dumps({"regions":[{"name":"back","status":"prior_only","source_frames":[]}]}))
    mesh = prism()
    truth = tmp_path / "truth.ply"
    write_ply(truth, mesh)
    for name in ("initial_template.ply","prior_only.ply","T1_B1.ply"):
        write_ply(reconstruction/name, mesh)
    result = evaluate_run(reconstruction,truth,metadata,scoring_config={"span_bounds_m":[0,3],"section_positions_m":[0.5,1.5,2.5],"sample_count":100,"regional_sample_count":50},output_dir=tmp_path/"evaluation-only")
    assert result["status"] == "SCORED"
    assert result["comparison"]["image_contribution"] == "NOT_YET_ESTABLISHED"
    assert result["surface_evidence"]["coverage_conclusion"] == "PARTIAL_OR_PRIOR_DEPENDENT"
    assert result["surface_evidence"]["output_surface_area_by_evidence_m2"] is None
    assert (tmp_path / "evaluation-only" / "scores.json").exists()


def test_improved_final_requires_same_configuration_control_evidence(tmp_path):
    reconstruction = tmp_path / "reconstruction"
    reconstruction.mkdir()
    metadata = {"target":"T1/B1", "units":"m", "coordinate_frame":"blade_root_local", "surface_scope":"whole_blade", "t_ref_sim_time_s":120.0}
    (reconstruction / "model_state.json").write_text(json.dumps(metadata))
    truth_mesh = prism()
    baseline = prism(chord=1.4,thickness=0.4)
    truth = tmp_path / "truth.ply"
    write_ply(truth,truth_mesh)
    write_ply(reconstruction/"T1_B1.ply",truth_mesh)
    write_ply(reconstruction/"initial_template.ply",baseline)
    write_ply(reconstruction/"prior_only.ply",baseline)
    config = {"span_bounds_m":[0,3],"sample_count":100,"regional_sample_count":50}
    unconfirmed = evaluate_run(reconstruction,truth,metadata,scoring_config=config,output_dir=tmp_path/"evaluation-only")
    assert unconfirmed["comparison"]["image_contribution"] == "NOT_YET_ESTABLISHED"
    assert unconfirmed["comparison"]["relative_to_prior_only"]["truth_to_reconstruction"]["delta_mean_m"] < 0
    provenance = {name:True for name in ("same_initial_parameters","same_optimizer","same_stage_schedule","same_regularization","same_budget","only_image_weights_differ")}
    provenance.update({"prior_only_image_weight":0,"final_image_weight":1})
    (reconstruction/"optimization.json").write_text(json.dumps({"comparison_provenance":provenance}))
    confirmed = evaluate_run(reconstruction,truth,metadata,scoring_config=config,output_dir=tmp_path/"evaluation-only")
    assert confirmed["comparison"]["image_contribution"] == "ESTABLISHED_ON_THIS_CLIP"
