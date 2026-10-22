"""S2/S3 reconstruction from ranges only; reference geometry never enters here."""
from dataclasses import dataclass
import math
import numpy as np

from .physics import first_hit
from .moving_tower import background_hit


METHOD_VERSIONS = {
    'hub-axis.v1': 'two-point-hub-extrapolation.v1',
    'hub-tls.v1': 'hub-constrained-tls.v1',
}


@dataclass(frozen=True)
class PairLimits:
    min_range_m: float = 5.
    max_range_m: float = 100.
    min_separation_m: float = .25
    min_orientation_projection_m: float = .01

    def valid(self):
        v = (self.min_range_m, self.max_range_m, self.min_separation_m,
             self.min_orientation_projection_m)
        return (all(type(x) in (int, float) and math.isfinite(x) for x in v)
                and 0 <= v[0] < v[1] and v[2] > 0 and v[3] > 0)


def vector(value):
    a = np.asarray(value, dtype=float)
    if a.shape != (3,) or not np.isfinite(a).all():
        raise ValueError('invalid_vector')
    return a


def validate_calibration(config):
    if str(config.get('status', '')).startswith('REJECTED'):
        raise ValueError('Rejected installation cannot be used for measurement')
    if config.get('schema') != 'wfrl.dual-beam-calibration.v1':
        raise ValueError('Unsupported dual-beam calibration')
    origins = np.asarray(config['origins_m'], float)
    angles = np.asarray(config['angles_deg'], float)
    if origins.shape != (3, 3) or not np.isfinite(origins).all():
        raise ValueError('Invalid beam origins')
    if angles.shape != (3,) or not np.isfinite(angles).all() or not np.all((angles > 0) & (angles < 90)):
        raise ValueError('Invalid beam angles')
    if np.any(np.diff(angles) <= 0):
        raise ValueError('S1/S2/S3 angles must increase toward rotor')
    if config['common_origin_approximation'] and not np.array_equal(origins, np.tile(origins[0], (3, 1))):
        raise ValueError('Common-origin declaration differs from origins')
    limits = PairLimits(*config['range_m'], config['min_point_separation_m'],
                        config['min_orientation_projection_m'])
    if not limits.valid():
        raise ValueError('Invalid measurement limits')
    for key in ('effective_length_m', 'measurement_hold_s', 'alarm_hold_s', 'expected_window_half_angle_deg'):
        if not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError('Invalid calibration: ' + key)
    if config['expected_window_half_angle_deg'] >= 60:
        raise ValueError('Expected blade windows must not overlap')
    a = np.radians(angles)
    return origins, np.column_stack((-np.sin(a), np.zeros(3), -np.cos(a))), limits


def validate_inner_origins(origins, hub_m, shaft_tilt_deg, nacelle_x_extent_m, nacelle_half_width_m):
    """Reject rotor-front or off-nacelle placements before optical scoring.

    This checks the permitted mounting region, not blade/housing separation.
    The latter still requires a swept source-geometry audit.
    """
    points = np.asarray(origins, float)
    hub = vector(hub_m)
    angle = math.radians(shaft_tilt_deg)
    toward_nacelle = np.array([math.cos(angle), 0., math.sin(angle)])
    lo, hi = nacelle_x_extent_m
    if (points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all()
            or np.any((points-hub) @ toward_nacelle <= 0)
            or np.any(points[:, 0] < lo) or np.any(points[:, 0] > min(hi, 0.))
            or np.any(np.abs(points[:, 1]) > nacelle_half_width_m)):
        raise ValueError('Installation must remain on the inner side between rotor and tower, within the nacelle footprint')


def observe(time_s, origin, direction, blades, blade_triangles, tower, tower_triangles, limits=PairLimits()):
    """All blade surfaces, opaque moving tower and ground compete before range filtering."""
    row = dict(time_s=float(time_s), origin_m=None, direction=None, slant_range_m=None,
               point_m=None, first_object=None, blade_id=None, valid=False, observed=False,
               reason='invalid_geometry_or_calibration',
               time_support='instantaneous_source_geometry',
               sensor_model='ideal_geometric_first_hit_not_hardware_DP')
    try:
        o, d = vector(origin), vector(direction)
        if not math.isfinite(time_s) or not limits.valid() or not np.isclose(np.linalg.norm(d), 1, atol=1e-10, rtol=0):
            return row
        if len(blades) != 3 or not np.isfinite(blades).all() or not np.isfinite(tower).all():
            return row
        row.update(origin_m=o.tolist(), direction=d.tolist())
        hits = []
        bg = background_hit(o, d, tower, tower_triangles)
        if bg:
            hits.append((bg[0], bg[1], bg[2], None))
        for b, points in enumerate(blades, 1):
            hit = first_hit(o, d, points, blade_triangles)
            if hit:
                hits.append((hit[0], hit[1], 'blade', b))
        row['observed'] = True
        if not hits:
            row['reason'] = 'no_return'
            return row
        distance, point, kind, blade = min(hits, key=lambda x: x[0])
        row.update(slant_range_m=distance, point_m=point, first_object=kind, blade_id=blade)
        in_range = limits.min_range_m <= distance <= limits.max_range_m
        row['valid'] = kind == 'blade' and in_range
        row['reason'] = 'valid' if row['valid'] else ('out_of_range' if not in_range else kind + '_first')
        return row
    except (ValueError, TypeError, IndexError):
        return row


