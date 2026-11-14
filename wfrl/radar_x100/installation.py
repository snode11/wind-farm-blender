"""Finite-grid installation ranking with explicit denominators and missing data."""
import itertools
from dataclasses import asdict, dataclass
import numpy as np
from .schema import digest, PROFILE, UNIT_ENVELOPE
from .sampling import AREA_ALGORITHM

SCHEMA = 'x100-installation-search.v2'
SEARCH_REPORT_MAX_BYTES = 16 * 1024 * 1024
SEARCH_SUMMARY_SUFFIX = '.summary.json'
PASSAGE_METRIC = 'DISTAL_ROI_PASSAGE_DETECTIONS.v1'
DEFAULT_ROI = (-15., -15., -70., -2.5, 15., -40.)
_PASSAGE_RANK_BOUNDS = dict(worst_passage_fraction=1., longest_known_miss_duration_s=np.inf,
                            nadir_support_fraction=1., worst_blade_fraction=1., tower_error_rmse_m=np.inf,
                            points_per_frame_mean=np.inf)


def _finite_number(value, lower=-np.inf, upper=np.inf):
    return (isinstance(value, (int, float, np.integer, np.floating))
            and not isinstance(value, (bool, np.bool_)) and np.isfinite(value) and lower <= value <= upper)


def downward_rotation(longitudinal_deg, lateral_deg, rotor_sign=-1.):
    """XYZ Euler angles for longitudinal and lateral tilts of the down-facing aperture."""
    if (not np.isfinite([longitudinal_deg, lateral_deg, rotor_sign]).all()
            or abs(longitudinal_deg) > 20 or abs(lateral_deg) > 20 or rotor_sign not in (-1, 1)):
        raise ValueError('Research tilts must be within 20 degrees and rotor sign must be +/-1')
    p, r = np.radians([90 + rotor_sign * longitudinal_deg, lateral_deg])
    cp, sp, cr, sr = np.cos(p), np.sin(p), np.cos(r), np.sin(r)
    base = np.array([[-cp, 0., -sp], [0., -1., 0.], [-sp, 0., cp]])
    matrix = np.array([[1., 0., 0.], [0., cr, -sr], [0., sr, cr]]) @ base
    pitch = np.arctan2(-matrix[2, 0], np.hypot(matrix[0, 0], matrix[1, 0]))
    if abs(np.cos(pitch)) < 1e-10:
        roll, yaw = 0., np.pi
    else:
        roll = np.arctan2(matrix[2, 1], matrix[2, 2])
        yaw = np.arctan2(matrix[1, 0], matrix[0, 0])
    return tuple(float(x) for x in np.degrees([roll, pitch, yaw]))


def clip_triangle(triangle, values, lower, upper):
    """Clip a triangle by linearly interpolated scalar coordinates, retaining boundary points."""
    triangle, values = np.asarray(triangle, float), np.asarray(values, float)
    lower, upper = np.asarray(lower, float), np.asarray(upper, float)
    if (triangle.shape != (3, 3) or values.ndim != 2 or values.shape[0] != 3
            or lower.shape != (values.shape[1],) or upper.shape != lower.shape
            or not all(np.isfinite(x).all() for x in (triangle, values, lower, upper))
            or np.any(lower > upper)):
        raise ValueError('Expected finite triangle coordinates and ordered clipping bounds')
    polygon = np.column_stack((triangle, values))
    for axis, (lo, hi) in enumerate(zip(lower, upper), start=3):
        for bound, sign in ((lo, 1), (hi, -1)):
            output = []
            for a, b in zip(polygon, np.roll(polygon, -1, axis=0)):
                da, db = sign * (a[axis] - bound), sign * (b[axis] - bound)
                if da >= 0:
                    output.append(a)
                if (da >= 0) != (db >= 0):
                    output.append(a + da / (da - db) * (b - a))
            if not output:
                return []
            polygon = np.asarray(output)
    if len(polygon) < 3:
        polygon = np.concatenate((polygon, np.repeat(polygon[-1:], 3 - len(polygon), axis=0)))
    return [polygon[[0, i, i + 1], :3] for i in range(1, len(polygon) - 1)]


