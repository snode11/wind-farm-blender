"""Ideal mathematical fixtures, deliberately separate from the RGB experiment."""
import copy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
import pytest

from camera import Camera
from model import Rotor, TurbineConfig, default_state
import recon
from recon import Obs
from input_bundle import load_bundle
from surface_geometry import (chart_binding, binding_to_chart, material_point,
                              intersect_pixel, point_visibility)
import window_surface_fit
from window_surface_fit import WindowSurfaceFitter, WindowFitConfig


def fixture():
    rotor = Rotor(TurbineConfig(n_sections=10, n_ring=16, tip_radius_m=15., hub_height_m=25.))
    state = default_state(psi=0., pitch=2.)
    state[4] = .5
    q = np.array([.6, .25])
    point, _, _ = material_point(rotor, state, 0, q)
    center = point + [35., 0., 0.]
    R = np.array([[0., 1., 0.], [0., 0., -1.], [-1., 0., 0.]])
    T = np.eye(4); T[:3, :3] = R; T[:3, 3] = -R @ center
    cam = Camera('fixture', 640, 480, [[450., 0., 319.5], [0., 450., 239.5], [0., 0., 1.]], T)
    return rotor, state, cam, q


def make_fitter(rotor, states, cam, times, active=(0, 4), **kwargs):
    config = WindowFitConfig(active_state_indices=list(active), max_nfev=40,
                             prior_weight=0., acceleration_weight=0., **kwargs)
    masks = [[Obs(np.zeros((cam.H, cam.W), np.uint8))] for _ in times]
    return WindowSurfaceFitter(rotor, [[cam] for _ in times], masks, times, states, config)


def test_chart_roundtrip_nonuniform_span_seam_and_fixed_faces():
    rotor, state, _, _ = fixture()
    for span, perimeter in ((.1, .01), (.62, .348), (.9, .99999), (1., 1.00001), (.5, -.0001)):
        q = [span, perimeter]
        binding = chart_binding(rotor, q)
        assert rotor.tpl.faces()[binding['triangle_id']].tolist() == binding['vertex_indices']
        back = binding_to_chart(rotor, binding['triangle_id'], binding['barycentric_weights'])
        assert back[0] == pytest.approx(span)
        assert back[1] % 1 == pytest.approx(perimeter % 1)
        assert sum(binding['barycentric_weights']) == pytest.approx(1.)
        assert min(binding['barycentric_weights']) >= -1e-12
    p1, _, _ = material_point(rotor, state, 0, [.6, 0.])
    p2, _, _ = material_point(rotor, state, 0, [.6, 1.])
    np.testing.assert_allclose(p1, p2)


def test_ray_initialization_on_declared_template_and_occlusion_diagnostic():
    rotor, state, cam, q = fixture()
    point, normal, _ = material_point(rotor, state, 0, q)
    uv, _ = cam.project(point[None])
    hit = intersect_pixel(rotor, state, cam, uv[0], 0)
    assert hit is not None and hit['q'] is not None
    np.testing.assert_allclose(hit['position'], point, atol=1e-7)
    np.testing.assert_allclose(hit['q'], q, atol=1e-7)
    vertices, axis = rotor.forward(state)
    visible = point_visibility(rotor, vertices, axis, cam, 0, q)
    assert visible['in_frame'] and not visible['back_facing'] and not visible['occluded_by_estimated_blade']
    assert normal @ (cam.center - point) > 0
    back = point_visibility(rotor, vertices, axis, cam, 0, [.6, .75])
    assert back['back_facing'] and back['occluded_by_estimated_blade']


def test_temporal_prior_unwrap_actual_dt_preserves_normal_rotation():
    rotor, state, cam, _ = fixture()
    times = np.array([0., .07, .20, .36])
    states = np.tile(state, (4, 1)); states[:, 0] = (355. + 40. * times) % 360.
    fitter = make_fitter(rotor, states, cam, times, active=(0,))
    fitter.config.acceleration_weight = 2.
    fitter._prepare_tracks([], None)
    p = fitter.initial[:, fitter.active].reshape(-1)
    acceleration = [b[0] for b in fitter._prior_blocks(p) if b[3] == 'acceleration_prior']
    np.testing.assert_allclose(acceleration, 0., atol=1e-12)
    assert fitter.initial[-1, 0] > 360.


