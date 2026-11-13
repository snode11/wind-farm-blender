"""Invalid observations and failed optimization must not look like measurements."""
import copy

import numpy as np
import pytest
import torch

from wfrl.nrel_reconstruction import contour, diagnostics, evaluate, greville_experiment
from wfrl.nrel_reconstruction import model, observations, optimize, parameter_audit, renderer


def observation():
    return {"camera_id": "C1", "frame_id": 0, "split": "fit",
            "labels": np.zeros((8, 8), dtype=np.uint8),
            "K": np.array([[8., 0, 3.5], [0, 8., 3.5], [0, 0, 1.]]),
            "T_camera_cv_from_world": np.eye(4), "T_world_from_blade_root": np.eye(4)}


def triangle():
    return (torch.tensor([[-1., -1., 4.], [1., -1., 4.], [0., 1., 4.]]),
            torch.tensor([[0, 1, 2]]), torch.tensor(observation()["K"], dtype=torch.float32),
            torch.eye(4), torch.zeros((8, 8), dtype=torch.uint8))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), .5])
def test_silhouette_rejects_invalid_label_values(bad):
    with pytest.raises(ValueError, match="labels|Labels"):
        renderer.silhouette_loss(torch.full((2, 2), .5), torch.full((2, 2), bad))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_silhouette_rejects_nonfinite_predictions_even_in_unknown_pixels(bad):
    with pytest.raises(ValueError, match="finite"):
        renderer.silhouette_loss(torch.tensor([[.5, bad]]), torch.tensor([[1, 2]]))


@pytest.mark.parametrize("field", ["K", "T_camera_cv_from_world", "T_world_from_blade_root"])
def test_optimizer_rejects_non_pinhole_or_non_homogeneous_calibration(field):
    item = observation()
    item[field][-1, 0] = .25
    with pytest.raises(ValueError, match="pinhole|rigid"):
        optimize._prepare([item], (8, 8), "cpu")


def test_optimizer_rejects_empty_label_image():
    with pytest.raises(ValueError, match="labels|Labels"):
        optimize._load_labels({"labels": np.empty((0, 8), dtype=np.uint8)})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), .5])
def test_contour_rejects_nonbinary_validity_before_boolean_conversion(bad):
    vertices, faces, k, eye, labels = triangle()
    valid = torch.ones((8, 8))
    valid[3, 3] = bad
    with pytest.raises(ValueError, match="binary|finite"):
        contour.projected_contour_candidates(vertices, faces, k, eye, eye, (8, 8), labels, valid)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_contour_rejects_nonfinite_observed_points(bad):
    vertices, faces, k, eye, labels = triangle()
    with pytest.raises(ValueError, match="finite"):
        contour.projected_contour_loss(vertices, faces, k, eye, eye, (8, 8), labels,
                                       [[3., 2.], [bad, 3.]])


@pytest.mark.parametrize("field", ["uncertainty_px", "robust_delta_px", "distance_scale_px"])
def test_contour_rejects_nonfinite_loss_scales(field):
    vertices, faces, k, eye, labels = triangle()
    with pytest.raises(ValueError, match="finite"):
        contour.projected_contour_loss(vertices, faces, k, eye, eye, (8, 8), labels,
                                       [[3., 2.]], **{field: float("nan")})