def mesh_clearance(first, second):
    """Minimum surface distance; degenerate triangles retain their segments and points."""
    a, b = np.asarray(first, float), np.asarray(second, float)
    if not len(a) or not len(b):
        return None
    if any(x.ndim != 3 or x.shape[1:] != (3, 3) or not np.isfinite(x).all() for x in (a, b)):
        raise ValueError('Expected finite triangle surfaces')
    best = float(np.min(np.sum((b - a[0, 0]) ** 2, axis=2)))
    blo, bhi = b.min(axis=1), b.max(axis=1)
    for start in range(0, len(a), 64):
        block = a[start:start + 64]
        separation = np.maximum(0., np.maximum(block.min(axis=1)[:, None] - bhi,
                                               blo - block.max(axis=1)[:, None]))
        i, j = np.nonzero(np.einsum('ijk,ijk->ij', separation, separation) <= best + 1e-12)
        if not len(i):
            continue
        left, right = block[i], b[j]
        for source, target in ((left, right), (right, left)):
            u, v = target[:, 1] - target[:, 0], target[:, 2] - target[:, 0]
            normal = np.cross(u, v)
            nn = np.einsum('ij,ij->i', normal, normal)
            uu, uv, vv = (np.einsum('ij,ij->i', x, y) for x, y in ((u, u), (u, v), (v, v)))
            valid = nn > 0
            denom = np.where(valid, nn, 1.)
            for k in range(3):
                p, q = source[:, k], source[:, (k + 1) % 3]
                signed = np.einsum('ij,ij->i', p - target[:, 0], normal)
                projected = p - normal * (signed / denom)[:, None]
                for point, distance in ((projected, signed ** 2 / denom),):
                    w = point - target[:, 0]
                    wu, wv = np.einsum('ij,ij->i', w, u), np.einsum('ij,ij->i', w, v)
                    beta, gamma = (wu * vv - wv * uv) / denom, (wv * uu - wu * uv) / denom
                    inside = valid & (beta >= -1e-10) & (gamma >= -1e-10) & (beta + gamma <= 1 + 1e-10)
                    if inside.any():
                        best = min(best, float(distance[inside].min()))
                step = np.einsum('ij,ij->i', q - p, normal)
                alpha = np.divide(-signed, step, out=np.full(len(step), np.inf), where=abs(step) > 1e-14)
                on_segment = (alpha >= 0) & (alpha <= 1)
                point = p + np.where(on_segment, alpha, 0.)[:, None] * (q - p)
                w = point - target[:, 0]
                wu, wv = np.einsum('ij,ij->i', w, u), np.einsum('ij,ij->i', w, v)
                beta, gamma = (wu * vv - wv * uv) / denom, (wv * uu - wu * uv) / denom
                if np.any(valid & on_segment & (beta >= 0) & (gamma >= 0) & (beta + gamma <= 1)):
                    return 0.
        for k, l in itertools.product(range(3), repeat=2):
            p, q = left[:, k], right[:, l]
            u, v = left[:, (k + 1) % 3] - p, right[:, (l + 1) % 3] - q
            w = p - q
            aa, bb, cc, dd, ee = (np.einsum('ij,ij->i', x, y)
                for x, y in ((u, u), (u, v), (v, v), (u, w), (v, w)))
            # Endpoint projections also cover parallel segments and zero-length edges.
            for endpoint, edge, length in ((w, v, cc), (w + u, v, cc), (-w, u, aa), (v - w, u, aa)):
                t = np.clip(np.divide(np.einsum('ij,ij->i', endpoint, edge), length,
                                      out=np.zeros(len(length)), where=length > 0), 0., 1.)
                delta = endpoint - t[:, None] * edge
                best = min(best, float(np.einsum('ij,ij->i', delta, delta).min()))
            denom = aa * cc - bb * bb
            nonparallel = denom > np.finfo(float).eps * aa * cc
            s = np.divide(bb * ee - cc * dd, denom, out=np.zeros(len(denom)), where=nonparallel)
            t = np.divide(aa * ee - bb * dd, denom, out=np.zeros(len(denom)), where=nonparallel)
            interior = nonparallel & (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
            if interior.any():
                delta = w + s[:, None] * u - t[:, None] * v
                best = min(best, float(np.einsum('ij,ij->i', delta, delta)[interior].min()))
    return float(np.sqrt(max(0., best)))


@dataclass(frozen=True)
class MechanicalPolicy:
    """Uncalibrated installation allowances, not manufacturer mechanical limits."""
    service_clearance_m: float = .02
    maximum_bracket_reach_m: float = .75
    motion_clearance_m: float = .10
    rear_inset_m: float = .50
    underside_drop_m: float = .50

    def __post_init__(self):
        if any(not np.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError('Mechanical research allowances must be finite and positive')


def profile_metadata(roi=DEFAULT_ROI, *, policy=None):
    """Keep hardware limits, evaluation ROI and unvalidated allowances independent."""
    bounds = np.asarray(roi, dtype=float)
    if bounds.shape != (6,) or not np.isfinite(bounds).all() or np.any(bounds[:3] >= bounds[3:]):
        raise ValueError('Invalid nacelle-local analysis ROI')
    policy = policy or MechanicalPolicy()
    return dict(
        hardware_profile=dict(id=PROFILE['id'], source=PROFILE['source'],
            source_document='CTSAI X100超级雷达数据手册-CN-V1.0_20260720160030.pdf',
            source_pages=dict(frequency_range_capacity=4, fov_dimensions_mounting=5),
            frequency_ghz=[76., 79.],
            dimensions_hwd_m=list(PROFILE['dimensions_hwd_m']),
            fov=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP',
                azimuth_deg=[-PROFILE['azimuth_deg'], PROFILE['azimuth_deg']],
                elevation_deg=[-PROFILE['elevation_deg'], PROFILE['elevation_deg']],
                interpretation='rectangular angular domain; not an antenna gain pattern'),
            range_m=list(PROFILE['range_m']), max_points=PROFILE['max_points']),
        analysis_roi=dict(frame='NACELLE', lower_m=bounds[:3].tolist(), upper_m=bounds[3:].tolist(),
            basis='RESEARCH_ASSUMPTION: nacelle-local distal passage region; CLI bounds are explicit research inputs',
            application='evaluation masks only; hardware gates and detection integration are unchanged'),
        comparison_devices=dict(applied_to_x100=False, profiles=[],
            note='Peer 80 GHz devices require separate sourced profiles; no peer limits enter this evaluation'),
        mechanical_policy=dict(parameters={k: dict(value=v, unit='m', basis='RESEARCH_ASSUMPTION')
                for k, v in asdict(policy).items()},
            unit_envelope=dict(dimensions_whd_m=list(UNIT_ENVELOPE['dimensions_whd_m']),
                               source=UNIT_ENVELOPE['source']),
            housing_axes='X=depth, Y=width, Z=height of the installed unit; envelope centred on the sensor origin',
            housing_axes_basis='RESEARCH_ASSUMPTION: no manufacturer phase-center or axis drawing',
            fixing=dict(method='bolt mounting', source='CTSAI X100 datasheet CN V1.0 PDF page 5',
                        hole_spacing_m=None, cable_envelope_m=None, bracket_cad=None),
            opening_policy='EXTERNAL_ONLY: no shell cuts or undocumented openings are assumed',
            physical_installation_approved=False))


def housing_half_extents():
    """Half extents of the installed unit along sensor X/Y/Z, centred on the sensor origin."""
    w, h, d = UNIT_ENVELOPE['dimensions_whd_m']
    return np.array([d, w, h]) / 2


def _shell_bounds(value):
    bounds = np.asarray(value, float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all() or np.any(bounds[0] >= bounds[1]):
        raise ValueError('Invalid shell geometry bounds')
    return bounds


def assess_front_mount(matrix, shell_bounds, rotor_x):
    """Front means the rotor-facing half of the bound shell, not the front end plate."""
    from .simulation import rigid_matrix
    matrix = rigid_matrix(matrix)
    lo, hi = _shell_bounds(shell_bounds)
    center_x = (lo[0] + hi[0]) / 2
    sign = np.sign(rotor_x - center_x)
    if not sign or not np.isfinite(rotor_x):
        raise ValueError('Rotor side must be defined by the source geometry')
    position, forward = matrix[:3, 3], matrix[:3, 0]
    front = bool(sign * (position[0] - center_x) > 0)
    return dict(front_half=front, below_shell=bool(position[2] < lo[2]),
        downward=bool(forward[2] < 0), location_m=position.tolist(), forward=forward.tolist(),
        angle_from_down_deg=float(np.degrees(np.arccos(np.clip(-forward[2], -1, 1)))),
        shell_bounds_m=[lo.tolist(), hi.tolist()], shell_midpoint_x_m=float(center_x),
        rotor_x_m=float(rotor_x), rotor_side='NEGATIVE_X' if sign < 0 else 'POSITIVE_X',
        meaning='rotor-facing longitudinal half; does not assert a front-face fixing or physical approval',
        source='bound nacelle mesh and turbine_geometry.hub_position; original pose from radar_x100/scene.py')


def static_constraints(matrix, shell_bounds, shell_distance_m, *, policy=None):
    """Conservative exterior housing/service space and opening-free angular exit.

    A separating plane of the shell AABB proves nonpenetration. The circumscribed
    angular cone is only a conservative mechanical exit test, not an RF model.
    Candidates needing a cutout are rejected because no cutout drawing is known.
    """
    from .simulation import rigid_matrix
    policy = policy or MechanicalPolicy()
    matrix = rigid_matrix(matrix)
    bounds = _shell_bounds(shell_bounds)
    if not _finite_number(shell_distance_m, 0):
        raise ValueError('Invalid shell geometry or surface distance')
    lo, hi = bounds
    position, forward = matrix[:3, 3], matrix[:3, 0]
    extent = np.abs(matrix[:3, :3]) @ housing_half_extents()
    lower, upper = position - extent, position + extent
    separation = np.maximum(lo - upper, lower - hi)
    outside = separation >= 0
    space = separation >= policy.service_clearance_m
    outward = np.where(lower >= hi, 1., -1.) * forward
    cone_cos = np.cos(np.radians(PROFILE['azimuth_deg'])) * np.cos(np.radians(PROFILE['elevation_deg']))
    cone_sin = np.sqrt(1 - cone_cos ** 2)
    minimum_outward = outward * cone_cos - np.sqrt(np.maximum(0., 1 - outward ** 2)) * cone_sin
    opening_free = bool(np.any(space & (minimum_outward >= 0)))
    reach = bool(shell_distance_m <= policy.maximum_bracket_reach_m)
    checks = dict(shell_exterior=bool(outside.any()), installation_space=bool(space.any()) and reach,
                  opening_free=opening_free)
    return dict(status='PASS_RESEARCH' if all(checks.values()) else 'REJECTED', checks=checks,
        housing_bounds_m=[lower.tolist(), upper.tolist()], shell_bounds_m=bounds.tolist(),
        shell_distance_m=float(shell_distance_m), bracket_reach_limit_m=policy.maximum_bracket_reach_m,
        service_clearance_m=policy.service_clearance_m,
        opening='NOT_REQUIRED_EXTERNAL' if opening_free else 'REQUIRED_OR_UNVERIFIED',
        motion_clearance=dict(status='NOT_EVALUATED'),
        scope='conservative shell envelope, exterior service space and opening-free exit; bracket/cable/fastener CAD unavailable',
        physical_installation_approved=False)


def point_inside_closed_mesh(point, vertices, triangles):
    """Inclusive solid-angle containment; reject open or inconsistently oriented parts."""
    from .analysis import closest_reference, validate_surface
    point, vertices, triangles = np.asarray(point, float), np.asarray(vertices, float), np.asarray(triangles)
    if (point.shape != (3,) or not np.isfinite(point).all()
            or not np.issubdtype(triangles.dtype, np.integer)):
        raise ValueError('Expected a finite point and integer mesh indices')
    validate_surface(vertices, triangles)
    edges = np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    _, inverse, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
    orientation = np.bincount(inverse, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1))
    if (np.any(counts != 2) or np.any(orientation != 0)
            or len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles)):
        raise ValueError('Containment requires a closed, consistently oriented mesh')
    faces = vertices[triangles] - point
    tolerance = 64 * np.finfo(float).eps * max(1., float(np.ptp(vertices[triangles].reshape(-1, 3), axis=0).max()))
    if closest_reference([point], vertices, triangles)[0][0] <= tolerance:
        return True
    lengths = np.linalg.norm(faces, axis=2)
    numerator = np.einsum('ij,ij->i', faces[:, 0], np.cross(faces[:, 1], faces[:, 2]))
    denominator = (np.prod(lengths, axis=1)
        + np.einsum('ij,ij->i', faces[:, 0], faces[:, 1]) * lengths[:, 2]
        + np.einsum('ij,ij->i', faces[:, 1], faces[:, 2]) * lengths[:, 0]
        + np.einsum('ij,ij->i', faces[:, 2], faces[:, 0]) * lengths[:, 1])
    return bool(abs(np.sum(2 * np.arctan2(numerator, denominator))) > 2 * np.pi)