def test_fixed_track_residual_dimension_and_invalid_points_never_zero_cost():
    rotor, state, cam, q = fixture()
    states = np.tile(state, (3, 1)); times = [0., .1, .2]
    point, _, _ = material_point(rotor, state, 0, q)
    uv, _ = cam.project(point[None])
    track = [{'id': 'point', 'blade_id': 0, 'observations': [
        {'frame_index': f, 'camera_index': 0, 'uv': uv[0].tolist(), 'image_visible': True} for f in range(3)]}]
    fitter = make_fitter(rotor, states, cam, times, active=(0, 4))
    fitter._prepare_tracks(track, np.array([q]))
    p = np.r_[states[:, fitter.active].reshape(-1), q]
    good = fitter.residuals(p)
    wrong = p.copy(); wrong[:fitter.n_state:2] += 180.
    bad = fitter.residuals(wrong)
    assert bad.shape == good.shape
    assert np.isfinite(bad).all() and np.sum(bad ** 2) > np.sum(good ** 2) + 100.
    # Rear surface remains an observation; it is penalized instead of dropped.
    rear = p.copy(); rear[-1] = .75
    assert fitter.residuals(rear).shape == good.shape
    assert np.sum(fitter.residuals(rear) ** 2) > 100.


def test_shared_q_fit_and_q_only_retain_fixed_geometry():
    rotor, state, cam, q = fixture()
    times = [0., .1, .2]
    states = np.tile(state, (3, 1)); states[:, 0] += [0., 4., 8.]
    states[:, 2] = [1.123456789012345, 1.234567890123456, 1.345678901234567]
    observations = []
    for f, s in enumerate(states):
        point, _, _ = material_point(rotor, s, 0, q)
        uv, _ = cam.project(point[None])
        observations.append({'frame_index': f, 'camera_index': 0, 'uv': uv[0].tolist(), 'image_visible': True})
    tracks = [{'id': 'ideal', 'blade_id': 0, 'group': 'ideal', 'observations': observations}]
    fitter = make_fitter(rotor, states, cam, times, active=(0, 4))
    result = fitter.fit_material_only(states, tracks, q_initial=np.array([q + [.02, .01]]), max_nfev=40)
    np.testing.assert_array_equal(result.states, states)
    assert result.q.shape == (1, 2), 'one q for the whole three-frame trajectory'
    np.testing.assert_allclose(result.q[0], q, atol=1e-4)
    assert result.solver['final_data_sum_squared'] < result.solver['initial_data_sum_squared'] * 1e-5
    assert result.diagnostics['data_sensitivity']['prior_rows_included'] is False
    assert result.diagnostics['data_sensitivity']['state_column_count'] == 0
    assert result.diagnostics['confirmed_points_dropped_for_model_visibility'] == 0


def test_joint_fit_reports_q_eliminated_data_sensitivity():
    rotor, state, cam, q = fixture()
    times = [0., .1, .2]
    states = np.tile(state, (3, 1)); states[:, 0] += [0., 4., 8.]
    tracks = []
    for j, qq in enumerate((q, q + [.12, -.03])):
        obs = []
        for f, s in enumerate(states):
            pt, _, _ = material_point(rotor, s, 0, qq)
            uv, _ = cam.project(pt[None])
            obs.append({'frame_index': f, 'camera_index': 0, 'uv': uv[0].tolist(), 'image_visible': True})
        tracks.append({'id': str(j), 'blade_id': 0, 'observations': obs})
    initial = states.copy(); initial[:, 0] += .5
    fitter = make_fitter(rotor, initial, cam, times, active=(0,))
    result = fitter.solve(tracks, np.array([q, q + [.12, -.03]]))
    sensitivity = result.diagnostics['data_sensitivity']
    assert sensitivity['q_column_count'] == 4
    assert sensitivity['state_column_count'] == 3
    assert sensitivity['domain_or_visibility_penalty_rows_included'] is False
    assert len(sensitivity['state_singular_values_after_q_elimination']) == 3
    assert result.solver['jac_sparsity_nnz'] > 0
    assert result.solver['pixel_robustification_count'] == 1