@pytest.mark.parametrize("render", ["soft", "hard", "contour"])
def test_projection_rejects_nonfinite_geometry_instead_of_empty_mask(render):
    vertices, faces, k, eye, labels = triangle()
    vertices[:, 0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        if render == "soft":
            renderer.soft_silhouette(vertices, faces, k, eye, eye, (8, 8))
        elif render == "hard":
            diagnostics.hard_silhouette(vertices.numpy(), faces.numpy(), k.numpy(), eye.numpy(), eye.numpy(), (8, 8))
        else:
            contour.projected_contour_candidates(vertices, faces, k, eye, eye, (8, 8), labels)


@pytest.mark.parametrize("field", ["sigma_px", "near_m"])
def test_soft_projection_rejects_nonfinite_scales(field):
    vertices, faces, k, eye, _ = triangle()
    with pytest.raises(ValueError, match="finite"):
        renderer.soft_silhouette(vertices, faces, k, eye, eye, (8, 8), **{field: float("nan")})


@pytest.mark.parametrize("module", [optimize, greville_experiment], ids=["adam", "coordinate_adam"])
@pytest.mark.parametrize("kind", ["image", "regularization"])
def test_nonfinite_objective_with_finite_gradient_aborts_before_step(monkeypatch, module, kind):
    initial = model.BladeModel(span_samples=3, ring_samples=6, control_stations=4)
    cfg = optimize.OptimizationConfig(resolutions=((8, 8),), iterations_per_stage=(1,),
                                      image_objective="projected_contour")
    monkeypatch.setattr(module, "_prepare", lambda items, *args, **kwargs: items)
    monkeypatch.setattr(module, "_image_loss", lambda m, *args: (
        m().sum() * 0 + (float("nan") if kind == "image" else 1.),
        {"contour_terms_available": torch.tensor(1.)}))
    if kind == "regularization":
        def invalid_regularization(self):
            return {"relative_parameter_change": self().sum() * 0 + float("nan"),
                    "span_smoothness": self().sum() * 0}
        monkeypatch.setattr(model.BladeModel, "regularization", invalid_regularization)
    steps = []
    monkeypatch.setattr(torch.optim.Adam, "step", lambda self: steps.append(True))
    fit = optimize._fit if module is optimize else greville_experiment.fit_coordinate
    with pytest.raises(RuntimeError, match="[Nn]onfinite.*loss|[Nn]onfinite.*objective"):
        fit(initial, [{"split": "fit"} for _ in range(6)], cfg, 1.)
    assert steps == []


def test_last_optimizer_step_cannot_return_nonfinite_raw_parameters(monkeypatch):
    initial = model.BladeModel(span_samples=3, ring_samples=6, control_stations=4)
    cfg = optimize.OptimizationConfig(resolutions=((8, 8),), iterations_per_stage=(1,))
    def corrupt_step(self):
        with torch.no_grad():
            self.param_groups[0]["params"][0].fill_(float("inf"))
    monkeypatch.setattr(torch.optim.Adam, "step", corrupt_step)
    with pytest.raises(RuntimeError, match="[Nn]onfinite.*parameter"):
        optimize._fit(initial, [observation()], cfg, 0.)


def test_all_unknown_residual_is_missing_not_zero_and_fit_aborts():
    initial = model.BladeModel(span_samples=3, ring_samples=6, control_stations=4)
    cfg = optimize.OptimizationConfig(resolutions=((8, 8),), iterations_per_stage=(1,))
    item = observation()
    item["labels"].fill(2)
    prepared = optimize._prepare([item], (8, 8), "cpu")
    residual = optimize._residuals(initial, prepared, (8, 8), cfg)[0]
    assert residual["image_loss"] is None
    assert set(residual["missing_terms"]) == {"foreground", "background"}
    with pytest.raises(RuntimeError, match="[Mm]issing.*(silhouette|image)"):
        optimize._fit(initial, [item], cfg, 1.)


@pytest.mark.parametrize("field", ["image_weight", "relative_change_weight", "smoothness_weight",
                                  "foreground_distance_weight", "sigma_px", "learning_rate"])
def test_nonfinite_optimizer_configuration_rejected(field):
    with pytest.raises(ValueError, match="finite"):
        optimize.OptimizationConfig(**{field: float("nan")})


def test_eroded_away_foreground_is_rejected_without_nan_centroid():
    rgb = np.zeros((80, 80, 3), dtype=np.uint8)
    rgb[20:60, 30:33] = 255
    with pytest.raises(ValueError, match="foreground"):
        observations.segment_rgb(rgb, "C2")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1., 256.])
def test_segmentation_rejects_invalid_rgb_before_uint8_cast(bad):
    rgb = np.full((40, 40, 3), 255., dtype=float)
    rgb[20, 20] = bad
    with pytest.raises(ValueError, match="RGB"):
        observations.segment_rgb(rgb, "C2")