def summarize_motion(rows, *, policy=None):
    """Every expected source time contributes a clearance or an explicit unknown."""
    policy = policy or MechanicalPolicy()
    known = [row for row in rows if isinstance(row, dict) and _finite_number(row.get('clearance_m'))
             and isinstance(row.get('inside'), (bool, np.bool_))]
    unknown = len(rows) - len(known)
    failed = sum(row['clearance_m'] < policy.motion_clearance_m or row['inside'] for row in known)
    return dict(status='REJECTED' if failed else 'UNKNOWN' if unknown or not rows else 'PASS_RESEARCH',
        expected_frames=len(rows), known_frames=len(known), unknown_frames=unknown, failed_frames=failed,
        minimum_clearance_m=min((row['clearance_m'] for row in known), default=None),
        required_clearance_m=policy.motion_clearance_m,
        scope='housing/service enclosing sphere versus saved blades and tower at declared grid times; between-frame motion unverified',
        frames=rows)


def roi_mask(frame, world_from_analysis, roi):
    """Select output points for evaluation without modifying the detection frame."""
    from .simulation import rigid_matrix
    if frame is None or frame.completeness != 'COMPLETE':
        return None
    bounds = profile_metadata(roi)['analysis_roi']
    sensor, analysis = rigid_matrix(frame.spatial['world_from_sensor']), rigid_matrix(world_from_analysis)
    world = frame.xyz.astype(float) @ sensor[:3, :3].T + sensor[:3, 3]
    local = (world - analysis[:3, 3]) @ analysis[:3, :3]
    return (local >= bounds['lower_m']).all(axis=1) & (local <= bounds['upper_m']).all(axis=1)