def reconstruct(s2, s3, hub_m, effective_length_m, clearance_at, *, time_s,
                limits=PairLimits(), method='hub-axis.v1'):
    """clearance_at accepts only an estimated point and a declared tower model.

    No truth tip, axis, clearance, old estimate, or per-frame length is accepted.
    The default uses the original two-point secant. ``hub-tls.v1`` fits an
    equal-weight line through the known hub to the two reconstructed points;
    its direction is a structural approximation, not a measured flexible axis.
    """
    result = dict(valid=False, reason=None, blade_id=None, p2_m=None, p3_m=None,
                  separation_m=None, direction=None, hub_m=None,
                  effective_length_m=effective_length_m, tip_estimate_m=None,
                  clearance_estimate=None, method=method)
    def invalid(reason):
        result['reason'] = reason
        return result
    if not isinstance(method, str) or method not in METHOD_VERSIONS:
        return invalid('unsupported_reconstruction_method')
    if (not limits.valid() or not isinstance(effective_length_m, (int, float))
            or not math.isfinite(effective_length_m) or effective_length_m <= 0):
        return invalid('invalid_calibration')
    if any(r.get('time_support', 'instantaneous_source_geometry') != 'instantaneous_source_geometry'
           for r in (s2, s3)):
        return invalid('not_instantaneous_observations')
    if not isinstance(time_s, (int, float)) or not math.isfinite(time_s) or any(r.get('time_s') != time_s for r in (s2, s3)):
        return invalid('not_same_time')
    for name, row in (('s2', s2), ('s3', s3)):
        if not row.get('observed') or not row.get('valid') or row.get('first_object') != 'blade':
            return invalid(name + '_' + row.get('reason', 'invalid'))
    if s2.get('blade_id') not in (1, 2, 3) or s2.get('blade_id') != s3.get('blade_id'):
        return invalid('different_blades')
    try:
        h = vector(hub_m)
        points = []
        for row in (s2, s3):
            o, d = vector(row['origin_m']), vector(row['direction'])
            distance = row['slant_range_m']
            if (not np.isclose(np.linalg.norm(d), 1, atol=1e-10, rtol=0)
                    or not math.isfinite(distance) or not limits.min_range_m <= distance <= limits.max_range_m):
                return invalid('invalid_range_or_direction')
            with np.errstate(over='ignore', invalid='ignore'):
                points.append(vector(o + distance * d))
        p2, p3 = points
        separation = float(np.linalg.norm(p3 - p2))
        result.update(blade_id=s2['blade_id'], p2_m=p2.tolist(), p3_m=p3.tolist(),
                      separation_m=separation, hub_m=h.tolist())
        if not math.isfinite(separation):
            return invalid('invalid_input')
        if separation < limits.min_separation_m:
            return invalid('points_too_close')
        # Retain the original pair-validity guard for both methods, including
        # its arithmetic, so choosing TLS never admits a previously rejected
        # pair. TLS still needs its own outward sign to be determinable.
        u = (p3 - p2) / separation
        projection = float(u @ ((p2 + p3) / 2 - h))
        if not math.isfinite(projection):
            return invalid('invalid_input')
        if abs(projection) < limits.min_orientation_projection_m:
            return invalid('ambiguous_root_to_tip')
        if method == 'hub-tls.v1':
            # Do not center these rows: centering would discard the hub
            # constraint and turn this back into the two-point secant.
            with np.errstate(over='ignore', invalid='ignore'):
                relative = np.stack((p2 - h, p3 - h))
            if not np.isfinite(relative).all():
                return invalid('tls_numerical_failure')
            scale = float(np.max(np.abs(relative)))
            if scale == 0:
                return invalid('tls_direction_not_unique')
            scaled = relative / scale
            try:
                _, singular, axes = np.linalg.svd(scaled, full_matrices=False)
            except np.linalg.LinAlgError:
                return invalid('tls_numerical_failure')
            if not np.isfinite(singular).all() or not np.isfinite(axes).all():
                return invalid('tls_numerical_failure')
            # This is a floating-point uniqueness test, not a tuned geometric
            # filter. Equal leading singular values leave the TLS axis free.
            tolerance = 8 * np.finfo(float).eps * max(scaled.shape) * singular[0]
            if singular[0] == 0 or singular[0] - singular[1] <= tolerance:
                return invalid('tls_direction_not_unique')
            u = axes[0]
            with np.errstate(over='ignore', invalid='ignore'):
                projection = float((u @ (scaled.mean(axis=0))) * scale)
        if not math.isfinite(projection):
            return invalid('tls_numerical_failure' if method == 'hub-tls.v1' else 'invalid_input')
        if abs(projection) < limits.min_orientation_projection_m:
            return invalid('ambiguous_root_to_tip')
        if projection < 0:
            u = -u
        tip = h + effective_length_m * u
        if not np.isfinite(tip).all():
            return invalid('invalid_input')
        result.update(direction=u.tolist(), tip_estimate_m=tip.tolist())
        try:
            clearance = float(clearance_at(tip))
        except ValueError:
            return invalid('no_tower_section_at_estimated_height')
        if not math.isfinite(clearance):
            return invalid('invalid_tower_clearance')
        result.update(valid=True, reason='valid', clearance_estimate=clearance)
        return result
    except (ValueError, TypeError, KeyError):
        return invalid('invalid_input')


def s1_state(observation):
    """Raw sample alarm, independent of pair reconstruction and numerical clearance."""
    if not observation.get('observed'):
        return 'unknown'
    if observation.get('valid') and observation.get('first_object') == 'blade':
        return 'triggered'
    if observation.get('reason') == 'out_of_range':
        return 'unknown'
    return 'not_triggered'