@pytest.mark.parametrize("faces", [[[0, 1.5, 2]], [[0, float("nan"), 2]]])
def test_mesh_rejects_nonintegral_face_indices(faces):
    with pytest.raises(ValueError, match="face|Face"):
        evaluate.Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces)


@pytest.mark.parametrize("reader", [diagnostics.read_mesh_ply, evaluate.read_ply])
def test_ply_negative_element_count_is_corruption(tmp_path, reader):
    path = tmp_path / "invalid.ply"
    path.write_text("ply\nformat ascii 1.0\nelement vertex -1\nproperty float x\nproperty float y\nproperty float z\n"
                    "element face 0\nproperty list uchar int vertex_indices\nend_header\n")
    with pytest.raises(ValueError, match="count|size|negative|Negative"):
        reader(path)


def test_diagnostic_ply_missing_declared_vertices_is_not_an_empty_model(tmp_path):
    path = tmp_path / "truncated.ply"
    path.write_text("ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\n"
                    "element face 0\nproperty list uchar int vertex_indices\nend_header\n")
    with pytest.raises(ValueError, match="[Tt]runcated|vertices"):
        diagnostics.read_mesh_ply(path)


@pytest.mark.parametrize("samples,stations", [([np.nan], [0, 1]), ([.5], [0, np.inf]),
                                           ([0], [0, 0]), ([0], [0])])
def test_linear_interpolation_rejects_nonfinite_or_degenerate_stations(samples, stations):
    with pytest.raises(ValueError, match="finite|station"):
        model.linear_weights(np.asarray(samples), np.asarray(stations))


@pytest.mark.parametrize("field", ["center_offsets_m", "control_chord_log_scale"])
def test_frozen_parameter_restore_rejects_nan(field):
    metadata = copy.deepcopy(model.BladeModel().metadata())
    if field == "center_offsets_m":
        metadata[field][1][0] = float("nan")
    else:
        metadata[field][1] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        parameter_audit.restore_model(metadata)


@pytest.mark.parametrize("reference_time", [float("nan"), float("inf"), None])
def test_invalid_reference_time_rejected_before_fitting(tmp_path, monkeypatch, reference_time):
    def unexpected_fit(*args, **kwargs):
        pytest.fail("Invalid reference time reached optimizer")
    monkeypatch.setattr(optimize, "_fit", unexpected_fit)
    monkeypatch.setattr(optimize, "backend_check", lambda: {"passed": True})
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="reference|Reference"):
        optimize.reconstruct([observation()], output, {"t_ref_sim_time_s": reference_time})
    assert not output.exists()


@pytest.mark.parametrize("faces", [[[0, 1.5, 2]], [[0, float("nan"), 2]], [[0, 3, 2]], [[0, 1]]])
def test_model_export_rejects_invalid_topology_without_replacing_file(tmp_path, faces):
    path = tmp_path / "model.ply"
    path.write_bytes(b"previous valid mesh")
    with pytest.raises(ValueError, match="face|Face"):
        model.write_ply(path, [[0, 0, 0], [1, 0, 0], [0, 1, 0]], faces)
    assert path.read_bytes() == b"previous valid mesh"


def test_single_surface_sample_has_explicit_missing_standard_error():
    mesh = evaluate.Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    result = evaluate._distance_stats(mesh, mesh, 1, 10)
    assert result["mean_m"] == pytest.approx(0.)
    assert result["standard_error_mean_m"] is None
    assert result["sample_count"] == 1


def test_surface_sampling_is_repeatable_without_mutating_global_rng():
    mesh = evaluate.Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    before = np.random.get_state()
    first, _ = evaluate.sample_surface(mesh, 30, 123)
    second, _ = evaluate.sample_surface(mesh, 30, 123)
    assert np.array_equal(first, second)
    after = np.random.get_state()
    assert before[0] == after[0] and np.array_equal(before[1], after[1]) and before[2:] == after[2:]