def _installation_parameters(location, rotation):
    try:
        parameters = np.asarray([location, rotation], dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('Installation needs finite position and XYZ angles') from exc
    if parameters.shape != (2, 3) or not np.isfinite(parameters).all():
        raise ValueError('Installation needs finite position and XYZ angles')
    return tuple(parameters[0].tolist()), tuple(parameters[1].tolist())


def _rotation_matrix(rotation):
    roll, pitch, yaw = np.radians(np.remainder(rotation, 360.))
    cr, sr, cp, sp, cy, sy = np.cos(roll), np.sin(roll), np.cos(pitch), np.sin(pitch), np.cos(yaw), np.sin(yaw)
    return np.array([[cy*cp, cy*sp*sr-sy*cr, cy*sp*cr+sy*sr],
                     [sy*cp, sy*sp*sr+cy*cr, sy*sp*cr-cy*sr], [-sp, cp*sr, cp*cr]])


def candidates(locations, rolls, pitches, yaws, baselines=()):
    """Unique rigid poses with baseline precedence; retain the first pose's ID and angles."""
    result, seen = [], set()
    rolls, pitches, yaws = tuple(rolls), tuple(pitches), tuple(yaws)
    for name, location, rotation in itertools.chain(baselines,
            ((name, location, rotation) for name, location in locations
             for rotation in itertools.product(rolls, pitches, yaws))):
        location, rotation = _installation_parameters(location, rotation)
        key = location, tuple(_rotation_matrix(rotation).round(12).flat)
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(id=digest((location, rotation))[:16], location_m=location, rotation_deg=rotation, location_name=name))
        if len(result) > 256:
            raise ValueError('Installation search must contain 1..256 candidates')
    if not result:
        raise ValueError('Installation search must contain 1..256 candidates')
    return result


