"""Finite-grid refinement keeps missing terms, fixed budgets and endpoint ranges."""
import copy
import hashlib
import json

import pytest

from scripts.nrel_dynamic_dy1_refine import (
    GROUPS, PROTOCOL_SCHEMA, aggregate_camera_metrics, ambiguity_summary,
    checked_planes, validate_protocol,
)


def protocol():
    return {"schema": PROTOCOL_SCHEMA, "status": "FROZEN_BEFORE_COMPUTATION", "target": "T1/B1",
            "frames": [42, 43, 44, 45], "primary_group": "all3", "secondary_groups": list(GROUPS)[1:],
            "candidate_model": {"q_grid_per_axis_m": [-.5, 0, .5], "q_units": "m",
                "q_directions": ["blade_root_x", "blade_root_y"], "maximum_tip_q_component_m": .5,
                "state_count": 9},
            "budget": {"threads": 4, "image_size": [320, 180], "time_limit_s": 900,
                       "optimizer_iterations": 0, "max_candidate_states": 36, "max_image_evaluations": 108},
            "equivalence_tolerance_native_px": 3., "candidate_structural_point": {"mapping_status": "unverified"},
            "calibration_uncertainty": "ideal inputs; finite grid excludes calibration and time error"}


def camera(name, distance, valid=True, loss=1.):
    return {"camera_id": name, "native_mean_distance_px": distance, "loss": loss, "valid": valid}


def row(q, distance, d, valid=True):
    return {"q_m": q, "native_mean_distance_px": distance, "candidate_d_m": d, "valid_image_terms": valid}


def test_primary_requires_all_three_but_secondary_reuses_complete_subset():
    images = [camera("C1", 2), camera("C2", 4), camera("C3", None, False, None)]
    all3 = aggregate_camera_metrics(images, GROUPS["all3"])
    assert all3["valid_image_terms"] is False
    assert all3["native_mean_distance_px"] is None and all3["loss"] is None
    assert all3["missing_or_invalid_camera_ids"] == ["C3"]
    pair = aggregate_camera_metrics(images, GROUPS["C1C2"])
    assert pair["valid_image_terms"] is True and pair["native_mean_distance_px"] == 3
    assert aggregate_camera_metrics(images, GROUPS["C2C3"])["valid_image_terms"] is False


@pytest.mark.parametrize("images", [[], [camera("C1", 0, False, 0)], [camera("C1", float("nan"))],
                                   [camera("C1", 2, loss=float("inf"))], [camera("C1", True)]])
def test_empty_zero_invalid_and_nonfinite_terms_cannot_win(images):
    result = aggregate_camera_metrics(images, GROUPS["C1"])
    assert result["native_mean_distance_px"] is None and result["loss"] is None
    assert result["valid_image_terms"] is False


def test_duplicate_camera_terms_cannot_be_double_weighted():
    with pytest.raises(ValueError, match="Duplicate"):
        aggregate_camera_metrics([camera("C1", 1), camera("C1", 2)], GROUPS["C1"])


def test_equivalent_range_keeps_endpoint_pair_and_boundary_beyond_best():
    values = [row([0, 0], 1, 2), row([-.5, 0], 4, -4), row([.5, 0], 3, 7),
              row([0, .5], 4.00001, 100), row([0, -.5], None, -100, False)]
    result = ambiguity_summary(values, 3)
    assert result["best_q_m"] == [0, 0]
    assert result["search_boundary_best"] is False
    assert result["search_boundary_equivalent"] is True
    assert result["equivalent_boundary_state_count"] == 2
    assert result["equivalent_state_count"] == 3
    assert result["d_range_m"] == [-4, 7] and result["d_span_m"] == 11
    assert result["extreme_equivalent_pair"]["minimum_d"]["q_m"] == [-.5, 0]
    assert result["extreme_equivalent_pair"]["maximum_d"]["q_m"] == [.5, 0]
    assert result["invalid_candidate_count"] == 1
    assert result["not_a_confidence_interval"] is True and result["structural_acceptance"] is False


