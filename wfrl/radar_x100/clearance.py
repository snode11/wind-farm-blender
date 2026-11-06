"""Observation-only blade/tower separation and structural-tip clearance.

Inputs are measured positions, Doppler, observed power/noise ratio, a known
tower surface, root poses, and an unloaded section loft. Material labels,
signal-only model power/SNR, loaded geometry and true tips are not inputs.
All thresholds are research assumptions in ClearanceConfig, not calibration.
"""
from dataclasses import dataclass
import math
import numpy as np
from . import analysis


OTHER, BLADE, TOWER = 0, 1, 2


@dataclass(frozen=True)
class ClearanceConfig:
    """Metres, seconds and m/s; SNR is a linear observed power/noise ratio.

    Tower: distance <= max(tower_band_m, position_sigmas * position_std_m)
    and Doppler within static_speed_m_s of the near-tower median (>=5 points).
    Blade: unique root corridor, outside the tower band, and either motion
    plus neighbourhood support, or static_separation_m plus that support.
    Neighbours must be within neighbour_radius_m and Doppler difference <=
    neighbour_speed_m_s. A recent fitted surface can support isolated points.
    Zero Doppler is deliberately allowed away from the known tower surface.
    """
    tower_band_m: float = .20
    position_sigmas: float = 3.
    static_speed_m_s: float = .15
    static_separation_m: float = .50
    minimum_snr_linear: float = 5.
    corridor_m: float = 10.
    minimum_span_fraction: float = .02
    maximum_span_fraction: float = 1.03
    neighbour_radius_m: float = 2.
    neighbour_speed_m_s: float = .5
    minimum_neighbours: int = 3  # Includes the point itself.
    temporal_surface_band_m: float = .35
    fit_start_fraction: float = .45
    minimum_fit_points: int = 16
    minimum_fit_span_fraction: float = .20
    maximum_extrapolation_fraction: float = .16
    minimum_fit_bins: int = 5
    fit_bins: int = 10
    huber_m: float = .15
    maximum_residual_m: float = .25
    maximum_condition: float = 200.
    model_std_m: float = .20
    extrapolation_std_per_m: float = .02
    maximum_clearance_std_m: float = .75
    max_gap_s: float = .12
    process_std_m_s: float = 3.

    def __post_init__(self):
        if (not np.isfinite(list(vars(self).values())).all()
                or min(vars(self).values()) <= 0
                or not 0 < self.minimum_span_fraction < self.fit_start_fraction < 1
                or not 1 <= self.maximum_span_fraction <= 1.2
                or not 0 < self.minimum_fit_span_fraction < 1
                or not 0 < self.maximum_extrapolation_fraction < .5
                or not 3 <= self.minimum_fit_bins <= self.fit_bins <= 100
                or self.minimum_fit_points < 8 or self.minimum_neighbours < 2
                or any(type(getattr(self, name)) is not int for name in
                       ('minimum_neighbours', 'minimum_fit_points', 'minimum_fit_bins', 'fit_bins'))):
            raise ValueError('Invalid clearance research parameters')


