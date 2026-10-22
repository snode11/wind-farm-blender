"""Exact kinematic DP and structural-prior surface/cache contracts."""
import copy
import itertools

import numpy as np
import pytest
import torch

from scripts.nrel_dynamic_sequence import PROTOCOL_SCHEMA, checked_cache, record_status, validate_protocol
from wfrl.nrel_reconstruction.dynamic_geometry import DynamicBladeReference, plane_readout, transform_points
from wfrl.nrel_reconstruction.dynamic_sequence import PRIOR_SCHEMA, StructuralPointPrior, solve_sequence
from wfrl.nrel_reconstruction.model import BladeModel


def structural_fixture():
    static = BladeModel(interpolation="bspline")
    with torch.no_grad():
        static.center_raw.copy_(torch.linspace(-.2, .1, 12).reshape(6, 2))
    reference = DynamicBladeReference.from_metadata(static.metadata())
    prior = {"schema": PRIOR_SCHEMA, "target": "T1/B1", "coordinate_frame": "blade_root_local", "units": "m",
             "station_m": [0, 30., 61.5], "aero_reference_B_m": [[0, 0, 0], [.3, 0, 30], [.6, 0, 61.5]],
             "structural_reference_B_m": [[0, 0, 0], [.4, -.1, 30], [.9, -.2, 61.5]],
             "aero_twist_deg": [12., 7., 11.], "source_sha256": {"static_container": "a" * 64}}
    return reference, prior


@pytest.mark.parametrize("q", [[0, 0], [.5, -.5], [-.25, .15]])
def test_prior_defined_structural_point_preserves_a02_surface_for_all_states(q):
    reference, raw = structural_fixture()
    prior = StructuralPointPrior(reference, raw)
    result, original = prior.evaluate(q), reference.evaluate(q)
    assert np.array_equal(result["vertices"], original["vertices"])
    assert result["surface_identity_max_error_m"] < 1e-12
    assert result["mapping_status"] == "PRIOR_DEFINED_STRUCTURAL_POINT"
    expected = result["centerline"] + np.einsum("nij,nj->ni", result["section_frames"], prior.offset_reference)
    assert np.allclose(result["mechanical_reference_axis"], expected, atol=1e-14, rtol=0)
    assert np.array_equal(result["structural_tip"], expected[-1])
    assert not np.array_equal(result["structural_tip"], result["aero_axis_tip"])
    assert prior.provenance["mechanical_tangent_frame_used"] is False
    assert prior.provenance["BeamDyn_verified"] is False


def test_source_twist_offset_has_declared_direction_and_same_endpoint_span():
    reference, raw = structural_fixture()
    prior = StructuralPointPrior(reference, raw)
    result = prior.evaluate([0, 0])
    angle = np.deg2rad(-11.)
    expected = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]) @ [.3, -.2]
    assert result["structural_tip"][2] == 61.5
    assert np.allclose((result["structural_tip"] - result["axis_tip"])[:2], expected, atol=1e-14)
    raw["station_m"][-1] = 61.4999
    with pytest.raises(ValueError, match="61.5"):
        StructuralPointPrior(reference, raw)


def test_mechanical_d_is_invariant_under_a_common_rigid_pose():
    reference, raw = structural_fixture()
    geometry = StructuralPointPrior(reference, raw).evaluate([.3, -.2])
    root = np.eye(4); root[:3, 3] = [2., -3., 80.]
    point = transform_points(geometry["structural_tip"], root)
    center, normal = np.array([1., -2., 80.]), np.array([1., 0., 0.])
    before = plane_readout(point, center, normal)
    common = np.array([[0., -1., 0., 30.], [1., 0., 0., -10.], [0., 0., 1., 4.], [0., 0., 0., 1.]])
    after = plane_readout(transform_points(point, common), transform_points(center, common), common[:3, :3] @ normal)
    assert after["d"] == pytest.approx(before["d"], abs=1e-13)
    assert np.allclose(after["foot"], transform_points(before["foot"], common), atol=1e-13)


def test_dp_matches_independent_small_brute_force_minimum():
    grid = np.array([[-.1, 0], [0, 0], [.1, 0]])
    times = [0., .05, .1, .15]
    image = np.array([[0., 9., 20.], [12., 0., 12.], [9., 5., 0.], [8., 3., 0.]])
    solution = solve_sequence(grid, times, image)
    def cost(path):
        q = grid[list(path)]
        if path[1] != 1 or np.any(abs(np.diff(q, axis=0)) > .2 + 1e-12):
            return np.inf
        return float(np.sum(image[np.arange(4), path] / 10) + .01 * np.sum((q / .5) ** 2)
                     + .05 * np.sum((np.diff(q, axis=0) / .1) ** 2))
    minimum = min(cost(path) for path in itertools.product(range(3), repeat=4))
    assert solution["total_cost"] == pytest.approx(minimum)
    assert cost(tuple(solution["state_indices"])) == pytest.approx(minimum)
    assert solution["q_m"][1] == [0., 0.]
    assert solution["transition_evaluations"] == 3 * 3 ** 2
    assert solution["offline_future_frames_used"] is True


