"""Truth-free tests of the actual reversible Greville model and its domain."""
import pytest
import torch

from wfrl.nrel_reconstruction.greville_model import GrevilleBladeModel
from wfrl.nrel_reconstruction.model import BladeModel


def original(dtype=torch.float64, seed=None, controls=7):
    model = BladeModel(interpolation="bspline", dtype=dtype, control_stations=controls)
    if seed is not None:
        rng = torch.Generator().manual_seed(seed)
        with torch.no_grad():
            model.center_raw.copy_(torch.rand(model.center_raw.shape, generator=rng, dtype=dtype) * 2.4 - 1.2)
            model.chord_raw.copy_(torch.rand(model.chord_raw.shape, generator=rng, dtype=dtype) * 2.4 - 1.2)
    return model


@pytest.mark.parametrize("dtype,atol", [(torch.float64, 2e-14), (torch.float32, 3e-6)])
@pytest.mark.parametrize("seed", [None, 1701, 1702, 1703])
def test_initial_and_random_feasible_models_preserve_vertices_penalties_and_buffers(dtype, atol, seed):
    old = original(dtype, seed)
    new = GrevilleBladeModel(old)
    for name, value in old.named_buffers():
        assert torch.equal(getattr(new, name), value)
    assert torch.equal(new.chord_raw, old.chord_raw)
    assert torch.allclose(new(), old(), rtol=0, atol=atol)
    for key, penalty in old.regularization().items():
        assert torch.allclose(new.regularization()[key], penalty, rtol=0, atol=atol)
    old_center, old_chord = old.control_values()
    center, chord = new.control_values()
    assert torch.allclose(center, old_center, rtol=0, atol=atol)
    assert torch.equal(chord, old_chord)
    assert torch.equal(center[0], torch.zeros(2, dtype=dtype))
    assert new.check_feasible()
    assert torch.allclose(new.bound_margins()["center_m"], 5 - old_center[1:].abs(), rtol=0, atol=atol)
    assert torch.equal(new.bound_margins()["chord_log"], .35 - old_chord.abs())


def test_optimization_variables_are_independent_greville_samples_with_fixed_normalization():
    model = GrevilleBladeModel(original())
    assert set(dict(model.named_parameters())) == {"greville_raw", "chord_raw"}
    assert sum(parameter.numel() for parameter in model.parameters()) == 19
    assert not hasattr(model, "center_raw")
    centers = model.greville_centers().detach().clone()
    with torch.no_grad():
        model.greville_raw[2, 1] += .02
    changed = model.greville_centers() - centers
    expected = torch.zeros_like(changed)
    expected[3, 1] = .1
    assert torch.allclose(changed, expected, rtol=0, atol=1e-15)
    center, chord = model.control_values()
    assert torch.allclose(model.greville_basis @ center + model.greville_offset(chord),
                          model.greville_centers(), rtol=0, atol=2e-15)
    # Keeping free G fixed while changing ell changes reference coefficients;
    # the root G follows chord while the root reference-axis coefficient is 0.
    free = model.greville_centers()[1:].detach().clone()
    before_center = center.detach().clone()
    with torch.no_grad():
        model.chord_raw[0] = .4
    assert torch.equal(model.greville_centers()[1:], free)
    assert not torch.equal(model.greville_centers()[0], centers[0])
    assert not torch.equal(model.control_values()[0][1:], before_center[1:])
    assert torch.equal(model.control_values()[0][0], torch.zeros(2, dtype=torch.float64))


def test_inverse_chain_gradient_matches_original_for_geometry_and_original_penalties():
    old = original(seed=4105)
    new = GrevilleBladeModel(old)
    weights = torch.linspace(-.3, .7, old().numel(), dtype=torch.float64).reshape(-1, 3)

    def objective(model):
        vertices = model()
        penalties = model.regularization()
        return (vertices * weights).sin().mean() + .17 * penalties["relative_parameter_change"] + .23 * penalties["span_smoothness"]

    old_gradients = torch.autograd.grad(objective(old), tuple(old.parameters()))
    new_gradients = torch.autograd.grad(objective(new), tuple(new.parameters()))
    encoded = new.coordinates_from_controls(*old.control_values())
    chained = torch.autograd.grad(encoded, tuple(old.parameters()), grad_outputs=new_gradients)
    for expected, actual in zip(old_gradients, chained):
        assert torch.allclose(expected, actual, rtol=2e-12, atol=2e-14)


