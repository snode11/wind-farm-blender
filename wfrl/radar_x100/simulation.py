"""Deterministic triangle-centroid observations with geometric first-hit occlusion."""
from dataclasses import dataclass
import numpy as np
from .schema import RadarFrame, FrameTime, POINT_DTYPE, PROFILE, MAX_POINTS, digest


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


def in_fov(xyz):
    ranges = np.linalg.norm(xyz, axis=1)
    azimuth = np.arctan2(xyz[:, 1], xyz[:, 0])
    elevation = np.arctan2(xyz[:, 2], np.hypot(xyz[:, 0], xyz[:, 1]))
    return ((ranges >= PROFILE['range_m'][0]) & (ranges <= PROFILE['range_m'][1])
            & (np.abs(azimuth) <= np.radians(PROFILE['azimuth_deg']) + 1e-12)
            & (np.abs(elevation) <= np.radians(PROFILE['elevation_deg']) + 1e-12))


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


def simulate(sample_at, time_s, *, sensor_id, session_id, sequence, interval_s,
             seed=100, candidate_budget=512, difference_s=.001, visibility=None):
    """Use the same material centroids and moving sensor origin for velocity."""
    if not 1 <= candidate_budget <= MAX_POINTS or difference_s <= 0:
        raise ValueError('Invalid simulation budget or difference step')
    sample = sample_at(time_s)
    lo, hi = max(interval_s[0], time_s - difference_s), min(interval_s[1], time_s + difference_s)
    if hi <= lo:
        raise ValueError('No velocity difference interval')
    before, after = sample_at(lo), sample_at(hi)
    for other in (before, after):
        if (other.source_id != sample.source_id or other.geometry_id != sample.geometry_id
                or not np.array_equal(sample.triangles, other.triangles)):
            raise ValueError('Material topology or source changed during sampling')
    rng = np.random.default_rng(seed)
    ids = np.sort(rng.choice(len(sample.triangles), min(candidate_budget, len(sample.triangles)), replace=False))
    def material_points(snapshot):
        return snapshot.vertices[snapshot.triangles[ids]].mean(axis=1)
    world = material_points(sample)
    transform = sample.world_from_sensor
    xyz = (world - transform[:3, 3]) @ transform[:3, :3]
    keep = in_fov(xyz)
    visible = np.zeros(len(world), dtype=bool)
    visible[keep] = (visibility or visible_triangles)(sample, world[keep])
    mask = keep & visible
    relative_velocity = ((material_points(after) - material_points(before))
                         - (after.world_from_sensor[:3, 3] - before.world_from_sensor[:3, 3])) / (hi - lo)
    los = world - transform[:3, 3]
    radial = np.einsum('ij,ij->i', relative_velocity, los / np.maximum(np.linalg.norm(los, axis=1)[:, None], 1e-12))
    points = np.zeros(int(mask.sum()), dtype=POINT_DTYPE)
    for axis, name in enumerate(('x', 'y', 'z')):
        points[name] = xyz[mask, axis]
    points['ms'] = (np.abs(radial[mask]) > 1e-6).astype(np.uint8)
    for name in ('mag', 'rcs', 'snr'):
        points[name] = np.nan
    return RadarFrame(sensor_id, session_id, sequence, 'GEOMETRY_SIMULATION', 'GEOMETRY', points,
        time=FrameTime('SIMULATION:' + sample.source_id, time_s, (time_s, time_s), 'INSTANT', 'SIMULATION'),
        spatial=dict(frame='SENSOR_X_FORWARD_Y_LEFT_Z_UP', units='m', compensation='NONE',
                     alignment='SIMULATION', world_from_sensor=transform.tolist()),
        provenance=dict(profile=PROFILE, source_id=sample.source_id, geometry_id=sample.geometry_id,
            instantaneous=True, material_sampling='fixed triangle centroids', seed=seed,
            candidate_budget=candidate_budget, candidates_in_fov=int(keep.sum()),
            occluded=int((keep & ~visible).sum()), occlusion_scope=sample.occlusion_scope,
            occlusion_model='two-sided geometric first hit; tolerance 0.002 m',
            difference_interval_s=[lo, hi], velocity_sign='positive receding',
            unknown_attributes=['mag', 'rcs', 'snr', 'vendor_label'], label_semantics='NOT_GENERATED'),
        radial_velocity_m_s=radial[mask], material_ids=tuple(int(i) for i in ids[mask]))
