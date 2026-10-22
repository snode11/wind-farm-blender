"""Reversible Greville geometric-center coordinates for the existing blade.

The only new center variables are independent samples at the non-root Greville
positions. They do not parameterize a new geometric-center spline. Every mesh
and penalty is evaluated by inverse-mapping to the original physical controls.
This module reads no datasets and neither chooses nor runs an optimizer.
"""
from __future__ import annotations

import numpy as np
import torch
from torch import nn

from .model import BladeModel, bspline_weights


class GrevilleBladeModel(BladeModel):
    """Same B-spline model family with two genuinely independent parameters.

    ``greville_raw = (G_g[1:] - D_g(0)[1:]) / 5`` uses a fixed, dimensionless
    normalization. ``chord_raw`` retains the original ``ell=.35*tanh(raw)``.
    The dependent root is always ``G_g[0]=D_g(ell)[0]``. The inherited bound
    ``abs(C[1:]) < 5`` couples the two parameter blocks after the inverse map.

    Forward, original regularization and metadata reuse BladeModel. Infeasible
    values are rejected rather than clamped or regularized. An optimizer must
    check proposed steps with :meth:`check_feasible` and implement its declared
    constrained-step policy. This is compatible with the existing image-loss
    functions, but not the old unrestricted ``_fit`` loop / ``center_raw`` API.
    """

    center_scale_m = 5.0
    chord_log_bound = 0.35

    def __init__(self, reference: BladeModel):
        # Do not create or retain latent original center parameters. Copy the
        # actual reference buffers, including any original float32 rounding.
        nn.Module.__init__(self)
        if isinstance(reference, GrevilleBladeModel) or reference.interpolation != "bspline":
            raise ValueError("Reference must be an original B-spline BladeModel")
        if reference.z.dtype not in {torch.float32, torch.float64}:
            raise ValueError("Greville coordinates require float32 or float64")
        for name in ("span_samples", "ring_samples", "span_m", "interpolation"):
            setattr(self, name, getattr(reference, name))
        for name, value in reference.named_buffers():
            self.register_buffer(name, value.detach().clone())

        positions = self.control_z.detach().cpu().double().numpy()
        z = self.z.detach().cpu().double().numpy()
        basis = bspline_weights(positions, len(positions), self.span_m)
        # Same explicitly declared extension as the preceding family audit:
        # interpolate the original fixed ring priors at Greville positions.
        base = np.interp(positions, z, self.base_chord.detach().cpu().double().numpy())
        twist = np.interp(positions, z, self.twist.detach().cpu().double().numpy())
        self.register_buffer("greville_basis", self.z.new_tensor(basis))
        self.register_buffer("greville_base_chord", self.z.new_tensor(base))
        self.register_buffer("greville_twist", self.z.new_tensor(twist))
        axis = torch.stack((-torch.sin(self.greville_twist), torch.cos(self.greville_twist)), dim=-1)
        self.register_buffer("greville_zero_offset", self.greville_base_chord[:, None] * axis / 4.0)
        # The clamped root row is exactly [1,0,...,0]. Elimination makes C_0=0
        # exact, avoiding an unnecessary numerical solve for a dependent row.
        expected_root = self.z.new_zeros(len(positions))
        expected_root[0] = 1
        if not torch.equal(self.greville_basis[0], expected_root):
            raise ValueError("Greville root basis must be clamped")

        center, chord_log = reference.control_values()
        self._validate_controls(center, chord_log)
        encoded = self._encode_center(center, chord_log)
        self.greville_raw = nn.Parameter(encoded.detach().clone())
        self.chord_raw = nn.Parameter(reference.chord_raw.detach().clone())
        if not self.check_feasible():
            raise ValueError("Reference is too close to the bound for a feasible coordinate round trip")

    def _validate_shapes(self, center, chord_log):
        count = self.greville_basis.shape[0]
        if center.shape != (count, 2) or chord_log.shape != (count,):
            raise ValueError("Physical controls have incompatible shapes")

    def _validate_controls(self, center, chord_log):
        self._validate_shapes(center, chord_log)
        if not bool(torch.isfinite(center).all() and torch.isfinite(chord_log).all()):
            raise ValueError("Physical controls must be finite")
        if not bool((center[0] == 0).all()):
            raise ValueError("Original reference-axis root must be zero")
        if not bool((center[1:].abs() < self.center_scale_m).all()
                    and (chord_log.abs() < self.chord_log_bound).all()):
            raise ValueError("Coordinates violate original coupled physical bounds")

    def greville_offset(self, chord_log: torch.Tensor) -> torch.Tensor:
        """D_g(ell), using the frozen template's declared continuous extension."""
        return self.greville_zero_offset * torch.exp(self.greville_basis @ chord_log)[:, None]

    def _encode_center(self, center, chord_log):
        geometric = self.greville_basis @ center + self.greville_offset(chord_log)
        return (geometric[1:] - self.greville_zero_offset[1:]) / self.center_scale_m

    def coordinates_from_controls(self, center: torch.Tensor, chord_log: torch.Tensor):
        """Differentiable (C, ell) -> (greville_raw, chord_raw), strict domain."""
        self._validate_controls(center, chord_log)
        return self._encode_center(center, chord_log), torch.atanh(chord_log / self.chord_log_bound)

    def controls_from_coordinates(self, greville_raw: torch.Tensor,
                                  chord_raw: torch.Tensor, *, check_bounds: bool = True):
        """Differentiable inverse map, optionally allowing feasibility inspection.

        ``check_bounds=False`` is solely a numerical/domain inspection aid; it
        does not enlarge the accepted forward model domain.
        """
        count = self.greville_basis.shape[0]
        if greville_raw.shape != (count - 1, 2) or chord_raw.shape != (count,):
            raise ValueError("Greville coordinates have incompatible shapes")
        chord_log = self.chord_log_bound * torch.tanh(chord_raw)
        geometric_free = self.greville_zero_offset[1:] + self.center_scale_m * greville_raw
        reference_samples = geometric_free - self.greville_offset(chord_log)[1:]
        free = torch.linalg.solve(self.greville_basis[1:, 1:], reference_samples)
        center = torch.cat((free.new_zeros((1, 2)), free), dim=0)
        if check_bounds:
            if not bool(torch.isfinite(greville_raw).all() and torch.isfinite(chord_raw).all()):
                raise ValueError("Greville coordinates must be finite")
            self._validate_controls(center, chord_log)
        return center, chord_log

    def control_values(self):
        return self.controls_from_coordinates(self.greville_raw, self.chord_raw)

    def greville_centers(self) -> torch.Tensor:
        """Full G_g with a dependent root; no extra root optimization variable."""
        chord_log = self.chord_log_bound * torch.tanh(self.chord_raw)
        root = self.greville_offset(chord_log)[:1]
        free = self.greville_zero_offset[1:] + self.center_scale_m * self.greville_raw
        return torch.cat((root, free), dim=0)

    def bound_margins(self) -> dict[str, torch.Tensor]:
        """Signed strict-domain margins in original units, without a forward."""
        center, chord_log = self.controls_from_coordinates(
            self.greville_raw, self.chord_raw, check_bounds=False)
        return {"center_m": self.center_scale_m - center[1:].abs(),
                "chord_log": self.chord_log_bound - chord_log.abs()}

    def check_feasible(self) -> bool:
        """False for nonfinite coordinates or any nonpositive original margin."""
        with torch.no_grad():
            if not all(bool(torch.isfinite(value).all()) for value in self.parameters()):
                return False
            margins = self.bound_margins()
            return all(bool(torch.isfinite(value).all() and (value > 0).all())
                       for value in margins.values())

    def set_control_values(self, center: torch.Tensor, chord_log: torch.Tensor) -> None:
        """Set an existing model from feasible physical controls, without a fit."""
        new_center, new_chord = self.coordinates_from_controls(center, chord_log)
        # Validate the computed coordinate round trip before mutating state.
        self.controls_from_coordinates(new_center, new_chord)
        with torch.no_grad():
            self.greville_raw.copy_(new_center)
            self.chord_raw.copy_(new_chord)

    def metadata(self) -> dict:
        result = super().metadata()
        result.update({
            "parameterization": "reversible_greville_geometric_centers_v1",
            "optimization_coordinates": {
                "greville_raw": "(G_g[1:] - D_g(0)[1:]) / 5 m; independent dimensionless samples",
                "chord_raw": "original raw coordinates; ell = 0.35*tanh(chord_raw)",
                "root": "dependent G_g[0]=D_g(ell)[0]; original C[0]=0",
            },
            "greville_geometric_centers_m": self.greville_centers().detach().cpu().tolist(),
            "greville_extension": "analytic cubic basis; fixed chord/twist linearly interpolated from original rings",
            "constraint_policy": "original open boxes after inverse map; coupled in Greville coordinates; no clamping",
            "regularization_policy": "unchanged original formulas on inverse-mapped reference-axis coefficients and ell",
            "model_family_policy": "original mesh basis and fixed-prior buffers copied exactly; no independent center spline",
        })
        return result
