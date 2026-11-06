"""Observation-only blade segment fitting with declared root-pose and span priors."""
from dataclasses import dataclass
import math
import numpy as np
from .simulation import rigid_matrix


@dataclass(frozen=True)
class BladePrior:
    blade: int
    world_from_blade: np.ndarray
    tip_span_m: float

    def __post_init__(self):
        matrix = rigid_matrix(self.world_from_blade)
        if self.blade not in (1, 2, 3) or not math.isfinite(self.tip_span_m) or self.tip_span_m <= 0:
            raise ValueError('Invalid blade pose/span prior')
        matrix.flags.writeable = False
        object.__setattr__(self, 'world_from_blade', matrix)


def smooth_positions(times_s, positions, covariances, *, acceleration_noise_m2_s3=1.,
                     initial_velocity_std_m_s=3.):
    """Offline constant-velocity Kalman/RTS smoother in a declared moving frame.

    Missing measurements are None pairs. States are [position, velocity];
    continuous white acceleration has spectral density in m²/s³. No sample
    is used twice, including the first measurement and leading predictions.
    Rotation belongs to the supplied frame poses, not to the local velocity.
    An entirely unobserved sequence returns None.
    """
    times = np.asarray(times_s, float)
    if (times.ndim != 1 or len(times) != len(positions) or len(times) != len(covariances)
            or not np.isfinite(times).all() or np.any(np.diff(times) <= 0)
            or not np.isfinite([acceleration_noise_m2_s3, initial_velocity_std_m_s]).all()
            or min(acceleration_noise_m2_s3, initial_velocity_std_m_s) <= 0):
        raise ValueError('Invalid temporal observations or motion uncertainty')
    observed = []
    for i, (point, covariance) in enumerate(zip(positions, covariances)):
        if point is None and covariance is None:
            continue
        point, covariance = np.asarray(point, float), np.asarray(covariance, float)
        if (point.shape != (3,) or covariance.shape != (3, 3)
                or not np.isfinite(point).all() or not np.isfinite(covariance).all()
                or not np.allclose(covariance, covariance.T)
                or np.linalg.eigvalsh(covariance).min() <= 0):
            raise ValueError('Position observations need positive definite covariance')
        observed.append(i)
    if not observed:
        return None

    def transition(dt):
        matrix = np.eye(6)
        matrix[:3, 3:] = np.eye(3)*dt
        duration = abs(dt)
        noise = acceleration_noise_m2_s3 * np.kron(
            [[duration**3/3, dt*duration/2], [dt*duration/2, duration]], np.eye(3))
        return matrix, noise

    count, first = len(times), observed[0]
    means = np.zeros((count, 6))
    covariance = np.zeros((count, 6, 6))
    predicted, predicted_covariance = means.copy(), covariance.copy()
    means[first, :3] = positions[first]
    covariance[first, :3, :3] = covariances[first]
    covariance[first, 3:, 3:] = np.eye(3)*initial_velocity_std_m_s**2
    for i in range(first+1, count):
        matrix, noise = transition(times[i]-times[i-1])
        predicted[i] = matrix@means[i-1]
        predicted_covariance[i] = matrix@covariance[i-1]@matrix.T + noise
        means[i], covariance[i] = predicted[i], predicted_covariance[i]
        if positions[i] is not None:
            observation_covariance = np.asarray(covariances[i])
            gain = np.linalg.solve(covariance[i, :3, :3]+observation_covariance,
                                   covariance[i, :3, :]).T
            means[i] += gain@(np.asarray(positions[i])-means[i, :3])
            residual = np.eye(6)
            residual[:, :3] -= gain
            covariance[i] = residual@covariance[i]@residual.T + gain@observation_covariance@gain.T
    for i in range(count-2, first-1, -1):
        matrix, _ = transition(times[i+1]-times[i])
        gain = np.linalg.solve(predicted_covariance[i+1], matrix@covariance[i]).T
        means[i] += gain@(means[i+1]-predicted[i+1])
        covariance[i] += gain@(covariance[i+1]-predicted_covariance[i+1])@gain.T
    for i in range(first-1, -1, -1):
        matrix, noise = transition(times[i]-times[first])
        means[i] = matrix@means[first]
        covariance[i] = matrix@covariance[first]@matrix.T + noise
    covariance = (covariance+covariance.transpose(0, 2, 1))/2
    return means, covariance


