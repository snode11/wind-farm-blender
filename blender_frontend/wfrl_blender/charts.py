"""Bounded, ID-addressed telemetry history and optional Blender viewport plots.

Importing this module requires neither bpy nor GPU. Gaps are retained explicitly;
plotting never interpolates over missing/invalid records or across unit changes.
"""
from collections import OrderedDict, deque
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
    episode: object = None


def scalar(value):
    return type(value) in (int, float) and math.isfinite(value)


class ChartHistory:
    def __init__(self, capacity=600):
        if type(capacity) is not int or capacity < 2:
            raise ValueError('capacity must be an integer >= 2')
        self.capacity = capacity
        self.revision = 0
        self.clear()

    def clear(self):
        self.revision += 1
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
        episode = payload['episode'] if 'episode' in payload else payload.get('context', {}).get('episode')
        groups = {FARM: payload.get('farm', {})}
        groups.update((t['turbine_id'], t['channels']) for t in payload['turbines'])
        # Prune removed turbines to bound total history as well as each series.
        changed = any(key[0] not in groups for key in self.series)
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
                              deepcopy(record.get('provenance', {})),
                              episode)
                values = self.series.setdefault((tid, channel), deque(maxlen=self.capacity))
                if values and values[-1].step == step and values[-1].episode == point.episode:
                    if values[-1] != point:
                        values[-1] = point
                        changed = True
                else:
                    values.append(point)
                    changed = True
        if changed:
            self.revision += 1
        return True

    def points(self, turbine_id, channel):
        return tuple(self.series.get((turbine_id, channel), ()))

    def turbine_ids(self):
        return tuple(dict.fromkeys(tid for tid, _ in self.series if tid != FARM))


def segments(points):
    """Split gaps, episode/time resets and source/unit changes."""
    result, current, previous = [], [], None
    previous_time = None
    for point in points:
        signature = (point.unit, point.fidelity, point.episode)
        if (point.value is None or (previous is not None and signature != previous)
                or (previous_time is not None and point.time < previous_time)):
            if current:
                result.append(current)
            current = []
        if point.value is not None:
            current.append((point.time, point.value))
        previous = signature
        previous_time = point.time
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
_scene_histories = {}


class _SceneHistory:
    def __init__(self, scene):
        self.scene = scene
        self.history = ChartHistory()
        self.raw = HistoryWindow()
        self.run_id = uuid.uuid4().hex
        self.sequence = 0


def _scene_history(scene):
    for key, state in tuple(_scene_histories.items()):
        try:
            if hasattr(state.scene, 'as_pointer') and not state.scene.as_pointer():
                _scene_histories.pop(key, None)
        except ReferenceError:
            _scene_histories.pop(key, None)
    key = scene.as_pointer() if hasattr(scene, 'as_pointer') else id(scene)
    state = _scene_histories.get(key)
    if state is None or state.scene != scene:
        state = _scene_histories[key] = _SceneHistory(scene)
    return state


def history_for(scene=None):
    return history if scene is None else _scene_history(scene).history


def raw_history_for(scene=None):
    return raw_history if scene is None else _scene_history(scene).raw


def clear_scene_histories(*_args):
    _scene_histories.clear()
    invalidate_render_cache()


def clear(run_id=None, *, scene=None):
    global _local_run_id, _local_sequence
    history_for(scene).clear()
    raw_history_for(scene).clear(run_id)
    if scene is None:
        _local_run_id = run_id or uuid.uuid4().hex
        _local_sequence = 0
    else:
        state = _scene_history(scene)
        state.run_id, state.sequence = run_id or uuid.uuid4().hex, 0
    invalidate_render_cache()


def record_snapshot(message, *, scene=None):
    raw_history_for(scene).ingest(dict(message, type='snapshot'))
    return history_for(scene).ingest(message)