def _passage_indices(indices, size):
    ids = np.asarray(indices)
    if (ids.ndim != 1 or (ids.size and not np.issubdtype(ids.dtype, np.integer))
            or np.any(ids < 0) or np.any(ids >= size) or np.any(ids[1:] <= ids[:-1])):
        raise ValueError('Passage indices must address the explicit grid in order')
    return ids.astype(int)


def summarize(counts, passages, minimum_points=3):
    """Geometric distal material-point counts; -1 is unknown, never a miss."""
    counts = np.asarray(counts)
    if (counts.ndim != 2 or counts.shape[1] != 3 or type(minimum_points) is not int
            or minimum_points < 3 or not np.isfinite(counts).all() or np.any(counts < -1)
            or np.any(counts != np.floor(counts)) or len(passages) != 3):
        raise ValueError('Invalid coverage support matrix')
    rows, blades = [], []
    for blade in range(3):
        total = hits = unknown = 0
        for passage in passages[blade]:
            if passage['status'] != 'COMPLETE':
                continue
            ids = _passage_indices(passage['indices'], len(counts))
            if np.any(np.diff(ids) != 1):
                raise ValueError('Complete geometric passages must contain every grid sample')
            values = counts[ids, blade]
            n = len(values)
            missing = int(np.sum(values < 0))
            support = values >= minimum_points
            gap = longest = 0
            for value, good in zip(values, support):
                gap = gap + 1 if value >= 0 and not good else 0
                longest = max(longest, gap)
            rows.append(dict(blade=blade + 1, start_s=passage['start_s'], end_s=passage['end_s'],
                N=n, supported=int(support.sum()), unknown=missing,
                evaluation='N/A' if not n else 'UNKNOWN' if missing else 'EVALUATED',
                fraction=float(support.sum() / n) if n and not missing else None,
                fraction_known=float(support.sum() / (n-missing)) if n > missing else None,
                longest_gap_frames=longest))
            total += n
            hits += int(support.sum())
            unknown += missing
        blades.append(dict(blade=blade + 1, N=total, supported=hits, unknown=unknown,
            fraction=hits / total if total and not unknown else None,
            fraction_known=hits / (total-unknown) if total > unknown else None))
    eligible = all(b['N'] and not b['unknown'] for b in blades) and all(p['N'] for p in rows)
    return dict(status='EVALUATED' if eligible else 'INSUFFICIENT_OR_UNKNOWN',
        metric='GEOMETRIC_DISTAL_MATERIAL_POINTS', minimum_points=minimum_points,
        blades=blades, passages=rows,
        worst_blade_fraction=min(b['fraction'] for b in blades) if eligible else None,
        worst_passage_fraction=min(p['fraction'] for p in rows) if eligible else None,
        longest_gap_frames=max((p['longest_gap_frames'] for p in rows), default=None))


def _detection_count(row):
    value = row.get('target_associated_points') if isinstance(row, dict) else None
    return int(value) if _finite_number(value, 0) and value == np.floor(value) else None


