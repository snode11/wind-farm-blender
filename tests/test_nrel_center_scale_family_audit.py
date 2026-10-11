"""Analytic invariants for the truth-free family/regularization audit."""
import numpy as np
import hashlib
import json
import pytest
import torch

from wfrl.nrel_reconstruction.center_scale_family_audit import (
    audit, centroid_offset, fields, greville_audit, penalties, perturb_log, rooted_projection, run,
)
from wfrl.nrel_reconstruction.model import BladeModel


def fixture_model():
    model = BladeModel(interpolation="bspline")
    with torch.no_grad():
        model.center_raw.copy_(torch.linspace(-.3, .2, 12).reshape(6, 2))
        model.chord_raw.copy_(torch.linspace(-.2, .3, 7))
    center, chord = model.control_values()
    return model, center.detach().numpy().astype(float), chord.detach().numpy().astype(float)


def test_original_penalty_matches_production_including_root_row():
    model, center, chord = fixture_model()
    actual = penalties(center, chord)
    for key, value in model.regularization().items():
        assert actual[key] == pytest.approx(float(value.detach()), abs=2e-8)


def test_rooted_projection_recovers_exact_representable_field_and_root():
    model, center, _ = fixture_model()
    basis = model.weights.numpy().astype(float)
    delta = np.zeros((7, 2)); delta[1:, 0] = np.linspace(-.02, .01, 6)
    recovered, residual, info = rooted_projection(basis, basis @ delta, center)
    assert np.max(np.abs(recovered - delta)) < 1e-15
    assert np.max(np.abs(residual)) < 1e-15
    assert np.array_equal(recovered[0], [0., 0.])
    assert info["rank"] == 6 and not info["box_bounds_active"]


def test_infeasible_projection_is_rejected_without_relaxing_original_bounds():
    model, center, _ = fixture_model()
    basis = model.weights.numpy().astype(float)
    delta = np.zeros((7, 2)); delta[1, 0] = 10.
    with pytest.raises(ValueError, match="bounds"):
        rooted_projection(basis, basis @ delta, center)


@pytest.mark.parametrize("amplitude", [-.05, -.005, .005, .05])
def test_analytic_center_compensation_and_target_halfwidth_normalization(amplitude):
    model, center, chord = fixture_model()
    changed = perturb_log(model, chord, amplitude)
    basis, base, twist = fields(model, [9.225])
    measured = base * (np.exp(basis @ changed) - np.exp(basis @ chord)) / 2.
    assert measured[0] == pytest.approx(amplitude, abs=2e-15)
    all_basis, all_base, all_twist = fields(model, model.z.numpy())
    old_offset = centroid_offset(all_base, all_twist, all_basis, chord)
    new_offset = centroid_offset(all_base, all_twist, all_basis, changed)
    compensation = old_offset - new_offset
    before = all_basis @ center + old_offset
    after = all_basis @ center + compensation + new_offset
    assert np.max(np.abs(before - after)) < 1e-15
    assert np.array_equal(compensation[0], [0., 0.])
    _, residual, _ = rooted_projection(all_basis, compensation, center)
    # A real model field, analytically prescribed, is not exactly represented.
    assert np.max(np.abs(residual)) > 1e-6
    # Orthogonality verifies the least-squares optimum, not just a candidate.
    assert np.max(np.abs(all_basis[:, 1:].T @ residual)) < 1e-15


def test_greville_inverse_preserves_geometry_and_penalty_but_naive_forms_do_not():
    model, center, chord = fixture_model()
    result = greville_audit(model, center, chord)
    assert result["basis_rank"] == 7
    assert result["maximum_recovered_control_error_m"] < 1e-14
    assert result["maximum_recovered_ring_center_error_m"] < 1e-14
    assert result["max_regularization_pullback_error"] < 1e-14
    assert result["naive_interpolated_geometric_center_error"]["max_vector_norm_m"] > .01
    assert result["independent_absolute_center_spline_error"]["max_vector_norm_m"] > 1e-5
    assert result["independent_template_relative_center_spline_error"]["max_vector_norm_m"] > 1e-5
    regularization = result["regularization"]
    original = regularization["original_control_coefficients"]
    for name in ("naive_absolute_geometric_samples_as_controls", "naive_template_relative_geometric_samples_as_controls"):
        assert abs(original["span_smoothness"] - regularization[name]["span_smoothness"]) > 1e-5


def test_root_reference_anchor_does_not_anchor_geometric_root_for_chord0():
    model, center, chord = fixture_model()
    baseline = greville_audit(model, center, chord)
    changed = chord.copy(); changed[0] += .01
    after = greville_audit(model, center, changed)
    assert baseline["root_reference_axis_anchor_m"] == after["root_reference_axis_anchor_m"] == [0., 0.]
    assert np.linalg.norm(np.array(baseline["root_geometric_center_m"]) - after["root_geometric_center_m"]) > .001


def test_complete_audit_has_eight_predeclared_cases_and_25_ring_rows_each():
    model, _, _ = fixture_model()
    result, rows = audit(model.metadata())
    assert len(result["cases"]) == 8 and len(rows) == 200
    assert not result["conclusions"]["center_preserving_ring_chord_direction_is_exact_original_family"]
    assert not result["conclusions"]["new_fit_executed"]


def test_protocol_replay_accepts_fresh_output_and_refuses_overwrite(tmp_path, capsys):
    model, _, _ = fixture_model()
    rel = "outputs/nrel-video-single-blade/stages/02-second-run/reconstruction/model_state.json"
    state_path = tmp_path / rel
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"models": {"T1_B1": model.metadata()}}))
    protocol = tmp_path / "protocol.json"
    protocol.write_text(json.dumps({"inputs": {rel: hashlib.sha256(state_path.read_bytes()).hexdigest()}}))
    for name in ("first", "fresh_replay"):
        destination = tmp_path / name
        run(tmp_path, destination, protocol)
        result = json.loads((destination / "family_audit.json").read_text())
        before = json.loads((destination / "run_record.json").read_text())
        assert result["source_unchanged_since_run_started"]
        assert result["inputs_unchanged"]
        assert before["source_sha256"] == result["run_record"]["source_sha256"]
        with pytest.raises(FileExistsError):
            run(tmp_path, destination, protocol)
