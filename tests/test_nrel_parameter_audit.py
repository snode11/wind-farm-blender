"""Synthetic correctness tests for frozen forward diagnostics."""
import numpy as np
import pytest
import torch

from wfrl.nrel_reconstruction.model import BladeModel
from wfrl.nrel_reconstruction.parameter_audit import (
    contour_diagnostic, local_basis, nearest_segments, normalization, perspective_attribute,
    perturb_combination, perturb_model, restore_model, robust_values,
)


def test_export_restore_keeps_nonzero_bounded_bspline_mesh():
    model = BladeModel(interpolation="bspline")
    with torch.no_grad():
        model.center_raw.copy_(torch.linspace(-.4, .3, model.center_raw.numel()).reshape_as(model.center_raw))
        model.chord_raw.copy_(torch.linspace(-.2, .3, len(model.chord_raw)))
    restored = restore_model(model.metadata())
    assert torch.allclose(model(), restored(), atol=1e-6, rtol=0)
    assert torch.equal(model.faces, restored.faces)
    assert not restored.center_raw.requires_grad


@pytest.mark.parametrize("amplitude", [-.05, -.02, .02, .05])
def test_physical_perturbations_normalize_target_and_keep_root_fixed(amplitude):
    model = restore_model(BladeModel(interpolation="bspline").metadata())
    norm = normalization(model)
    basis = local_basis(model)
    for axis, parameter in enumerate(("center_x", "center_y")):
        perturbed = perturb_model(model, parameter, amplitude, norm)
        center, _ = perturbed.control_values()
        assert abs(float(basis @ center.numpy()[:, axis]) - amplitude) < 1e-7
        assert torch.equal(center[0], torch.zeros(2))
        assert torch.equal(perturbed.z, model.z)
    scaled = perturb_model(model, "chord_halfwidth", amplitude, norm)
    _, log_scale = scaled.control_values()
    chord = norm["continuous_field_chord_at_target_m"] * np.exp(basis @ log_scale.numpy())
    assert abs((chord - norm["continuous_field_chord_at_target_m"]) / 2 - amplitude) < 1e-7
    # The full 3D cross-section relative to its fixed center scales uniformly;
    # absolute thickness is coupled to chord, while z and t/c remain fixed.
    a, b = model().reshape(25, 12, 3), scaled().reshape(25, 12, 3)
    ratio = torch.exp(model.weights @ log_scale)
    assert torch.allclose(b[:, :, :2], a[:, :, :2] * ratio[:, None, None], atol=1e-6)
    assert torch.equal(model.thickness_ratio, scaled.thickness_ratio)


def test_perspective_span_interpolation_uses_inverse_depth():
    # Screen midpoint between depth 2 and depth 6 lies 1/4 along the 3D edge.
    z, depth = perspective_attribute(torch.tensor(.5), 0., 12., 2., 6.)
    assert float(z) == pytest.approx(3.)
    assert float(depth) == pytest.approx(3.)


@pytest.mark.parametrize("amplitude", [-.05, -.02, .02, .05])
def test_fixed_combination_keeps_physical_components_and_bounds(amplitude):
    model = restore_model(BladeModel(interpolation="bspline").metadata())
    norm = normalization(model); basis = local_basis(model)
    direction = np.array([.78, .54, -.31]); direction /= np.linalg.norm(direction)
    combined = perturb_combination(model, direction, amplitude, norm)
    center, chord_log = combined.control_values()
    center_at_z = basis @ center.numpy()
    halfwidth_change = norm['continuous_field_chord_at_target_m'] * (np.exp(basis @ chord_log.numpy()) - 1) / 2
    assert np.allclose([*center_at_z, halfwidth_change], direction*amplitude, atol=1e-7)
    assert torch.equal(center[0], torch.zeros(2))
    assert torch.equal(combined.z, model.z)
    assert torch.equal(combined.thickness_ratio, model.thickness_ratio)
    with pytest.raises(ValueError, match="bounds"):
        perturb_combination(model, np.array([1., 0., 0.]), 6., norm)
    with pytest.raises(ValueError, match="unit"):
        perturb_combination(model, np.array([1., 1., 0.]), .02, norm)


def test_chord_change_also_moves_geometric_centroid_by_quarter_chord_change():
    model = restore_model(BladeModel(interpolation="bspline").metadata())
    changed = perturb_model(model, "chord_halfwidth", .05)
    before = model().reshape(25, 12, 3).mean(dim=1)
    after = changed().reshape(25, 12, 3).mean(dim=1)
    _, before_log = model.control_values(); _, after_log = changed.control_values()
    delta_chord = model.base_chord * (torch.exp(model.weights @ after_log) - torch.exp(model.weights @ before_log))
    expected = torch.stack((-torch.sin(model.twist)*delta_chord/4,
                             torch.cos(model.twist)*delta_chord/4, torch.zeros_like(delta_chord)), dim=1)
    assert torch.allclose(after-before, expected, atol=3e-7, rtol=0)


def test_nearest_segments_do_not_bridge_mask_gap_and_deadband_is_explicit():
    segments = torch.tensor([[[0., 0.], [1., 0.]], [[3., 0.], [4., 0.]]])
    points = torch.tensor([[2., 0.], [3.5, 1.], [0., 0.]])
    distance, index, fraction = nearest_segments(points, segments)
    assert torch.equal(distance, torch.tensor([1., 1., 0.]))
    assert int(index[1]) == 1 and float(fraction[1]) == .5
    assert torch.equal(robust_values(distance, 1.), torch.zeros(3))
    assert float(robust_values(distance, .5)[0]) > 0


def test_diagnostic_preserves_production_bidirectional_contour_objective():
    from wfrl.nrel_reconstruction.contour import projected_contour_loss
    vertices = torch.tensor([[-1.,-1.,4.],[1.,-1.,4.],[1.,1.,4.],[-1.,1.,4.],
                             [-1.,-1.,6.],[1.,-1.,6.],[1.,1.,6.],[-1.,1.,6.]], dtype=torch.float64)
    faces = torch.tensor([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                          [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
    class Cube:
        def __init__(self): self.faces = faces
        def __call__(self): return vertices
    k = torch.tensor([[40.,0.,31.5],[0.,40.,31.5],[0.,0.,1.]], dtype=torch.float64)
    eye = torch.eye(4, dtype=torch.float64)
    coordinate = torch.linspace(20.8, 40.8, 50, dtype=torch.float64)
    observed = torch.cat([torch.stack((coordinate, torch.full_like(coordinate, 21.9)), dim=-1),
                          torch.stack((coordinate, torch.full_like(coordinate, 41.9)), dim=-1)])
    labels = torch.zeros((64,64), dtype=torch.uint8)
    valid = torch.ones_like(labels, dtype=torch.bool)
    observation = dict(K=k, camera=eye, root=eye, labels=labels,
                       contour_points=observed, contour_valid=valid, contour_uncertainty_px=.1)
    measured = contour_diagnostic(Cube(), observation, (64,64))
    expected, _ = projected_contour_loss(vertices, faces, k, eye, eye, (64,64), labels,
                                         observed, valid, uncertainty_px=.1)
    assert measured['regions'][0]['image_loss'] == pytest.approx(float(expected), abs=1e-12)
    assert measured['attribution_max_projection_error_px'] < 1e-12