@dataclass(frozen=True)
class BladeShape:
    """Unloaded, corresponding closed section rings in blade-root coordinates.

    tip_local_m identifies the structural reference point, not the cosmetic
    loft pole. reference_xy_m is the unloaded structural axis at each section.
    source must identify the CAD/reference assets, never a loaded snapshot.
    """
    spans_m: np.ndarray
    sections_xy_m: np.ndarray
    reference_xy_m: np.ndarray
    tip_local_m: np.ndarray
    source: str

    def __post_init__(self):
        for name in ('spans_m', 'sections_xy_m', 'reference_xy_m', 'tip_local_m'):
            object.__setattr__(self, name, analysis.readonly(getattr(self, name)))
        s, rings = self.spans_m, self.sections_xy_m
        if (s.ndim != 1 or len(s) < 3 or np.any(np.diff(s) <= 0)
                or rings.ndim != 3 or rings.shape[0] != len(s) or rings.shape[1] < 8
                or rings.shape[2] != 2 or self.reference_xy_m.shape != (len(s), 2)
                or self.tip_local_m.shape != (3,) or not isinstance(self.source, str) or not self.source
                or not all(np.isfinite(x).all() for x in
                           (s, rings, self.reference_xy_m, self.tip_local_m))
                or not s[-2] < self.tip_local_m[2] <= s[-1] + 1e-6):
            raise ValueError('Invalid unloaded blade section prior')

    def sections(self, z):
        i = np.clip(np.searchsorted(self.spans_m, z, side='right') - 1, 0, len(self.spans_m) - 2)
        alpha = np.clip((z - self.spans_m[i]) / (self.spans_m[i+1] - self.spans_m[i]), 0, 1)
        return self.sections_xy_m[i] * (1-alpha[:, None, None]) + self.sections_xy_m[i+1] * alpha[:, None, None]

    def basis(self, z):
        u = np.clip((np.asarray(z) - self.spans_m[0]) / (self.tip_local_m[2] - self.spans_m[0]), 0, 1.05)
        return np.stack((u*u, u*u*u), axis=-1)

    def axial_shift(self, z, coefficients):
        """Small-slope inextensible-axis correction relative to the unloaded axis.

        Section twist/strain are not fitted. Their mismatch belongs in model
        uncertainty; this approximation is not a structural solver.
        """
        grid = np.linspace(self.spans_m[0], self.tip_local_m[2], 129)
        reference = np.column_stack([np.interp(grid, self.spans_m, self.reference_xy_m[:, a]) for a in range(2)])
        loaded = reference + self.basis(grid) @ coefficients
        dr = np.diff(reference, axis=0) / np.diff(grid)[:, None]
        dl = np.diff(loaded, axis=0) / np.diff(grid)[:, None]
        shift = np.r_[0., np.cumsum(-.5 * np.sum(dl*dl-dr*dr, axis=1) * np.diff(grid))]
        return np.interp(z, grid, shift)

    def deform(self, points, coefficients):
        """Bending translation of the unloaded loft; no true motion is consumed."""
        result = np.array(points, dtype=float, copy=True)
        result[:, :2] += self.basis(result[:, 2]) @ coefficients
        result[:, 2] += self.axial_shift(result[:, 2], coefficients)
        return result


def _surface_residual(points, shape, coefficients):
    # Correct the axial coordinate before section interpolation. The remaining
    # section rotation and measurement errors are covered by the model floor.
    z = points[:, 2] - shape.axial_shift(points[:, 2], coefficients)
    basis = shape.basis(z)
    target = points[:, :2] - basis @ coefficients
    a = shape.sections(z)
    edge = np.roll(a, -1, axis=1) - a
    squared = np.sum(edge*edge, axis=2)
    fraction = np.clip(np.sum((target[:, None]-a)*edge, axis=2) / np.maximum(squared, 1e-16), 0, 1)
    closest = a + edge*fraction[:, :, None]
    j = np.sum((target[:, None]-closest)**2, axis=2).argmin(axis=1)
    k = np.arange(len(points))
    delta = target - closest[k, j]
    normal = np.column_stack((-edge[k, j, 1], edge[k, j, 0])) / np.maximum(np.sqrt(squared[k, j, None]), 1e-12)
    ends = (fraction[k, j] <= 1e-6) | (fraction[k, j] >= 1-1e-6)
    normal[ends] = delta[ends] / np.maximum(np.linalg.norm(delta[ends], axis=1)[:, None], 1e-12)
    residual = np.sum(delta*normal, axis=1)
    design = (basis[:, :, None] * normal[:, None, :]).reshape(-1, 4)
    return residual, design


