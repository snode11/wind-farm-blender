"""Low-dimensional video silhouette fitting with an identical prior-only control.

The caller provides RGB-derived F/B/U observations and fixed ideal calibration /
declared root motion. This module does not open videos, source replay or truth.
It loads only the selected small label images and never stores iteration images.
"""
from __future__ import annotations

import copy
import json
import resource
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch
from torch.nn import functional as F

from .model import BladeModel, write_ply
from .renderer import scale_intrinsics, silhouette_loss, soft_silhouette


@dataclass
class OptimizationConfig:
    model_interpolation: str = "linear"
    image_objective: str = "soft_silhouette"
    resolutions: tuple[tuple[int, int], ...] = ((160, 90), (320, 180))
    iterations_per_stage: tuple[int, ...] = (30, 20)
    learning_rate: float = 0.035
    image_weight: float = 1.0
    foreground_distance_weight: float = 0.15
    relative_change_weight: float = 0.03
    smoothness_weight: float = 0.10
    sigma_px: float = 0.65
    span_samples: int = 25
    ring_samples: int = 12
    control_stations: int = 7
    face_chunk: int = 48
    batch_size: int = 2
    device: str = "cpu"
    seed: int = 20261002
    threads: int = 4


def _tensor(value, device, dtype=torch.float32):
    return torch.as_tensor(value, dtype=dtype, device=device)


def _load_labels(observation):
    if "labels" in observation:
        array = np.asarray(observation["labels"])
    else:
        path = Path(observation["labels_path"]).resolve()
        if any(part in {"evaluation-only", "internal-capture"} for part in path.parts):
            raise ValueError("Optimizer may not load internal capture or evaluation data")
        if path.suffix.lower() == ".npy":
            array = np.load(path, allow_pickle=False)
        else:
            from PIL import Image
            with Image.open(path) as image:
                array = np.array(image, copy=True)
    if array.ndim != 2 or not np.isin(array, [0, 1, 2]).all():
        raise ValueError("Observation labels must be HxW with B=0/F=1/U=2")
    # PIL-backed arrays may be read-only; Torch warns and permits unsafe writes
    # to those buffers. Own the small selected label image before conversion.
    return np.array(array, dtype=np.uint8, copy=True)


def _prepare(observations, image_size, device, require_contours=False):
    prepared = []
    for observation in observations:
        array = _load_labels(observation)
        height, width = array.shape
        labels = _tensor(array, device)[None, None]
        labels = F.interpolate(labels, size=(image_size[1], image_size[0]), mode="nearest-exact")[0, 0].to(torch.uint8)
        K = scale_intrinsics(_tensor(observation["K"], device), (width, height), image_size)
        camera = _tensor(observation["T_camera_cv_from_world"], device)
        root = _tensor(observation["T_world_from_blade_root"], device)
        if K.shape != (3, 3) or camera.shape != (4, 4) or root.shape != (4, 4):
            raise ValueError("Observation matrices need K 3x3 and rigid transforms 4x4")
        if not all(bool(torch.isfinite(item).all()) for item in (K, camera, root)):
            raise ValueError("Nonfinite calibration / motion")
        for transform in (camera, root):
            rotation = transform[:3, :3]
            if not torch.allclose(rotation.T @ rotation, torch.eye(3, device=device), atol=1e-3) or float(torch.det(rotation)) < 0.999:
                raise ValueError("Calibration / root transform must be proper rigid poses in metres")
        prepared.append({"labels": labels, "K": K, "camera": camera, "root": root,
                         "camera_id": observation["camera_id"], "frame_id": int(observation["frame_id"]),
                         "split": observation.get("split", "fit"),
                         "sim_time_s": observation.get("sim_time_s"),
                         "foreground_pixels": int((labels == 1).sum()),
                         "background_pixels": int((labels == 0).sum()),
                         "uncertain_pixels": int((labels == 2).sum())})
        if require_contours:
            from PIL import Image
            paths = [Path(observation[key]).resolve() for key in ("contour_points_path", "contour_valid_path")]
            if any(part in {"evaluation-only", "internal-capture"} for path in paths for part in path.parts):
                raise ValueError("Optimizer may not load internal capture or evaluation contours")
            points = np.load(paths[0], allow_pickle=False)
            with Image.open(paths[1]) as image:
                valid = np.asarray(image)
            if (points.ndim != 2 or points.shape[1] != 2 or len(points) < 2
                    or not np.isfinite(points).all() or valid.shape != array.shape
                    or not np.isin(valid, [0, 255]).all()):
                raise ValueError("Trusted contours need finite Nx2 pixels and a matching binary valid mask")
            if (points[:, 0].min() < 0 or points[:, 1].min() < 0
                    or points[:, 0].max() >= width or points[:, 1].max() >= height):
                raise ValueError("Trusted contour points outside source image")
            scale = np.array([image_size[0] / width, image_size[1] / height])
            if not np.isclose(scale[0], scale[1]):
                raise ValueError("Contour objective requires aspect-preserving resize")
            uncertainty = float(observation["contour_uncertainty_px"])
            if not np.isfinite(uncertainty) or uncertainty < 0:
                raise ValueError("Invalid contour uncertainty")
            valid_tensor = _tensor(np.array(valid > 0, copy=True), device)[None, None]
            valid_tensor = F.interpolate(valid_tensor, size=(image_size[1], image_size[0]), mode="nearest-exact")[0, 0] > 0
            prepared[-1].update({"contour_points": _tensor((points + 0.5) * scale - 0.5, device),
                                 "contour_valid": valid_tensor,
                                 "contour_uncertainty_px": uncertainty * float(scale[0])})
    return prepared


