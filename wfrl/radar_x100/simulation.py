"""Fixed surface-material observations with geometric first-hit occlusion."""
from dataclasses import dataclass
import numpy as np
from .schema import RadarFrame, FrameTime, POINT_DTYPE, PROFILE, MAX_POINTS
from .sampling import make_plan, MaterialPlan, AREA_ALGORITHM, MAX_INTEGRATION_SAMPLES


def rigid_matrix(value):
    matrix = np.array(value, dtype=np.float64, copy=True)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all()
            or not np.allclose(matrix[3], [0, 0, 0, 1])
            or not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(matrix[:3, :3]), 1, atol=1e-6)):
        raise ValueError('Expected a right-handed rigid transform')
    return matrix


@dataclass(frozen=True)
class SceneSample:
    time_s: float
    source_id: str
    geometry_id: str
    vertices: np.ndarray
    triangles: np.ndarray
    world_from_sensor: np.ndarray
    occlusion_scope: str

    def __post_init__(self):
        vertices = np.array(self.vertices, dtype=np.float64, copy=True)
        triangles = np.array(self.triangles, dtype=np.int32, copy=True)
        matrix = rigid_matrix(self.world_from_sensor)
        if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
                or triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles)
                or triangles.min() < 0 or triangles.max() >= len(vertices)
                or not np.isfinite(self.time_s)):
            raise ValueError('Invalid same-time scene sample')
        for name, value in (('vertices', vertices), ('triangles', triangles), ('world_from_sensor', matrix)):
            value.flags.writeable = False
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class MaterialSample:
    time_s: float
    source_id: str
    geometry_id: str
    points: np.ndarray
    world_from_sensor: np.ndarray

    def __post_init__(self):
        points = np.array(self.points, dtype=np.float64, copy=True)
        matrix = rigid_matrix(self.world_from_sensor)
        if (points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all()
                or not np.isfinite(self.time_s)):
            raise ValueError('Invalid material sample')
        points.flags.writeable = matrix.flags.writeable = False
        object.__setattr__(self, 'points', points)
        object.__setattr__(self, 'world_from_sensor', matrix)


def field_of_view(fov_deg=None):
    """Rectangular azimuth/elevation half angles in degrees; defaults to the datasheet profile."""
    if fov_deg is None:
        return float(PROFILE['azimuth_deg']), float(PROFILE['elevation_deg'])
    azimuth, elevation = (float(value) for value in fov_deg)
    if not (0 < azimuth <= 90 and 0 < elevation <= 90):
        raise ValueError('Field-of-view half angles must lie in (0, 90] degrees')
    return azimuth, elevation


def in_fov(xyz, fov_deg=None):
    azimuth_limit, elevation_limit = field_of_view(fov_deg)
    ranges = np.linalg.norm(xyz, axis=1)
    azimuth = np.arctan2(xyz[:, 1], xyz[:, 0])
    elevation = np.arctan2(xyz[:, 2], np.hypot(xyz[:, 0], xyz[:, 1]))
    return ((ranges >= PROFILE['range_m'][0]) & (ranges <= PROFILE['range_m'][1])
            & (np.abs(azimuth) <= np.radians(azimuth_limit) + 1e-12)
            & (np.abs(elevation) <= np.radians(elevation_limit) + 1e-12))


def visible_triangles(sample, targets, tolerance_m=.002):
    """Two-sided segment/triangle intersection; no penetration or multipath model."""
    triangles = sample.vertices[sample.triangles]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    e1, e2 = b - a, c - a
    origin = sample.world_from_sensor[:3, 3]
    visible = []
    for point in targets:
        delta = point - origin
        length = np.linalg.norm(delta)
        direction = delta / length
        h = np.cross(np.broadcast_to(direction, e2.shape), e2)
        det = np.einsum('ij,ij->i', e1, h)
        good = np.abs(det) > 1e-12
        inv = np.divide(1., det, out=np.zeros_like(det), where=good)
        s = origin - a
        u = inv * np.einsum('ij,ij->i', s, h)
        q = np.cross(s, e1)
        v = inv * (q @ direction)
        distance = inv * np.einsum('ij,ij->i', e2, q)
        blocked = good & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (distance > 1e-5) & (distance < length - tolerance_m)
        visible.append(not blocked.any())
    return np.array(visible, dtype=bool)


@dataclass(frozen=True)
class _Observation:
    sample: SceneSample
    plan: MaterialPlan
    mask: np.ndarray
    xyz: np.ndarray
    radial: np.ndarray
    provenance: dict


