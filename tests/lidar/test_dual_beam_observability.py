"""Independent analytic observability and actual first-hit contracts."""
import json
from pathlib import Path

import numpy as np
import pytest

from scripts.lidar import audit_dual_beam_observability as audit
from wfrl.lidar.dual_beam import PairLimits
from wfrl.lidar.dual_beam_replay import digest


def analytic_forward(amplitudes):
    # Two declared observation positions, three independent polynomial DOFs.
    matrix = audit.test_shape_weights([.25, .75], 1.)
    ranges = np.array([10., 20.])+matrix@amplitudes
    observations = [dict(slant_range_m=float(r), first_object='blade', blade_id=1,
                         triangle_id=i, valid=True, range_branch='far')
                    for i, r in enumerate(ranges)]
    return dict(observations=observations, clearance_m=float(5.+np.sum(amplitudes)))


def test_polynomials_are_independent_and_clamped_at_attachment_root():
    weights = audit.test_shape_weights([1.5, 20., 40., 63.], 63., 1.5)
    assert weights[0] == pytest.approx(np.zeros(3))
    assert weights[-1] == pytest.approx(np.ones(3))
    assert np.linalg.matrix_rank(weights[1:]) == 3
    h = 1e-6
    derivative = (audit.test_shape_weights([1.5+h], 63., 1.5)
                  -audit.test_shape_weights([1.5], 63., 1.5))/h
    assert abs(derivative).max() < 1e-9
    with pytest.raises(ValueError):
        audit.test_shape_weights([0.], 63., 1.5)


def test_central_jacobian_matches_analytic_two_ranges_and_clearance():
    result = audit.finite_difference(analytic_forward)
    assert result['valid']
    assert np.asarray(result['range_jacobian_m_per_m']) == pytest.approx(
        audit.test_shape_weights([.25, .75], 1.), abs=2e-12)
    assert result['clearance_gradient_m_per_m'] == pytest.approx([1.]*3)
    half = audit.finite_difference(analytic_forward, audit.FD_STEP_M/2)
    assert np.asarray(half['range_jacobian_m_per_m']) == pytest.approx(
        np.asarray(result['range_jacobian_m_per_m']), abs=5e-12)


def test_two_ranges_leave_clearance_sensitive_null_direction():
    result = audit.finite_difference(analytic_forward)
    diagnostic = audit.nullspace_diagnostic(result['range_jacobian_m_per_m'],
                                          result['clearance_gradient_m_per_m'])
    assert diagnostic['rank'] == 2 and diagnostic['nullity'] == 1
    direction = np.asarray(diagnostic['unit_coefficient_null_direction'])
    assert np.linalg.norm(direction) == pytest.approx(1.)
    assert diagnostic['null_range_residual_m_per_m'] < 1e-12
    assert diagnostic['null_clearance_sensitivity_m_per_m'] > .1
    nominal, changed = analytic_forward(np.zeros(3)), analytic_forward(direction)
    assert audit.two_expected_ranges(changed) == pytest.approx(audit.two_expected_ranges(nominal))
    assert changed['clearance_m']-nominal['clearance_m'] > .1


def test_svd_metric_has_explicit_metres_and_scale_factor():
    j = audit.test_shape_weights([.25, .75], 1.)
    original = audit.nullspace_diagnostic(j, np.ones(3))
    scaled = audit.nullspace_diagnostic(j, np.ones(3), state_scale_m=2., range_scale_m=.5)
    assert np.asarray(scaled['normalized_singular_values']) == pytest.approx(
        np.asarray(original['normalized_singular_values'])*4.)
    assert scaled['maximum_null_clearance_sensitivity_m_per_m'] == pytest.approx(
        original['maximum_null_clearance_sensitivity_m_per_m'])
    assert scaled['metric']['state_scale_m'] == 2. and scaled['metric']['range_scale_m'] == .5
    with pytest.raises(ValueError):
        audit.nullspace_diagnostic(j, np.ones(3), range_scale_m=0.)


def test_rank_threshold_exposes_a_weak_second_observation():
    value = audit.nullspace_diagnostic([[1., 0., 0.], [0., 1e-12, 0.]], [0., 1., 1.])
    assert value['rank'] == 1 and value['nullity'] == 2
    assert value['maximum_null_clearance_sensitivity_m_per_m'] == pytest.approx(np.sqrt(2.))


