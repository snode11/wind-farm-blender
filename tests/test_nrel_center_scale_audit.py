"""Synthetic algebra, fixed-center geometry, and production-backend checks."""
import numpy as np
import pytest
import torch

from wfrl.nrel_reconstruction.center_scale_audit import (
    AMPLITUDES_M, FrozenMesh, decompose_chord, evaluate_mesh, region_mask,
)
from wfrl.nrel_reconstruction.model import BladeModel
from wfrl.nrel_reconstruction.parameter_audit import contour_diagnostic, perturb_model, restore_model


@pytest.mark.parametrize("amplitude", [sign*a for a in AMPLITUDES_M for sign in (-1, 1)])
def test_analytic_decomposition_preserves_geometry_invariants(amplitude):
    source = BladeModel(interpolation="bspline")
    with torch.no_grad():
        source.center_raw.copy_(torch.linspace(-.05, .12, source.center_raw.numel()).reshape_as(source.center_raw))
        source.chord_raw.copy_(torch.linspace(-.14, .21, len(source.chord_raw)))
    model = restore_model(source.metadata())
    meshes, checks = decompose_chord(model, amplitude)
    original_a = perturb_model(model, "chord_halfwidth", amplitude)
    assert torch.equal(meshes["A"](), original_a())
    base = model().double().reshape(25, 12, 3)
    a, b, c = [meshes[k]().double().reshape_as(base) for k in ("A", "B", "C")]
    assert torch.allclose(a-base, b+c-2*base, atol=5e-7, rtol=0)
    assert torch.allclose(b.mean(1), base.mean(1), atol=5e-7, rtol=0)
    assert torch.allclose(c-c.mean(1, keepdim=True), base-base.mean(1, keepdim=True), atol=5e-7, rtol=0)
    for mesh in meshes.values():
        assert torch.equal(mesh()[:12], model()[:12])
        assert torch.equal(mesh()[-12:], model()[-12:])
        assert torch.equal(mesh()[:, 2], model()[:, 2])
        assert torch.equal(mesh.faces, model.faces)
        assert not mesh().requires_grad
    assert checks["fixed_thickness_to_chord"] and checks["fixed_twist"]
    assert checks["B_isotropic_centered_ring_scale_max_error_m"] < 5e-7


def test_zero_amplitude_and_frozen_mesh_independence():
    model = restore_model(BladeModel(interpolation="bspline").metadata())
    meshes, _ = decompose_chord(model, 0.)
    assert all(torch.equal(mesh(), model()) for mesh in meshes.values())
    with pytest.raises(ValueError, match="finite"):
        decompose_chord(model, float("nan"))
    vertices = model().clone(); frozen = FrozenMesh(vertices, model.faces)
    vertices[0, 0] += 1
    assert not torch.equal(vertices, frozen())


def test_root_band_uses_inclusive_fixed_bounds():
    z = np.array([0, 6.149, 6.15, 9.225, 12.3, 12.301, 61.5])
    expected = np.array([False, False, True, True, True, False, False])
    assert np.array_equal(region_mask(z, "root_band"), expected)
    assert np.array_equal(region_mask(z, "outside_root_band"), ~expected)


def test_forward_reuses_original_contour_distance_loss_and_fixed_observations():
    vertices = torch.tensor([[-1.,-1.,4.],[1.,-1.,4.],[1.,1.,4.],[-1.,1.,4.],
                             [-1.,-1.,6.],[1.,-1.,6.],[1.,1.,6.],[-1.,1.,6.]], dtype=torch.float64)
    faces = torch.tensor([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                          [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]])
    mesh = FrozenMesh(vertices, faces)
    k = torch.tensor([[40.,0.,31.5],[0.,40.,31.5],[0.,0.,1.]], dtype=torch.float64)
    eye = torch.eye(4, dtype=torch.float64)
    coordinate = torch.linspace(20.8, 40.8, 50, dtype=torch.float64)
    observed = torch.cat([torch.stack((coordinate, torch.full_like(coordinate, 21.9)), -1),
                          torch.stack((coordinate, torch.full_like(coordinate, 41.9)), -1)])
    labels = torch.zeros((64,64), dtype=torch.uint8)
    observation = dict(K=k, camera=eye, root=eye, labels=labels, contour_points=observed,
                       contour_valid=torch.ones_like(labels, dtype=torch.bool), contour_uncertainty_px=.1)
    base = evaluate_mesh(mesh, observation, (64,64))
    old = contour_diagnostic(mesh, observation, (64,64))
    assert base["regions"] == old["regions"]
    assert np.array_equal(base["o2c_native_px"], old["o2c_native_px"].numpy())
    assert base["branches"]["nearest_original_edge"].shape == (len(observed), 2)
    changed = evaluate_mesh(FrozenMesh(vertices+vertices.new_tensor([.02,.03,0]), faces), observation, (64,64), base["observed_z"])
    assert torch.equal(changed["observed_z"], base["observed_z"])
    assert np.array_equal(changed["observed_native_pixels"], base["observed_native_pixels"])
