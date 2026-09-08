"""Bounded, ID-addressed telemetry history and optional Blender viewport plots.

Importing this module requires neither bpy nor GPU. Gaps are retained explicitly;
plotting never interpolates over missing/invalid records or across unit changes.
"""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
import math
import uuid
from .history import HistoryWindow, ExportJob
from .training import STAT_LABELS

CHANNELS = ('power', 'reward', 'yaw', 'pitch', 'pitch_command', 'torque', 'rotor_speed', 'load')
LABELS = dict(power='Power', reward='Reward', yaw='Yaw', pitch='Pitch measured',
              pitch_command='Pitch command', torque='Torque', rotor_speed='RPM', load='Load')
FARM = '__farm__'


@dataclass(frozen=True)
class Point:
    step: int
    time: float
    value: object
    unit: str
    fidelity: str
    validity: str
    provenance: object


def scalar(value):
    return type(value) in (int, float) and math.isfinite(value)


class ChartHistory:
    def __init__(self, capacity=600):
        if type(capacity) is not int or capacity < 2:
            raise ValueError('capacity must be an integer >= 2')
        self.capacity = capacity
        self.clear()

    def clear(self):
        self.series = {}
        self.session = None
        self.identity = None
        self.last_step = None
        self.last_sequence = None

    def ingest(self, message):
        payload = message.get('payload', message)
        identity = (payload['mode'], payload['timestamp']['timebase'], payload.get('run_id'))
        session = message.get('session_id')
        step = payload['step']
        sequence = message.get('sequence')
        if session == self.session and identity == self.identity and sequence is not None and self.last_sequence is not None and sequence <= self.last_sequence:
            return False
        if session != self.session or identity != self.identity or (self.last_step is not None and step < self.last_step):
            self.clear()
        self.session, self.identity = session, identity
        self.last_step, self.last_sequence = step, sequence
        groups = {FARM: payload.get('farm', {})}
        groups.update((t['turbine_id'], t['channels']) for t in payload['turbines'])
        # Prune removed turbines to bound total history as well as each series.
        self.series = {key: values for key, values in self.series.items() if key[0] in groups}
        for tid, records in groups.items():
            for channel in CHANNELS:
                record = records.get(channel, {})
                value = record.get('value')
                validity = record.get('validity', 'unsupported')
                if validity == 'valid' and not scalar(value):
                    validity = 'invalid'
                if record.get('source_age_seconds', 0) >= record.get('stale_after_seconds', 2):
                    validity = 'stale'
                point = Point(step, payload['timestamp']['value'], value if validity == 'valid' else None,
                              record.get('unit', ''), record.get('fidelity', 'UNKNOWN'), validity,
                              deepcopy(record.get('provenance', {})))
                values = self.series.setdefault((tid, channel), deque(maxlen=self.capacity))
                if values and values[-1].step == step:
                    values[-1] = point
                else:
                    values.append(point)
        return True

    def points(self, turbine_id, channel):
        return tuple(self.series.get((turbine_id, channel), ()))

    def turbine_ids(self):
        return tuple(dict.fromkeys(tid for tid, _ in self.series if tid != FARM))


def segments(points):
    """Split gaps and source/unit changes so visual continuity is truthful."""
    result, current, previous = [], [], None
    for point in points:
        signature = (point.unit, point.fidelity)
        if point.value is None or (previous is not None and signature != previous):
            if current:
                result.append(current)
            current = []
        if point.value is not None:
            current.append((point.time, point.value))
        previous = signature
    if current:
        result.append(current)
    return result


# Tear down a previous module instance before replacing its handler reference.
_previous_unregister = globals().get('unregister')
if _previous_unregister:
    _previous_unregister()

history = ChartHistory()
raw_history = HistoryWindow()
export_job = ExportJob()
_local_run_id = uuid.uuid4().hex
_local_sequence = 0


def clear(run_id=None):
    global _local_run_id, _local_sequence
    history.clear()
    raw_history.clear(run_id)
    _local_run_id = run_id or uuid.uuid4().hex
    _local_sequence = 0


def record_snapshot(message):
    raw_history.ingest(dict(message, type='snapshot'))
    return history.ingest(message)


def record_training(message):
    raw_history.ingest(message)
    p = message['payload']
    for key, record in p['stats'].items():
        value = record['value'] if record['validity'] == 'valid' and scalar(record['value']) else None
        point = Point(p['step'], p['timestamp']['value'], value, record['unit'], record['fidelity'],
                      record['validity'], deepcopy(record['provenance']))
        history.series.setdefault((FARM, key), deque(maxlen=600)).append(point)


