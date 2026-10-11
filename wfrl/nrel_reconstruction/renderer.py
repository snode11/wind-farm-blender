"""Small differentiable CV-camera triangle silhouette renderer for CPU PyTorch.

Soft edge occupancy is combined as a probabilistic triangle union.  It renders
only pixels inside the declared image. Near-plane clipping is differentiable
within each clipping case. This is a first silhouette backend, not photometry.
"""
from __future__ import annotations

import torch
from torch.utils.checkpoint import checkpoint


def scale_intrinsics(K, source_size, target_size):
    """Resize about half-pixel centres; sizes are (width, height)."""
    out = K.clone() if isinstance(K, torch.Tensor) else __import__("numpy").array(K, dtype=float, copy=True)
    sx, sy = target_size[0] / source_size[0], target_size[1] / source_size[1]
    out[0, :] *= sx
    out[1, :] *= sy
    out[0, 2] += (sx - 1) * 0.5
    out[1, 2] += (sy - 1) * 0.5
    return out


def project_vertices(vertices, K, T_camera_cv_from_world, T_world_from_blade_root):
    transform = T_camera_cv_from_world @ T_world_from_blade_root
    camera = vertices @ transform[:3, :3].T + transform[:3, 3]
    homogeneous = camera @ K.T
    return homogeneous[:, :2] / homogeneous[:, 2:3], camera[:, 2]


def _clip_near(triangles: torch.Tensor, near: float) -> torch.Tensor:
    inside = triangles[:, :, 2] > near
    full = triangles[inside.all(dim=1)]
    crossing = triangles[inside.any(dim=1) & ~inside.all(dim=1)]
    output = [triangle for triangle in full.unbind()]
    for triangle in crossing.unbind():
        clipped = []
        for index in range(3):
            start, end = triangle[index], triangle[(index + 1) % 3]
            in_start, in_end = bool(start[2].detach() > near), bool(end[2].detach() > near)
            if in_start:
                clipped.append(start)
            if in_start != in_end:
                fraction = (near - start[2]) / (end[2] - start[2])
                clipped.append(start + fraction * (end - start))
        for index in range(1, len(clipped) - 1):
            output.append(torch.stack((clipped[0], clipped[index], clipped[index + 1])))
    return torch.stack(output) if output else triangles[:0]


def soft_silhouette(vertices, faces, K, T_camera_cv_from_world,
                    T_world_from_blade_root, image_size, sigma_px=0.65,
                    face_chunk=48, near_m=0.01, return_distance=False):
    """Return HxW occupancy in [0,1], with gradients to mesh vertices.

    image_size is (width,height). No loss is constructed outside this domain.
    For efficiency culling margins are detached and extend twelve sigmas from
    projected triangle bounds; gradients are smooth inside this active set.
    """
    if sigma_px <= 0 or face_chunk <= 0:
        raise ValueError("sigma_px and face_chunk must be positive")
    width, height = map(int, image_size)
    if width <= 0 or height <= 0:
        raise ValueError("Invalid image size")
    transform = T_camera_cv_from_world @ T_world_from_blade_root
    camera = vertices @ transform[:3, :3].T + transform[:3, 3]
    triangles = _clip_near(camera[faces], near_m)
    if not len(triangles):
        empty = vertices.sum() * 0 + vertices.new_zeros((height, width))
        return (empty, empty - max(width, height)) if return_distance else empty
    projected = triangles @ K.T
    projected = projected[:, :, :2] / projected[:, :, 2:3]
    margin = 12 * sigma_px
    limits = projected.detach()
    minimum, maximum = limits.min(dim=1).values, limits.max(dim=1).values
    active = ((maximum[:, 0] >= -margin) & (minimum[:, 0] <= width - 1 + margin)
              & (maximum[:, 1] >= -margin) & (minimum[:, 1] <= height - 1 + margin))
    # Foreground distance must still attract a nonoverlapping initial model;
    # keep offscreen triangles when that positive coverage term is requested.
    if not return_distance:
        projected = projected[active]
    ys, xs = torch.meshgrid(torch.arange(height, device=vertices.device, dtype=vertices.dtype),
                            torch.arange(width, device=vertices.device, dtype=vertices.dtype), indexing="ij")
    points = torch.stack((xs, ys), dim=-1)
    log_transmittance = vertices.new_zeros((height, width)) + vertices.sum() * 0
    union_distance = vertices.new_full((height, width), -float("inf"))
    def rasterize_chunk(chunk):
        edge = torch.roll(chunk, shifts=-1, dims=1) - chunk
        twice_area = edge[:, 0, 0] * (-edge[:, 2, 1]) - edge[:, 0, 1] * (-edge[:, 2, 0])
        orientation = torch.where(twice_area >= 0, 1.0, -1.0)
        signed_edges = []
        for index in range(3):
            relative = points[None] - chunk[:, index, None, None]
            cross = edge[:, index, 0, None, None] * relative[..., 1] - edge[:, index, 1, None, None] * relative[..., 0]
            length = torch.sqrt(edge[:, index].square().sum(dim=-1).clamp_min(1e-16))
            signed_edges.append(cross * orientation[:, None, None] / length[:, None, None])
        distance = torch.stack(signed_edges, dim=0).min(dim=0).values
        valid_triangle = twice_area.abs() > 1e-10
        coverage_distance = torch.where(valid_triangle[:, None, None], distance,
                                        distance.new_full(distance.shape, -float(max(width, height))))
        occupancy = torch.sigmoid(distance / sigma_px)
        occupancy = occupancy * valid_triangle[:, None, None]
        return torch.log1p(-occupancy.clamp(max=1 - 1e-6)).sum(dim=0), coverage_distance.max(dim=0).values

    for chunk in projected.split(face_chunk):
        if not len(chunk):
            continue
        # Checkpoint prevents retaining HxWxfaces edge arrays for the entire
        # mesh. Backward recomputes one small chunk at a time.
        if torch.is_grad_enabled() and chunk.requires_grad:
            log_piece, distance_piece = checkpoint(rasterize_chunk, chunk, use_reentrant=False)
        else:
            log_piece, distance_piece = rasterize_chunk(chunk)
        log_transmittance = log_transmittance + log_piece
        if return_distance:
            union_distance = torch.maximum(union_distance, distance_piece)
    occupancy = -torch.expm1(log_transmittance)
    if return_distance:
        union_distance = torch.nan_to_num(union_distance, neginf=-float(max(width, height)))
        return occupancy, union_distance
    return occupancy


def silhouette_loss(prediction: torch.Tensor, labels: torch.Tensor):
    """F=1 and B=0 normalized separately; U=2 receives no constraint."""
    if prediction.shape != labels.shape:
        raise ValueError("Prediction and F/B/U labels have different sizes")
    if bool(((labels < 0) | (labels > 2)).any()):
        raise ValueError("Labels must use B=0, F=1, U=2")
    foreground, background = labels == 1, labels == 0
    loss = prediction.sum() * 0
    terms = {}
    if bool(foreground.any()):
        terms["foreground"] = (1 - prediction[foreground]).square().mean()
        loss = loss + terms["foreground"]
    if bool(background.any()):
        terms["background"] = prediction[background].square().mean()
        loss = loss + terms["background"]
    return loss, terms