def test_loader_allowlist_rgb_color_and_no_neighbor_truth(tmp_path):
    rotor, state, cam, _ = fixture()
    bgr = np.zeros((cam.H, cam.W, 3), np.uint8); bgr[:] = [5, 10, 200]
    cv2.imwrite(str(tmp_path / 'rgb.png'), bgr)
    cv2.imwrite(str(tmp_path / 'mask.png'), np.zeros((cam.H, cam.W), np.uint8))
    camera = cam.to_dict(); camera['T_cv_from_model'] = camera.pop('T_cv_from_world')
    camera.update(rgb='rgb.png', mask='mask.png')
    meta = {'schema': 'blade-surface-input.v1', 'extrinsic_from_frame': 'model',
            'pixel_coordinates': 'original_image_pixel_centers', 'color_order': 'RGB',
            'turbine': rotor.cfg.to_dict(), 'frames': [{'frame': 1, 't': 0., 'cameras': [camera]}]}
    (tmp_path / 'input_bundle.json').write_text(json.dumps(meta))
    (tmp_path / 'truth.json').write_text('INVALID JSON THAT MUST NEVER BE READ')
    bundle = load_bundle(tmp_path)
    np.testing.assert_array_equal(bundle.rgbs[0][0][0, 0], [200, 10, 5])
    assert [a['path'] for a in bundle.access_log] == ['input_bundle.json', 'rgb.png', 'mask.png']
    malicious = copy.deepcopy(meta); malicious['frames'][0]['cameras'][0]['rgb'] = 'truth.json'
    (tmp_path / 'input_bundle.json').write_text(json.dumps(malicious))
    with pytest.raises(ValueError, match='forbidden'):
        load_bundle(tmp_path)
    malicious['frames'][0]['cameras'][0]['rgb'] = '../outside.png'
    (tmp_path / 'input_bundle.json').write_text(json.dumps(malicious))
    with pytest.raises(ValueError, match='inside declared bundle'):
        load_bundle(tmp_path)


def test_missed_first_ray_is_an_explicit_failure():
    rotor, state, cam, _ = fixture()
    fitter = make_fitter(rotor, state[None], cam, [0.], active=(0,))
    tracks = [{'id': 'miss', 'blade_id': 0, 'observations': [
        {'frame_index': 0, 'camera_index': 0, 'uv': [1., 1.], 'image_visible': True}]}]
    with pytest.raises(ValueError, match='INITIAL_RAY_MISS'):
        fitter.solve(tracks)


def test_model_to_camera_direct_equals_world_chain_but_not_double_transform():
    rotor, state, cam_model, q = fixture()
    point, _, _ = material_point(rotor, state, 0, q)
    angle = np.radians(23.)
    T_world_model = np.array([[np.cos(angle), -np.sin(angle), 0., 11.],
                              [np.sin(angle), np.cos(angle), 0., -6.],
                              [0., 0., 1., 3.], [0., 0., 0., 1.]])
    T_camera_model = np.eye(4)
    T_camera_model[:3, :3] = cam_model.R
    T_camera_model[:3, 3] = cam_model.t
    T_camera_world = T_camera_model @ np.linalg.inv(T_world_model)
    cam_world = Camera('world-chain', cam_model.W, cam_model.H, cam_model.K, T_camera_world)
    point_world = (T_world_model @ np.r_[point, 1.])[:3]
    uv_direct, z_direct = cam_model.project(point[None])
    uv_chain, z_chain = cam_world.project(point_world[None])
    np.testing.assert_allclose(uv_direct, uv_chain, atol=1e-10)
    np.testing.assert_allclose(z_direct, z_chain, atol=1e-10)
    # a02's historical T_cv_from_world is actually this T_camera_model.
    # Multiplying T_world_model again is therefore a real, measurable error.
    uv_wrong, _ = cam_model.project(point_world[None])
    assert np.linalg.norm(uv_wrong - uv_direct) > 20.


def test_pixel_center_scaling_and_crop_affine_roundtrip():
    rotor, state, cam, q = fixture()
    point, _, _ = material_point(rotor, state, 0, q)
    uv, _ = cam.project(point[None])
    scaled = cam.scaled(.47)
    sx, sy = scaled.W / cam.W, scaled.H / cam.H
    affine_resize = np.array([[sx, 0., (sx - 1) / 2],
                              [0., sy, (sy - 1) / 2], [0., 0., 1.]])
    expected = (affine_resize @ np.r_[uv[0], 1.])[:2]
    actual, _ = scaled.project(point[None])
    np.testing.assert_allclose(actual[0], expected, atol=1e-10)
    np.testing.assert_allclose(scaled.K, affine_resize @ cam.K)
    # Crop integer offsets first, then resize in pixel-center convention.
    crop_x, crop_y = 73, 49
    crop_affine = np.array([[1., 0., -crop_x], [0., 1., -crop_y], [0., 0., 1.]])
    cropped_k = crop_affine @ cam.K
    T = np.eye(4); T[:3, :3] = cam.R; T[:3, 3] = cam.t
    cropped = Camera('crop', 500, 360, cropped_k, T)
    cropped_scaled = cropped.scaled(.61)
    cx, cy = cropped_scaled.W / cropped.W, cropped_scaled.H / cropped.H
    crop_resize = np.array([[cx, 0., (cx - 1) / 2],
                            [0., cy, (cy - 1) / 2], [0., 0., 1.]]) @ crop_affine
    observed, _ = cropped_scaled.project(point[None])
    homogeneous = crop_resize @ np.r_[uv[0], 1.]
    np.testing.assert_allclose(observed[0], homogeneous[:2], atol=1e-10)
    np.testing.assert_allclose((np.linalg.inv(crop_resize) @ np.r_[observed[0], 1.])[:2], uv[0], atol=1e-10)


