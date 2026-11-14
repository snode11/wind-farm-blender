"""Independent invariants of the optional smooth blade parameterization."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")

from wfrl.nrel_reconstruction.model import BladeModel, bspline_knots, bspline_weights


def test_clamped_basis_has_nonnegative_partition_and_endpoint_interpolation():
    z = np.linspace(0, 61.5, 1001)
    weights = bspline_weights(z, 7)
    assert weights.shape == (1001, 7)
    assert weights.min() >= -1e-15
    assert np.allclose(weights.sum(axis=1), 1.0, atol=1e-14)
    assert np.array_equal(weights[0], [1, 0, 0, 0, 0, 0, 0])
    assert np.array_equal(weights[-1], [0, 0, 0, 0, 0, 0, 1])


def test_first_and_second_derivatives_are_continuous_at_simple_interior_knots():
    # Arbitrary coefficients exercise both bend components and chord log-ratio.
    controls = np.array([[0.0, 0.0, 0.0], [1.2, -0.8, 0.1], [-0.7, 1.1, -0.2],
                         [2.2, -1.4, 0.3], [0.4, 0.8, 0.2], [-1.0, 1.5, -0.1], [0.8, -0.2, 0.0]])
    h = 1e-3
    for knot in np.unique(bspline_knots(7))[1:-1]:
        positions = np.array([knot - 3 * h, knot - 2 * h, knot - h, knot,
                              knot + h, knot + 2 * h, knot + 3 * h])
        values = bspline_weights(positions, 7) @ controls
        derivative_left = (3 * values[3] - 4 * values[2] + values[1]) / (2 * h)
        derivative_right = (-3 * values[3] + 4 * values[4] - values[5]) / (2 * h)
        curvature_left = (values[3] - 2 * values[2] + values[1]) / h ** 2
        curvature_right = (values[3] - 2 * values[4] + values[5]) / h ** 2
        assert np.allclose(derivative_left, derivative_right, atol=1e-7)
        assert np.allclose(curvature_left, curvature_right, atol=2e-5)


def test_zero_initial_parameters_are_bitwise_identical_to_linear_v1():
    linear = BladeModel()
    smooth = BladeModel(interpolation="bspline")
    assert torch.equal(linear(), smooth())
    assert torch.equal(linear.faces, smooth.faces)
    assert torch.equal(linear.base_chord, smooth.base_chord)
    assert torch.equal(linear.twist, smooth.twist)
    assert torch.equal(linear.thickness_ratio, smooth.thickness_ratio)
    # Defaults retain the old linear behavior and no added smoothing penalty.
    assert linear.interpolation == "linear"
    assert set(linear.regularization()) == {"relative_parameter_change", "span_smoothness"}


def test_root_anchor_and_positive_bounded_chord_for_arbitrary_parameters():
    model = BladeModel(span_samples=151, ring_samples=12, interpolation="bspline", dtype=torch.float64)
    with torch.no_grad():
        model.center_raw.copy_(torch.tensor([[20., -20], [-20, 20], [5, 5], [-5, -5], [4, -4], [-4, 4]], dtype=torch.float64))
        model.chord_raw.copy_(torch.tensor([-20, 20, -4, 4, -6, 6, -20], dtype=torch.float64))
    centre, chord_log = model.control_values()
    interpolated_centre = model.weights @ centre
    interpolated_log = model.weights @ chord_log
    assert torch.equal(interpolated_centre[0], torch.zeros(2, dtype=torch.float64))
    assert bool((interpolated_centre.abs() <= 5.0 + 1e-12).all())
    assert bool((interpolated_log.abs() <= 0.35 + 1e-12).all())
    assert bool((model.base_chord * torch.exp(interpolated_log) > 0).all())
    assert bool(torch.isfinite(model()).all())
    assert torch.equal(model()[:, 2].unique(), model.z)


def test_smooth_model_has_finite_parameter_gradient_and_same_primary_penalties():
    linear = BladeModel(dtype=torch.float64)
    smooth = BladeModel(interpolation="bspline", dtype=torch.float64)
    with torch.no_grad():
        offsets = torch.linspace(-0.2, 0.3, 12, dtype=torch.float64).reshape(6, 2)
        chords = torch.linspace(-0.3, 0.4, 7, dtype=torch.float64)
        for model in (linear, smooth):
            model.center_raw.copy_(offsets)
            model.chord_raw.copy_(chords)
    old, new = linear.regularization(), smooth.regularization()
    assert all(torch.equal(old[name], new[name]) for name in old)
    objective = smooth().square().mean() + sum(new.values())
    objective.backward()
    assert all(parameter.grad is not None and bool(torch.isfinite(parameter.grad).all())
               and bool((parameter.grad.abs() > 1e-10).any()) for parameter in smooth.parameters())
    metadata = smooth.metadata()
    assert metadata["interpolation"] == "bspline" and metadata["interpolation_degree"] == 3
    assert len(metadata["basis_knots_m"]) == 11


def test_bspline_input_contract_and_four_control_cubic_case():
    with pytest.raises(ValueError, match="four"):
        BladeModel(control_stations=3, interpolation="bspline")
    with pytest.raises(ValueError, match="interpolation"):
        BladeModel(interpolation="cubic_unknown")
    weights = bspline_weights(np.linspace(0, 61.5, 20), 4)
    assert np.allclose(weights.sum(axis=1), 1.0)
