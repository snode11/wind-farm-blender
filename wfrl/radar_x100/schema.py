"""Versioned radar frames; unknown physical attributes remain absent."""
from dataclasses import dataclass, field
import copy
import hashlib
import json
import math
import numpy as np

SCHEMA = 'x100-frame.v1'
MAX_POINTS = 4096
MAX_METADATA = 256 * 1024
POINT_DTYPE = np.dtype([(n, t) for n, t in (
    ('x', '<f4'), ('y', '<f4'), ('z', '<f4'), ('ms', 'u1'),
    ('mag', '<f4'), ('rcs', '<f4'), ('snr', '<f4'), ('label', 'u1'))])
PROFILE = dict(id='ctsai-x100-datasheet-v1', dimensions_hwd_m=[.120, .095, .040],
               azimuth_deg=60., elevation_deg=22., range_m=[.2, 396.], max_points=MAX_POINTS,
               source='CTSAI X100 数据手册 CN V1.0; nominal specifications, not measured')
SOURCES = {'GEOMETRY_SIMULATION', 'MEASURED_REPLAY', 'MEASURED_LIVE', 'TEST'}
STREAMS = {'GEOMETRY', 'UNKNOWN', 'NEAR', 'FAR', 'COMBINED', 'TRACK'}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class ClockMapping:
    identity: str
    source_domain: str
    target_domain: str
    scale: float
    offset_s: float
    valid_interval_s: tuple
    error_s: float
    basis: str

    def __post_init__(self):
        object.__setattr__(self, 'valid_interval_s', tuple(self.valid_interval_s))
        lo, hi = self.valid_interval_s
        if (not all(math.isfinite(x) for x in (self.scale, self.offset_s, lo, hi, self.error_s))
                or self.scale <= 0 or lo > hi or self.error_s < 0
                or not all((self.identity, self.source_domain, self.target_domain, self.basis))):
            raise ValueError('Invalid clock mapping')

    def map(self, source_time_s, *, target_domain, tolerance_s):
        if (not math.isfinite(source_time_s) or not math.isfinite(tolerance_s)
                or target_domain != self.target_domain or tolerance_s < self.error_s
                or not self.valid_interval_s[0] <= source_time_s <= self.valid_interval_s[1]):
            raise ValueError('Clock mapping is outside its domain, interval or error tolerance')
        return self.scale * source_time_s + self.offset_s


@dataclass(frozen=True)
class FrameTime:
    clock_domain: str = 'UNKNOWN'
    source_time_s: float | None = None
    acquisition_interval_s: tuple | None = None
    timestamp_reference: str = 'UNKNOWN'
    quality: str = 'UNKNOWN'
    mapping: dict | None = None

    def __post_init__(self):
        if not self.clock_domain or self.timestamp_reference not in {'START', 'CENTER', 'END', 'INSTANT', 'UNKNOWN'}:
            raise ValueError('Invalid time reference')
        if self.quality not in {'UNKNOWN', 'SOURCE', 'FPS_DERIVED', 'SIMULATION'}:
            raise ValueError('Invalid time quality')
        if self.source_time_s is not None and not math.isfinite(self.source_time_s):
            raise ValueError('Nonfinite source time')
        if self.acquisition_interval_s is not None:
            object.__setattr__(self, 'acquisition_interval_s', tuple(self.acquisition_interval_s))
            lo, hi = self.acquisition_interval_s
            if not all(map(math.isfinite, (lo, hi))) or lo > hi:
                raise ValueError('Invalid acquisition interval')
            if self.source_time_s is not None and not lo <= self.source_time_s <= hi:
                raise ValueError('Timestamp is outside acquisition interval')
        if self.mapping is not None:
            object.__setattr__(self, 'mapping', copy.deepcopy(self.mapping))
            mapping = ClockMapping(**self.mapping)
            if mapping.source_domain != self.clock_domain:
                raise ValueError('Clock mapping source domain mismatch')