def record_training(message, *, scene=None):
    history = history_for(scene)
    raw_history_for(scene).ingest(message)
    p = message['payload']
    # Versions track writes, including multiple observations at one global step.
    if p['stats']:
        history.revision += 1
    for key, record in p['stats'].items():
        value = record['value'] if record['validity'] == 'valid' and scalar(record['value']) else None
        point = Point(p['step'], p['timestamp']['value'], value, record['unit'], record['fidelity'],
                      record['validity'], deepcopy(record['provenance']))
        history.series.setdefault((FARM, key), deque(maxlen=600)).append(point)


def record_curve(message, *, scene=None):
    history = history_for(scene)
    raw_history_for(scene).ingest(message)
    p = message['payload']; record = p['data']
    history.revision += 1
    values = history.series.setdefault((p['turbine_id'] or FARM, p['channel']), deque(maxlen=600))
    for stamp, value in record['value'] or []:
        values.append(Point(p['step'], stamp, value if record['validity'] == 'valid' else None,
                            record['unit'], record['fidelity'], record['validity'], deepcopy(record['provenance'])))


def record_farm(time_s, poses, manifest, extra=None, *, scene=None):
    """Record only fields present in the physical result; preserve absent channels."""
    global _local_sequence
    if scene is None:
        _local_sequence += 1
        sequence, run_id = _local_sequence, _local_run_id
    else:
        state = _scene_history(scene)
        state.sequence += 1
        sequence, run_id = state.sequence, state.run_id
    provenance = {'source': manifest.get('source','FAST.Farm'), 'status': manifest['status'],
                  'policy_sha256': manifest.get('policy', {}).get('sha256'),
                  'geometry_sha256': manifest['files']['geometry.npz'],
                  'pitch': 'mean of three measured blade pitches'}
    flat_provenance = all(type(value) in (str, int, float, bool, type(None)) for value in provenance.values())
    def record(value, unit):
        return dict(value=float(value), unit=deepcopy(unit), validity='valid', error=None,
                    fidelity='FAST.Farm', source_age_seconds=0., stale_after_seconds=2.,
                    provenance=provenance.copy() if flat_provenance else deepcopy(provenance))
    turbines = []
    for tid, pose in zip(manifest['turbine_ids'], poses):
        channels = {'yaw': record(pose[0], 'deg'),
                    'pitch': record(sum(pose[3:]) / 3, 'deg'),
                    'rotor_speed': record(pose[2], 'rpm')}
        for name, value in (extra or {}).get(tid, {}).items():
            channels[name] = record(value['value'], value['unit'])
            channels[name]['provenance'].update(source_channel=deepcopy(value['source_channel']),
                                               source_sha256=deepcopy(value['source_sha256']))
        turbines.append(dict(turbine_id=tid, channels=channels))
    farm = {}
    if all('power' in turbine['channels'] for turbine in turbines):
        farm['power'] = record(sum(t['channels']['power']['value'] for t in turbines), 'MW')
    message = dict(type='snapshot', session_id='mappo-results', sequence=sequence,
        payload=dict(mode='replay', run_id=run_id, step=round(time_s * 1000),
                     timestamp=dict(value=time_s, timebase='simulation_seconds'),
                     turbines=turbines, farm=farm))
    raw_history_for(scene).ingest(message, copy_records=False)
    return history_for(scene).ingest(message)


_handle = None


def axis_ticks(values, count=5):
    """Readable bounds even for a constant signal or a single timestamp."""
    low, high = min(values), max(values)
    if low == high:
        pad = max(abs(low) * 1e-6, .5)
        low, high = low - pad, high + pad
    return tuple(low + (high - low) * i / (count - 1) for i in range(count))


_CACHE_LIMIT = 8
_data_cache = OrderedDict()
_resource_cache = OrderedDict()
_registered = False
_COLORS = ((.25, .85, 1, 1), (1, .7, .2, 1), (.5, 1, .55, 1), (.85, .6, 1, 1))
_MUTED = (.6, .68, .76, 1)


@dataclass(frozen=True)
class _PlotData:
    series: tuple
    all_ids: tuple
    units: tuple
    bounds: object
    timebase: str
    channel: str
    selected: str