def _observe(sample_at, time_s, *, interval_s,
             seed=100, candidate_budget=512, difference_s=.001, visibility=None,
             material_at=None, sampling_plan=None, sampling_mode='AREA', fov_deg=None):
    fov = field_of_view(fov_deg)
    if (len(interval_s) != 2 or type(candidate_budget) is not int
            or not 1 <= candidate_budget <= MAX_INTEGRATION_SAMPLES or type(seed) is not int or seed < 0
            or not np.isfinite(difference_s) or difference_s <= 0
            or not np.isfinite([time_s, *interval_s]).all()
            or not interval_s[0] <= time_s <= interval_s[1]):
        raise ValueError('Invalid simulation budget or difference step')
    sample = sample_at(time_s)
    if sample is None:
        return None
    if sample.time_s != time_s:
        raise ValueError('Scene sample time does not match the requested time')
    plan = sampling_plan
    if plan is None:
        reference = sample if time_s == interval_s[0] else sample_at(interval_s[0])
        if reference is None:
            return None
        if reference.time_s != interval_s[0]:
            raise ValueError('Reference sample time does not match the requested time')
        plan = make_plan(reference, candidate_budget, seed, sampling_mode)
    if (plan.method != sampling_mode or plan.seed != seed or plan.budget != candidate_budget
            or plan.reference_time_s != interval_s[0]):
        raise ValueError('Sampling plan does not match the requested profile')
    lo, hi = max(interval_s[0], time_s - difference_s), min(interval_s[1], time_s + difference_s)
    if hi <= lo:
        raise ValueError('No velocity difference interval')
    ids = plan.triangle_ids
    world = plan.points(sample)
    transform = sample.world_from_sensor
    xyz = (world - transform[:3, 3]) @ transform[:3, :3]
    keep = in_fov(xyz, fov)
    visible = np.zeros(len(world), dtype=bool)
    visible[keep] = (visibility or visible_triangles)(sample, world[keep])
    mask = keep & visible
    if material_at is None:
        before, after = sample_at(lo), sample_at(hi)
        if before is None or after is None:
            return None
        for other, stamp in ((before, lo), (after, hi)):
            if (other.source_id != sample.source_id or other.geometry_id != sample.geometry_id
                    or other.time_s != stamp or not np.array_equal(sample.triangles, other.triangles)):
                raise ValueError('Material topology, source or time changed during sampling')
        before_points, after_points = plan.points(before)[mask], plan.points(after)[mask]
    else:
        selected = ids[mask]
        options = dict(barycentric=plan.barycentric[mask]) if plan.method == 'AREA' else {}
        before, after = material_at(lo, selected, **options), material_at(hi, selected, **options)
        if before is None or after is None:
            return None
        for other, stamp in ((before, lo), (after, hi)):
            if (other.source_id != sample.source_id or other.geometry_id != sample.geometry_id
                    or other.time_s != stamp or other.points.shape != (len(selected), 3)):
                raise ValueError('Material sample identity, time or count changed during sampling')
        before_points, after_points = before.points, after.points
    relative_velocity = ((after_points - before_points)
                         - (after.world_from_sensor[:3, 3] - before.world_from_sensor[:3, 3])) / (hi - lo)
    los = world[mask] - transform[:3, 3]
    radial = np.einsum('ij,ij->i', relative_velocity, los / np.maximum(np.linalg.norm(los, axis=1)[:, None], 1e-12))
    provenance = dict(profile=PROFILE, field_of_view_deg=list(fov), source_id=sample.source_id, geometry_id=sample.geometry_id,
            instantaneous=True, material_sampling=plan.method, sampling_plan_id=plan.identity,
            sampling_algorithm=AREA_ALGORITHM if plan.method == 'AREA' else 'triangle-centroid.v1',
            reference_time_s=plan.reference_time_s, seed=seed,
            candidate_budget=candidate_budget, candidates_in_fov=int(keep.sum()),
            geometric_visible_samples=int(mask.sum()),
            occluded=int((keep & ~visible).sum()), occlusion_scope=sample.occlusion_scope,
            occlusion_model='two-sided geometric first hit; tolerance 0.002 m',
            difference_interval_s=[lo, hi], velocity_sign='positive receding',
            unknown_attributes=['mag', 'rcs', 'snr', 'vendor_label'], label_semantics='NOT_GENERATED')
    return _Observation(sample, plan, mask, xyz[mask], radial, provenance)


