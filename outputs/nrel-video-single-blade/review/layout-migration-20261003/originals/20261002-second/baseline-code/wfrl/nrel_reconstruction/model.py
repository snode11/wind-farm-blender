"""Independent, deliberately approximate NREL 5 MW blade parameterization.

This module never loads the scene's reference mesh, airfoil coordinates, SourceLoft
or flexible replay.  The coarse chord/twist dimensions come from the public
NREL/TP-500-38060 design report (Table 3-1); the rounded section is a modelling
prior, not a copy of an exact test surface.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch import nn


TEMPLATE_SOURCE = "https://www.nrel.gov/docs/fy09osti/38060.pdf"


def linear_weights(samples: np.ndarray, stations: np.ndarray) -> np.ndarray:
    """Linear station interpolation; endpoints remain constant."""
    result = np.zeros((len(samples), len(stations)), dtype=np.float64)
    for row, z in enumerate(samples):
        right = int(np.clip(np.searchsorted(stations, z, side="right"), 1, len(stations) - 1))
        left = right - 1
        fraction = float(np.clip((z - stations[left]) / (stations[right] - stations[left]), 0, 1))
        result[row, left], result[row, right] = 1 - fraction, fraction
    return result


class BladeModel(nn.Module):
    """Fixed 61.5 m span; bounded centreline and positive chord parameters.

    Right handed blade-root axes: x downstream/thickness, y chordwise, z span.
    Thickness and section twist remain declared priors in the first silhouette
    fit.  The root centre is anchored and global scale/pose are not variables.
    """

    def __init__(self, span_samples: int = 25, ring_samples: int = 12,
                 control_stations: int = 7, dtype: torch.dtype = torch.float32):
        super().__init__()
        if span_samples < 3 or ring_samples < 6 or control_stations < 3:
            raise ValueError("Insufficient blade sampling")
        self.span_samples, self.ring_samples = span_samples, ring_samples
        self.span_m = 61.5
        controls = np.linspace(0, self.span_m, control_stations)
        z = np.linspace(0, self.span_m, span_samples)
        # Coarse interpolation of Table 3-1; RNodes are rotor-centred so subtract
        # the separately declared 1.5 m hub radius. Endpoint values are priors.
        public_z = np.array([0, 1.3667, 6.8333, 10.25, 14.35, 22.55, 30.75,
                             38.95, 47.15, 54.6667, 60.1333, 61.5])
        public_chord = np.array([3.542, 3.542, 4.167, 4.557, 4.652, 4.249,
                                3.748, 3.256, 2.764, 2.313, 1.419, 0.8])
        public_twist = np.array([13.308, 13.308, 13.308, 13.308, 11.480, 9.011,
                                6.544, 4.188, 2.319, 0.863, 0.106, 0.0])
        ratio = np.interp(z, [0, 6.8333, 10.25, 22.55, 38.95, 61.5],
                           [1.0, 1.0, 0.4, 0.3, 0.21, 0.18])
        self.register_buffer("z", torch.tensor(z, dtype=dtype))
        self.register_buffer("control_z", torch.tensor(controls, dtype=dtype))
        self.register_buffer("weights", torch.tensor(linear_weights(z, controls), dtype=dtype))
        self.register_buffer("base_chord", torch.tensor(np.interp(z, public_z, public_chord), dtype=dtype))
        self.register_buffer("twist", torch.tensor(np.deg2rad(np.interp(z, public_z, public_twist)), dtype=dtype))
        self.register_buffer("thickness_ratio", torch.tensor(ratio, dtype=dtype))
        self.register_buffer("theta", torch.arange(ring_samples, dtype=dtype) * (2 * np.pi / ring_samples))
        self.center_raw = nn.Parameter(torch.zeros(control_stations - 1, 2, dtype=dtype))
        self.chord_raw = nn.Parameter(torch.zeros(control_stations, dtype=dtype))
        faces = []
        for i in range(span_samples - 1):
            for j in range(ring_samples):
                a, b = i * ring_samples + j, i * ring_samples + (j + 1) % ring_samples
                c, d = a + ring_samples, b + ring_samples
                faces.extend([(a, b, c), (b, d, c)])
        # Fan caps close the approximate model without adding free vertices.
        for j in range(1, ring_samples - 1):
            faces.append((0, j + 1, j))
            tip = (span_samples - 1) * ring_samples
            faces.append((tip, tip + j, tip + j + 1))
        self.register_buffer("faces", torch.tensor(faces, dtype=torch.long))

    def control_values(self):
        centre = torch.cat((self.center_raw.new_zeros((1, 2)), 5.0 * torch.tanh(self.center_raw)), dim=0)
        # Relative chord permitted in exp([-0.35, +0.35]); always positive.
        chord_log = 0.35 * torch.tanh(self.chord_raw)
        return centre, chord_log

    def forward(self) -> torch.Tensor:
        centre, chord_log = self.control_values()
        centre = self.weights @ centre
        chord = self.base_chord * torch.exp(self.weights @ chord_log)
        y = chord[:, None] * (0.25 - 0.5 * torch.cos(self.theta)[None, :])
        x = (0.5 * chord * self.thickness_ratio)[:, None] * torch.sin(self.theta)[None, :]
        cos_t, sin_t = torch.cos(self.twist)[:, None], torch.sin(self.twist)[:, None]
        # Positive twist is right-handed about +z; no test-surface twist loaded.
        xr = cos_t * x - sin_t * y + centre[:, 0, None]
        yr = sin_t * x + cos_t * y + centre[:, 1, None]
        zr = self.z[:, None].expand(-1, self.ring_samples)
        return torch.stack((xr, yr, zr), dim=-1).reshape(-1, 3)

    def regularization(self) -> dict[str, torch.Tensor]:
        centre, chord_log = self.control_values()
        change = (centre / 5.0).square().mean() + chord_log.square().mean()
        smooth = ((centre[2:] - 2 * centre[1:-1] + centre[:-2]) / 5.0).square().mean()
        smooth = smooth + (chord_log[2:] - 2 * chord_log[1:-1] + chord_log[:-2]).square().mean()
        return {"relative_parameter_change": change, "span_smoothness": smooth}

    def metadata(self) -> dict:
        centre, chord_log = self.control_values()
        return {
            "template_kind": "independent_coarse_rounded_section_prior",
            "source": TEMPLATE_SOURCE,
            "source_scope": "public span and coarse Table 3-1 chord/twist dimensions",
            "excluded_sources": ["scene_reference_mesh", "SourceLoft", "exact_airfoil_coordinates", "replay_deformation"],
            "coordinate_frame": "blade_root_local", "units": "m",
            "axes": {"x": "downstream/thickness", "y": "chordwise", "z": "radial/span"},
            "span_m": self.span_m,
            "control_z_m": self.control_z.detach().cpu().tolist(),
            "center_offsets_m": centre.detach().cpu().tolist(),
            "control_chord_log_scale": chord_log.detach().cpu().tolist(),
            "fixed_priors": ["section_thickness", "section_twist", "hidden_surface", "61.5_m_span"],
            "approximation": "rounded elliptical sections; 0.8 m tip endpoint prior; no exact NREL airfoil surface",
            "span_samples": self.span_samples, "ring_samples": self.ring_samples,
        }


def write_ply(path: str | Path, vertices, faces) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    vertices, faces = np.asarray(vertices), np.asarray(faces)
    if not np.isfinite(vertices).all() or vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError("Invalid mesh vertices")
    with path.open("w", encoding="utf-8") as handle:
        handle.write("ply\nformat ascii 1.0\n")
        handle.write(f"element vertex {len(vertices)}\nproperty float x\nproperty float y\nproperty float z\n")
        handle.write(f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n")
        for point in vertices:
            handle.write(" ".join(f"{float(x):.9g}" for x in point) + "\n")
        for face in faces:
            handle.write("3 " + " ".join(str(int(x)) for x in face) + "\n")
