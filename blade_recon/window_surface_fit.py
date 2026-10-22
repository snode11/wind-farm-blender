"""Small offline window fitter, with shared material coordinates per RGB track.

Legacy recon.Fitter remains unchanged. A1 and A3 use this same implementation,
state activity, initialization, bounds, priors, and termination rules. The only
A3 addition is the fixed set of confirmed 2D observations and shared q variables.
This module reads no files and therefore cannot discover neighboring truth.
"""
from dataclasses import dataclass, field, asdict
from collections import Counter
import copy

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

try:
    from . import recon
    from .model import N_STATE, STATE_NAMES
    from .surface_geometry import (chart_binding, intersect_pixel,
                                   point_from_vertices, point_visibility)
except ImportError:
    import recon
    from model import N_STATE, STATE_NAMES
    from surface_geometry import (chart_binding, intersect_pixel,
                                  point_from_vertices, point_visibility)


@dataclass
class WindowFitConfig:
    active_state_indices: list = field(default_factory=lambda: list(range(10)))
    max_nfev: int = 60
    # psi is an unwrapped phase, with no physical +/-1e9-degree boundary.
    # A fake distant boundary badly scales sparse TRF/LSMR columns.
    azimuth_unbounded: bool = True
    # Scalar, or 15 canonical entries: 13 states then [q_span, q_perimeter].
    # This is scipy's relative diff_step; zero-valued variables use scipy's
    # nonzero machine-step fallback. Saved sensitivity uses its own absolute
    # parameter-scaled central differences and is reported separately.
    finite_difference_relative_step: float | list = 1e-3
    state_lower: list = field(default_factory=lambda: [-1e9] + [-5.] * 3 + [-3.] * 3 + [-2.] * 3 + [-3.] * 3)
    state_upper: list = field(default_factory=lambda: [1e9] + [90.] * 3 + [12.] * 3 + [2.] * 3 + [3.] * 3)
    state_scale: list = field(default_factory=lambda: [1.] * 4 + [.5] * 3 + [.1] * 3 + [.5] * 3)
    prior_mean: list = field(default_factory=lambda: [0.] * 4 + [3.] * 3 + [0.] * 6)
    prior_sigma: list = field(default_factory=lambda: [1e9] + [25.] * 3 + [4.] * 3 + [1.] * 3 + [4.] * 3)
    prior_weight: float = 2.
    # Actual acceleration: (v[i+1]-v[i])/((dt[i+1]+dt[i])/2).
    # Units are deg/s^2 for psi/pitch, m/s^2 for flap/edge/flap2.
    acceleration_sigma: list = field(default_factory=lambda: [300.] + [25.] * 3 + [10.] * 3 + [4.] * 3 + [10.] * 3)
    acceleration_weight: float = 2.
    # Optional velocity-minus-initial-velocity prior. None means disabled.
    # Units deg/s for psi/pitch and m/s otherwise; constant rotor speed has
    # zero acceleration residual and is never shrunk toward zero velocity.
    velocity_sigma: list | None = None
    velocity_weight: float = 1.
    contour_sigma_px: float = 1.
    contour_robust_px: float = 2.
    contour_observation_weight: float = .7
    track_sigma_px: float = 2.
    track_robust_sigma: float = 1.
    track_weight: float = 12.
    normalize_track_groups: bool = True
    q_span_delta: float = .15
    q_perimeter_delta: float = .125
    q_scale: list = field(default_factory=lambda: [.02, .04])
    min_depth_m: float = .05
    backface_penalty_px: float = 30.
    ftol: float = 1e-7
    xtol: float = 1e-7
    gtol: float = 1e-7
    sensitivity_step: float = 1e-4


@dataclass
class WindowFitResult:
    states: np.ndarray
    q: np.ndarray
    bindings: list
    solver: dict
    diagnostics: dict
    config: dict

    def to_dict(self):
        return {'states': self.states.tolist(), 'q': self.q.tolist(),
                'bindings': self.bindings, 'solver': self.solver,
                'diagnostics': self.diagnostics, 'config': self.config}


def _finite_uv(cam, point, min_depth):
    xc = cam.to_cam(point)
    z = float(xc[2])
    safe = max(z, min_depth)
    uv = (cam.K @ xc)[:2] / safe
    return np.clip(uv, -1e6, 1e6), z