class _PlotResources:
    def __init__(self, owner, key, shader, commands):
        self.owner = owner
        self.key = key
        self.shader = shader
        self.commands = commands

    def release(self):
        # GPUBatch owns its vertex buffers and exposes no explicit free method.
        self.commands = ()
        self.shader = None
        self.owner = ()
        self.key = None
        self.data = None


def invalidate_render_cache(*_args):
    """Release cached buffers and CPU geometry without changing observations."""
    for resources in _resource_cache.values():
        resources.release()
    _resource_cache.clear()
    _data_cache.clear()


def _plot_data(channel, selected, scene=None):
    history = history_for(scene)
    token = (history, history.revision, history.session, history.identity)
    for cached in tuple(_data_cache):
        if cached[0][0] is history and cached[0] != token:
            _data_cache.pop(cached)
    key = (token, channel, selected)
    if key in _data_cache:
        _data_cache.move_to_end(key)
        return _data_cache[key]
    all_ids = history.turbine_ids()
    ids = all_ids if selected == 'ALL' else (selected,)
    if channel == 'reward' or channel in STAT_LABELS:
        ids = (FARM,)
    series = tuple((tid, history.points(tid, channel)) for tid in ids)
    available = [point for _, points in series for point in points if point.value is not None]
    bounds = None
    if available:
        bounds = (min(p.time for p in available), max(p.time for p in available),
                  min(p.value for p in available), max(p.value for p in available))
    data = _PlotData(
        tuple((tid, tuple(tuple(segment) for segment in segments(points)),
               '/'.join(sorted({p.fidelity for p in points if p.value is not None})) or 'UNKNOWN')
              for tid, points in series),
        all_ids, tuple(sorted({p.unit for p in available})), bounds,
        'unix_seconds' if channel in STAT_LABELS else (history.identity[1] if history.identity else 'simulation_seconds'),
        channel, selected)
    _data_cache[key] = data
    if len(_data_cache) > _CACHE_LIMIT:
        _data_cache.popitem(last=False)
    return data