def separate_echoes(world_points, radial_velocity_m_s, tower_distance_m, priors, *,
                    observed_power_w=None, observed_snr_linear=None, position_std_m=.12,
                    previous=None, shapes=None, config=ClearanceConfig()):
    """Partition every input index; unknown attributes do not become zero.

    No frame/provenance/material argument is accepted. Unknown Doppler or
    tower distance yields OTHER. Missing power/SNR disables that quality gate.
    previous contains only recent inferred coefficients, in root coordinates.
    """
    points = np.asarray(world_points, float)
    velocity, distance = np.asarray(radial_velocity_m_s, float), np.asarray(tower_distance_m, float)
    if (points.ndim != 2 or points.shape[1:] != (3,) or velocity.shape != (len(points),)
            or distance.shape != (len(points),) or len(priors) != 3
            or {p.blade for p in priors} != {1, 2, 3}):
        raise ValueError('Invalid clearance observations/priors')
    sigma = np.broadcast_to(np.asarray(position_std_m, float), (len(points),))
    if np.any(~np.isfinite(sigma)) or np.any(sigma <= 0):
        raise ValueError('Position standard deviations must be finite and positive')
    valid = np.isfinite(points).all(axis=1) & np.isfinite(velocity) & np.isfinite(distance) & (distance >= 0)
    for values, lower in ((observed_power_w, 0.), (observed_snr_linear, config.minimum_snr_linear)):
        if values is not None:
            values = np.asarray(values, float)
            if values.shape != (len(points),):
                raise ValueError('Quality attributes must match observations')
            valid &= np.isfinite(values) & (values > lower)
    band = np.maximum(config.tower_band_m, config.position_sigmas*sigma)
    near = valid & (distance <= band)
    static_speed = float(np.median(velocity[near])) if near.sum() >= 5 else 0.
    static = np.abs(velocity-static_speed) <= config.static_speed_m_s
    labels = np.full(len(points), OTHER, dtype=np.int8)
    labels[near & static] = TOWER
    assignment = np.zeros(len(points), dtype=np.int8)
    local, gates = [], []
    for prior in priors:
        xyz = analysis.local(points, prior.world_from_blade)
        local.append(xyz)
        gates.append(valid & ~near & (xyz[:, 2] >= config.minimum_span_fraction*prior.tip_span_m)
            & (xyz[:, 2] <= config.maximum_span_fraction*prior.tip_span_m)
            & (np.linalg.norm(xyz[:, :2], axis=1) <= config.corridor_m))
    unique = np.sum(gates, axis=0) == 1
    for prior, xyz, gate in zip(priors, local, gates):
        indices = np.flatnonzero(gate & unique)
        supported = np.zeros(len(indices), bool)
        for start in range(0, len(indices), 256):
            part = indices[start:start+256]
            close = np.sum((xyz[part, None]-xyz[indices])**2, axis=2) <= config.neighbour_radius_m**2
            close &= np.abs(velocity[part, None]-velocity[indices]) <= config.neighbour_speed_m_s
            supported[start:start+len(part)] = close.sum(axis=1) >= config.minimum_neighbours
        if previous and prior.blade in previous and shapes is not None and len(indices):
            residual, _ = _surface_residual(xyz[indices], shapes[prior.blade], previous[prior.blade])
            supported |= np.abs(residual) <= config.temporal_surface_band_m + config.position_sigmas*sigma[indices]
        accepted = indices[supported & (~static[indices] | (distance[indices] > np.maximum(band[indices], config.static_separation_m)))]
        labels[accepted], assignment[accepted] = BLADE, prior.blade
    return dict(labels=labels, blade_assignment=assignment, local=local,
                static_radial_velocity_m_s=static_speed, valid=valid,
                ambiguous_points=int(np.sum(np.sum(gates, axis=0) > 1)))