def _image_loss(model, observation, size, config):
    if config.image_objective == "projected_contour":
        from .contour import projected_contour_loss
        return projected_contour_loss(model(), model.faces, observation["K"], observation["camera"],
                                      observation["root"], size, observation["labels"],
                                      observation["contour_points"],
                                      candidate_valid_mask=observation["contour_valid"],
                                      uncertainty_px=observation["contour_uncertainty_px"])
    mask, distance = soft_silhouette(model(), model.faces, observation["K"], observation["camera"],
                                     observation["root"], size, config.sigma_px, config.face_chunk,
                                     return_distance=True)
    loss, terms = silhouette_loss(mask, observation["labels"])
    fg = observation["labels"] == 1
    # This positive foreground coverage term supplies useful gradients even if
    # the initial silhouette does not overlap the visible leaf. It imposes no
    # negative constraint on U, hidden surface, or pixels outside the image.
    if bool(fg.any()):
        miss_px = torch.relu(-distance[fg])
        attraction = F.smooth_l1_loss(miss_px / 10.0, torch.zeros_like(miss_px), reduction="mean")
        terms["foreground_distance"] = attraction
        loss = loss + config.foreground_distance_weight * attraction
    return loss, terms


def _residuals(model, prepared, size, config):
    residuals = []
    with torch.no_grad():
        for observation in prepared:
            loss, terms = _image_loss(model, observation, size, config)
            contour_missing = config.image_objective == "projected_contour" and not float(terms["contour_terms_available"])
            term_values = {name: (None if contour_missing and name in {
                "candidate_to_observed", "observed_to_candidate", "candidate_to_observed_mean_px",
                "observed_to_candidate_mean_px"} else float(value)) for name, value in terms.items()}
            residuals.append({"camera_id": observation["camera_id"], "frame_id": observation["frame_id"],
                              "split": observation["split"], "resolution": list(size),
                              "foreground_pixels": observation["foreground_pixels"],
                              "background_pixels": observation["background_pixels"],
                              "uncertain_pixels": observation["uncertain_pixels"],
                              "image_loss": None if contour_missing else float(loss),
                              "terms": term_values,
                              "missing_terms": (["candidate_to_observed", "observed_to_candidate"]
                                                if contour_missing
                                                else [name for name in ("foreground", "background") if name not in terms]
                                                if config.image_objective == "soft_silhouette" else [])})
    return residuals


def _fit(initial, observations, config, image_weight):
    model = copy.deepcopy(initial)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    history, elapsed = [], time.perf_counter()
    for stage_index, (size, iterations) in enumerate(zip(config.resolutions, config.iterations_per_stage)):
        prepared = _prepare(observations, size, config.device, config.image_objective == "projected_contour")
        fitting = [item for item in prepared if item["split"] != "heldout"]
        if not fitting:
            raise ValueError("At least one fitting observation is required")
        for iteration in range(iterations):
            started = time.perf_counter()
            optimizer.zero_grad(set_to_none=True)
            values = model.regularization()
            regularization = (config.relative_change_weight * values["relative_parameter_change"]
                              + config.smoothness_weight * values["span_smoothness"])
            regularization.backward()
            image_sum = 0.0
            # Every fitting image has equal weight. A graph is discarded after
            # each small batch; no graph for the full video is retained.
            if image_weight:
                for begin in range(0, len(fitting), config.batch_size):
                    batch = fitting[begin:begin + config.batch_size]
                    batch_loss = model.center_raw.sum() * 0
                    for observation in batch:
                        item_loss, terms = _image_loss(model, observation, size, config)
                        if config.image_objective == "projected_contour" and not float(terms["contour_terms_available"]):
                            raise RuntimeError("Missing trusted/predicted contour terms in a fitting frame")
                        batch_loss = batch_loss + item_loss / len(fitting)
                    image_sum += float(batch_loss.detach())
                    (image_weight * batch_loss).backward()
            gradients = [parameter.grad for parameter in model.parameters() if parameter.grad is not None]
            if not gradients or not all(bool(torch.isfinite(gradient).all()) for gradient in gradients):
                raise RuntimeError("Nonfinite / absent optimizer gradients")
            torch.nn.utils.clip_grad_norm_(model.parameters(), 10.0)
            optimizer.step()
            history.append({"stage": stage_index, "iteration": iteration,
                            "resolution": list(size), "image_loss": image_sum,
                            "regularization_loss": float(regularization.detach()),
                            "total_loss": image_weight * image_sum + float(regularization.detach()),
                            "elapsed_s": time.perf_counter() - started})
    return model, history, time.perf_counter() - elapsed


