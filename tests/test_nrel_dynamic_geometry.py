"""Geometry/coordinate invariants for truth-free DY1 dynamic candidates."""
import hashlib
import json

import numpy as np
import pytest
import torch

from wfrl.nrel_reconstruction.dynamic_geometry import (
    DynamicBladeReference, MAPPING_STATUS, _center_derivative, _minimum_rotation,
    plane_readout, signed_distance, transform_points,
)
from wfrl.nrel_reconstruction.model import BladeModel, bspline_weights


def fixture_reference(interpolation="bspline"):
    static = BladeModel(interpolation=interpolation)
    with torch.no_grad():
        static.center_raw.copy_(torch.linspace(-.2, .1, 12).reshape(6, 2))
        static.chord_raw.copy_(torch.linspace(-.1, .2, 7))
    return static, DynamicBladeReference.from_metadata(static.metadata())


@pytest.mark.parametrize("interpolation", ["linear", "bspline"])
def test_zero_q_exactly_preserves_frozen_surface_and_section_frames(interpolation):
    static, reference = fixture_reference(interpolation)
    result = reference.evaluate([0., 0.])
    assert np.array_equal(result["vertices"], static().detach().numpy())
    assert np.array_equal(result["vertices"], reference.reference_vertices)
    assert np.array_equal(result["section_frames"], np.repeat(np.eye(3)[None], 25, axis=0))
    assert result["mapping_status"] == MAPPING_STATUS


def test_candidate_preserves_root_connection_direction_topology_and_section_size():
    _, reference = fixture_reference()
    before, after = reference.evaluate([0., 0.]), reference.evaluate([.5, -.5])
    assert np.array_equal(before["vertices"][:12], after["vertices"][:12])
    assert np.array_equal(before["tangents"][0], after["tangents"][0])
    assert np.array_equal(before["faces"], after["faces"])
    assert np.array_equal(before["material_ids"], after["material_ids"])
    assert np.allclose(after["axis_tip"] - before["axis_tip"], [.5, -.5, 0.], atol=1e-14)
    before_offsets = before["vertices"].reshape(25, 12, 3) - before["centerline"][:, None]
    after_offsets = after["vertices"].reshape(25, 12, 3) - after["centerline"][:, None]
    assert np.allclose(np.linalg.norm(before_offsets, axis=-1), np.linalg.norm(after_offsets, axis=-1), atol=1e-13)
    frames = after["section_frames"]
    assert np.allclose(frames.transpose(0, 2, 1) @ frames, np.eye(3), atol=1e-14)
    a, b = before["tangents"], after["tangents"]
    assert np.allclose(np.einsum("nij,nj->ni", frames, a / np.linalg.norm(a, axis=1)[:, None]), b / np.linalg.norm(b, axis=1)[:, None], atol=1e-14)


def test_axis_tip_and_ring_centroid_are_separate_material_definitions():
    static, reference = fixture_reference()
    result = reference.evaluate([.2, -.1])
    assert np.array_equal(result["axis_tip"], result["centerline"][-1])
    assert np.array_equal(result["ring_centroid"], result["vertices"][-12:].mean(axis=0))
    assert np.linalg.norm(result["ring_centroid"] - result["axis_tip"]) > .1
    assert result["axis_tip"][2] == 61.5
    assert not np.array_equal(result["axis_tip"][:2], static.metadata()["center_offsets_m"][-1])


def test_clamped_spline_tangents_match_independent_finite_differences_at_boundaries():
    static, reference = fixture_reference()
    controls = np.asarray(static.metadata()["center_offsets_m"])
    samples = np.array([0., 8., 30.75, 61.5])
    step = 1e-5
    lower, upper = np.maximum(samples - step, 0.), np.minimum(samples + step, 61.5)
    numerical = ((bspline_weights(upper, len(controls)) - bspline_weights(lower, len(controls))) @ controls
                 / (upper - lower)[:, None])
    assert np.allclose(_center_derivative(samples, controls, "bspline", 61.5), numerical, atol=2e-7, rtol=0)
    tiny = reference.evaluate([1e-9, -1e-9])
    assert np.max(np.abs(tiny["vertices"] - reference.reference_vertices)) < 2e-9
    assert np.max(np.abs(tiny["section_frames"] - np.eye(3))) < 1e-9