def summarize_passage_detections(times, frames, passages, nadir_frames, *, min_points=1, grid_hz=30.):
    """Score only complete blade passages; unknown frames never become zero detections.

    A known miss occupies one source-grid period. Complete passages contain every
    grid sample; runs stop at passage boundaries or unknown frames. Nadir observations use the exact interpolated
    crossing time, independently of the regular grid. Tower errors use supported frames.
    """
    times = np.asarray(times, float)
    if (times.ndim != 1 or not np.isfinite(times).all() or np.any(np.diff(times) <= 0)
            or len(times) != len(frames) or len(passages) != 3 or len(nadir_frames) != 3
            or type(min_points) is not int or min_points < 1 or not np.isfinite(grid_hz) or grid_hz <= 0):
        raise ValueError('Invalid passage detection grid')
    ticks = (times - times[0]) * grid_hz if len(times) else times
    if (not np.allclose(ticks, np.rint(ticks), atol=1e-6, rtol=0)
            or np.any(np.diff(ticks) < 1 - 1e-6)):
        raise ValueError('Passage times must align with grid_hz')
    rows, blades, all_errors = [], [], []
    for blade, windows in enumerate(passages):
        if len(windows) != len(nadir_frames[blade]):
            raise ValueError('Every passage needs an explicit nadir observation')
        for window, nadir in zip(windows, nadir_frames[blade]):
            if window['status'] != 'COMPLETE':
                continue
            ids = _passage_indices(window['indices'], len(times))
            if len(ids) and (not _finite_number(window.get('start_s')) or not _finite_number(window.get('end_s'))
                    or not np.allclose(times[ids[[0, -1]]], [window['start_s'], window['end_s']], atol=1e-8, rtol=0)
                    or not np.allclose(np.diff(times[ids]) * grid_hz, 1., atol=1e-6, rtol=0)):
                raise ValueError('Complete detection passages must contain every grid sample from start_s to end_s')
            values = [frames[i] if frames[i] is None or 'blade_truth' not in frames[i]
                      else frames[i] | frames[i]['blade_truth'].get(str(blade + 1), {}) for i in ids]
            counts = [_detection_count(row) for row in values]
            known = [n for n in counts if n is not None]
            supported = sum(n >= min_points for n in known)
            run = longest = 0
            for count in counts:
                run = run + 1 if count is not None and count < min_points else 0
                longest = max(longest, run)
            supported_rows = [row for row, count in zip(values, counts) if count is not None and count >= min_points]
            errors = [row['tower_distance_error_m'] for row in supported_rows
                      if _finite_number(row.get('tower_distance_error_m'))]
            all_errors.extend(errors)
            nadir_count, nadir_time = _detection_count(nadir), window.get('nadir_time_s')
            nadir_support = bool(nadir_count >= min_points) if nadir_count is not None and _finite_number(nadir_time) else None
            if nadir is not None and 'source_time_s' in nadir and (
                    not _finite_number(nadir['source_time_s']) or not _finite_number(nadir_time)
                    or not np.isclose(nadir['source_time_s'], nadir_time, atol=1e-8, rtol=0)):
                nadir_support = None
            rows.append(dict(blade=blade + 1, start_s=window['start_s'], end_s=window['end_s'],
                N=len(ids), known_frames=len(known), unknown_frames=len(ids) - len(known), supported_frames=supported,
                support_fraction=supported / len(ids) if len(ids) and len(known) == len(ids) else None,
                support_fraction_known=supported / len(known) if known else None,
                grid_times_s=times[ids].tolist(), non_false_alarm_points=counts,
                points_per_frame_mean=float(np.mean(known)) if known else None,
                points_per_frame_min=min(known) if known else None, points_per_frame_max=max(known) if known else None,
                longest_known_miss_run_frames=longest, longest_known_miss_duration_s=longest / grid_hz,
                nadir_time_s=window.get('nadir_time_s'), nadir_supported=nadir_support, nadir_frame=nadir,
                tower_error_frames=len(errors), tower_error_missing_supported_frames=sum(
                    not _finite_number(row.get('tower_distance_error_m'))
                    and (row.get('tower_distance_truth_status') != 'NO_DISTAL_SURFACE_IN_ROI'
                         or row.get('tower_distance_error_m') is not None) for row in supported_rows),
                tower_error_not_applicable_frames=sum(row is not None
                    and row.get('tower_distance_truth_status') == 'NO_DISTAL_SURFACE_IN_ROI' for row in values),
                tower_error_bias_m=float(np.mean(errors)) if errors else None,
                tower_error_mae_m=float(np.mean(np.abs(errors))) if errors else None,
                tower_error_rmse_m=float(np.sqrt(np.mean(np.square(errors)))) if errors else None))
        selected = [row for row in rows if row['blade'] == blade + 1]
        n, unknown, hits = (sum(row[key] for row in selected) for key in ('N', 'unknown_frames', 'supported_frames'))
        blades.append(dict(blade=blade + 1, N=n, unknown_frames=unknown, supported_frames=hits,
                           support_fraction=hits / n if n and not unknown else None))
    known = bool(rows) and all(row['N'] and not row['unknown_frames'] and row['nadir_supported'] is not None
                             and not row['tower_error_missing_supported_frames'] for row in rows)
    known = known and all(blade['N'] for blade in blades)
    counts = [count for row in frames if (count := _detection_count(row)) is not None]
    return dict(metric=PASSAGE_METRIC, status='KNOWN' if known else 'INSUFFICIENT_OR_UNKNOWN',
        min_points=min_points, grid_hz=grid_hz, passages=rows, blades=blades,
        expected_frames=sum(row['N'] for row in rows), unknown_frames=sum(row['unknown_frames'] for row in rows),
        worst_blade_fraction=min(row['support_fraction'] for row in blades) if known else None,
        worst_passage_fraction=min(row['support_fraction'] for row in rows) if known else None,
        longest_known_miss_duration_s=max((row['longest_known_miss_duration_s'] for row in rows), default=None),
        nadir_support_fraction=sum(row['nadir_supported'] is True for row in rows) / len(rows) if known else None,
        points_per_frame_mean=float(np.mean(counts)) if counts else None,
        tower_error_rmse_m=float(np.sqrt(np.mean(np.square(all_errors)))) if all_errors else None,
        continuous_support=bool(known and all(row['support_fraction'] == 1 and row['nadir_supported'] for row in rows)),
        miss_duration_definition='number of consecutive known missed grid samples / grid_hz; unknowns break runs',
        tower_distance_definition='minimum non-false-alarm ROI point distance to same-time tower side mesh minus minimum clipped distal blade surface distance; no target identity inferred')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _profiles(candidate, key):
    values = candidate.get(key, [])
    return [_mapping(value) for value in values] if isinstance(values, (list, tuple)) else []