def _frame(observation, sensor_id, session_id, sequence, points, **kwargs):
    sample = observation.sample
    return RadarFrame(sensor_id, session_id, sequence, 'GEOMETRY_SIMULATION', 'GEOMETRY', points,
        time=FrameTime('SIMULATION:' + sample.source_id, sample.time_s,
                       (sample.time_s, sample.time_s), 'INSTANT', 'SIMULATION'),
        spatial=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP', units='m', compensation='NONE',
                     alignment='SIMULATION', world_from_sensor=sample.world_from_sensor.tolist()),
        provenance=observation.provenance, **kwargs)


def simulate(sample_at, time_s, *, sensor_id, session_id, sequence, interval_s,
             seed=100, candidate_budget=512, difference_s=.001, visibility=None,
             material_at=None, sampling_plan=None, sampling_mode='AREA', fov_deg=None):
    """Geometric control observations; a missing source sample remains None."""
    if type(candidate_budget) is not int or not 1 <= candidate_budget <= MAX_POINTS:
        raise ValueError('Invalid geometric report budget')
    observation = _observe(sample_at, time_s, interval_s=interval_s, seed=seed,
        candidate_budget=candidate_budget, difference_s=difference_s, visibility=visibility,
        material_at=material_at, sampling_plan=sampling_plan, sampling_mode=sampling_mode, fov_deg=fov_deg)
    if observation is None:
        return None
    points = np.zeros(len(observation.xyz), dtype=POINT_DTYPE)
    for axis, name in enumerate(('x', 'y', 'z')):
        points[name] = observation.xyz[:, axis]
    points['ms'] = (np.abs(observation.radial) > 1e-6).astype(np.uint8)
    for name in ('mag', 'rcs', 'snr'):
        points[name] = np.nan
    observation.provenance['material_point_indices'] = np.flatnonzero(observation.mask).tolist()
    return _frame(observation, sensor_id, session_id, sequence, points,
        radial_velocity_m_s=observation.radial,
        material_ids=tuple(int(i) for i in observation.plan.triangle_ids[observation.mask]))


def simulate_detections(sample_at, time_s, *, sensor_id, session_id, sequence, interval_s,
                        integration_budget=16384, output_point_limit=MAX_POINTS,
                        sampling_seed=100, noise_seed=100, config=None, difference_s=.001,
                        visibility=None, material_at=None, sampling_plan=None):
    """Generate synthetic detections from independently budgeted area quadrature.

    Reference-area weights are transported with the material; current normals
    supply the two-sided incidence response. Surface strain does not change the
    reference measure. Missing source or difference samples return None.
    """
    from .detection import DetectionConfig, generate_detections, surface_rcs
    config = DetectionConfig() if config is None else config
    observation = _observe(sample_at, time_s, interval_s=interval_s, seed=sampling_seed,
        candidate_budget=integration_budget, difference_s=difference_s, visibility=visibility,
        material_at=material_at, sampling_plan=sampling_plan, sampling_mode='AREA',
        fov_deg=(config.azimuth_limit_deg, config.elevation_limit_deg))
    if observation is None:
        return None
    sample, plan, mask = observation.sample, observation.plan, observation.mask
    faces = sample.vertices[sample.triangles[plan.triangle_ids[mask]]]
    normals = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    normals = np.divide(normals, lengths[:, None], out=np.zeros_like(normals), where=lengths[:, None] > 0)
    sensor_los = observation.xyz / np.linalg.norm(observation.xyz, axis=1)[:, None]
    world_los = sensor_los @ sample.world_from_sensor[:3, :3].T
    incidence = np.abs(np.einsum('ij,ij->i', normals, world_los))
    rcs = surface_rcs(plan.area_weights_m2[mask], incidence, config=config)
    observation.provenance.pop('candidate_budget')
    observation.provenance.update(integration_budget=integration_budget, sampling_seed=sampling_seed,
        reference_area_m2=float(plan.area_weights_m2.sum()),
        visible_reference_area_m2=float(plan.area_weights_m2[mask].sum()))
    template = _frame(observation, sensor_id, session_id, sequence, np.zeros(0, dtype=POINT_DTYPE))
    return generate_detections(observation.xyz, observation.radial, rcs,
        template=template, config=config, seed=noise_seed, output_point_limit=output_point_limit,
        response_kind='REFERENCE_AREA_SURFACE')
