"""RGB-only 2D contour checks with explicit uncertainty/occlusion handling.

F/B/U alone defines a trusted boundary only at direct F--B adjacency.  An
uncertainty ring separating F and B is never bridged.  Independently reviewed
RGB contour points need a separate candidate-valid domain, since the legacy U
label mixes actual occlusion with uncertainty around otherwise visible edges.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree


def _points(value: np.ndarray, name: str) -> np.ndarray:
    points = np.asarray(value, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError(f"{name} must be finite Nx2 points in x,y pixel-centre coordinates")
    return points


def _binary_boundary(mask: np.ndarray) -> np.ndarray:
    """Foreground-side 4-neighbour boundary, without inventing outside pixels."""
    boundary = np.zeros_like(mask, dtype=bool)
    boundary[:, :-1] |= mask[:, :-1] & ~mask[:, 1:]
    boundary[:, 1:] |= mask[:, 1:] & ~mask[:, :-1]
    boundary[:-1, :] |= mask[:-1, :] & ~mask[1:, :]
    boundary[1:, :] |= mask[1:, :] & ~mask[:-1, :]
    return boundary


def _foreground_background_boundary(foreground: np.ndarray, background: np.ndarray) -> np.ndarray:
    boundary = np.zeros_like(foreground, dtype=bool)
    boundary[:, :-1] |= foreground[:, :-1] & background[:, 1:]
    boundary[:, 1:] |= foreground[:, 1:] & background[:, :-1]
    boundary[:-1, :] |= foreground[:-1, :] & background[1:, :]
    boundary[1:, :] |= foreground[1:, :] & background[:-1, :]
    return boundary


def _mask_points(mask: np.ndarray) -> np.ndarray:
    y, x = np.nonzero(mask)
    return np.column_stack((x, y)).astype(float)


def _filter_points(points: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, int]:
    # nearest pixel-centre domain membership; locations outside the image fail.
    indices = np.floor(points + .5).astype(np.int64)
    inside = (indices[:,0] >= 0) & (indices[:,0] < valid.shape[1]) & (indices[:,1] >= 0) & (indices[:,1] < valid.shape[0])
    accepted = np.zeros(len(points),dtype=bool)
    accepted[inside] = valid[indices[inside,1],indices[inside,0]]
    return points[accepted],int((~accepted).sum())


def _direction(source: np.ndarray, target: np.ndarray, name: str) -> dict[str, Any]:
    reason = "NO_TRUSTED_OBSERVED_BOUNDARY" if not len(source) and name == "observed_to_candidate" else "NO_VALID_CANDIDATE_BOUNDARY" if not len(source) else None
    if reason is None and not len(target):
        reason = "NO_VALID_CANDIDATE_BOUNDARY" if name == "observed_to_candidate" else "NO_TRUSTED_OBSERVED_BOUNDARY"
    if reason:
        return {"mean_px":None,"p95_px":None,"max_px":None,"point_count":len(source),"null_reason":reason}
    distances = cKDTree(target).query(source,k=1)[0]
    return {"mean_px":float(distances.mean()),"p95_px":float(np.quantile(distances,.95)),
            "max_px":float(distances.max()),"point_count":len(source),"null_reason":None}


def boundary_metrics(labels: np.ndarray, predicted_foreground: np.ndarray | None = None, *,
                     observed_boundary_points: np.ndarray | None = None,
                     predicted_boundary_points: np.ndarray | None = None,
                     candidate_valid_mask: np.ndarray | None = None,
                     label_values: Mapping[str,int] | None = None,
                     exclusion_radius_px: int = 1,
                     pixel_size_px: tuple[float,float] = (1.0,1.0)) -> dict[str,Any]:
    """Compare sampled 2D contour points, never evaluation truth.

    Explicit RGB points require ``candidate_valid_mask`` to declare actual
    occlusion/cropping exclusions separately from an edge-uncertainty U band.
    ``pixel_size_px=(sx,sy)`` expresses resized pixels in the reporting frame;
    both contour sets receive the same scale.  No 3D interpretation is implied.
    """
    values = dict(label_values or {"F":1,"B":0,"U":2})
    labels = np.asarray(labels)
    if labels.ndim != 2 or min(labels.shape) < 3 or set(values) != {"F","B","U"} or len(set(values.values())) != 3:
        raise ValueError("Need HxW labels and distinct F/B/U values")
    if not np.isin(labels,list(values.values())).all():
        raise ValueError("Unknown F/B/U label value")
    if not isinstance(exclusion_radius_px,(int,np.integer)) or exclusion_radius_px < 0:
        raise ValueError("exclusion_radius_px must be a nonnegative integer")
    scale = np.asarray(pixel_size_px,dtype=float)
    if scale.shape != (2,) or not np.isfinite(scale).all() or (scale <= 0).any():
        raise ValueError("pixel_size_px must contain positive finite x/y scales")
    foreground,background,uncertain = (labels==values[key] for key in ("F","B","U"))
    if observed_boundary_points is not None and candidate_valid_mask is None:
        raise ValueError("Explicit RGB contour points need a separate candidate_valid_mask for real occlusion/cropping")
    if candidate_valid_mask is None:
        excluded = ndimage.binary_dilation(uncertain,iterations=exclusion_radius_px) if exclusion_radius_px else uncertain
        valid = ~excluded
    else:
        valid_source = np.asarray(candidate_valid_mask)
        if valid_source.shape != labels.shape or not np.isfinite(valid_source).all() or not np.isin(valid_source,[0,1,False,True]).all():
            raise ValueError("candidate_valid_mask must be a finite HxW binary mask matching labels")
        valid = valid_source.astype(bool,copy=True)
    border = max(1,exclusion_radius_px)
    valid[:border] = False;valid[-border:] = False;valid[:,:border] = False;valid[:,-border:] = False
    raw_observed = _points(observed_boundary_points,"observed_boundary_points") if observed_boundary_points is not None else _mask_points(_foreground_background_boundary(foreground,background))
    observed,removed_observed = _filter_points(raw_observed,valid)
    if predicted_boundary_points is not None:
        raw_candidate = _points(predicted_boundary_points,"predicted_boundary_points")
    else:
        if predicted_foreground is None:
            raise ValueError("Need predicted_foreground or predicted_boundary_points")
        prediction = np.asarray(predicted_foreground)
        if prediction.shape != labels.shape or not np.isfinite(prediction).all() or not np.isin(prediction,[0,1,False,True]).all():
            raise ValueError("predicted_foreground must be a finite HxW binary mask")
        raw_candidate = _mask_points(_binary_boundary(prediction.astype(bool)))
    candidate,removed_candidate = _filter_points(raw_candidate,valid)
    result = {"schema":"nrel-rgb-boundary-metrics.v1", "units":"px", "method":"bidirectional nearest sampled pixel-centre contour point",
              "observed_source":"separately reviewed RGB contour" if observed_boundary_points is not None else "direct F-B adjacency only; no U bridging",
              "observed_boundary_count":len(observed), "candidate_boundary_count":len(candidate),
              "excluded_observed_points":removed_observed,"excluded_candidate_points":removed_candidate,
              "raw_observed_boundary_count":len(raw_observed),"raw_candidate_boundary_count":len(raw_candidate),
              "direct_f_b_boundary_count":int(_foreground_background_boundary(foreground,background).sum()),
              "foreground_uncertain_edge_pixels":int((_binary_boundary(foreground)&ndimage.binary_dilation(uncertain,iterations=1)).sum()),
              "exclusion_radius_px":int(exclusion_radius_px),"pixel_size_px":scale.tolist(),
              "explicit_domain_used":candidate_valid_mask is not None,
              "observed_to_candidate":_direction(observed*scale,candidate*scale,"observed_to_candidate"),
              "candidate_to_observed":_direction(candidate*scale,observed*scale,"candidate_to_observed"),
              "interpretation":"2D sampled contour residual only; not 3D accuracy or full-surface coverage"}
    result["status"] = "AVAILABLE" if len(observed) and len(candidate) else "NULL_NO_COMPARABLE_TRUSTED_BOUNDARY"
    return result