def test_prior_only_and_zero_image_cost_have_same_all_zero_trajectory():
    grid = [[-.2, 0], [0, 0], [.2, 0]]
    times = [0., .05, .1, .15]
    prior = solve_sequence(grid, times)
    zeros = solve_sequence(grid, times, np.zeros((4, 3)))
    assert prior["q_m"] == zeros["q_m"] == [[0., 0.]] * 4
    assert prior["image_cost_used"] is False and zeros["image_cost_used"] is True


def test_speed_cap_forbids_cheapest_unreachable_state():
    grid = [[-.5, 0], [0, 0], [.5, 0]]
    image = [[0, 100, 0], [0, 100, 0], [0, 100, 0], [0, 100, 0]]
    solution = solve_sequence(grid, [0., .05, .1, .15], image)
    assert solution["q_m"] == [[0., 0.]] * 4


def test_missing_anchor_cost_is_unavailable_and_never_zero():
    image = np.zeros((4, 3)); image[1, 1] = np.nan
    result = solve_sequence([[-.1, 0], [0, 0], [.1, 0]], [0., .05, .1, .15], image)
    assert result["status"] == "UNAVAILABLE"
    assert result["q_m"] is None and result["total_cost"] is None
    with pytest.raises(ValueError):
        solve_sequence([[.1, 0]], [0, .05])


def test_group_status_does_not_imply_images_in_prior_only_controls():
    for frame in (42, 43, 44, 45):
        assert record_status("G1", frame) == "PRIOR_DEFINED_BASELINE"
        assert record_status("G2", frame) == "PRIOR_ONLY_AMBIGUOUS"
    assert record_status("G3", 42) == record_status("G3", 45) == "PRIOR_ASSISTED_AMBIGUOUS"
    assert record_status("G3", 43) == record_status("G3", 44) == "IMAGE_CONSTRAINED_AMBIGUOUS"


def protocol():
    return {"schema": PROTOCOL_SCHEMA, "status": "FROZEN_BEFORE_COMPUTATION", "target": "T1/B1",
        "frames": [42, 43, 44, 45], "q_grid_per_axis_m": [round(-.5 + i * .05, 2) for i in range(21)],
        "anchor_frame_id": 43, "anchor_q_m": [0., 0.], "initialization_q_m": [0., 0.],
        "dt_s": .05, "temporal_weight": .05, "temporal_scale_m": .1, "node_weight": .01,
        "node_scale_m": .5, "component_speed_cap_m_s": 4., "image_scale_native_px": 10.,
        "equivalence_tolerance_native_px": 3., "budget": {"threads": 4, "optimizer_iterations": 0, "restarts": 0,
             "max_dp_transition_evaluations_per_group": 3 * 441 ** 2, "time_limit_s": 300}}


def test_protocol_locks_grid_prior_and_exact_budget():
    p = protocol()
    assert len(validate_protocol(p)) == 441
    for key, value in (("anchor_frame_id", True), ("component_speed_cap_m_s", 5), ("equivalence_tolerance_native_px", 2)):
        bad = copy.deepcopy(p); bad[key] = value
        with pytest.raises(ValueError): validate_protocol(bad)
    bad = copy.deepcopy(p); bad["budget"]["max_dp_transition_evaluations_per_group"] += 1
    with pytest.raises(ValueError): validate_protocol(bad)


def cache_fixture():
    grid = validate_protocol(protocol())
    moves = {frame: {"sim_time_s": 120 + (frame + .5) / 20} for frame in [42, 43, 44, 45]}
    cache = {"schema": "nrel-dynamic-dy1-refine-grid.v1", "status": "DIAGNOSTIC_COMPLETE_NOT_STRUCTURAL_ACCEPTANCE",
        "target": "T1/B1", "primary_group": "all3", "truth_read": False, "finite_grid_only": True,
        "optimizer_iterations": 0, "time_interpolation": False, "candidate_states": 1764, "image_evaluations": 5292,
        "static_model_provenance": {"model_state_sha256": "modelhash"}, "budget": {"image_size": [320, 180]},
        "post_run_integrity": {"unchanged": True},
        "frame_summary": {str(frame): {"equivalence_tolerance_native_px": 3} for frame in moves},
        "rows": [{"frame_id": frame, "q_m": list(q), "sim_time_s": moves[frame]["sim_time_s"],
                  "valid_image_terms": True, "native_mean_distance_px": 1.,
                  "camera_metrics": [{"camera_id": camera, "valid": True, "native_mean_distance_px": 1.}
                      for camera in ["C1", "C2", "C3"]]} for frame in moves for q in grid]}
    return cache, grid, moves


def test_cache_retains_missing_complete_camera_terms_and_rejects_zero_substitution():
    cache, grid, moves = cache_fixture()
    assert checked_cache(cache, grid, "modelhash", moves)[0].shape == (4, 441)
    cache["rows"][0]["valid_image_terms"] = False
    cache["rows"][0]["native_mean_distance_px"] = None
    cache["rows"][0]["camera_metrics"][0].update(valid=False, native_mean_distance_px=None)
    assert np.isinf(checked_cache(cache, grid, "modelhash", moves)[0][0, 0])
    cache["rows"][0]["native_mean_distance_px"] = 0.
    with pytest.raises(ValueError, match="never zero"):
        checked_cache(cache, grid, "modelhash", moves)
    cache["rows"][0]["native_mean_distance_px"] = None
    cache["rows"][0]["valid_image_terms"] = True
    with pytest.raises(ValueError, match="equal camera mean"):
        checked_cache(cache, grid, "modelhash", moves)
