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


def record_farm(time_s, poses, manifest, extra=None):
    """Record only fields present in the physical result; preserve absent channels."""
    global _local_sequence
    _local_sequence += 1
    provenance = {'source': 'FAST.Farm', 'status': manifest['status'],
                  'policy_sha256': manifest['policy']['sha256'],
                  'geometry_sha256': manifest['files']['geometry.npz'],
                  'pitch': 'mean of three measured blade pitches'}
    def record(value, unit):
        return dict(value=float(value), unit=unit, validity='valid', error=None,
                    fidelity='FAST.Farm', source_age_seconds=0., stale_after_seconds=2.,
                    provenance=provenance)
    turbines = []
    for tid, pose in zip(manifest['turbine_ids'], poses):
        channels = {'yaw': record(pose[0], 'deg'),
                    'pitch': record(sum(pose[3:]) / 3, 'deg'),
                    'rotor_speed': record(pose[2], 'rpm')}
        for name, value in (extra or {}).get(tid, {}).items():
            channels[name] = record(value['value'], value['unit'])
            channels[name]['provenance'] = dict(provenance, source_channel=value['source_channel'],
                                                source_sha256=value['source_sha256'])
        turbines.append(dict(turbine_id=tid, channels=channels))
    farm = {}
    if all('power' in turbine['channels'] for turbine in turbines):
        farm['power'] = record(sum(t['channels']['power']['value'] for t in turbines), 'MW')
    return record_snapshot(dict(session_id='mappo-results', sequence=_local_sequence,
        payload=dict(mode='replay', run_id=_local_run_id, step=round(time_s * 1000),
                     timestamp=dict(value=time_s, timebase='simulation_seconds'),
                     turbines=turbines, farm=farm)))


_handle = None


def axis_ticks(values, count=5):
    """Readable bounds even for a constant signal or a single timestamp."""
    low, high = min(values), max(values)
    if low == high:
        pad = max(abs(low) * 1e-6, .5)
        low, high = low - pad, high + pad
    return tuple(low + (high - low) * i / (count - 1) for i in range(count))


def draw_plot():
    import bpy
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    scene = context.scene
    if not getattr(scene, 'wfrl_chart_visible', False) or context.region.type != 'WINDOW':
        return
    scale = max(1., context.preferences.system.ui_scale)
    region_width = context.region.width
    if context.preferences.system.use_region_overlap and context.space_data.show_region_ui:
        region_width -= max((r.width for r in context.area.regions if r.type == 'UI'), default=0)
    panel_width = min(660 * scale, region_width - 24 * scale)
    if panel_width < 280 * scale or context.region.height < 300 * scale:
        return
    channel = scene.wfrl_chart_channel
    selected = getattr(scene, 'wfrl_selected_turbine', 'ALL')
    ids = history.turbine_ids() if selected == 'ALL' else (selected,)
    if channel == 'reward' or channel in STAT_LABELS:
        ids = (FARM,)
    series = [(tid, history.points(tid, channel)) for tid in ids]
    available = [point for _, points in series for point in points if point.value is not None]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    colors = ((.25, .85, 1, 1), (1, .7, .2, 1), (.5, 1, .55, 1), (.85, .6, 1, 1))
    muted = (.6, .68, .76, 1)
    def label(x, y, text, color=(.95, .97, 1, 1), size=11):
        blf.position(0, x, y, 0); blf.size(0, size * scale); blf.color(0, *color); blf.draw(0, text)
    def line(coords, color, thickness=1, kind='LINE_STRIP'):
        gpu.state.line_width_set(thickness)
        shader.bind(); shader.uniform_float('color', color)
        batch_for_shader(shader, kind, {'pos': coords}).draw(shader)
    x, y = 12 * scale, 12 * scale
    left, bottom = x + 66 * scale, y + 54 * scale
    width, height = panel_width - 92 * scale, 118 * scale
    columns = max(1, int((panel_width - 24 * scale) // (175 * scale)))
    # Keep the chart bounded for large farms; the existing turbine filter
    # exposes each individual series without an overflowing legend.
    shown = series[:columns * 2]
    rows = max(1, math.ceil(len(shown) / columns))
    top = bottom + height + (rows * 20 + 34) * scale
    old_blend = gpu.state.blend_get()
    old_width = gpu.state.line_width_get()
    gpu.state.blend_set('ALPHA')
    try:
        line([(x,y),(x+panel_width,y),(x+panel_width,top),(x,y),(x+panel_width,top),(x,top)],
             (.025,.041,.061,.96), kind='TRIS')
        units = {p.unit for p in available}
        title = {**LABELS, **STAT_LABELS}[channel] + ' / ' + (', '.join(sorted(units)) or '—')
        label(x + 12 * scale, top - 20 * scale, title)
        if not available or len(units) != 1:
            label(x + 12 * scale, bottom + height / 2,
                  'No valid samples' if not available else 'Mixed units / select one turbine', muted)
            return
        xticks = axis_ticks([p.time for p in available], 3 if width < 350 * scale else 5)
        yticks = axis_ticks([p.value for p in available], 4)
        xmin, xmax, ymin, ymax = xticks[0], xticks[-1], yticks[0], yticks[-1]
        def px(value): return left + (value - xmin) / (xmax - xmin) * width
        def py(value): return bottom + (value - ymin) / (ymax - ymin) * height
        for value in yticks:
            yy = py(value)
            line([(left, yy), (left + width, yy)], (.2,.28,.36,.6))
            label(x + 6 * scale, yy - 4 * scale, f'{value:.3g}', muted, 10)
        timebase = 'unix_seconds' if channel in STAT_LABELS else (history.identity[1] if history.identity else 'simulation_seconds')
        origin = xmin if timebase == 'unix_seconds' else 0.
        for value in xticks:
            xx = px(value)
            line([(xx, bottom), (xx, bottom + height)], (.2,.28,.36,.4))
            text = f'{value-origin:.4g}'
            blf.size(0, 10 * scale)
            label(xx - blf.dimensions(0, text)[0] / 2, bottom - 17 * scale, text, muted, 10)
        axis_label = f's since {origin:.3f} Unix' if origin else timebase
        label(left, y + 12 * scale, axis_label, muted, 10)
        all_ids = history.turbine_ids()
        for index, (tid, points) in enumerate(series):
            color = colors[(all_ids.index(tid) if tid in all_ids else 0) % len(colors)]
            for segment in segments(points):
                coords = [(px(tx), py(v)) for tx, v in segment]
                gpu.state.point_size_set(4 * scale)
                line(coords, color, 2.5 if selected != 'ALL' else 1.5,
                     'LINE_STRIP' if len(coords) > 1 else 'POINTS')
            if index < len(shown):
                sources = '/'.join(sorted({p.fidelity for p in points if p.value is not None})) or 'UNKNOWN'
                name = 'Farm' if tid == FARM else tid
                text = f'{name}: {sources}'
                blf.size(0, 10 * scale)
                limit = (panel_width - 24 * scale) / columns - 10 * scale
                while len(text) > 3 and blf.dimensions(0, text)[0] > limit:
                    text = text[:-4] + '...'
                label(x + (12 + index % columns * 175) * scale,
                      top - (40 + index // columns * 20) * scale, text, color, 10)
        if len(series) > len(shown):
            label(left, y + 28 * scale, f'+{len(series)-len(shown)} series / use turbine filter', muted, 10)
    finally:
        gpu.state.line_width_set(old_width)
        gpu.state.point_size_set(1.)
        gpu.state.blend_set(old_blend)


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