def test_degenerate_and_antiparallel_tangent_frames_are_rejected():
    for tangent in ([0., 0., 0.], [0., 0., -1.], [np.nan, 0., 1.]):
        with pytest.raises(ValueError):
            _minimum_rotation(np.array([[0., 0., 1.]]), np.asarray([tangent]))


def test_same_geometry_readout_and_common_rigid_transform_invariance():
    _, reference = fixture_reference()
    geometry = reference.evaluate([.3, -.2])
    root = np.eye(4)
    angle = .7
    root[:3, :3] = [[np.cos(angle), 0., np.sin(angle)], [0., 1., 0.], [-np.sin(angle), 0., np.cos(angle)]]
    root[:3, 3] = [2., -3., 80.]
    tip = transform_points(geometry["axis_tip"], root)
    center, normal = np.array([1., -2., 80.]), np.array([2., .2, -.1])
    readout = plane_readout(tip, center, normal)
    assert np.dot(readout["normal"], readout["foot"] - center) == pytest.approx(0., abs=1e-13)
    assert np.linalg.norm(tip - readout["foot"]) == pytest.approx(readout["abs_d"])
    common = np.eye(4)
    common[:3, :3] = [[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]]
    common[:3, 3] = [30., 20., -10.]
    changed = signed_distance(transform_points(tip, common), transform_points(center, common), common[:3, :3] @ normal)
    assert changed == pytest.approx(readout["d"], abs=1e-13)
    assert np.allclose(transform_points(geometry["vertices"], root)[-12:].mean(axis=0), transform_points(geometry["ring_centroid"], root), atol=1e-13)
    # evaluate is in B only: no stateful accumulation or hidden root transform.
    assert np.array_equal(geometry["vertices"], reference.evaluate([.3, -.2])["vertices"])


@pytest.mark.parametrize("q", [[np.nan, 0.], [0., np.inf], [.50001, 0.], [0.], [0., 0., 0.]])
def test_invalid_candidates_are_rejected(q):
    _, reference = fixture_reference()
    with pytest.raises(ValueError):
        reference.evaluate(q)


@pytest.mark.parametrize("normal", [[0., 0., 0.], [np.nan, 0., 1.], [0., 1.]])
def test_invalid_planes_are_rejected(normal):
    with pytest.raises(ValueError):
        plane_readout([1., 2., 3.], [0., 0., 0.], normal)


def test_invalid_coordinates_scale_reflection_and_metadata_are_rejected():
    with pytest.raises(ValueError):
        plane_readout([np.inf, 0., 0.], [0., 0., 0.], [1., 0., 0.])
    with pytest.raises(ValueError):
        plane_readout([0., 0., 0.], [np.nan, 0., 0.], [1., 0., 0.])
    for scale in (2., -1.):
        transform = np.eye(4); transform[0, 0] = scale
        with pytest.raises(ValueError, match="proper rigid"):
            transform_points([0., 0., 0.], transform)
    metadata = BladeModel().metadata()
    metadata["center_offsets_m"][0][0] = 1.
    with pytest.raises(ValueError, match="root"):
        DynamicBladeReference.from_metadata(metadata)


def test_float32_pose_roundoff_is_preserved_without_projecting_input_rotation():
    transform = np.eye(4)
    transform[0, 0] += 3e-7
    points = np.array([[1., 2., 3.]])
    assert np.array_equal(transform_points(points, transform), points @ transform[:3, :3].T)
    transform[0, 0] += 1e-4
    with pytest.raises(ValueError, match="proper rigid"):
        transform_points(points, transform)


def test_loader_selects_video_key_and_checks_frozen_hash(tmp_path):
    static, _ = fixture_reference()
    path = tmp_path / "model_state.json"
    path.write_text(json.dumps({"target": "T1/B1", "units": "m", "coordinate_frame": "blade_root_local",
                                "reference_shape": "static_near_t_ref", "t_ref_sim_time_s": 122.175,
                                "models": {"T1_B1": static.metadata(), "prior_only": {"invalid": True}}}))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    reference = DynamicBladeReference.from_model_state(path, expected_sha256=digest)
    assert reference.provenance["model_key"] == "T1_B1"
    assert np.array_equal(reference.evaluate([0., 0.])["vertices"], static().detach().numpy())
    with pytest.raises(ValueError, match="SHA256"):
        DynamicBladeReference.from_model_state(path, expected_sha256="0" * 64)