def _candidate_problems(candidate):
    """Shared eligibility for ranking and report import; geometric scores are auxiliary."""
    problems = []
    mechanics = _mapping(candidate.get('mechanical'))
    for label, status in (('机械约束', mechanics.get('status')),
                          ('运动净空', _mapping(mechanics.get('motion_clearance')).get('status'))):
        if status is None:
            problems.append('缺少' + label + '证据')
        elif status != 'PASS_RESEARCH':
            problems.append(label + '证据未通过研究筛选')
    detection_profiles = _profiles(candidate, 'detection_profiles')
    supports = [_mapping(profile.get('simulated_detection_support')) for profile in detection_profiles]
    if not supports or any(row.get('status') is None or 'support_fraction' not in row for row in supports):
        problems.append('缺少检测证据（simulated_detection_support 的 status 和 support_fraction）')
    elif any(row['status'] != 'KNOWN' for row in supports):
        problems.append('检测证据不完整或含未知帧')
    elif any(row['support_fraction'] is None or _finite_number(row['support_fraction'], upper=0) for row in supports):
        problems.append('缺少支持率大于 0 的检测证据')
    elif any(not _finite_number(row['support_fraction'], 0, 1) for row in supports):
        problems.append('检测支持率无效（support_fraction 必须为 0..1 的有限值）')
    if any(profile.get('errors') for profile in detection_profiles):
        problems.append('检测证据含错误帧')
    profiles = _profiles(candidate, 'profiles')
    if not profiles or any(not isinstance(p.get('geometric_coverage'), dict) for p in profiles):
        problems.append('缺少 v2 几何覆盖证据（geometric_coverage）')
    passage_supports = [_mapping(p.get('passage_detection_support')) for p in detection_profiles]
    if passage_supports and any(p.get('metric') != PASSAGE_METRIC for p in passage_supports):
        problems.append('缺少叶片经过检测证据（passage_detection_support）')
    elif passage_supports and any(p.get('status') != 'KNOWN' for p in passage_supports):
        problems.append('叶片经过检测证据不完整或含未知帧')
    elif passage_supports:
        invalid = [key for key, upper in _PASSAGE_RANK_BOUNDS.items()
                   if any(not _finite_number(p.get(key), 0, upper) for p in passage_supports)]
        if invalid:
            problems.append('叶片经过检测指标缺失或无效（' + '、'.join(invalid) + '）')
        elif any(p['worst_blade_fraction'] <= 0 for p in passage_supports):
            problems.append('缺少每片叶片支持率大于 0 的检测证据')
    if 'location_m' not in candidate or 'rotation_deg' not in candidate:
        problems.append('缺少安装参数（location_m、rotation_deg）')
    else:
        try:
            _installation_parameters(candidate['location_m'], candidate['rotation_deg'])
        except ValueError:
            problems.append('安装参数无效（location_m、rotation_deg 必须各含三个有限数值，rotation_deg 为 XYZ 欧拉角）')
    return problems


def rank(results):
    results = list(results)
    ids = [candidate.get('id') if isinstance(candidate, dict) else None for candidate in results]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('Candidate IDs must be unique nonempty strings')
    scored = []
    for candidate in results:
        profiles = _profiles(candidate, 'profiles')
        detection_profiles = _profiles(candidate, 'detection_profiles')
        eligible = not _candidate_problems(candidate)
        score = None
        detection_score = None
        if eligible:
            supports = [p['passage_detection_support'] for p in detection_profiles]
            score = (min(p['worst_passage_fraction'] for p in supports),
                -max(p['longest_known_miss_duration_s'] for p in supports),
                min(p['nadir_support_fraction'] for p in supports),
                min(p['worst_blade_fraction'] for p in supports),
                -max(p['tower_error_rmse_m'] for p in supports),
                min(p['points_per_frame_mean'] for p in supports))
            detection_score = score[0]
        geometry = [_mapping(p.get('geometric_coverage')).get('worst_blade_fraction') for p in profiles]
        geometric_score = min(geometry) if geometry and all(_finite_number(v, 0, 1) for v in geometry) else None
        scored.append(candidate | dict(score=score, detection_score=detection_score, geometric_score=geometric_score))
    return sorted(scored, key=lambda c: (c['score'] is not None, c['score'] or (),
                                        c['geometric_score'] if c['geometric_score'] is not None else -1., c['id']), reverse=True)