def _fit(points, sigma, shape, config):
    row = dict(status='INSUFFICIENT_POINTS', points=len(points), tip_local=None,
               tip_world=None, clearance_m=None, clearance_std_m=None, clearance_interval95_m=None,
               tip_covariance_local_m2=None, coefficients=None, fit_residual_m=None,
               extrapolation_m=None, span_fraction=None, condition=None)
    if len(points) < config.minimum_fit_points:
        return row
    z = points[:, 2]
    lo, hi = float(z.min()), float(z.max())
    length = shape.tip_local_m[2]
    extrapolation = max(0., length-hi)
    row.update(extrapolation_m=extrapolation, span_fraction=(hi-lo)/length)
    if hi-lo < config.minimum_fit_span_fraction*length:
        return row | dict(status='INSUFFICIENT_SPAN')
    if extrapolation > config.maximum_extrapolation_fraction*length:
        return row | dict(status='EXTRAPOLATION_TOO_FAR')
    bins = np.minimum(((z-lo)/(hi-lo)*config.fit_bins).astype(int), config.fit_bins-1)
    if len(np.unique(bins)) < config.minimum_fit_bins:
        return row | dict(status='INSUFFICIENT_BINS')
    basis = shape.basis(z)
    center = np.median(shape.sections(z), axis=1)
    coefficients = np.linalg.lstsq(basis, points[:, :2]-center, rcond=None)[0]
    # The centroid initializes axial bending. Chordwise centroid depends on the
    # illuminated airfoil side, so chordwise bending is initialized at zero.
    coefficients[:, 1] = 0.
    converged = False
    for _ in range(40):
        residual, design = _surface_residual(points, shape, coefficients)
        robust = np.minimum(1., config.huber_m/np.maximum(np.abs(residual), 1e-12))
        weights = robust / np.maximum(sigma, .03)**2
        weighted = design*np.sqrt(weights[:, None])
        condition = float(np.linalg.cond(weighted))
        row['condition'] = condition
        if not np.isfinite(condition) or condition > config.maximum_condition:
            return row | dict(status='ILL_CONDITIONED')
        step, _, rank, _ = np.linalg.lstsq(weighted, residual*np.sqrt(weights), rcond=None)
        if rank != 4 or np.linalg.norm(step) > 30:
            return row | dict(status='UNSTABLE_FIT')
        coefficients += step.reshape(2, 2)
        if np.linalg.norm(step) < 1e-4:
            converged = True
            break
    residual, design = _surface_residual(points, shape, coefficients)
    rms = float(np.sqrt(np.mean(residual**2)))
    row['fit_residual_m'] = rms
    if not converged or rms > config.maximum_residual_m:
        return row | dict(status='UNSTABLE_FIT')
    # Bins are the effective independent spatial units. Resolution cells and
    # template mismatch must not create arbitrarily small point-count errors.
    robust = np.minimum(1., config.huber_m/np.maximum(np.abs(residual), 1e-12))
    noise = np.maximum(sigma, max(.03, rms))
    information = design.T @ ((robust/noise**2)[:, None]*design)
    parameter_covariance = np.linalg.pinv(information)*max(1., len(points)/len(np.unique(bins)))
    tip = shape.deform([shape.tip_local_m], coefficients)[0]
    jacobian = np.zeros((3, 4))
    for j in range(4):
        perturbed = coefficients.copy().reshape(-1)
        perturbed[j] += 1e-4
        jacobian[:, j] = (shape.deform([shape.tip_local_m], perturbed.reshape(2, 2))[0]-tip)/1e-4
    model_std = config.model_std_m + config.extrapolation_std_per_m*extrapolation
    covariance = jacobian @ parameter_covariance @ jacobian.T + np.eye(3)*model_std**2
    return row | dict(status='SUPPORTED', tip_local=tip, tip_covariance_local_m2=covariance,
                      coefficients=coefficients)