def test_negative_depth_track_keeps_nonzero_finite_domain_penalty():
    rotor, state, cam, q = fixture()
    T = np.eye(4); T[:3, :3] = cam.R; T[:3, 3] = cam.t
    T[2, 3] -= 100.
    behind = Camera('behind', cam.W, cam.H, cam.K, T)
    track = [{'id': 'still_image_confirmed', 'blade_id': 0, 'observations': [
        {'frame_index': 0, 'camera_index': 0, 'uv': [319.5, 239.5], 'image_visible': True}]}]
    fitter = make_fitter(rotor, state[None], behind, [0.], active=(0,))
    fitter._prepare_tracks(track, np.array([q]))
    p = np.r_[state[0], q]
    r = fitter.residuals(p)
    assert np.isfinite(r).all() and np.sum(r ** 2) > 1e6
    assert len(fitter.point_slots) == 1


def test_supported_contour_invalid_projection_keeps_fixed_cost_slots(monkeypatch):
    rotor, state, cam, _ = fixture()
    mask = np.zeros((cam.H, cam.W), np.uint8)
    mask[50:430, 60:580] = 255
    cfg = WindowFitConfig(active_state_indices=[0], prior_weight=0., acceleration_weight=0.)
    fitter = WindowSurfaceFitter(rotor, [[cam]], [[Obs(mask)]], [0.], state[None], cfg)
    fitter._prepare_tracks([], None)
    p = np.array([state[0]])
    initial = fitter.residuals(p)
    assert fitter._model_support[0][0].any()
    original = recon.silhouettes
    def invalid_silhouette(rotor_, camera_, state_, with_rings=False):
        value = original(rotor_, camera_, state_, with_rings)
        if with_rings:
            return tuple(np.full_like(v, np.nan) for v in value)
        return np.full_like(value, np.nan)
    monkeypatch.setattr(recon, 'silhouettes', invalid_silhouette)
    invalid = fitter.residuals(p)
    assert invalid.shape == initial.shape
    assert np.isfinite(invalid).all()
    assert np.sum(invalid ** 2) > np.sum(initial ** 2)


def test_sparse_solver_uses_unbounded_phase_and_explicit_state_q_steps(monkeypatch):
    rotor, state, cam, q = fixture()
    times = [0., .1, .2]
    states = np.tile(state, (3, 1)); states[:, 0] += [0., 4., 8.]
    obs = []
    for f, s in enumerate(states):
        point, _, _ = material_point(rotor, s, 0, q)
        uv, _ = cam.project(point[None])
        obs.append({'frame_index': f, 'camera_index': 0, 'uv': uv[0].tolist(), 'image_visible': True})
    tracks = [{'id': 'ideal', 'blade_id': 0, 'observations': obs}]
    steps = [1e-3] * 13 + [2e-4, 3e-4]
    fitter = make_fitter(rotor, states, cam, times, active=(0, 4), finite_difference_relative_step=steps)
    original = window_surface_fit.least_squares
    captured = {}
    def recording_solver(*args, **kwargs):
        captured.update(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(window_surface_fit, 'least_squares', recording_solver)
    result = fitter.solve(tracks, np.array([q + [.01, .005]]))
    lower, upper = captured['bounds']
    assert np.isneginf(lower[:6:2]).all() and np.isposinf(upper[:6:2]).all()
    np.testing.assert_array_equal(lower[1:6:2], [-3.] * 3)
    np.testing.assert_array_equal(upper[1:6:2], [12.] * 3)
    np.testing.assert_array_equal(captured['diff_step'], [1e-3] * 6 + [2e-4, 3e-4])
    assert result.config['azimuth_unbounded'] is True
    assert result.solver['azimuth_bound_policy'] == 'unbounded unwrapped phase'
    assert np.isfinite(result.solver['raw_gradient_inf_norm'])
    assert result.solver['final_data_sum_squared'] < result.solver['initial_data_sum_squared']


@pytest.mark.parametrize('steps', [0., -1e-3, [1e-3] * 14, float('nan')])
def test_invalid_finite_difference_configuration_is_rejected(steps):
    rotor, state, cam, _ = fixture()
    with pytest.raises(ValueError, match='finite_difference_relative_step'):
        make_fitter(rotor, state[None], cam, [0.], finite_difference_relative_step=steps)