def test_unavailable_nonfinite_states_and_invalid_tolerance_remain_explicit():
    assert ambiguity_summary([row([0, 0], None, 0, False), row([0, 0], 1, float("nan"))], 3)["d_range_m"] is None
    for tolerance in (True, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ambiguity_summary([], tolerance)


def test_protocol_computes_exact_budget_and_supports_alternate_resolution():
    p = protocol()
    p["budget"]["image_size"] = [640, 360]
    result = validate_protocol(p)
    assert result["candidate_states"] == 36 and result["image_evaluations"] == 108
    assert len(result["grid"]) == 9 and result["size"] == (640, 360)
    p["candidate_model"]["q_grid_per_axis_m"] = [round(-.5 + i * .05, 2) for i in range(21)]
    p["candidate_model"]["state_count"] = 441
    p["budget"].update(max_candidate_states=1764, max_image_evaluations=5292)
    assert validate_protocol(p)["image_evaluations"] == 5292


@pytest.mark.parametrize("grid", [[-.5, .5, 0], [-.5, 0, 0, .5], [-.51, 0, .5],
                                 [-.5, True, .5], [-.5, float("nan"), .5], [-.4, 0, .5]])
def test_invalid_grid_is_rejected(grid):
    p = protocol()
    p["candidate_model"]["q_grid_per_axis_m"] = grid
    with pytest.raises(ValueError):
        validate_protocol(p)


@pytest.mark.parametrize("path,value", [("schema", "nrel-dynamic-dy1-sensitivity.v1"),
    ("target", "T1/B2"), ("frames", [42., 43, 44, 45]), ("frames", [42, 43, 44]),
    ("primary_group", "C1C2"), ("equivalence_tolerance_native_px", True),
    ("equivalence_tolerance_native_px", 2), ("budget.threads", True), ("budget.threads", 3),
    ("budget.max_image_evaluations", 107), ("budget.max_candidate_states", 36.),
    ("budget.optimizer_iterations", False), ("budget.time_limit_s", 901),
    ("budget.image_size", [320., 180]), ("budget.image_size", [640, 180])])
def test_unregistered_identity_or_inconsistent_budget_is_rejected(path, value):
    p = protocol()
    parts = path.split(".")
    container = p if len(parts) == 1 else p[parts[0]]
    container[parts[-1]] = value
    with pytest.raises(ValueError):
        validate_protocol(p)


def plane_fixture():
    sampling = {"frame_count_per_camera": 2}
    payload = {"schema": "nrel-dynamic-rigid-plane.v1", "target": "T1/B1", "coordinate_frame": "world",
               "sampling": sampling, "condition": "IDEAL_SHARED_RIGID_PLANE_ASSISTANCE",
               "structural_tip_or_flexible_truth_included": False,
               "records": [{"frame_id": i, "sim_time_s": 120 + (i + .5) / 20,
                            "c_world_m": [0, 0, 0], "n_world_unit": [1, 0, 0]} for i in range(2)]}
    moves = {i: {"sim_time_s": record["sim_time_s"]} for i, record in enumerate(payload["records"])}
    return payload, {"sampling": sampling}, moves


def sign(payload):
    raw = {k: v for k, v in payload.items() if k != "payload_sha256"}
    payload["payload_sha256"] = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return payload


def test_plane_signature_and_exact_sample_time_are_checked():
    payload, data, moves = plane_fixture()
    assert set(checked_planes(sign(payload), data, moves)) == {0, 1}
    bad = copy.deepcopy(payload)
    bad["records"][0]["sim_time_s"] += .001
    with pytest.raises(ValueError, match="time mismatch"):
        checked_planes(sign(bad), data, moves)
    bad = copy.deepcopy(payload)
    bad["records"][0]["c_world_m"] = [1, 0, 0]
    with pytest.raises(ValueError, match="signature"):
        checked_planes(bad, data, moves)
    bad = copy.deepcopy(payload)
    bad["records"][1]["frame_id"] = 0
    with pytest.raises(ValueError, match="duplicate"):
        checked_planes(sign(bad), data, moves)
    bad = copy.deepcopy(payload)
    bad["records"][0]["n_world_unit"] = [0, 0, 0]
    with pytest.raises(ValueError, match="plane geometry"):
        checked_planes(sign(bad), data, moves)