class ClearanceTracker:
    """Causal root-coordinate filtering; no output from a missed/rejected frame.

    Identity, reverse time, duplicate time with a new key, or a long gap resets
    history. Repeated (identity, time, key) is idempotent. A supplied tower_query
    maps world points to (distance, nearest world point); it must query only
    the known same-time tower surface, never the loaded blade geometry.
    """
    def __init__(self, shapes, config=ClearanceConfig()):
        if set(shapes) != {1, 2, 3} or not all(isinstance(s, BladeShape) for s in shapes.values()):
            raise ValueError('Three declared unloaded shapes are required')
        self.shapes, self.config = dict(shapes), config
        self.reset()

    def reset(self):
        self.identity = self.time = self.frame_key = self.result = None
        self.tracks = {}

    def update(self, world_points, radial_velocity_m_s, priors, tower_vertices, tower_triangles, *,
               time_s, identity, frame_key, observed_power_w=None, observed_snr_linear=None,
               position_std_m=.12, tower_query=None):
        if not math.isfinite(time_s):
            raise ValueError('Clearance tracking needs finite source time')
        if self.identity == identity and self.time == time_s and self.frame_key == frame_key:
            return self.result
        if (self.identity != identity or self.time is not None and
                (time_s <= self.time or time_s-self.time > self.config.max_gap_s)):
            self.reset()
        if any(not np.isclose(p.tip_span_m, self.shapes[p.blade].tip_local_m[2]) for p in priors):
            raise ValueError('Root span and shape prior disagree')
        query = tower_query or (lambda points: analysis.closest_reference(points, tower_vertices, tower_triangles)[:2])
        points = np.asarray(world_points, float)
        distances, _ = query(points)
        separation = separate_echoes(points, radial_velocity_m_s, distances, priors,
            observed_power_w=observed_power_w, observed_snr_linear=observed_snr_linear,
            position_std_m=position_std_m, previous={b: t['coefficients'] for b, t in self.tracks.items()},
            shapes=self.shapes, config=self.config)
        sigma = np.broadcast_to(position_std_m, (len(points),))
        rows, tracks = [], {}
        for prior, local in zip(priors, separation.pop('local')):
            indices = np.flatnonzero((separation['blade_assignment'] == prior.blade)
                & (local[:, 2] >= self.config.fit_start_fraction*prior.tip_span_m))
            row = _fit(local[indices], sigma[indices], self.shapes[prior.blade], self.config)
            row.update(blade=prior.blade, indices=indices.tolist(), consecutive_frames=0)
            if row['status'] == 'SUPPORTED':
                tip, covariance = row['tip_local'], row['tip_covariance_local_m2']
                previous = self.tracks.get(prior.blade)
                count = 1
                if previous is not None:
                    elapsed = time_s-self.time
                    prediction_cov = previous['covariance'] + np.eye(3)*(self.config.process_std_m_s*elapsed)**2
                    gain = prediction_cov @ np.linalg.inv(prediction_cov+covariance)
                    innovation = tip-previous['tip']
                    tip = previous['tip'] + gain@innovation
                    covariance = (np.eye(3)-gain)@prediction_cov
                    # Shared shape bias is systematic, not reduced by repeated frames.
                    covariance += np.eye(3)*self.config.model_std_m**2
                    covariance += np.outer(innovation, innovation)*.25
                    count = previous['count']+1
                matrix = prior.world_from_blade
                world = matrix[:3, :3]@tip + matrix[:3, 3]
                distance, nearest = query(np.array([world]))
                d = float(distance[0])
                normal = (world-nearest[0])/max(d, 1e-12)
                normal_local = matrix[:3, :3].T@normal
                std = float(np.sqrt(max(0., normal_local@covariance@normal_local))) if d > 1e-12 else float(np.sqrt(np.linalg.eigvalsh(covariance).max()))
                if not np.isfinite([d, std]).all() or std > self.config.maximum_clearance_std_m:
                    row.update(status='UNCERTAIN', tip_local=None, coefficients=None, tip_covariance_local_m2=None)
                else:
                    row.update(tip_local=tip, tip_world=world, clearance_m=d, clearance_std_m=std,
                        clearance_interval95_m=[max(0., d-1.96*std), d+1.96*std],
                        tip_covariance_local_m2=covariance, consecutive_frames=count)
                    tracks[prior.blade] = dict(tip=tip.copy(), covariance=covariance.copy(), count=count,
                                              coefficients=row['coefficients'].copy())
            rows.append(row)
        result = separation | dict(blades=rows,
            uncertainty='Approximate 95% interval: spatial-bin fit covariance, shape/extrapolation floor and causal innovation; uncalibrated')
        self.tracks = tracks
        self.identity, self.time, self.frame_key, self.result = identity, time_s, frame_key, result
        return result