@dataclass(frozen=True)
class FitConfig:
    minimum_points: int = 8
    minimum_bins: int = 4
    bins: int = 10
    minimum_span_fraction: float = .18
    distal_start_fraction: float = .55
    corridor_m: float = 10.
    background_margin_m: float = .15
    max_extrapolation_fraction: float = .06
    max_residual_m: float = .8
    max_tip_spread_m: float = 1.
    smoothing_s: float = .10
    max_gap_s: float = .20

    def __post_init__(self):
        values = tuple(vars(self).values())
        if (not np.isfinite(values).all() or min(values) <= 0 or self.minimum_points < 4
                or not 3 <= self.minimum_bins <= self.bins or self.bins > 100
                or not 0 < self.distal_start_fraction < 1 or not 0 < self.minimum_span_fraction < 1
                or not 0 < self.max_extrapolation_fraction < .5):
            raise ValueError('Invalid blade fitting research parameters')


def _segment(local, indices, prior, config):
    row = dict(blade=prior.blade, status='INSUFFICIENT_POINTS', points=len(indices),
        indices=indices.tolist(), tip_local=None, tip_world=None, curve_world=None,
        fit_residual_m=None, fit_spread_m=None, extrapolation_m=None, span_fraction=None)
    if len(indices) < config.minimum_points:
        return row
    points = local[indices]
    z = points[:, 2]
    lo, hi = float(z.min()), float(z.max())
    row['span_fraction'] = (hi - lo) / prior.tip_span_m
    row['extrapolation_m'] = max(0., prior.tip_span_m - hi)
    if row['span_fraction'] < config.minimum_span_fraction:
        return row | dict(status='INSUFFICIENT_SPAN')
    edges = np.linspace(lo, hi, config.bins + 1)
    bins = np.minimum(np.searchsorted(edges, z, side='right') - 1, config.bins - 1)
    centers = np.array([np.median(points[bins == b], axis=0) for b in range(config.bins) if np.any(bins == b)])
    if len(centers) < config.minimum_bins:
        return row | dict(status='INSUFFICIENT_BINS')
    origin, scale = (lo + hi) / 2, hi - lo
    x = (centers[:, 2] - origin) / scale
    design = np.column_stack((np.ones(len(x)), x, x*x))
    weights = np.ones(len(x))
    coefficients = None
    for _ in range(5):
        weighted = design * np.sqrt(weights[:, None])
        coefficients, _, rank, _ = np.linalg.lstsq(weighted, centers[:, :2] * np.sqrt(weights[:, None]), rcond=None)
        if rank != 3 or np.linalg.cond(weighted) > 100:
            return row | dict(status='ILL_CONDITIONED')
        residual = np.linalg.norm(design @ coefficients - centers[:, :2], axis=1)
        robust = max(.03, 1.4826 * float(np.median(np.abs(residual - np.median(residual)))))
        weights = np.minimum(1., 1.5 * robust / np.maximum(residual, 1e-12))
    rms = float(np.sqrt(np.mean(residual**2)))
    tip_x = (prior.tip_span_m - origin) / scale
    tip_design = np.array([1., tip_x, tip_x*tip_x])
    spread = float(max(rms, .03) * np.sqrt(tip_design @ np.linalg.pinv(design.T @ design) @ tip_design))
    row.update(fit_residual_m=rms, fit_spread_m=spread)
    span = np.linspace(lo, min(hi, prior.tip_span_m), 32)
    normalized = (span - origin) / scale
    curve = np.column_stack((np.column_stack((np.ones(len(span)), normalized, normalized**2)) @ coefficients, span))
    matrix = prior.world_from_blade
    row['curve_world'] = (curve @ matrix[:3, :3].T + matrix[:3, 3]).tolist()
    if row['extrapolation_m'] > config.max_extrapolation_fraction * prior.tip_span_m:
        return row | dict(status='EXTRAPOLATION_TOO_FAR')
    if rms > config.max_residual_m or spread > config.max_tip_spread_m:
        return row | dict(status='UNSTABLE_FIT')
    tip = np.r_[tip_design @ coefficients, prior.tip_span_m]
    return row | dict(status='SUPPORTED', tip_local=tip.tolist(),
        tip_world=(matrix[:3, :3] @ tip + matrix[:3, 3]).tolist())