def test_case_outputs_unknown_and_rechecks_finite_null_states():
    class ToyModel:
        def __init__(self):
            self.called = []
        def forward(self, a, phase, pitch):
            self.called.append(np.asarray(a).copy())
            return analytic_forward(a)
    model = ToyModel()
    case = audit.audit_case(model, dict(id='declared', phase_deg=180., pitch_deg=0.))
    assert case['clearance_output_status'] == 'UNKNOWN_UNVALIDATED_SHAPE_PRIOR'
    assert case['half_step_check']['passed'] and len(case['finite_null_probes']) == 2
    half = case['half_step_finite_difference']
    assert half['valid'] and half['step_m'] == audit.FD_STEP_M/2
    assert half['nominal'] == analytic_forward(np.zeros(3))
    assert len(half['columns']) == 3
    assert np.asarray(half['range_jacobian_m_per_m']) == pytest.approx(
        audit.test_shape_weights([.25, .75], 1.), abs=5e-12)
    assert half['clearance_gradient_m_per_m'] == pytest.approx([1.]*3)
    for index, column in enumerate(half['columns']):
        perturbation = np.zeros(3); perturbation[index] = audit.FD_STEP_M/2
        assert column['plus'] == analytic_forward(perturbation)
        assert column['minus'] == analytic_forward(-perturbation)
        assert not column['branch_changes']
    assert len(model.called) == 16  # 7 nominal/FD + 7 half-step + 2 finite re-hits.
    for probe in case['finite_null_probes']:
        assert probe['coefficient_l2_m'] == pytest.approx(1.)
        assert max(abs(np.asarray(probe['range_delta_m']))) < 1e-10
        assert abs(probe['clearance_delta_m']) > .1


@pytest.mark.parametrize('change', ['triangle_id', 'blade_id', 'range_branch', 'valid'])
def test_finite_difference_marks_branch_changes_and_unavailable_columns(change):
    def forward(a):
        state = analytic_forward(a)
        if a[0] > 0:
            state['observations'][0][change] = {
                'triangle_id': 99, 'blade_id': 2, 'range_branch': 'near', 'valid': False}[change]
        return state
    result = audit.finite_difference(forward)
    assert not result['valid'] and result['columns'][0]['branch_changes'] == ['plus']
    assert 'range_jacobian_m_per_m' not in result


def test_ray_model_recomputes_first_object_instead_of_moving_fixed_hit():
    blade = np.array([[-1., -1., 5.], [1., -1., 5.], [0., 1., 5.]])
    surfaces = np.stack([blade, blade+[100., 0., 0.], blade+[200., 0., 0.]])
    triangles = np.array([[0, 1, 2]])
    tower = np.array([[100., -1., 0.], [100., 1., 0.], [100., 0., 10.]])
    args = (np.array([0., 0., 10.]), np.array([0., 0., -1.]))
    nominal = audit.ray_observation(*args, surfaces, triangles, tower, triangles, PairLimits(min_range_m=.1))
    assert nominal['first_object'] == 'blade' and nominal['slant_range_m'] == 5.
    changed = surfaces.copy(); changed[0] += np.array([10., 0., 0.])
    actual = audit.ray_observation(*args, changed, triangles, tower, triangles, PairLimits(min_range_m=.1))
    assert actual['first_object'] == 'ground' and actual['slant_range_m'] == 10.
    assert not actual['valid'] and audit.branch_signature(actual) != audit.branch_signature(nominal)


def scalars():
    return dict(ShftTilt=-5., OverHang=-5., TowerHt=87.6, Twr2Shft=2.,
                **{'PreCone(1)': -2.5, 'HubRad': 1.5})