def selected_installation(report, source_id, geometry_id):
    regenerate = '；请重新运行 scripts/radar_x100/optimize_mount.py 生成 v2 报告'
    if not isinstance(report, dict) or report.get('schema') != SCHEMA:
        raise ValueError('安装搜索报告版本不受支持，仅接受 x100-installation-search.v2' + regenerate)
    for key, expected in (('source_id', source_id), ('geometry_id', geometry_id)):
        if not isinstance(expected, str) or not expected or report.get(key) != expected:
            raise ValueError('安装搜索结果与当前几何来源不匹配：' + key)
    if report.get('sampling_method') != 'AREA' or report.get('sampling_algorithm') != AREA_ALGORITHM:
        raise ValueError('安装搜索报告的 sampling_method 或 sampling_algorithm 不受支持' + regenerate)
    if (report.get('evaluation_scope', 'ALL_COMPLETE_PASSAGES') != 'ALL_COMPLETE_PASSAGES'
            or report.get('passages_per_blade', 0) != 0):
        raise ValueError('安装搜索仅含部分经过筛选，未覆盖全部完整经过' + regenerate)
    shell = _mapping(report.get('shell_geometry'))
    if 'geometry_id' in shell and shell['geometry_id'] != geometry_id:
        raise ValueError('安装搜索报告的 shell_geometry.geometry_id 与当前几何来源不匹配')
    winner = report.get('winner_id')
    if report.get('errors'):
        raise ValueError('安装搜索含未知帧，不能应用推荐安装')
    policy = _mapping(report.get('mechanical_policy'))
    expected_policy = profile_metadata()['mechanical_policy']
    dimensions = _mapping(policy.get('unit_envelope')).get('dimensions_whd_m')
    if not isinstance(dimensions, list) or dimensions != expected_policy['unit_envelope']['dimensions_whd_m']:
        raise ValueError('安装搜索报告的 mechanical_policy.unit_envelope.dimensions_whd_m 缺失或与当前安装包络不符' + regenerate)
    for field in ('parameters', 'housing_axes', 'opening_policy', 'physical_installation_approved'):
        actual, expected = policy.get(field), expected_policy[field]
        if not isinstance(actual, type(expected)) or actual != expected:
            raise ValueError('安装搜索报告的 mechanical_policy.' + field + ' 缺失或与当前研究筛选要求不符' + regenerate)
    candidates = report.get('candidates', [])
    if not isinstance(candidates, (list, tuple)) or any(not isinstance(row, dict) for row in candidates):
        raise ValueError('安装搜索报告的 candidates 格式无效' + regenerate)
    ids = [row.get('id') for row in candidates]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError('安装搜索候选 id 缺失或重复' + regenerate)
    for row in candidates:
        mechanics = _mapping(row.get('mechanical'))
        if 'geometry_id' in mechanics and mechanics['geometry_id'] != geometry_id:
            raise ValueError('安装搜索候选 ' + row['id'] + ' 的 mechanical.geometry_id 与当前几何来源不匹配')
    selected = next((row for row in candidates if row.get('id') == winner), None)
    if selected is None:
        raise ValueError('安装搜索报告未指定有效的推荐候选' + regenerate)
    problems = _candidate_problems(selected)
    if problems:
        raise ValueError('安装搜索推荐不可应用：' + '；'.join(problems) + regenerate)
    rows = rank(candidates)
    if rows[0]['id'] != winner:
        raise ValueError('安装搜索报告的推荐安装与候选排名不一致' + regenerate)
    if rows[0]['score'] is None:
        raise ValueError('推荐安装未通过叶片经过检测校验：需要完整、已知的检测、正下方时刻和塔距误差证据' + regenerate)
    location, rotation = rows[0]['location_m'], rows[0]['rotation_deg']
    return location, rotation


def _fields(row, *names):
    return {name: row[name] for name in names if name in row}


def candidate_summary(row):
    """Keep the installation identity, eligibility checks, and ranking inputs."""
    compact = _fields(row, 'id', 'location_name', 'location_m', 'rotation_deg', 'detection_evaluation')
    mechanics = _mapping(row.get('mechanical'))
    compact['mechanical'] = _fields(mechanics, 'status', 'geometry_id', 'checks', 'physical_installation_approved')
    compact['mechanical']['motion_clearance'] = _fields(_mapping(mechanics.get('motion_clearance')), 'status',
        'expected_frames', 'known_frames', 'unknown_frames', 'failed_frames', 'minimum_clearance_m', 'required_clearance_m')
    compact['profiles'] = []
    for profile in _profiles(row, 'profiles'):
        projected = _fields(profile, 'seed', 'budget')
        coverage = profile.get('geometric_coverage')
        if isinstance(coverage, dict):
            projected['geometric_coverage'] = _fields(coverage, 'status', 'worst_blade_fraction')
        compact['profiles'].append(projected)
    compact['detection_profiles'] = []
    for profile in _profiles(row, 'detection_profiles'):
        projected = _fields(profile, 'sampling_seed', 'noise_seed', 'integration_budget', 'output_point_limit', 'sampling_plan_id')
        projected['simulated_detection_support'] = _fields(_mapping(profile.get('simulated_detection_support')),
            'status', 'support_fraction', 'expected_frames', 'known_frames', 'unknown_frames', 'supported_frames')
        projected['passage_detection_support'] = _fields(_mapping(profile.get('passage_detection_support')),
            'metric', 'status', 'expected_frames', 'unknown_frames', 'continuous_support', *_PASSAGE_RANK_BOUNDS)
        if errors := profile.get('errors'):
            projected['errors'] = errors[:1] if isinstance(errors, (list, tuple)) else [{}]
        compact['detection_profiles'].append(projected)
    return compact


def import_summary(report):
    """Project an applicable v2 report to provenance and candidate ranking inputs."""
    report = _mapping(report)
    source_id, geometry_id = report.get('source_id'), report.get('geometry_id')
    selected_installation(report, source_id, geometry_id)
    summary = _fields(report, 'schema', 'source_id', 'geometry_id', 'shell_geometry', 'mechanical_policy',
        'hardware_profile', 'analysis_roi', 'sampling_method', 'sampling_algorithm', 'sampling_plans',
        'source_interval_s', 'source_grid_hz', 'source_frames', 'evaluation_scope', 'passages_per_blade',
        'minimum_points', 'distal_span', 'half_window_deg', 'code_identity', 'ranking',
        'errors', 'winner_id', 'best_evaluated_id', 'recommended_observation_mode', 'default_mode_basis')
    summary['candidates'] = rank(candidate_summary(row) for row in report['candidates'])
    selected_installation(summary, source_id, geometry_id)
    return summary
