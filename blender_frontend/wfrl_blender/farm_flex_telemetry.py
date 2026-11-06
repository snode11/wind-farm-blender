"""Validated telemetry arrays and scene-owned replay recording."""
import json
from pathlib import Path

import numpy as np


def _read_telemetry(path, manifest, times, readers, *, _source=None):
    """Read matching telemetry and retain numeric arrays for interpolation."""
    if 'telemetry.json' not in manifest['files']:
        return None
    if _source is not None and _source.path != Path(path).resolve():
        raise ValueError('verified source belongs to another package')
    extra = (_source.telemetry if _source is not None else
             json.loads((Path(path) / 'telemetry.json').read_text(encoding='utf-8')))
    if extra is None:
        return None
    if (extra['schema'] != 'wfrl.farm-telemetry.v1'
            or extra['geometry_sha256'] != manifest['files']['geometry.npz']
            or not np.array_equal(extra['times'], times)
            or set(extra['turbines']) != set(readers)):
        raise ValueError('Telemetry and geometry do not match')
    for channels in extra['turbines'].values():
        for record in channels.values():
            values = np.asarray(record['values'])
            if values.shape != times.shape or not np.isfinite(values).all():
                raise ValueError('Invalid telemetry samples')
            record['values'] = np.asarray(values, dtype=float)
    return extra


def record_telemetry(self, scene, pose=None):
    if not scene.wfrl_channel_telemetry:
        return
    from . import clearance_replay, charts
    value = clearance_replay.sample(scene)
    if value is None or value['time_s'] == self.last_telemetry_frame:
        return
    t = value['time_s']
    if pose is None:
        pose = np.array([[np.interp(t, self.times, self.poses[:, k, j])
                          for j in range(6)] for k in range(len(self.readers))])
    if self.last_telemetry_frame is not None and t < self.last_telemetry_frame:
        charts.clear(scene=scene)
    extra = {}
    if self.telemetry:
        for tid, channels in self.telemetry['turbines'].items():
            extra[tid] = {name: dict(value=float(np.interp(t, self.times, record['values'])),
                                    unit=record['unit'], source_channel=record['source_channel'],
                                    source_sha256=self.telemetry['sources'][tid]['sha256'])
                          for name, record in channels.items()}
    charts.record_farm(t, pose, self.manifest, extra, scene=scene)
    scene['wfrl_yaw_deg'] = pose[:, 0].tolist()
    scene['wfrl_pitch_deg'] = pose[:, 3:].mean(axis=1).tolist()
    scene['wfrl_rpm'] = pose[:, 2].tolist()
    scene['wfrl_telemetry_time_s'] = t
    self.last_telemetry_frame = t
