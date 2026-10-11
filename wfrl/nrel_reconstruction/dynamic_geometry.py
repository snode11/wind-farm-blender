"""Truth-free DY1 geometric candidates around a frozen video-fitted blade.

This is a two-parameter sensitivity representation, not a fitted dynamic model.
The reference-axis endpoint is only an assumed structural-point mapping.  Its
section centroid is returned separately and must not be substituted silently.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import numpy as np
import torch

from .model import BladeModel, bspline_knots


MAPPING_STATUS = "assumed_reference_axis_to_structural_tip_not_verified"
# Frozen A inputs store Blender float32 poses. Measured R.T@R residuals are
# <=5.28e-7 even for camera@root composition (300 matrices); this tolerance
# accepts that representation error without projecting or changing the input.
RIGID_TOLERANCE = 1e-5


def _finite(value, name, shape=None):
    array = np.asarray(value, dtype=float)
    if (shape is not None and array.shape != shape) or not np.isfinite(array).all():
        raise ValueError(f"{name} must be finite with shape {shape}")
    return array


def _unit(vector, name):
    vector = _finite(vector, name, (3,))
    length = float(np.linalg.norm(vector))
    if not np.isfinite(length) or length <= 1e-12:
        raise ValueError(f"{name} must be nondegenerate")
    return vector / length


def _rigid_transform(transform):
    transform = _finite(transform, "transform", (4, 4))
    rotation = transform[:3, :3]
    if (not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-10, rtol=0)
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=RIGID_TOLERANCE, rtol=0)
            or not np.isclose(np.linalg.det(rotation), 1., atol=RIGID_TOLERANCE, rtol=0)):
        raise ValueError("transform must be a proper rigid transform; scale is not permitted")
    return transform


def transform_points(points, transform):
    """Apply one explicitly supplied B-to-H (or H-to-world) rigid transform."""
    points = _finite(points, "points")
    if points.ndim < 1 or points.shape[-1] != 3:
        raise ValueError("points must end in a coordinate dimension of three")
    transform = _rigid_transform(transform)
    return points @ transform[:3, :3].T + transform[:3, 3]


def plane_readout(tip, center, normal):
    """Recompute signed distance and perpendicular foot from the same geometry.

    A finite nonzero normal is normalized without changing its declared sign.
    There is no implicit choice between axis_tip and ring_centroid.
    """
    tip = _finite(tip, "tip", (3,))
    center = _finite(center, "plane center", (3,))
    normal = _unit(normal, "plane normal")
    distance = float(normal @ (tip - center))
    if not np.isfinite(distance):
        raise ValueError("plane distance is nonfinite")
    return {"d": distance, "abs_d": abs(distance), "foot": tip - distance * normal,
            "normal": normal}


def signed_distance(tip, center, normal):
    return plane_readout(tip, center, normal)["d"]


def _basis_of_degree(samples, knots, degree):
    # Left limit at the final knot gives the clamped endpoint derivative.
    z = np.minimum(samples, np.nextafter(knots[-1], -np.inf))
    basis = ((z[:, None] >= knots[:-1]) & (z[:, None] < knots[1:])).astype(float)
    for order in range(1, degree + 1):
        next_basis = np.zeros((len(samples), len(knots) - order - 1))
        for index in range(next_basis.shape[1]):
            left = knots[index + order] - knots[index]
            right = knots[index + order + 1] - knots[index + 1]
            if left > 0:
                next_basis[:, index] += (z - knots[index]) / left * basis[:, index]
            if right > 0:
                next_basis[:, index] += (knots[index + order + 1] - z) / right * basis[:, index + 1]
        basis = next_basis
    return basis


def _center_derivative(samples, controls, interpolation, span):
    if interpolation == "linear":
        stations = np.linspace(0., span, len(controls))
        left = np.clip(np.searchsorted(stations, samples, side="right") - 1, 0, len(stations) - 2)
        return (controls[left + 1] - controls[left]) / (stations[left + 1] - stations[left])[:, None]
    knots = bspline_knots(len(controls), span)
    lower = _basis_of_degree(samples, knots, 2)
    derivative = np.zeros((len(samples), len(controls)))
    for index in range(len(controls)):
        left, right = knots[index + 3] - knots[index], knots[index + 4] - knots[index + 1]
        if left > 0:
            derivative[:, index] += 3. / left * lower[:, index]
        if right > 0:
            derivative[:, index] -= 3. / right * lower[:, index + 1]
    return derivative @ controls


def _minimum_rotation(reference_tangents, tangents):
    output = []
    for before, after in zip(reference_tangents, tangents):
        before, after = _unit(before, "reference tangent"), _unit(after, "tangent")
        cosine = float(np.clip(before @ after, -1., 1.))
        if cosine <= -1. + 1e-10:
            raise ValueError("Antiparallel tangents have no unique minimum rotation")
        x, y, z = np.cross(before, after)
        cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
        output.append(np.eye(3) + cross + cross @ cross / (1. + cosine))
    return np.asarray(output)


class DynamicBladeReference:
    """Frozen section prior with q=(qx,qy) in metres in the blade-root frame.

    Each component is bounded by 0.5 m.  The centreline increment is
    ``(s / 61.5)**2 * (qx, qy, 0)``.  Its value and derivative vanish at the
    root.  Minimum rotations transport the *existing* section frames, so q=0
    preserves the original surface rather than rebuilding normal sections.
    """

    @classmethod
    def from_model_state(cls, path, *, expected_sha256=None):
        raw = Path(path).read_bytes()
        signature = hashlib.sha256(raw).hexdigest()
        if expected_sha256 is not None and signature != expected_sha256:
            raise ValueError("Frozen model_state SHA256 mismatch")
        state = json.loads(raw)
        if (state.get("target") != "T1/B1" or state.get("coordinate_frame") != "blade_root_local"
                or state.get("units") != "m" or state.get("reference_shape") != "static_near_t_ref"):
            raise ValueError("Expected the frozen static video model contract for T1/B1")
        try:
            metadata = state["models"]["T1_B1"]
        except (KeyError, TypeError) as error:
            raise ValueError("model_state must contain the video model models['T1_B1']") from error
        model = cls.from_metadata(metadata)
        model.provenance = {"model_state_sha256": signature, "model_key": "T1_B1",
                            "reference_time_s": state.get("t_ref_sim_time_s"),
                            "initialization_uses_video": True}
        return model

    @classmethod
    def from_metadata(cls, metadata: Mapping):
        if (metadata.get("template_kind") != "independent_coarse_rounded_section_prior"
                or metadata.get("coordinate_frame") != "blade_root_local"
                or metadata.get("units") != "m" or metadata.get("span_m") != 61.5):
            raise ValueError("Unsupported frozen blade metadata or coordinate contract")
        centers = _finite(metadata.get("center_offsets_m"), "center controls")
        if centers.ndim != 2 or centers.shape[1] != 2 or len(centers) < 3:
            raise ValueError("center controls must have shape (n>=3, 2)")
        chord_log = _finite(metadata.get("control_chord_log_scale"), "chord controls", (len(centers),))
        if (not np.array_equal(centers[0], [0., 0.]) or np.any(abs(centers) > 5.)
                or np.any(abs(chord_log) > .35 + 1e-8)):
            raise ValueError("Frozen model violates original root or parameter bounds")
        counts = [metadata.get(key) for key in ("span_samples", "ring_samples")]
        if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
            raise ValueError("Mesh sample counts must be integers")
        model = BladeModel(span_samples=counts[0], ring_samples=counts[1],
                           control_stations=len(centers), interpolation=metadata.get("interpolation", "linear"))
        control_z = _finite(metadata.get("control_z_m"), "control stations", (len(centers),))
        if not np.allclose(control_z, model.control_z.numpy(), atol=1e-6, rtol=0):
            raise ValueError("Control stations differ from the declared frozen basis")
        if model.interpolation == "bspline":
            knots = _finite(metadata.get("basis_knots_m"), "basis knots", tuple(model.basis_knots.shape))
            if not np.allclose(knots, model.basis_knots.numpy(), atol=1e-6, rtol=0):
                raise ValueError("Basis knots differ from the declared frozen basis")
        # Replay the original float32 geometry operations directly from saved
        # physical coefficients, without inverse-tanh / parameter refitting.
        with torch.no_grad():
            center = model.weights @ torch.tensor(centers, dtype=model.weights.dtype)
            chord = model.base_chord * torch.exp(model.weights @ torch.tensor(chord_log, dtype=model.weights.dtype))
            y = chord[:, None] * (.25 - .5 * torch.cos(model.theta)[None, :])
            x = (.5 * chord * model.thickness_ratio)[:, None] * torch.sin(model.theta)[None, :]
            cosine, sine = torch.cos(model.twist)[:, None], torch.sin(model.twist)[:, None]
            vertices = torch.stack((cosine * x - sine * y + center[:, 0, None],
                                    sine * x + cosine * y + center[:, 1, None],
                                    model.z[:, None].expand(-1, model.ring_samples)), dim=-1)
            centerline = torch.cat((center, model.z[:, None]), dim=1)
        result = cls()
        result.span_m = model.span_m
        result.span_samples, result.ring_samples = model.span_samples, model.ring_samples
        result.reference_vertices = vertices.numpy().astype(float).reshape(-1, 3)
        result.reference_centerline = centerline.numpy().astype(float)
        result.s = model.z.numpy().astype(float)
        result.faces = model.faces.numpy().copy()
        result.material_ids = np.stack(np.meshgrid(np.arange(model.span_samples), np.arange(model.ring_samples), indexing="ij"), axis=-1).reshape(-1, 2)
        result.reference_tangents = np.column_stack((_center_derivative(result.s, centers, model.interpolation, model.span_m), np.ones(model.span_samples)))
        result.provenance = {"model_key": "T1_B1", "initialization_uses_video": True}
        for value in (result.reference_vertices, result.reference_centerline, result.s,
                      result.faces, result.material_ids, result.reference_tangents):
            value.setflags(write=False)
        return result

    def evaluate(self, q):
        q = _finite(q, "q", (2,))
        if np.any(abs(q) > .5):
            raise ValueError("DY1 candidate q exceeds the per-component 0.5 m bounds")
        delta = np.array([q[0], q[1], 0.])
        centerline = self.reference_centerline + (self.s / self.span_m)[:, None] ** 2 * delta
        tangents = self.reference_tangents + (2. * self.s / self.span_m ** 2)[:, None] * delta
        if np.array_equal(q, [0., 0.]):
            frames = np.repeat(np.eye(3)[None], self.span_samples, axis=0)
            vertices = self.reference_vertices.copy()
        else:
            frames = _minimum_rotation(self.reference_tangents, tangents)
            offsets = self.reference_vertices.reshape(self.span_samples, self.ring_samples, 3) - self.reference_centerline[:, None]
            vertices = (np.einsum("nij,nkj->nki", frames, offsets) + centerline[:, None]).reshape(-1, 3)
        if not np.isfinite(vertices).all():
            raise ValueError("Reconstructed candidate is nonfinite")
        return {"vertices": vertices, "faces": self.faces.copy(), "centerline": centerline,
                "axis_tip": centerline[-1].copy(),
                "ring_centroid": vertices.reshape(self.span_samples, self.ring_samples, 3)[-1].mean(axis=0),
                "material_ids": self.material_ids.copy(), "material_span_m": self.s.copy(),
                "section_frames": frames, "tangents": tangents,
                "q_m": q.copy(), "coordinate_frame": "blade_root_local", "units": "m",
                "mapping_status": MAPPING_STATUS, "axis_tip_material_span_m": self.span_m,
                "axis_tip_definition": "frozen model reference axis at s=61.5 m; structural mapping prior",
                "ring_centroid_definition": "equal-weight mean of this model's terminal ring vertices; not SourceGeometry.tip_reference"}