def backend_check():
    """Actual backward + finite-difference check of a CV-camera soft triangle."""
    vertices = torch.tensor([[-0.6, -0.5, 4.0], [0.7, -0.5, 4.0], [0.0, 0.7, 4.0]],
                            dtype=torch.float64, requires_grad=True)
    faces = torch.tensor([[0, 1, 2]], dtype=torch.long)
    K = torch.tensor([[35., 0, 11.5], [0, 35., 11.5], [0, 0, 1]], dtype=torch.float64)
    identity = torch.eye(4, dtype=torch.float64)
    labels = torch.zeros((24, 24), dtype=torch.uint8)
    labels[9:15, 9:15] = 1
    labels[:3] = 2
    def objective(points):
        prediction = soft_silhouette(points, faces, K, identity, identity, (24, 24), sigma_px=0.8)
        return silhouette_loss(prediction, labels)[0]
    loss = objective(vertices)
    loss.backward()
    analytic = float(vertices.grad[0, 0])
    epsilon = 1e-5
    plus, minus = vertices.detach().clone(), vertices.detach().clone()
    plus[0, 0] += epsilon
    minus[0, 0] -= epsilon
    finite_difference = float((objective(plus) - objective(minus)) / (2 * epsilon))
    relative = abs(analytic - finite_difference) / max(abs(analytic), abs(finite_difference), 1e-8)
    passed = bool(torch.isfinite(vertices.grad).all()) and abs(analytic) > 1e-6 and relative < 0.005
    return {"backend": "torch_cpu_soft_triangle_union", "torch_version": torch.__version__,
            "actual_backward": True, "finite_difference_epsilon": epsilon,
            "analytic_gradient": analytic, "finite_difference_gradient": finite_difference,
            "relative_error": relative, "passed": passed}


