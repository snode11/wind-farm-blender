"""Meaningful safety and gradient tests for the first silhouette solver."""
import json

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from wfrl.nrel_reconstruction.model import BladeModel
from wfrl.nrel_reconstruction.optimize import OptimizationConfig, backend_check, reconstruct
from wfrl.nrel_reconstruction.renderer import scale_intrinsics, silhouette_loss, soft_silhouette


def test_renderer_actual_backward_agrees_with_finite_difference():
    result = backend_check()
    assert result["passed"], result
    assert result["relative_error"] < 0.005


def test_uncertain_pixels_have_no_negative_constraint_and_classes_normalize_separately():
    prediction = torch.tensor([[0.1, 0.8, 0.9], [0.2, 0.4, 0.7]], requires_grad=True)
    labels = torch.tensor([[1, 0, 2], [1, 0, 2]])
    loss, _ = silhouette_loss(prediction, labels)
    expected = ((1 - prediction[:, 0]) ** 2).mean() + (prediction[:, 1] ** 2).mean()
    assert torch.allclose(loss, expected)
    loss.backward()
    assert torch.equal(prediction.grad[:, 2], torch.zeros(2))


def test_half_pixel_intrinsic_resize():
    K = np.array([[100., 0, 49.5], [0, 100., 29.5], [0, 0, 1]])
    actual = scale_intrinsics(K, (100, 60), (50, 30))
    assert np.allclose(actual, [[50, 0, 24.5], [0, 50, 14.5], [0, 0, 1]])


def test_near_clipping_and_offscreen_triangle_dont_create_invalid_gradients():
    vertices = torch.tensor([[-0.2, -0.2, -0.1], [0.2, -0.2, 1.0], [0, 0.2, 1.0]],
                            dtype=torch.float64, requires_grad=True)
    identity = torch.eye(4, dtype=torch.float64)
    K = torch.tensor([[10., 0, 7.5], [0, 10., 7.5], [0, 0, 1]], dtype=torch.float64)
    mask = soft_silhouette(vertices, torch.tensor([[0, 1, 2]]), K, identity, identity, (16, 16))
    mask.sum().backward()
    assert torch.isfinite(mask).all() and torch.isfinite(vertices.grad).all()
    assert mask.shape == (16, 16)


def test_blade_stays_anchored_with_positive_chord_and_fixed_span():
    model = BladeModel(span_samples=9, ring_samples=8)
    with torch.no_grad():
        model.center_raw.fill_(20)
        model.chord_raw.fill_(-20)
    centre, scale = model.control_values()
    assert torch.equal(centre[0], torch.zeros(2))
    assert torch.all(centre <= 5)
    vertices = model()
    assert float(vertices[:, 2].detach().min()) == 0 and float(vertices[:, 2].detach().max()) == 61.5
    assert torch.isfinite(vertices).all()
    assert float((model.base_chord * torch.exp(model.weights @ scale)).detach().min()) > 0


def test_reconstruct_control_and_video_image_optimization(tmp_path):
    torch.set_num_threads(2)
    model = BladeModel(span_samples=7, ring_samples=8, control_stations=4)
    # A deliberately image-derived synthetic measurement verifies solver
    # execution; it is explicitly not the NREL video reconstruction experiment.
    truth = BladeModel(span_samples=7, ring_samples=8, control_stations=4)
    with torch.no_grad():
        truth.center_raw[:, 1] = 0.10
        truth.chord_raw[:] = 0.3
    camera = torch.tensor([[0., 1, 0, 0], [0, 0, -1, 30.75], [-1., 0, 0, 80], [0, 0, 0, 1]])
    K = torch.tensor([[80., 0, 15.5], [0, 80., 35.5], [0, 0, 1]])
    identity = torch.eye(4)
    with torch.no_grad():
        target = soft_silhouette(truth(), truth.faces, K, camera, identity, (32, 72))
    labels = (target.numpy() > 0.5).astype(np.uint8)
    labels[:2] = 2
    observation = dict(camera_id="C1", frame_id=0, K=K.numpy(),
                       T_camera_cv_from_world=camera.numpy(), T_world_from_blade_root=identity.numpy(),
                       labels=labels, sim_time_s=120.0)
    heldout = {**observation, "frame_id": 1, "split": "heldout"}
    result = reconstruct([observation, heldout], tmp_path,
                         {"t_ref_sim_time_s": 120.0},
                         OptimizationConfig(resolutions=((32, 72),), iterations_per_stage=(6,),
                                            span_samples=7, ring_samples=8, control_stations=4,
                                            batch_size=1, threads=2, learning_rate=0.05))
    assert result["backend_check"]["passed"]
    state = json.loads((tmp_path / "model_state.json").read_text())
    assert state["models"]["prior_only"]["center_offsets_m"] == state["models"]["initial_template"]["center_offsets_m"]
    assert state["models"]["T1_B1"]["center_offsets_m"] != state["models"]["initial_template"]["center_offsets_m"]
    optimization = json.loads((tmp_path / "optimization.json").read_text())
    initial = optimization["residuals"]["initial_template"][0]["image_loss"]
    final = optimization["residuals"]["T1_B1"][0]["image_loss"]
    assert final < initial
    assert len(optimization["prior_only_history"]) == len(optimization["video_history"]) == 6
    assert optimization["residuals"]["T1_B1"][1]["split"] == "heldout"
    assert (tmp_path / "T1_B1.ply").is_file()


def test_optimizer_rejects_truth_label_paths(tmp_path):
    truth = tmp_path / "evaluation-only" / "labels.npy"
    truth.parent.mkdir()
    np.save(truth, np.zeros((4, 4), dtype=np.uint8))
    from wfrl.nrel_reconstruction.optimize import _load_labels
    with pytest.raises(ValueError, match="evaluation"):
        _load_labels({"labels_path": str(truth)})


def test_saved_labels_own_writable_memory(tmp_path):
    from PIL import Image
    from wfrl.nrel_reconstruction.optimize import _load_labels
    path = tmp_path / "FBU.png"
    Image.fromarray(np.array([[0, 1], [2, 1]], dtype=np.uint8)).save(path)
    labels = _load_labels({"labels_path": str(path)})
    assert labels.flags.writeable and labels.flags.owndata
    labels[0, 0] = 2
    assert np.array(Image.open(path))[0, 0] == 0


def test_diagnostic_hard_projection_pixel_centres_and_near_clipping():
    from wfrl.nrel_reconstruction.diagnostics import hard_silhouette
    vertices = np.array([[-1, -1, 4], [1, -1, 4], [0, 1, 4]], dtype=float)
    K = np.array([[8., 0, 3.5], [0, 8., 3.5], [0, 0, 1.]])
    identity = np.eye(4)
    mask = hard_silhouette(vertices, np.array([[0, 1, 2]]), K, identity, identity, (8, 8))
    assert mask[3, 3] and mask[3, 4] and not mask[0, 0]
    assert not hard_silhouette(vertices * [1, 1, -1], np.array([[0, 1, 2]]), K, identity, identity, (8, 8)).any()
    vertices[0, 2] = -0.5
    assert hard_silhouette(vertices, np.array([[0, 1, 2]]), K, identity, identity, (8, 8)).any()