def test_zero_state_rest_frame_round_trip_avoids_double_rigid_motion(monkeypatch):
    local = np.zeros((3, 19, 3, 3)); local[..., 2] = np.linspace(1.5, 63., 19)[None, :, None]
    local[..., 0] = np.array([-.5, 0., .5])[None, None, :]
    surfaces = []
    for blade in (1, 2, 3):
        hub, axes = audit.blade_root_frame(scalars(), [0.]*6, blade, audit.REST_NACELLE)
        world = hub+local[blade-1]@axes.T
        reconstructed, residual = audit.world_to_rest_local(world, scalars(), blade)
        assert reconstructed == pytest.approx(local[blade-1], abs=1e-12) and residual < 1e-12
        surfaces.append(world.reshape(-1, 3))
    captured = []
    def capture(origin, direction, blades, *args):
        captured.append(np.asarray(blades))
        return dict(slant_range_m=None, first_object=None, blade_id=None,
                    triangle_id=None, valid=False, range_branch='non_blade')
    monkeypatch.setattr(audit, 'ray_observation', capture)
    model = audit.IndependentModel(local, audit.test_shape_weights(local[..., 2].mean(2), 63., 1.5)[:, :, None],
        np.array([[0, 1, 2]]), np.zeros((3, 3)), np.array([[0, 1, 2]]), np.array([-1., 0., 63.]),
        scalars(), np.zeros((3, 3)), np.array([[0., 0., -1.]]*3), PairLimits())
    model.forward(np.zeros(3), 0., 0.)
    assert captured[0] == pytest.approx(np.asarray(surfaces), abs=1e-12)


def test_cases_are_predeclared_and_do_not_select_on_range_validity():
    cases = audit.analytic_cases()
    assert len(cases) == 21 and len({c['id'] for c in cases}) == 21
    assert sorted({c['phase_deg'] for c in cases}) == list(audit.PHASES_DEG)
    assert sorted({c['pitch_deg'] for c in cases}) == list(audit.PITCHES_DEG)
    assert all('time_s' not in c and 'turbine_id' not in c for c in cases)


def test_loader_never_accesses_dynamic_archives_or_source_geometry(tmp_path, monkeypatch):
    source = tmp_path/'source'; source.mkdir()
    s = scalars()
    local = np.zeros((3, 19, 3, 3)); local[..., 2] = np.linspace(1.5, 63., 19)[None, :, None]
    blades = []
    for blade in (1, 2, 3):
        hub, axes = audit.blade_root_frame(s, [0.]*6, blade, audit.REST_NACELLE)
        blades.append(hub+local[blade-1]@axes.T)
    np.savez(source/'reference-surfaces.npz', blades=blades, triangles=np.array([[0, 1, 2]]),
             tower=np.zeros((3, 3)), tower_triangles=np.array([[0, 1, 2]]))
    (source/'deflection-t1.json').write_text(json.dumps({'scalars': s}))
    (source/'blade-reference.json').write_text(json.dumps({'tip_local_m': [-1., 0., 63.],
        'reference_state': 'independent zero-load stationary solve'}))
    files = {name: digest(source/name) for name in
             ('reference-surfaces.npz', 'blade-reference.json', 'deflection-t1.json')}
    (source/'manifest.json').write_text(json.dumps({'schema': 'wfrl.farm-flex-review.v3', 'status': 'REVIEW_ONLY',
        'reference_frame': 'independent zero-load stationary solve', 'files': files}))
    calibration = tmp_path/'calibration.json'
    calibration.write_text(json.dumps(dict(schema='wfrl.dual-beam-calibration.v1', status='TEST',
        origins_m=[[0., 0., 10.]]*3, angles_deg=[10., 12., 14.], common_origin_approximation=True,
        range_m=[5., 100.], min_point_separation_m=.25, min_orientation_projection_m=.01,
        effective_length_m=63., measurement_hold_s=1., alarm_hold_s=1., expected_window_half_angle_deg=15.)))
    original = audit.np.load
    accessed = []
    def guarded(path, *args, **kwargs):
        accessed.append(Path(path).name)
        assert Path(path).name == 'reference-surfaces.npz'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(audit.np, 'load', guarded)
    model, inputs = audit.load_independent_model(source, calibration)
    assert accessed == ['reference-surfaces.npz'] and model.local_blades.shape == (3, 19, 3, 3)
    assert 'geometry.npz' in inputs['excluded_inputs']
    assert not hasattr(model, 'transforms') and not hasattr(model, 'true_tip')
    # A source without any dynamic files is sufficient for this diagnostic.
    assert not (source/'geometry.npz').exists() and not (source/'data.json').exists()