def _plot_commands(data, scale, panel_width, blf):
    """Build geometry and text layout once for the selected data and dimensions."""
    commands = []
    def label(x, y, text, color=(.95, .97, 1, 1), size=11):
        commands.append(('label', x, y, text, color, size * scale))
    def line(coords, color, thickness=1, kind='LINE_STRIP'):
        commands.append(('line', coords, color, thickness, kind))
    x, y = 12 * scale, 12 * scale
    left, bottom = x + 66 * scale, y + 54 * scale
    width, height = panel_width - 92 * scale, 118 * scale
    columns = max(1, int((panel_width - 24 * scale) // (175 * scale)))
    # Every selected series is drawn; only the legend is limited to two rows.
    shown = data.series[:columns * 2]
    rows = max(1, math.ceil(len(shown) / columns))
    top = bottom + height + (rows * 20 + 34) * scale
    line([(x,y),(x+panel_width,y),(x+panel_width,top),(x,y),(x+panel_width,top),(x,top)],
         (.025,.041,.061,.96), kind='TRIS')
    title = {**LABELS, **STAT_LABELS}[data.channel] + ' / ' + (', '.join(data.units) or '—')
    label(x + 12 * scale, top - 20 * scale, title)
    if data.bounds is None or len(data.units) != 1:
        label(x + 12 * scale, bottom + height / 2,
              'No valid samples' if data.bounds is None else 'Mixed units / select one turbine', _MUTED)
        return tuple(commands)
    xticks = axis_ticks(data.bounds[:2], 3 if width < 350 * scale else 5)
    yticks = axis_ticks(data.bounds[2:], 4)
    xmin, xmax, ymin, ymax = xticks[0], xticks[-1], yticks[0], yticks[-1]
    def px(value): return left + (value - xmin) / (xmax - xmin) * width
    def py(value): return bottom + (value - ymin) / (ymax - ymin) * height
    for value in yticks:
        yy = py(value)
        line([(left, yy), (left + width, yy)], (.2,.28,.36,.6))
        label(x + 6 * scale, yy - 4 * scale, f'{value:.3g}', _MUTED, 10)
    origin = xmin if data.timebase == 'unix_seconds' else 0.
    for value in xticks:
        xx = px(value)
        line([(xx, bottom), (xx, bottom + height)], (.2,.28,.36,.4))
        text = f'{value-origin:.4g}'
        blf.size(0, 10 * scale)
        label(xx - blf.dimensions(0, text)[0] / 2, bottom - 17 * scale, text, _MUTED, 10)
    axis_label = f's since {origin:.3f} Unix' if origin else data.timebase
    label(left, y + 12 * scale, axis_label, _MUTED, 10)
    for index, (tid, runs, sources) in enumerate(data.series):
        color = _COLORS[(data.all_ids.index(tid) if tid in data.all_ids else 0) % len(_COLORS)]
        for segment in runs:
            coords = [(px(tx), py(v)) for tx, v in segment]
            line(coords, color, 2.5 if data.selected != 'ALL' else 1.5,
                 'LINE_STRIP' if len(coords) > 1 else 'POINTS')
        if index < len(shown):
            name = 'Farm' if tid == FARM else tid
            text = f'{name}: {sources}'
            blf.size(0, 10 * scale)
            limit = (panel_width - 24 * scale) / columns - 10 * scale
            while len(text) > 3 and blf.dimensions(0, text)[0] > limit:
                text = text[:-4] + '...'
            label(x + (12 + index % columns * 175) * scale,
                  top - (40 + index // columns * 20) * scale, text, color, 10)
    if len(data.series) > len(shown):
        label(left, y + 28 * scale, f'+{len(data.series)-len(shown)} series / use turbine filter', _MUTED, 10)
    return tuple(commands)


def _pointer(value):
    try:
        return value.as_pointer()
    except (AttributeError, ReferenceError):
        return 0


def _owner(context):
    return (context.window, context.window.screen, context.area, context.region,
            context.space_data, context.scene)


def _owner_key(owner):
    return tuple(_pointer(value) for value in owner)


def _forget_resources(key):
    resources = _resource_cache.pop(key, None)
    if resources is not None:
        resources.release()


def _prune_plot_resources(context):
    if not _resource_cache:
        return
    live = set()
    for window in context.window_manager.windows:
        screen, scene = window.screen, window.scene
        if (screen is None or scene is None or scene.get('wfrl_scene_kind') == 'manual'
                or not getattr(scene, 'wfrl_chart_visible', False)):
            continue
        for area in screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type == 'WINDOW':
                    live.add(_owner_key((window, screen, area, region, area.spaces.active, scene)))
    for key, resources in tuple(_resource_cache.items()):
        if key not in live or _owner_key(resources.owner) != key:
            _forget_resources(key)


def _cache_maintenance():
    if not _registered:
        return None
    import bpy
    _prune_plot_resources(bpy.context)
    return .5


def _plot_resources(context, data, scale, panel_width, gpu, blf, batch_for_shader):
    owner = _owner(context)
    owner_key = _owner_key(owner)
    if not all(owner_key):
        return None
    system = context.preferences.system
    key = (id(data), context.region.width, context.region.height, panel_width, scale,
           system.ui_scale, getattr(system, 'pixel_size', None), getattr(system, 'dpi', None),
           system.use_region_overlap, context.space_data.show_region_ui)
    resources = _resource_cache.get(owner_key)
    if resources is not None and resources.key == key and _owner_key(resources.owner) == owner_key:
        _resource_cache.move_to_end(owner_key)
        return resources
    _forget_resources(owner_key)
    # Only a draw callback may construct GPU resources, in its owning window.
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    commands = []
    for command in _plot_commands(data, scale, panel_width, blf):
        if command[0] == 'line':
            _, coords, color, thickness, kind = command
            commands.append(('line', batch_for_shader(shader, kind, {'pos': coords}), color, thickness))
        else:
            commands.append(command)
    # Retain data so its object ID cannot be reused while batches reference it.
    resources = _PlotResources(owner, key, shader, tuple(commands))
    resources.data = data
    _resource_cache[owner_key] = resources
    if len(_resource_cache) > _CACHE_LIMIT:
        _, evicted = _resource_cache.popitem(last=False)
        evicted.release()
    return resources


def draw_plot():
    import bpy
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    context = bpy.context
    scene, region = context.scene, context.region
    if scene is None or region is None or region.type != 'WINDOW' or context.window is None:
        return
    _prune_plot_resources(context)
    if scene.get('wfrl_scene_kind') == 'manual' or not getattr(scene, 'wfrl_chart_visible', False):
        return
    scale = max(1., context.preferences.system.ui_scale)
    region_width = region.width
    if context.preferences.system.use_region_overlap and context.space_data.show_region_ui:
        region_width -= max((r.width for r in context.area.regions if r.type == 'UI'), default=0)
    panel_width = min(660 * scale, region_width - 24 * scale)
    if panel_width < 280 * scale or region.height < 300 * scale:
        _forget_resources(_owner_key(_owner(context)))
        return
    data = _plot_data(scene.wfrl_chart_channel, getattr(scene, 'wfrl_selected_turbine', 'ALL'), scene)
    resources = _plot_resources(context, data, scale, panel_width, gpu, blf, batch_for_shader)
    if resources is None:
        return
    shader = resources.shader
    old_blend, old_width = gpu.state.blend_get(), gpu.state.line_width_get()
    gpu.state.blend_set('ALPHA')
    gpu.state.point_size_set(4 * scale)
    try:
        for command in resources.commands:
            if command[0] == 'line':
                _, batch, color, thickness = command
                gpu.state.line_width_set(thickness)
                shader.bind()
                shader.uniform_float('color', color)
                batch.draw(shader)
            else:
                _, x, y, text, color, size = command
                blf.position(0, x, y, 0)
                blf.size(0, size)
                blf.color(0, *color)
                blf.draw(0, text)
    except (ReferenceError, RuntimeError):
        # A lost GPU context must rebuild its batches on the next draw.
        _forget_resources(_owner_key(_owner(context)))
        raise
    finally:
        gpu.state.line_width_set(old_width)
        gpu.state.point_size_set(1.)
        gpu.state.blend_set(old_blend)


def register():
    global _handle, _registered
    import bpy
    invalidate_render_cache()
    bpy.types.Scene.wfrl_chart_visible = bpy.props.BoolProperty(name='Show curve in viewport', default=False)
    bpy.types.Scene.wfrl_chart_channel = bpy.props.EnumProperty(name='Curve', items=[(c, label, '') for c, label in {**LABELS, **STAT_LABELS}.items()])
    if _handle is None and not bpy.app.background:
        _handle = bpy.types.SpaceView3D.draw_handler_add(draw_plot, (), 'WINDOW', 'POST_PIXEL')
    _registered = True
    bpy.app.handlers.persistent(invalidate_render_cache)
    bpy.app.handlers.persistent(clear_scene_histories)
    for name in ('load_pre', 'undo_pre', 'redo_pre'):
        handlers = getattr(bpy.app.handlers, name)
        if invalidate_render_cache not in handlers:
            handlers.append(invalidate_render_cache)
        if clear_scene_histories not in handlers:
            handlers.append(clear_scene_histories)
    if not bpy.app.background and not bpy.app.timers.is_registered(_cache_maintenance):
        bpy.app.timers.register(_cache_maintenance, first_interval=.5, persistent=True)


def unregister():
    global _handle, _registered
    import bpy
    _registered = False
    if bpy.app.timers.is_registered(_cache_maintenance):
        bpy.app.timers.unregister(_cache_maintenance)
    for name in ('load_pre', 'undo_pre', 'redo_pre'):
        handlers = getattr(bpy.app.handlers, name)
        if invalidate_render_cache in handlers:
            handlers.remove(invalidate_render_cache)
        if clear_scene_histories in handlers:
            handlers.remove(clear_scene_histories)
    invalidate_render_cache()
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    for name in ('wfrl_chart_visible', 'wfrl_chart_channel'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    clear()
    clear_scene_histories()