@dataclass(frozen=True)
class RadarFrame:
    sensor_id: str
    session_id: str
    sequence: int
    source_kind: str
    stream: str
    points: np.ndarray
    time: FrameTime = field(default_factory=FrameTime)
    completeness: str = 'COMPLETE'
    spatial: dict = field(default_factory=lambda: dict(frame='VENDOR_RAW', units='m',
        compensation='UNKNOWN', alignment='UNALIGNED', axis_mapping=None, world_from_sensor=None))
    provenance: dict = field(default_factory=dict)
    radial_velocity_m_s: np.ndarray | None = None
    material_ids: tuple | None = None
    schema: str = SCHEMA

    def __post_init__(self):
        if (self.schema != SCHEMA or any(not isinstance(value, str) or not 1 <= len(value) <= 256
                for value in (self.sensor_id, self.session_id))
                or type(self.sequence) is not int or self.sequence < 0
                or self.source_kind not in SOURCES or self.stream not in STREAMS
                or self.completeness not in {'COMPLETE', 'INCOMPLETE', 'CORRUPT'}):
            raise ValueError('Invalid frame identity or completeness')
        points = np.array(self.points, copy=True)
        if points.dtype != POINT_DTYPE or points.ndim != 1 or len(points) > MAX_POINTS:
            raise ValueError('Invalid X100 point array')
        if np.any((points['ms'] != 0) & (points['ms'] != 1)):
            raise ValueError('Unsupported ms encoding')
        points.flags.writeable = False
        object.__setattr__(self, 'points', points)
        if self.radial_velocity_m_s is not None:
            velocity = np.array(self.radial_velocity_m_s, dtype='<f4', copy=True)
            if velocity.shape != (len(points),) or not np.isfinite(velocity).all():
                raise ValueError('Invalid radial velocity')
            velocity.flags.writeable = False
            object.__setattr__(self, 'radial_velocity_m_s', velocity)
        if self.material_ids is not None:
            if len(self.material_ids) != len(points):
                raise ValueError('Material identity count mismatch')
            object.__setattr__(self, 'material_ids', tuple(self.material_ids))
        for name in ('spatial', 'provenance'):
            value = copy.deepcopy(getattr(self, name))
            json_bytes(value)
            object.__setattr__(self, name, value)
        if self.spatial.get('units') != 'm' or not self.spatial.get('frame'):
            raise ValueError('Radar coordinates must declare frame and meter units')
        transform = self.spatial.get('world_from_sensor')
        if transform is not None:
            matrix = np.asarray(transform, dtype=float)
            if (matrix.shape != (4,4) or not np.isfinite(matrix).all()
                    or not np.allclose(matrix[3], [0,0,0,1])
                    or not np.allclose(matrix[:3,:3].T @ matrix[:3,:3], np.eye(3), atol=1e-6)
                    or not np.isclose(np.linalg.det(matrix[:3,:3]), 1, atol=1e-6)):
                raise ValueError('Invalid radar world transform')
        if len(json_bytes(self.metadata())) > MAX_METADATA - 128:
            raise ValueError('Radar frame metadata exceeds channel budget')

    @property
    def identity(self):
        return self.sensor_id, self.session_id, self.stream, self.sequence

    @property
    def xyz(self):
        return np.column_stack([self.points[n] for n in ('x', 'y', 'z')])

    @property
    def finite_mask(self):
        return np.isfinite(self.xyz).all(axis=1)

    @property
    def validity(self):
        if self.completeness != 'COMPLETE':
            return self.completeness
        return 'VALID' if len(self.points) else 'VALID_EMPTY'

    def metadata(self):
        from dataclasses import asdict
        return dict(schema=self.schema, sensor_id=self.sensor_id, session_id=self.session_id,
                    sequence=self.sequence, source_kind=self.source_kind, stream=self.stream,
                    time=asdict(self.time), completeness=self.completeness,
                    spatial=copy.deepcopy(self.spatial), provenance=copy.deepcopy(self.provenance),
                    material_ids=copy.deepcopy(self.material_ids))

    def snapshot(self):
        return frame_from_metadata(self.metadata(), self.points, self.radial_velocity_m_s)


def frame_from_metadata(metadata, points, velocity=None):
    fields = dict(metadata)
    fields['time'] = FrameTime(**fields['time'])
    return RadarFrame(**fields, points=points, radial_velocity_m_s=velocity)