def record_curve(message):
    raw_history.ingest(message)
    p = message['payload']; record = p['data']
    values = history.series.setdefault((p['turbine_id'] or FARM, p['channel']), deque(maxlen=600))
    for stamp, value in record['value'] or []:
        values.append(Point(p['step'], stamp, value if record['validity'] == 'valid' else None,
                            record['unit'], record['fidelity'], record['validity'], deepcopy(record['provenance'])))


def record_demo(sample, turbine_ids=('T1', 'T2', 'T3'), *, manual=False):
    global _local_sequence
    _local_sequence += 1
    def record(value, unit):
        return dict(value=value, unit=unit, validity='valid', error=None, fidelity='SYNTH',
                    source_age_seconds=0., stale_after_seconds=2.,
                    provenance={'formula': 'sample_demo; scripted preview'})
    turbines = []
    for index, tid in enumerate(turbine_ids):
        channels = {name: record(values[index], unit) for name, values, unit in (
            ('yaw', sample.yaw_deg, 'deg'), ('pitch', sample.pitch_deg, 'deg'),
            ('rotor_speed', sample.rpm, 'rpm'), ('power', sample.power_mw, 'MW'))}
        if manual:
            for channel in channels.values():
                channel.update(value=None, validity='unsupported', error='Display pose has no physical power model')
        turbines.append(dict(turbine_id=tid, channels=channels))
    farm = {} if manual else {'power': record(sum(sample.power_mw), 'MW')}
    return record_snapshot(dict(session_id='local-demo', sequence=_local_sequence, payload=dict(mode='demo', run_id=_local_run_id,
        step=round(sample.time_s * 1000), timestamp=dict(value=sample.time_s, timebase='simulation_seconds'),
        turbines=turbines, farm=farm)))


_handle = None


def draw_plot():
    import bpy
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    scene = context.scene
    if not getattr(scene, 'wfrl_chart_visible', False) or context.region.width < 400:
        return
    channel = scene.wfrl_chart_channel
    selected = getattr(scene, 'wfrl_selected_turbine', 'ALL')
    ids = history.turbine_ids() if selected == 'ALL' else (selected,)
    if channel == 'reward' or channel in STAT_LABELS:
        ids = (FARM,)
    series = [(tid, history.points(tid, channel)) for tid in ids]
    available = [point for _, points in series for point in points if point.value is not None]
    def label(x, y, text):
        blf.position(0, x, y, 0); blf.size(0, 12); blf.color(0, 0.95, 0.97, 1, 1); blf.draw(0, text)
    left, bottom, width, height = 40, 90, min(560, context.region.width - 80), 140
    units = {p.unit for p in available}
    title = {**LABELS, **STAT_LABELS}[channel] + ' / ' + ', '.join(sorted(units))
    label(left, bottom + height + 44, title + ' / last 600 samples')
    if not available:
        label(left, bottom + height + 22, 'Unavailable / no valid scalar samples (never zero-filled)')
        return
    if len(units) != 1:
        label(left, bottom + height + 22, 'Mixed units: plot unavailable')
        return
    xmin, xmax = min(p.time for p in available), max(p.time for p in available)
    ymin, ymax = min(p.value for p in available), max(p.value for p in available)
    dx, dy = max(xmax - xmin, 1e-9), max(ymax - ymin, 1e-9)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    colors = ((0.25, 0.85, 1, 1), (1, 0.7, 0.2, 1), (0.5, 1, 0.55, 1))
    for index, (tid, points) in enumerate(series):
        for segment in segments(points):
            coords = [(left + (x-xmin)/dx*width, bottom + (y-ymin)/dy*height) for x, y in segment]
            batch = batch_for_shader(shader, 'LINE_STRIP' if len(coords) > 1 else 'POINTS', {'pos': coords})
            shader.bind(); shader.uniform_float('color', colors[index % len(colors)]); batch.draw(shader)
        sources = '/'.join(sorted({p.fidelity for p in points if p.value is not None})) or 'UNKNOWN'
        label(left + index * 180, bottom + height + 22, f'{tid}: {sources}')
    timebase = 'unix_seconds' if channel in STAT_LABELS else (history.identity[1] if history.identity else 'simulation_seconds')
    label(left, bottom - 20, f'{xmin:.3g}–{xmax:.3g} {timebase}   range {ymin:.5g}–{ymax:.5g}')


def register():
    global _handle
    import bpy
    bpy.types.Scene.wfrl_chart_visible = bpy.props.BoolProperty(name='Show curve in viewport', default=False)
    bpy.types.Scene.wfrl_chart_channel = bpy.props.EnumProperty(name='Curve', items=[(c, label, '') for c, label in {**LABELS, **STAT_LABELS}.items()])
    if _handle is None and not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw_plot, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle
    import bpy
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    for name in ('wfrl_chart_visible', 'wfrl_chart_channel'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    clear()