def test_inverse_map_passes_independent_finite_difference_gradcheck():
    model = GrevilleBladeModel(original(seed=831))
    inputs = tuple(parameter.detach().requires_grad_() for parameter in model.parameters())
    assert torch.autograd.gradcheck(model.controls_from_coordinates, inputs,
                                   eps=1e-6, atol=1e-8, rtol=1e-6)


def test_bounds_couple_center_and_chord_and_do_not_clamp_invalid_values():
    model = GrevilleBladeModel(original())
    center = torch.zeros((7, 2), dtype=torch.float64)
    center[1:, 1] = 4.99
    chord = torch.full((7,), .35 * torch.tanh(torch.tensor(1.)).item(), dtype=torch.float64)
    model.set_control_values(center, chord)
    assert model.check_feasible()
    # Some normalized G samples exceed 1 yet are valid in the original domain.
    assert bool((model.greville_raw.abs() > 1).any())
    kept = model.greville_raw.detach().clone()
    with torch.no_grad():
        model.chord_raw.fill_(-1)
    assert torch.equal(model.greville_raw, kept)
    assert not model.check_feasible()
    assert float(model.bound_margins()["center_m"].detach().min()) < 0
    for action in (model.control_values, model.forward, model.regularization):
        with pytest.raises(ValueError, match="coupled physical bounds"):
            action()
    assert torch.equal(model.greville_raw, kept)


def test_independent_unit_box_is_not_the_original_coupled_domain():
    model = GrevilleBladeModel(original())
    with torch.no_grad():
        model.greville_raw[:, 0] = torch.tensor([.9, -.9, .9, -.9, .9, -.9], dtype=torch.float64)
    assert bool((model.greville_raw.abs() < 1).all())
    assert not model.check_feasible()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_parameters_fail_feasibility_and_forward(invalid):
    model = GrevilleBladeModel(original())
    with torch.no_grad():
        model.chord_raw[0] = invalid
    assert not model.check_feasible()
    with pytest.raises(ValueError, match="finite"):
        model()


def test_strict_boundary_root_and_shape_rejections_are_atomic():
    model = GrevilleBladeModel(original())
    before = {name: parameter.detach().clone() for name, parameter in model.named_parameters()}
    center, chord = (value.detach().clone() for value in model.control_values())
    for bad_center, bad_chord, match in [
        (center.clone().index_fill(0, torch.tensor([1]), 5.), chord, "bounds"),
        (center, torch.full_like(chord, .35), "bounds"),
        (center.clone().index_fill(0, torch.tensor([0]), .01), chord, "root"),
        (center[1:], chord, "shapes"),
    ]:
        with pytest.raises(ValueError, match=match):
            model.set_control_values(bad_center, bad_chord)
        assert all(torch.equal(parameter, before[name]) for name, parameter in model.named_parameters())
    with pytest.raises(ValueError, match="shapes"):
        model.controls_from_coordinates(model.greville_raw[:-1], model.chord_raw)


def test_four_control_case_and_float32_buffers_promoted_without_rebuilding_template():
    reference = original(torch.float32, seed=541, controls=4).double()
    model = GrevilleBladeModel(reference)
    assert torch.equal(model.weights, reference.weights)
    assert torch.allclose(model(), reference(), rtol=0, atol=5e-15)
    assert model.metadata()["parameterization"] == "reversible_greville_geometric_centers_v1"
    with pytest.raises(ValueError, match="B-spline"):
        GrevilleBladeModel(BladeModel())