class WindowSurfaceFitter:
    def __init__(self, rotor, cameras_by_frame, observations_by_frame, times,
                 initial_states, config=None, contour_cameras_by_frame=None):
        self.rotor = rotor
        self.config = copy.deepcopy(config or WindowFitConfig())
        self.cameras = cameras_by_frame
        self.contour_cameras = contour_cameras_by_frame or cameras_by_frame
        self.observations = observations_by_frame
        self.times = np.asarray(times, float)
        self.initial = np.asarray(initial_states, float).copy()
        self.F = len(self.times)
        self.active = np.asarray(self.config.active_state_indices, int)
        if self.initial.shape != (self.F, N_STATE) or not np.isfinite(self.initial).all():
            raise ValueError('initial_states must have finite (frames,13) values')
        if self.F < 1 or not np.isfinite(self.times).all() or np.any(np.diff(self.times) <= 0):
            raise ValueError('finite strictly increasing actual timestamps required')
        if len(set(self.active)) != len(self.active) or np.any((self.active < 0) | (self.active >= N_STATE)):
            raise ValueError('active indices must be distinct valid state indices')
        if len(self.cameras) != self.F or len(self.observations) != self.F or len(self.contour_cameras) != self.F:
            raise ValueError('camera/observation count differs from timestamp count')
        for cc, oo in zip(self.contour_cameras, self.observations):
            if len(cc) != len(oo):
                raise ValueError('one contour observation per contour camera required')
            for cam, obs in zip(cc, oo):
                if obs.mask.shape != (cam.H, cam.W):
                    raise ValueError('contour mask and contour camera dimensions disagree')
        for name in ('state_lower', 'state_upper', 'state_scale', 'prior_mean', 'prior_sigma', 'acceleration_sigma'):
            array = np.asarray(getattr(self.config, name), float)
            if array.shape != (N_STATE,) or not np.isfinite(array).all():
                raise ValueError(name + ' requires 13 finite values')
        if np.any(np.asarray(self.config.state_lower) >= np.asarray(self.config.state_upper)):
            raise ValueError('state bounds must be ordered')
        for name in ('state_scale', 'prior_sigma', 'acceleration_sigma'):
            if np.min(getattr(self.config, name)) <= 0:
                raise ValueError(name + ' must be positive')
        if self.config.max_nfev < 1 or min(self.config.contour_sigma_px, self.config.track_sigma_px) <= 0:
            raise ValueError('positive optimization budget and pixel noise scales required')
        if not isinstance(self.config.azimuth_unbounded, bool):
            raise ValueError('azimuth_unbounded must be explicit bool')
        difference_step = np.asarray(self.config.finite_difference_relative_step, float)
        if difference_step.ndim > 1 or (difference_step.ndim == 1 and difference_step.shape != (15,)) or not np.isfinite(difference_step).all() or np.any(difference_step <= 0):
            raise ValueError('finite_difference_relative_step requires positive scalar or 15 canonical values')
        # Unwrap degrees in psi, then pitch. This does not repair 120-degree
        # blade identity swaps; identity is an explicit candidate diagnostic.
        for idx in (0, 1, 2, 3):
            if idx in self.active:
                # Add exact whole turns instead of radians→degrees round
                # trips, so unchanged and fixed angle values remain bitwise.
                raw = self.initial[:, idx].copy()
                unwrapped = np.degrees(np.unwrap(np.radians(raw)))
                self.initial[:, idx] = raw + 360. * np.rint((unwrapped - raw) / 360.)
        lower, upper = np.asarray(self.config.state_lower), np.asarray(self.config.state_upper)
        self.initial[:, self.active] = np.clip(self.initial[:, self.active], lower[self.active], upper[self.active])
        self.A = len(self.active)
        self.n_state = self.F * self.A
        self._model_support = []
        for f in range(self.F):
            masks = []
            for cam, obs in zip(self.contour_cameras[f], self.observations[f]):
                sil = recon.densify(recon.silhouettes(rotor, cam, self.initial[f])).reshape(-1, 2)
                valid = (np.isfinite(sil).all(1) & (sil[:, 0] >= 0) & (sil[:, 0] <= cam.W - 1)
                         & (sil[:, 1] >= 0) & (sil[:, 1] <= cam.H - 1))
                masks.append(valid & bool(obs.has))
            self._model_support.append(masks)

    def initialize_q(self, tracks):
        q, logs = [], []
        for track in tracks:
            visible = [o for o in track['observations'] if o['image_visible']]
            if not visible:
                raise ValueError('track %s has no independently confirmed visible RGB observation' % track['id'])
            first = min(visible, key=lambda o: (o['frame_index'], o['camera_index']))
            f, c = int(first['frame_index']), int(first['camera_index'])
            hit = intersect_pixel(self.rotor, self.initial[f], self.cameras[f][c], first['uv'], int(track['blade_id']))
            log = {'track_id': track['id'], 'frame_index': f, 'camera_index': c,
                   'pixel': first['uv'], 'method': 'first_RGB_pixel_ray_on_declared_template_at_initial_state',
                   'rebind_each_frame': False, 'hit': hit}
            logs.append(log)
            if hit is None or hit['q'] is None:
                # Deliberate hard failure: no nearest vertex or hidden source
                # binding is substituted for a missed RGB ray.
                raise ValueError('INITIAL_RAY_MISS for track %s at pixel %s; initialization log=%s' %
                                 (track['id'], first['uv'], logs))
            q.append(hit['q'])
        return np.asarray(q, float).reshape(-1, 2), logs

    def _prepare_tracks(self, tracks, q_initial):
        self.tracks = copy.deepcopy(tracks or [])
        self.point_slots, rejected = [], []
        ids = [t['id'] for t in self.tracks]
        if len(ids) != len(set(ids)):
            raise ValueError('track identities must be unique')
        for j, track in enumerate(self.tracks):
            if int(track['blade_id']) not in (0, 1, 2):
                raise ValueError('explicit blade identity must be 0,1,2')
            for o in track['observations']:
                f, c = int(o['frame_index']), int(o['camera_index'])
                if not 0 <= f < self.F or not 0 <= c < len(self.cameras[f]):
                    raise ValueError('track frame/camera index outside input window')
                if not isinstance(o['image_visible'], bool):
                    raise ValueError('RGB visibility must be independently explicit')
                if not o['image_visible']:
                    rejected.append({'track_id': track['id'], 'frame_index': f, 'camera_index': c,
                                     'reason': 'annotated_not_image_supported'})
                    continue
                uv = np.asarray(o['uv'], float)
                cam = self.cameras[f][c]
                if uv.shape != (2,) or not np.isfinite(uv).all() or not (0 <= uv[0] <= cam.W - 1 and 0 <= uv[1] <= cam.H - 1):
                    raise ValueError('confirmed track point must be inside original image')
                w = float(o.get('weight', 1))
                if not np.isfinite(w) or w <= 0:
                    raise ValueError('track weights must be finite positive')
                self.point_slots.append((j, f, c, uv, w, track.get('group', track['id'])))
        if q_initial is None:
            self.q0, initialization = self.initialize_q(self.tracks)
        else:
            self.q0 = np.asarray(q_initial, float).reshape(-1, 2).copy()
            if self.q0.shape != (len(self.tracks), 2):
                raise ValueError('q_initial must have one shared q per declared track')
            initialization = [{'track_id': t['id'], 'method': 'explicit_solver_binding_reuse',
                               'rebind_each_frame': False} for t in self.tracks]
        if not np.isfinite(self.q0).all() or np.any((self.q0[:, 0] < 0) | (self.q0[:, 0] > 1)):
            raise ValueError('invalid canonical q initialization')
        group_counts = Counter(slot[-1] for slot in self.point_slots)
        self.slot_scales = [self.config.track_weight * np.sqrt(slot[4] /
                           (group_counts[slot[-1]] if self.config.normalize_track_groups else 1))
                            for slot in self.point_slots]
        self.track_preparation = {'initialization': initialization, 'rejected_observations': rejected,
                                  'confirmed_observation_count': len(self.point_slots),
                                  'total_annotation_count': len(self.point_slots) + len(rejected),
                                  'frozen_observation_set': True, 'group_observation_counts': dict(group_counts)}

    def _unpack(self, p):
        states = self.initial.copy()
        states[:, self.active] = p[:self.n_state].reshape(self.F, self.A)
        return states, p[self.n_state:].reshape(-1, 2)

    def _data_blocks(self, p, raw=False):
        states, qq = self._unpack(p)
        blocks = []
        for f in range(self.F):
            for c, (cam, obs) in enumerate(zip(self.contour_cameras[f], self.observations[f])):
                sil0, rings = recon.silhouettes(self.rotor, cam, states[f], with_rings=True)
                sil = recon.densify(sil0)
                uv = sil.reshape(-1, 2)
                finite = np.isfinite(uv).all(1)
                clipped = np.clip(np.nan_to_num(uv, nan=0.), [0., 0.], [cam.W - 1., cam.H - 1.])
                outside = np.linalg.norm(np.nan_to_num(uv, nan=0.) - clipped, axis=1)
                d = obs.sample_dt(uv) if raw else obs.sample_dt(clipped) + outside
                d = np.nan_to_num(d, nan=2 * np.hypot(cam.W, cam.H))
                d[~finite] = 2 * np.hypot(cam.W, cam.H)
                d = np.where(self._model_support[f][c], d, 0.)
                if obs.has:
                    dd = np.fmin(recon.point_to_polylines(obs.pts, np.concatenate([sil[:, :, 0], sil[:, :, 1]], 0)),
                                 recon.point_to_polylines(obs.pts, rings.reshape(-1, rings.shape[2], 2)))
                    dd = np.nan_to_num(dd, nan=2 * np.hypot(cam.W, cam.H))
                else:
                    dd = np.zeros(len(obs.pts))
                image = np.r_[d, self.config.contour_observation_weight * dd] / self.config.contour_sigma_px
                if not raw:
                    image = recon.robust(image, self.config.contour_robust_px / self.config.contour_sigma_px)
                blocks.append((image, [f], None, 'contour'))
        meshes = {}
        for slot, scale in zip(self.point_slots, self.slot_scales):
            j, f, c, observed_uv, _, _ = slot
            if f not in meshes:
                meshes[f] = self.rotor.forward(states[f])
            vertices, axis = meshes[f]
            point, normal, _ = point_from_vertices(self.rotor, vertices, axis, self.tracks[j]['blade_id'], qq[j])
            cam = self.cameras[f][c]
            uv, depth = _finite_uv(cam, point, self.config.min_depth_m)
            if raw:
                real_uv, _ = cam.project(point[None])
                uv_data = np.nan_to_num(real_uv[0], nan=1e6)
            else:
                uv_data = uv
            image = (uv_data - observed_uv) / self.config.track_sigma_px
            if not raw:
                image = recon.robust(image, self.config.track_robust_sigma)
            view = cam.center - point
            facing = float(normal @ (view / max(np.linalg.norm(view), 1e-12)))
            outside = np.linalg.norm(uv - np.clip(uv, [0, 0], [cam.W - 1, cam.H - 1]))
            barriers = np.array([max(0., self.config.min_depth_m - depth) * cam.K[0, 0] / self.config.min_depth_m,
                                 outside, max(0., -facing) * self.config.backface_penalty_px]) / self.config.track_sigma_px
            # The domain penalties are quadratic, and never become zero by
            # rejecting a point that the input annotation confirmed visible.
            blocks.append((scale * (image if raw else np.r_[image, barriers]), [f], j, 'track'))
        return blocks

    def _prior_blocks(self, p):
        states, _ = self._unpack(p)
        cc = self.config
        blocks = []
        for f in range(self.F):
            delta = (states[f, self.active] - np.asarray(cc.prior_mean)[self.active]) / np.asarray(cc.prior_sigma)[self.active]
            blocks.append((cc.prior_weight * delta, [f], None, 'physical_prior'))
        dt = np.diff(self.times)
        velocity = np.diff(states[:, self.active], axis=0) / dt[:, None]
        if cc.velocity_sigma is not None:
            sigma = np.asarray(cc.velocity_sigma, float)
            if sigma.shape != (N_STATE,) or np.min(sigma) <= 0:
                raise ValueError('velocity_sigma requires 13 positive values')
            v0 = np.diff(self.initial[:, self.active], axis=0) / dt[:, None]
            for f in range(self.F - 1):
                blocks.append((cc.velocity_weight * (velocity[f] - v0[f]) / sigma[self.active],
                               [f, f + 1], None, 'velocity_minus_initial_prior'))
        for f in range(self.F - 2):
            acceleration = (velocity[f + 1] - velocity[f]) / ((dt[f + 1] + dt[f]) / 2)
            blocks.append((cc.acceleration_weight * acceleration / np.asarray(cc.acceleration_sigma)[self.active],
                           [f, f + 1, f + 2], None, 'acceleration_prior'))
        return blocks

    def residuals(self, p, data_only=False, raw=False):
        blocks = self._data_blocks(p, raw)
        if not data_only:
            blocks += self._prior_blocks(p)
        return np.concatenate([b[0] for b in blocks]) if blocks else np.empty(0)

    def _sparsity(self, p):
        blocks = self._data_blocks(p) + self._prior_blocks(p)
        sparsity = lil_matrix((sum(len(b[0]) for b in blocks), len(p)), dtype=int)
        row = 0
        for values, frames, qj, _ in blocks:
            cols = [f * self.A + k for f in frames for k in range(self.A)]
            if qj is not None:
                cols += [self.n_state + 2 * qj, self.n_state + 2 * qj + 1]
            if cols and len(values):
                sparsity[row:row + len(values), cols] = 1
            row += len(values)
        return sparsity.tocsr()

    def data_sensitivity(self, p, scales):
        """Finite differences of standardized *data only*, prior excluded.

        Shared q is removed with J_eff=(I-Jq Jq^+) Jx. The report is local
        sensitivity, never a confidence interval or a uniqueness certificate.
        """
        base = self.residuals(p, data_only=True, raw=True)
        jac = np.empty((len(base), len(p)))
        for k in range(len(p)):
            step = self.config.sensitivity_step * scales[k]
            plus, minus = p.copy(), p.copy()
            plus[k] += step; minus[k] -= step
            # Canonical span has a real boundary; use one-sided differences.
            if k >= self.n_state and (k - self.n_state) % 2 == 0 and (minus[k] < 0 or plus[k] > 1):
                move = plus if minus[k] < 0 else minus
                jac[:, k] = (self.residuals(move, True, True) - base) / (move[k] - p[k]) * scales[k]
            else:
                jac[:, k] = (self.residuals(plus, True, True) - self.residuals(minus, True, True)) / (2 * step) * scales[k]
        jx, jq = jac[:, :self.n_state], jac[:, self.n_state:]
        eff = jx - jq @ (np.linalg.pinv(jq, rcond=1e-8) @ jx) if jq.size else jx
        singular = np.linalg.svd(eff, compute_uv=False) if eff.size else np.empty(0)
        raw_singular = np.linalg.svd(jx, compute_uv=False) if jx.size else np.empty(0)
        threshold = max(1e-8, float(singular[0]) * 1e-6) if len(singular) else 1e-8
        return {'residual_kind': 'standardized_raw_pixel_contour_and_track_rows_only',
                'prior_rows_included': False, 'state_column_count': self.n_state,
                'domain_or_visibility_penalty_rows_included': False,
                'invalid_projection_rule': 'fixed diagnostic sentinel; no domain barrier derivative adds rank',
                'q_column_count': jq.shape[1], 'state_scales': scales[:self.n_state].tolist(),
                'state_singular_values_before_q_elimination': raw_singular.tolist(),
                'state_singular_values_after_q_elimination': singular.tolist(),
                'rank_after_q_elimination': int(np.count_nonzero(singular > threshold)),
                'rank_threshold': threshold,
                'effective_state_column_norms': np.linalg.norm(eff, axis=0).reshape(self.F, self.A).tolist(),
                'active_state_names': [STATE_NAMES[i] for i in self.active],
                'interpretation': 'local data sensitivity only; concentrated repair points do not establish global identifiability'}

    def solve(self, tracks=None, q_initial=None, compute_sensitivity=True):
        self._prepare_tracks(tracks, q_initial)
        cc = self.config
        initial = np.r_[self.initial[:, self.active].reshape(-1), self.q0.reshape(-1)]
        state_lower = np.tile(np.asarray(cc.state_lower)[self.active], self.F)
        state_upper = np.tile(np.asarray(cc.state_upper)[self.active], self.F)
        if cc.azimuth_unbounded and 0 in self.active:
            phase_column = int(np.flatnonzero(self.active == 0)[0])
            state_lower[phase_column::self.A] = -np.inf
            state_upper[phase_column::self.A] = np.inf
        q_lower = self.q0 - [cc.q_span_delta, cc.q_perimeter_delta]
        q_upper = self.q0 + [cc.q_span_delta, cc.q_perimeter_delta]
        q_lower[:, 0] = np.maximum(q_lower[:, 0], 0)
        q_upper[:, 0] = np.minimum(q_upper[:, 0], 1)
        lower, upper = np.r_[state_lower, q_lower.reshape(-1)], np.r_[state_upper, q_upper.reshape(-1)]
        scales = np.r_[np.tile(np.asarray(cc.state_scale)[self.active], self.F), np.tile(cc.q_scale, len(self.tracks))]
        initial_data = self.residuals(initial, data_only=True)
        if not len(initial):
            raise ValueError('no active states and no material tracks to optimize')
        difference_step = np.asarray(cc.finite_difference_relative_step, float)
        if difference_step.ndim == 0:
            difference_step = float(difference_step)
        else:
            difference_step = np.r_[np.tile(difference_step[:N_STATE][self.active], self.F),
                                    np.tile(difference_step[N_STATE:], len(self.tracks))]
        res = least_squares(self.residuals, initial, bounds=(lower, upper), loss='linear',
                            x_scale=scales, jac_sparsity=self._sparsity(initial),
                            diff_step=difference_step,
                            max_nfev=cc.max_nfev, ftol=cc.ftol, xtol=cc.xtol, gtol=cc.gtol)
        states, qq = self._unpack(res.x)
        visibility = []
        meshes = {}
        for j, f, c, observed_uv, _, _ in self.point_slots:
            if f not in meshes:
                meshes[f] = self.rotor.forward(states[f])
            vv, axis = meshes[f]
            check = point_visibility(self.rotor, vv, axis, self.cameras[f][c], self.tracks[j]['blade_id'], qq[j])
            check.update({'track_id': self.tracks[j]['id'], 'frame_index': f, 'camera_index': c,
                          'confirmed_image_visible': True, 'observed_uv': observed_uv.tolist()})
            check['contradiction'] = bool(not check['in_frame'] or check['depth_m'] <= cc.min_depth_m or
                                          check['back_facing'] or check['occluded_by_estimated_blade'])
            check['uv'] = [float(v) if np.isfinite(v) else None for v in check['uv']]
            visibility.append(check)
        contradictions = sum(v['contradiction'] for v in visibility)
        solver = {'status': int(res.status), 'success': bool(res.success), 'message': str(res.message),
                  'nfev': int(res.nfev), 'njev': None if res.njev is None else int(res.njev),
                  'cost': float(res.cost), 'optimality': float(res.optimality),
                  'raw_gradient_inf_norm': float(np.max(np.abs(res.jac.T @ res.fun))),
                  'max_nfev': cc.max_nfev, 'least_squares_loss': 'linear',
                  'azimuth_bound_policy': 'unbounded unwrapped phase' if cc.azimuth_unbounded else 'configured finite bounds',
                  'finite_difference_relative_step': difference_step.tolist() if isinstance(difference_step, np.ndarray) else difference_step,
                  'pixel_robustification_count': 1,
                  'initial_data_sum_squared': float(initial_data @ initial_data),
                  'final_data_sum_squared': float(np.sum(self.residuals(res.x, True) ** 2)),
                  'bound_hit_count': int(np.count_nonzero(res.active_mask)),
                  'residual_dimension': len(res.fun), 'jac_sparsity_nnz': self._sparsity(initial).nnz}
        diagnostics = {'track_preparation': self.track_preparation, 'visibility': visibility,
                       'visibility_contradiction_count': contradictions,
                       'experiment_status': 'VISIBILITY_CONTRADICTION' if contradictions else
                                            ('SOLVER_TERMINATED_REVIEW_ONLY' if res.success else 'SOLVER_NOT_CONVERGED_REVIEW_ONLY'),
                       'confirmed_points_dropped_for_model_visibility': 0,
                       'time_prior': {'angle_unwrap': '360-degree unwrap of psi/pitch, identity fixed',
                                      'dt_seconds': np.diff(self.times).tolist(),
                                      'acceleration_formula': '(next_velocity - previous_velocity)/mean_adjacent_dt',
                                      'units': 'degree/s^2 for angles; meter/s^2 for deformation',
                                      'normal_constant_rotation_residual': 0.0,
                                      'velocity_prior': 'disabled' if cc.velocity_sigma is None else 'velocity-minus-A0-initial-velocity'},
                       'model_contour_slots': 'frozen initial in-frame support; invalid supported projections penalized',
                       'scope': 'declared healthy template; source-template bias is an independent evaluation question'}
        if compute_sensitivity:
            diagnostics['data_sensitivity'] = self.data_sensitivity(res.x, scales)
        bindings = [{'track_id': t['id'], 'blade_id': int(t['blade_id']), **chart_binding(self.rotor, q)}
                    for t, q in zip(self.tracks, qq)]
        return WindowFitResult(states, qq, bindings, solver, diagnostics, asdict(cc))

    def fit_material_only(self, states, tracks, q_initial=None, max_nfev=30, compute_sensitivity=True):
        config = copy.deepcopy(self.config)
        config.active_state_indices = []
        config.max_nfev = max_nfev
        fixed = WindowSurfaceFitter(self.rotor, self.cameras, self.observations, self.times,
                                    states, config, self.contour_cameras)
        result = fixed.solve(tracks, q_initial, compute_sensitivity)
        result.diagnostics['role'] = 'fixed_geometry_material_localization_sidecar; no state optimization'
        return result
