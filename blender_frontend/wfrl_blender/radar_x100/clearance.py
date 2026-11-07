"""Observation-only clearance inputs and frame-bound derived recording results."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import numpy as np
from .core import clearance as estimator, recording, schema

SCHEMA = 'x100-clearance-display.v1'
NOTICE = '工程模型估计 · UNCALIBRATED（未标定）'
PRIORS = '依赖同刻塔面几何、根部姿态与未加载叶片形状先验'
REASONS = dict(INSUFFICIENT_POINTS='叶片检测点不足', INSUFFICIENT_SPAN='覆盖长度不足',
    INSUFFICIENT_BINS='跨度分布不足', ILL_CONDITIONED='拟合条件不足',
    EXTRAPOLATION_TOO_FAR='叶尖外推过远', UNSTABLE_FIT='拟合不稳定', UNCERTAIN='估计不确定度过大')
ROW_FIELDS = ('blade', 'status', 'points', 'tip_world', 'clearance_m', 'clearance_std_m',
              'clearance_interval95_m', 'fit_residual_m', 'extrapolation_m', 'consecutive_frames')


def unloaded_shapes(sampler):
    """Read immutable SourceLoft sections and the independent structural reference."""
    from ..turbine_geometry import geometry_data
    scalars = geometry_data()['scalars']
    reference = sampler.flex.reference
    tip = np.array(reference['tip_local_m'] if reference is not None else [0., 0., scalars['TipRad']])
    shapes = {}
    for blade, (_, rest, *_) in enumerate(sampler.blades, 1):
        spans, counts = np.unique(rest[:, 2], return_counts=True)
        ring_size = int(counts.max())
        spans = spans[counts == ring_size]
        sections = np.array([rest[rest[:, 2] == z, :2] for z in spans])
        spans = np.r_[spans, tip[2]]
        sections = np.concatenate((sections, np.broadcast_to(rest[-1, :2], (1, ring_size, 2))))
        axis = np.zeros((len(spans), 2))
        if reference is not None:
            curve = np.asarray(reference['curve'])
            for a in range(2):
                axis[:, a] = np.interp(spans-scalars['HubRad'], curve[:, 2], curve[:, a])
        shapes[blade] = estimator.BladeShape(spans, sections, axis, tip,
            'FarmFlex immutable unloaded SourceLoft + independent blade-reference.json; '
            'rest SHA256 ' + hashlib.sha256(rest.tobytes()).hexdigest())
    return shapes


def observation_inputs(frame):
    """Allow only observed power and declared measurement error, never signal/truth labels."""
    if frame.radial_velocity_m_s is None:
        raise ValueError('缺少径向速度')
    model = frame.provenance.get('observation_model', {})
    parameters = model.get('parameters', {})
    errors = [parameters.get(name) for name in ('range_std_m', 'azimuth_std_deg', 'elevation_std_deg')]
    if any(not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0 for v in errors):
        raise ValueError('缺少有效的位置误差模型')
    ranges = np.linalg.norm(frame.xyz, axis=1)
    sigma = np.sqrt(errors[0]**2 + ranges**2 * np.radians(math.hypot(*errors[1:]))**2)
    sigma = np.where(frame.finite_mask, np.maximum(sigma, .01), .01)
    power = snr = None
    if frame.model_diagnostics is not None:
        noise = model.get('derived_parameters', {}).get('detector_noise_power_w')
        if not isinstance(noise, (float, int)) or not math.isfinite(noise) or noise <= 0:
            raise ValueError('缺少有效的观测噪声功率')
        power = frame.model_diagnostics.values['model_observed_power_w']
        snr = power / noise
    return dict(position_std_m=sigma, observed_power_w=power, observed_snr_linear=snr)


def display_result(result):
    """Keep only derived display fields; no geometry, fitting inputs or truth are archived."""
    rows = []
    for row in result['blades']:
        values = {key: row[key].tolist() if isinstance(row[key], np.ndarray) else row[key]
                  for key in ROW_FIELDS}
        rows.append(values)
    return dict(labels=np.asarray(result['labels'], dtype=np.int8).tolist(), blades=rows,
                uncertainty=result['uncertainty'])


def lines(result, error=''):
    if result is None:
        return ('不可估计：' + (error or '等待当前检测帧'),)
    output = []
    for row in result['blades']:
        if row['status'] == 'SUPPORTED':
            lo, hi = row['clearance_interval95_m']
            output.append(f"B{row['blade']} 估计叶尖塔距 {row['clearance_m']:.3f} m · 95% 区间 [{lo:.3f}, {hi:.3f}] m")
        else:
            output.append(f"B{row['blade']} 不可估计：{REASONS.get(row['status'], row['status'])}")
    return tuple(output)


def frame_digest(frame):
    digest = hashlib.sha256(schema.json_bytes(frame.metadata()))
    digest.update(frame.points.tobytes())
    if frame.radial_velocity_m_s is not None:
        digest.update(frame.radial_velocity_m_s.tobytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class RecordedFrame:
    """Queue an immutable observation snapshot alongside its independent derived result."""
    frame: object
    result: dict | None
    error: str

    def __getattr__(self, name):
        return getattr(self.frame, name)

    def snapshot(self):
        return RecordedFrame(self.frame.snapshot(), deepcopy(self.result), self.error)


class Recorder(recording.Recorder):
    """The existing recording worker writes companion results before committing each raw row."""
    def append(self, frame, replay_time_s, **kwargs):
        try:
            if isinstance(frame, RecordedFrame):
                from ..atomic_json import write_json
                payload = dict(schema=SCHEMA, frame_sha256=frame_digest(frame.frame),
                               result=frame.result, error=frame.error)
                payload['sha256'] = schema.digest(payload)
                write_json(self.path / 'clearance' / f'{self.rows:08d}.json', payload, mkdir=True)
                frame = frame.frame
            super().append(frame, replay_time_s, **kwargs)
        except Exception as exc:
            self.fail(str(exc))
            raise


def read_recorded(replay, index, frame):
    path = replay.path / 'clearance' / f'{index:08d}.json'
    if not path.exists():
        return None, '记录中无此帧的估计；缺少同帧先验'
    with path.open('rb') as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('记录的估计数据超出预算')
    payload = json.loads(raw)
    digest = payload.pop('sha256', None)
    if (payload.get('schema') != SCHEMA or schema.digest(payload) != digest
            or payload.get('frame_sha256') != frame_digest(frame)):
        raise ValueError('记录的估计与当前帧不匹配或已损坏')
    result = payload['result']
    if result is not None:
        labels = np.asarray(result['labels'])
        if labels.shape != (len(frame.points),) or not np.isin(labels, [0, 1, 2]).all():
            raise ValueError('记录的回波分类无效')
        rows = result['blades']
        if len(rows) != 3 or {row['blade'] for row in rows} != {1, 2, 3}:
            raise ValueError('记录的叶片估计无效')
        for row in rows:
            if row['status'] == 'SUPPORTED':
                lo, hi = row['clearance_interval95_m']
                value, std = row['clearance_m'], row['clearance_std_m']
                if not np.isfinite([lo, hi, value, std]).all() or not 0 <= lo <= value <= hi or std < 0:
                    raise ValueError('记录的塔距区间无效')
            elif row['status'] not in REASONS or any(row[key] is not None for key in
                    ('clearance_m', 'clearance_std_m', 'clearance_interval95_m', 'tip_world')):
                raise ValueError('记录的不可估计状态无效')
    return result, str(payload['error'])
