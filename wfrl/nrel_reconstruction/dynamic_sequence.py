"""Kinematic sequence prior with an explicit prior-defined structural point.

This transports a static aero-to-structural offset using the existing
aerodynamic-axis minimum rotations. It is not a BeamDyn section solution.
The visible surface remains exactly the pre-existing DY1 candidate surface.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .dynamic_geometry import DynamicBladeReference
from .model import BladeModel

PRIOR_SCHEMA = "nrel-dynamic-static-structural-prior.v1"
MAPPING_STATUS = "PRIOR_DEFINED_STRUCTURAL_POINT"


def _finite(value, shape, name):
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"{name} must be finite with shape {shape}")
    return array


def rotation_z(angle_rad):
    angle = np.asarray(angle_rad, dtype=float)
    cosine, sine = np.cos(angle), np.sin(angle)
    result = np.zeros(angle.shape + (3, 3))
    result[..., 0, 0] = result[..., 1, 1] = cosine
    result[..., 0, 1], result[..., 1, 0] = -sine, sine
    result[..., 2, 2] = 1
    return result


class StructuralPointPrior:
    """Fixed static offset, transported by aerodynamic reference-axis tangents."""

    @classmethod
    def from_files(cls, model_path, prior_path, *, model_sha256=None, prior_sha256=None):
        raw = Path(prior_path).read_bytes()
        signature = hashlib.sha256(raw).hexdigest()
        if prior_sha256 is not None and signature != prior_sha256:
            raise ValueError("Static prior SHA256 mismatch")
        reference = DynamicBladeReference.from_model_state(model_path, expected_sha256=model_sha256)
        result = cls(reference, json.loads(raw))
        result.provenance = {**reference.provenance, "static_prior_sha256": signature,
                             "mapping_status": MAPPING_STATUS,
                             "frame_transport": "aerodynamic_reference_axis_tangent_minimum_rotation",
                             "mechanical_tangent_frame_used": False, "BeamDyn_verified": False}
        return result

    def __init__(self, reference, prior):
        if (prior.get("schema") != PRIOR_SCHEMA or prior.get("target") != "T1/B1"
                or prior.get("coordinate_frame") != "blade_root_local" or prior.get("units") != "m"):
            raise ValueError("Unsupported static structural-point prior identity")
        station = np.asarray(prior.get("station_m"), dtype=float)
        if (station.ndim != 1 or len(station) < 2 or not np.isfinite(station).all()
                or station[0] != 0 or station[-1] != 61.5 or np.any(np.diff(station) <= 0)):
            raise ValueError("Static stations must strictly increase from 0 to exactly 61.5 m")
        aero = _finite(prior.get("aero_reference_B_m"), (len(station), 3), "static aero axis")
        structural = _finite(prior.get("structural_reference_B_m"), (len(station), 3), "static structural axis")
        twist = _finite(prior.get("aero_twist_deg"), (len(station),), "static aerodynamic twist")
        if not np.allclose(aero[:, 2], station, atol=1e-9, rtol=0) or not np.allclose(structural[:, 2], station, atol=1e-9, rtol=0):
            raise ValueError("Static axes must share the same material-span station")
        self.reference = reference
        self.static_prior = prior
        samples = reference.s
        a0 = np.column_stack([np.interp(samples, station, aero[:, i]) for i in range(3)])
        b0 = np.column_stack([np.interp(samples, station, structural[:, i]) for i in range(3)])
        source_frame = rotation_z(np.deg2rad(np.interp(samples, station, twist)))
        self.h0 = np.einsum("nji,nj->ni", source_frame, b0 - a0)
        # Reuse the unchanged template's exact float32 twist field.
        template = BladeModel(span_samples=reference.span_samples, ring_samples=reference.ring_samples)
        self.template_frame = rotation_z(template.twist.numpy().astype(float))
        self.offset_reference = np.einsum("nij,nj->ni", self.template_frame, self.h0)
        self.mechanical_reference = reference.reference_centerline + self.offset_reference
        self.provenance = {**reference.provenance, "mapping_status": MAPPING_STATUS,
                           "frame_transport": "aerodynamic_reference_axis_tangent_minimum_rotation",
                           "mechanical_tangent_frame_used": False, "BeamDyn_verified": False}

    def evaluate(self, q):
        geometry = self.reference.evaluate(q)
        frames = geometry["section_frames"]
        mechanical = geometry["centerline"] + np.einsum("nij,nj->ni", frames, self.offset_reference)
        reference_offsets = self.reference.reference_vertices.reshape(self.reference.span_samples, self.reference.ring_samples, 3) - self.mechanical_reference[:, None]
        equivalent_surface = (mechanical[:, None] + np.einsum("nij,nkj->nki", frames, reference_offsets)).reshape(-1, 3)
        error = float(np.max(np.abs(equivalent_surface - geometry["vertices"])))
        if error > 1e-12 or not np.isfinite(mechanical).all():
            raise ValueError("Structural prior changed the existing DY1 surface")
        if np.array_equal(np.asarray(q), [0, 0]) and not np.array_equal(geometry["vertices"], self.reference.reference_vertices):
            raise ValueError("q=0 must exactly preserve the frozen v2 surface")
        return {**geometry, "mechanical_reference_axis": mechanical, "structural_tip": mechanical[-1].copy(),
                "aero_axis_tip": geometry["axis_tip"].copy(), "mapping_status": MAPPING_STATUS,
                "surface_identity_max_error_m": error,
                "structural_tip_definition": "static source offset transported in v2 aerodynamic tangent frames; kinematic prior only"}


def solve_sequence(grid, times_s, image_mean_native_px=None, *, anchor_index=1,
                   temporal_weight=.05, temporal_scale_m=.1, node_weight=.01,
                   node_scale_m=.5, speed_cap_m_s=4., image_scale_px=10.):
    """Exact finite-state DP; missing image costs forbid a state, never become zero.

    Without image_mean_native_px the image term is identically absent (G2).
    All-zero initialization is a declared nominal trajectory, not an extra
    boundary condition. The single anchor is q=0 at anchor_index.
    """
    grid = np.asarray(grid, dtype=float)
    times = np.asarray(times_s, dtype=float)
    if (grid.ndim != 2 or grid.shape[1] != 2 or not len(grid) or not np.isfinite(grid).all()
            or np.any(abs(grid) > .5) or len(np.unique(grid, axis=0)) != len(grid)
            or times.ndim != 1 or len(times) < 2 or not np.isfinite(times).all() or np.any(np.diff(times) <= 0)
            or type(anchor_index) is not int or not 0 <= anchor_index < len(times)):
        raise ValueError("Invalid finite grid, sampled times or anchor")
    for value in (temporal_weight, node_weight, temporal_scale_m, node_scale_m, speed_cap_m_s, image_scale_px):
        if type(value) not in (int, float) or not np.isfinite(value) or value < 0:
            raise ValueError("Invalid kinematic penalty or scale")
    if min(temporal_scale_m, node_scale_m, speed_cap_m_s, image_scale_px) <= 0:
        raise ValueError("Kinematic scales and cap must be positive")
    zero = np.flatnonzero(np.all(grid == 0, axis=1))
    if len(zero) != 1:
        raise ValueError("Grid must contain one exact q=0 anchor")
    node = np.tile(node_weight * np.sum((grid / node_scale_m) ** 2, axis=1), (len(times), 1))
    if image_mean_native_px is not None:
        image = np.asarray(image_mean_native_px, dtype=float)
        if image.shape != node.shape or np.any(image[np.isfinite(image)] < 0):
            raise ValueError("Image-cost array has invalid dimensions or negative distances")
        node += np.where(np.isfinite(image), image / image_scale_px, np.inf)
    node[anchor_index, np.arange(len(grid)) != zero[0]] = np.inf
    dp = node[0].copy()
    predecessors = []
    delta = grid[None, :, :] - grid[:, None, :]
    transition_base = temporal_weight * np.sum((delta / temporal_scale_m) ** 2, axis=2)
    transitions = 0
    for index, dt in enumerate(np.diff(times), 1):
        allowed = np.all(abs(delta) <= speed_cap_m_s * dt + 1e-12, axis=2)
        transition = np.where(allowed, transition_base, np.inf)
        options = dp[:, None] + transition
        previous = np.argmin(options, axis=0)
        dp = options[previous, np.arange(len(grid))] + node[index]
        predecessors.append(previous)
        transitions += len(grid) ** 2
    last = int(np.argmin(dp))
    if not np.isfinite(dp[last]):
        return {"status": "UNAVAILABLE", "reason": "no complete finite path satisfying anchor and speed cap",
                "q_m": None, "state_indices": None, "total_cost": None,
                "transition_evaluations": transitions, "image_cost_used": image_mean_native_px is not None}
    indices = [last]
    for previous in reversed(predecessors):
        indices.append(int(previous[indices[-1]]))
    indices.reverse()
    chosen = grid[indices]
    return {"status": "FINITE_GRID_KINEMATIC_SOLUTION_ONLY", "q_m": chosen.tolist(),
            "state_indices": indices, "total_cost": float(dp[last]), "transition_evaluations": transitions,
            "image_cost_used": image_mean_native_px is not None,
            "initialization_q_m": [[0., 0.] for _ in times],
            "offline_future_frames_used": True, "structural_acceptance": False}