def fit_blades(world_points, priors, background_distance_m, config=FitConfig()):
    """No material identity, deformed reference or frame provenance enters inference."""
    points = np.asarray(world_points, dtype=float)
    distance = np.asarray(background_distance_m, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 3 or distance.shape != (len(points),)
            or len(priors) != 3 or {p.blade for p in priors} != {1, 2, 3}):
        raise ValueError('Invalid blade observations or declared priors')
    valid = np.isfinite(points).all(axis=1) & np.isfinite(distance)
    background = valid & (distance <= config.background_margin_m)
    local, gates = [], []
    for prior in priors:
        matrix = prior.world_from_blade
        xyz = (points - matrix[:3, 3]) @ matrix[:3, :3]
        local.append(xyz)
        gates.append(valid & ~background & (xyz[:, 2] >= config.distal_start_fraction * prior.tip_span_m)
            & (xyz[:, 2] <= 1.03 * prior.tip_span_m) & (np.linalg.norm(xyz[:, :2], axis=1) <= config.corridor_m))
    gates = np.array(gates)
    unique = gates.sum(axis=0) == 1
    assignment = np.full(len(points), -1, dtype=int)
    assignment[background] = -2
    rows = []
    for k, prior in enumerate(priors):
        indices = np.flatnonzero(gates[k] & unique)
        assignment[indices] = prior.blade
        rows.append(_segment(local[k], indices, prior, config))
    return dict(blades=rows, assignment=assignment.tolist(), ambiguous=int(np.sum(gates.sum(axis=0) > 1)),
        background=int(background.sum()), invalid=int((~valid).sum()),
        model='quadratic centerline in declared blade-root coordinates at nominal tip span',
        uncertainty='fit spread only; not a calibrated physical confidence interval')


class BladeTracker:
    """Causal smoothing in blade coordinates; rejected frames never reuse old tips.

    consecutive_frames counts successive supported estimates within max_gap_s.
    It does not measure source-grid coverage between estimates.
    """
    def __init__(self, config=FitConfig()):
        self.config = config
        self.reset()

    def reset(self):
        self.identity = self.time = self.frame_key = self.result = None
        self.tracks = {}

    def update(self, world_points, priors, background_distance_m, *, time_s, identity, frame_key):
        if not math.isfinite(time_s):
            raise ValueError('Blade tracking needs a finite source timestamp')
        if self.identity != identity or (self.time is not None and (time_s < self.time or time_s - self.time > self.config.max_gap_s)):
            self.reset()
        if self.identity == identity and self.frame_key == frame_key:
            return self.result
        result = fit_blades(world_points, priors, background_distance_m, self.config)
        for row, prior in zip(result['blades'], priors):
            previous = self.tracks.get(prior.blade)
            row['consecutive_frames'] = 0
            if row['status'] != 'SUPPORTED':
                self.tracks.pop(prior.blade, None)
                continue
            tip = np.array(row['tip_local'])
            count = 1
            if previous is not None and self.time is not None and time_s > self.time:
                alpha = 1 - math.exp(-(time_s - self.time) / self.config.smoothing_s)
                tip[:2] = previous[0][:2] + alpha * (tip[:2] - previous[0][:2])
                count = previous[1] + 1
            self.tracks[prior.blade] = tip, count
            row['tip_world_raw'] = row['tip_world']
            row['tip_local'] = tip.tolist()
            matrix = prior.world_from_blade
            row['tip_world'] = (matrix[:3, :3] @ tip + matrix[:3, 3]).tolist()
            row['consecutive_frames'] = count
        self.identity, self.time, self.frame_key, self.result = identity, time_s, frame_key, result
        return result