def reconstruct(observations, output_dir, reference_state, config=None):
    """Fit from permitted, RGB-derived observations; write three mesh controls.

    observation keys: camera_id/frame_id/K/T_camera_cv_from_world/
    T_world_from_blade_root, labels (numpy uint8) or labels_path, optional split
    ('fit'/'heldout'), sim_time_s. reference_state requires t_ref_sim_time_s.
    Returns metadata. output_dir must be a fresh reconstruction result folder.
    """
    config = OptimizationConfig(**config) if isinstance(config, dict) else (config or OptimizationConfig())
    if len(config.resolutions) != len(config.iterations_per_stage) or not config.resolutions:
        raise ValueError("Each resolution requires an iteration budget")
    if config.batch_size < 1 or any(iterations < 1 for iterations in config.iterations_per_stage):
        raise ValueError("Positive batch size and iteration counts required")
    if config.device != "cpu":
        raise ValueError("First backend is explicitly validated on CPU only")
    if config.image_objective not in {"soft_silhouette", "projected_contour"}:
        raise ValueError("Unknown image objective")
    if "t_ref_sim_time_s" not in reference_state:
        raise ValueError("Reference state requires a frozen t_ref_sim_time_s")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "T1_B1.ply").exists():
        raise FileExistsError("Use a new result directory; do not overwrite a previous model")
    observations = list(observations)
    if not observations:
        raise ValueError("No video-derived observations")
    if any(observation.get("split", "fit") not in {"fit", "heldout"} for observation in observations):
        raise ValueError("Observation split must be fit or heldout")
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.threads)
    started = time.perf_counter()
    if config.image_objective == "projected_contour":
        from .contour import contour_backend_check
        check = contour_backend_check()
    else:
        check = backend_check()
    if not check["passed"]:
        raise RuntimeError(f"Renderer gradient check failed: {check}")
    initial = BladeModel(config.span_samples, config.ring_samples, config.control_stations,
                         interpolation=config.model_interpolation).to(config.device)
    prior, prior_history, prior_elapsed = _fit(initial, observations, config, 0.0)
    final, final_history, final_elapsed = _fit(initial, observations, config, config.image_weight)
    models = {"initial_template": initial, "prior_only": prior, "T1_B1": final}
    sidecar = dict(reference_state)
    sidecar.update({"target": "T1/B1", "coordinate_frame": "blade_root_local", "units": "m",
                    "surface_scope": "entire_evaluated_blade_surface", "reference_shape": "static_near_t_ref",
                    "deformation_assistance": "none"})
    prepared = _prepare(observations, config.resolutions[-1], config.device, config.image_objective == "projected_contour")
    all_residuals = {}
    for name, model in models.items():
        write_ply(output_dir / f"{name}.ply", model().detach().cpu().numpy(), model.faces.cpu().numpy())
        metadata = {**sidecar, **model.metadata(), "model_name": name}
        (output_dir / f"{name}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        all_residuals[name] = _residuals(model, prepared, config.resolutions[-1], config)
    (output_dir / "model_state.json").write_text(json.dumps({**sidecar, "models": {name: model.metadata() for name, model in models.items()}}, indent=2), encoding="utf-8")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    runtime = {"wall_s": time.perf_counter() - started, "prior_only_wall_s": prior_elapsed,
               "video_fit_wall_s": final_elapsed, "peak_process_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024),
               "device": config.device, "threads": config.threads, "torch_version": torch.__version__,
               "cached_frames": len(prepared), "cache_policy": "selected labels, validity masks and contour points only; no video float tensor or feature cache"}
    optimization = {"config": asdict(config), "backend_check": check,
                    "control_contract": "same initial parameters / regularization / Adam / stages / budgets; image weight alone zero",
                    "comparison_provenance": {"same_initial_parameters": True, "same_optimizer": True,
                                              "same_stage_schedule": True, "same_regularization": True,
                                              "same_budget": True, "only_image_weights_differ": True,
                                              "prior_only_image_weight": 0.0,
                                              "final_image_weight": config.image_weight},
                    "fit_observations": [{"camera_id": item["camera_id"], "frame_id": item["frame_id"], "split": item["split"]} for item in prepared],
                    "prior_only_history": prior_history, "video_history": final_history,
                    "residuals": all_residuals,
                    "heldout_residuals": {name: [item for item in residuals if item["split"] == "heldout"]
                                          for name, residuals in all_residuals.items()},
                    "heldout_status": "AVAILABLE" if any(item["split"] == "heldout" for item in prepared) else "NO_HELDOUT_OBSERVATIONS",
                    "limitations": ["silhouette constraints only", "thickness/twist/backside fixed priors", "near-reference static shape; no free per-frame deformation"]}
    (output_dir / "optimization.json").write_text(json.dumps(optimization, indent=2), encoding="utf-8")
    (output_dir / "runtime_metrics.json").write_text(json.dumps(runtime, indent=2), encoding="utf-8")
    # A conservative region can be image-constrained in silhouette while its
    # backside and thickness are still prior-only. Coverage is not claimed from
    # closed topology. Save actual source frame IDs for independent inspection.
    sources = [{"camera_id": item["camera_id"], "frame_id": item["frame_id"]}
               for item in prepared if item["split"] != "heldout" and item["foreground_pixels"]]
    evidence = {"target": "T1/B1", "units": "m", "coordinate_frame": "blade_root_local",
                "regions": [{"name": name, "span_bounds_m": bounds, "status": "prior_only",
                             "source_frames": sources,
                             "note": "possible silhouette influence; surface visibility coverage not yet independently established"}
                            for name, bounds in [("root", [0, 20.5]), ("middle", [20.5, 41.0]), ("tip", [41.0, 61.5])]],
                "constraints": ("RGB trusted contour with independent crop/occlusion validity and annotation deadband"
                                if config.image_objective == "projected_contour" else
                                "RGB F/B/U silhouettes; U and outside-image have no negative loss"),
                "backside_and_thickness": "prior_only", "full_surface_recovery": False}
    (output_dir / "surface_evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return {"status": "MODEL_FIT_COMPLETED_GEOMETRY_EFFECT_UNSCORED", "output_dir": str(output_dir.resolve()),
            "reference_state": sidecar, "runtime": runtime, "backend_check": check}
