"""Plot cache reuse, invalidation and ownership without a GPU dependency."""
from collections import Counter
from types import SimpleNamespace
import sys
import weakref

import pytest

from wfrl_blender import charts
from wfrl_blender.history import HistoryWindow


class RNA(SimpleNamespace):
    def as_pointer(self):
        return id(self)

    def get(self, key, default=None):
        return getattr(self, key, default)


def snapshot(step, *, value=None, sequence=None, **changes):
    record = dict(value=step if value is None else value, unit='MW', fidelity='DIRECT',
                  validity='valid', provenance={'source': 'synthetic'})
    payload = dict(mode='replay', run_id='run', step=step,
                   timestamp=dict(value=step / 10, timebase='simulation_seconds'),
                   turbines=[dict(turbine_id='T1', channels={'power': record})], farm={})
    payload.update(changes)
    return dict(type='snapshot', session_id='session', sequence=step + 1 if sequence is None else sequence,
                payload=payload)


@pytest.fixture
def plot(monkeypatch):
    charts.invalidate_render_cache()
    monkeypatch.setattr(charts, 'history', charts.ChartHistory())
    monkeypatch.setattr(charts, 'raw_history', HistoryWindow(clock=lambda: 10.))
    monkeypatch.setattr(charts, '_handle', None)
    monkeypatch.setattr(charts, '_registered', False)
    calls = Counter()
    batches, labels = [], []

    class Batch:
        def __init__(self, kind, coords):
            self.kind, self.coords = kind, coords

        def draw(self, shader):
            calls['draw'] += 1

    def batch_for_shader(shader, kind, attrs):
        calls['batch'] += 1
        batch = Batch(kind, attrs['pos'])
        batches.append(weakref.ref(batch))
        return batch

    def shader(name):
        calls['shader'] += 1
        return SimpleNamespace(bind=lambda: None, uniform_float=lambda *args: None)

    def dimensions(font, text):
        calls['dimensions'] += 1
        return len(text) * 5, 10

    state = SimpleNamespace(blend='NONE', width=1., point=1.)
    gpu = SimpleNamespace(shader=SimpleNamespace(from_builtin=shader), state=SimpleNamespace(
        blend_get=lambda: state.blend, blend_set=lambda value: setattr(state, 'blend', value),
        line_width_get=lambda: state.width, line_width_set=lambda value: setattr(state, 'width', value),
        point_size_set=lambda value: setattr(state, 'point', value)))
    blf = SimpleNamespace(position=lambda *args: None, size=lambda *args: None,
                          color=lambda *args: None, draw=lambda font, text: labels.append(text),
                          dimensions=dimensions)
    scene = RNA(wfrl_chart_visible=True, wfrl_chart_channel='power', wfrl_selected_turbine='ALL')
    region = RNA(type='WINDOW', width=1000, height=700)
    space = RNA(show_region_ui=True)
    sidebar = RNA(type='UI', width=180)
    area = RNA(type='VIEW_3D', regions=[region, sidebar], spaces=SimpleNamespace(active=space))
    screen = RNA(areas=[area])
    window = RNA(scene=scene, screen=screen)
    context = SimpleNamespace(scene=scene, region=region, area=area, space_data=space,
                              window=window, window_manager=SimpleNamespace(windows=[window]),
                              preferences=SimpleNamespace(system=SimpleNamespace(ui_scale=1., use_region_overlap=True)))
    timers = set()
    handles = []
    handlers = SimpleNamespace(load_pre=[], undo_pre=[], redo_pre=[], persistent=lambda fn: fn)
    bpy = SimpleNamespace(context=context, app=SimpleNamespace(background=False, handlers=handlers,
        timers=SimpleNamespace(is_registered=lambda fn: fn in timers,
                               register=lambda fn, **kwargs: timers.add(fn), unregister=timers.remove)),
        props=SimpleNamespace(BoolProperty=lambda **kwargs: None, EnumProperty=lambda **kwargs: None),
        types=SimpleNamespace(Scene=type('Scene', (), {}), SpaceView3D=SimpleNamespace(
            draw_handler_add=lambda *args: handles.append(object()) or handles[-1],
            draw_handler_remove=lambda handle, kind: handles.remove(handle))))
    for name, module in (('bpy', bpy), ('gpu', gpu), ('blf', blf),
                         ('gpu_extras.batch', SimpleNamespace(batch_for_shader=batch_for_shader))):
        monkeypatch.setitem(sys.modules, name, module)
    yield SimpleNamespace(context=context, calls=calls, batches=batches, labels=labels,
                          state=state, bpy=bpy, timers=timers, handles=handles)
    charts.invalidate_render_cache()


def _record_snapshot(plot, message):
    return charts.record_snapshot(message, scene=plot.context.scene)


def test_paused_draw_reuses_data_geometry_and_batches(plot, monkeypatch):
    for step in range(600):
        _record_snapshot(plot, snapshot(step))
    raw = charts.raw_history_for(plot.context.scene).snapshot()
    charts.draw_plot()
    first = plot.calls.copy()
    resources = next(iter(charts._resource_cache.values()))
    lines = [command[1] for command in resources.commands if command[0] == 'line']
    assert len(lines[-1].coords) == 600

    def unexpected(*args):
        pytest.fail('Unchanged draw rebuilt CPU data or layout')

    with monkeypatch.context() as guard:
        for name in ('points', 'turbine_ids'):
            guard.setattr(charts.history, name, unexpected)
        for name in ('segments', 'axis_ticks', '_plot_commands'):
            guard.setattr(charts, name, unexpected)
        for _ in range(3):
            charts.draw_plot()
    assert plot.calls['batch'] == first['batch']
    assert plot.calls['shader'] == first['shader']
    assert plot.calls['dimensions'] == first['dimensions']
    assert plot.calls['draw'] == first['draw'] * 4
    assert charts.raw_history_for(plot.context.scene).snapshot() == raw
    assert plot.state.blend == 'NONE' and plot.state.width == 1. and plot.state.point == 1.

    assert _record_snapshot(plot, snapshot(599, value=900., sequence=601))
    charts.draw_plot()
    assert resources.commands == () and resources.shader is None
    assert next(iter(charts._resource_cache.values())).data.bounds[-1] == 900.
    assert len(charts.history_for(plot.context.scene).points('T1', 'power')) == 600
    assert _record_snapshot(plot, snapshot(600, sequence=602))
    charts.draw_plot()
    assert charts.history_for(plot.context.scene).points('T1', 'power')[0].step == 1
    revision = charts.history_for(plot.context.scene).revision
    counts = plot.calls.copy()
    assert not _record_snapshot(plot, snapshot(600, sequence=602))
    charts.draw_plot()
    assert charts.history_for(plot.context.scene).revision == revision
    assert plot.calls['batch'] == counts['batch']
    assert _record_snapshot(plot, snapshot(600, sequence=603))
    charts.draw_plot()
    assert charts.history_for(plot.context.scene).revision == revision
    assert plot.calls['batch'] == counts['batch']
    assert charts.raw_history_for(plot.context.scene).last_sequence == 603


def test_selection_dimensions_timebase_and_run_invalidate(plot):
    _record_snapshot(plot, snapshot(1))
    charts.draw_plot()
    context = plot.context
    for target, name, value in (
        (context.scene, 'wfrl_selected_turbine', 'T1'),
        (context.scene, 'wfrl_chart_channel', 'yaw'),
        (context.scene, 'wfrl_chart_channel', 'power'),
        (context.region, 'width', 950),
        (context.region, 'height', 650),
        (context.preferences.system, 'ui_scale', 1.25),
        (context.area.regions[1], 'width', 250),
        (context.space_data, 'show_region_ui', False),
    ):
        previous = next(iter(charts._resource_cache.values()))
        setattr(target, name, value)
        charts.draw_plot()
        assert previous.shader is None
        assert len(charts._resource_cache) == 1
    for changes in ({'timestamp': dict(value=1000., timebase='unix_seconds')}, {'run_id': 'other'}):
        previous = next(iter(charts._resource_cache.values()))
        _record_snapshot(plot, snapshot(1, **changes))
        charts.draw_plot()
        assert previous.shader is None
        assert len(charts.history_for(plot.context.scene).points('T1', 'power')) == 1
    assert any(text.startswith('s since ') for text in plot.labels)


def test_training_and_curve_writes_change_version_at_same_step(plot):
    record = dict(value=2., unit='MW', fidelity='DIRECT', validity='valid', provenance={})
    for kind in ('training_stats', 'curve'):
        before = charts.history_for(plot.context.scene).revision
        payload = dict(mode='formal_training', run_id='run', step=3,
                       timestamp=dict(value=100., timebase='unix_seconds'), record_kind='iteration_stats',
                       stats={'mean_power': record}, turbine_id='T1', channel='power',
                       data=dict(record, value=[(1., 2.), (2., 3.)]))
        message = dict(type=kind, session_id='s', sequence=2, payload=payload)
        callback = charts.record_training if kind == 'training_stats' else charts.record_curve
        callback(message, scene=plot.context.scene)
        first = charts._plot_data('mean_power' if kind == 'training_stats' else 'power', 'ALL', plot.context.scene)
        callback(dict(message, sequence=3), scene=plot.context.scene)
        second = charts._plot_data(first.channel, 'ALL', plot.context.scene)
        assert charts.history_for(plot.context.scene).revision > before
        assert first is not second


def test_gaps_source_episode_and_units_preserve_drawing_contract(plot):
    for step in range(6):
        message = snapshot(step, context={'episode': 1 if step < 4 else 2})
        record = message['payload']['turbines'][0]['channels']['power']
        if step == 1:
            record['validity'] = 'missing'
        if step >= 3:
            record['fidelity'] = 'EXPORTED'
        _record_snapshot(plot, message)
    charts.draw_plot()
    data = next(iter(charts._resource_cache.values())).data
    assert data.series[0][1] == (((0., 0),), ((.2, 2),), ((.3, 3),), ((.4, 4), (.5, 5)))
    message = snapshot(6)
    message['payload']['turbines'][0]['channels']['power']['unit'] = 'W'
    _record_snapshot(plot, message)
    charts.draw_plot()
    resources = next(iter(charts._resource_cache.values()))
    assert sum(command[0] == 'line' for command in resources.commands) == 1
    assert 'Mixed units / select one turbine' in plot.labels


def test_window_scene_eviction_and_lifecycle_release(plot):
    charts.register()
    _record_snapshot(plot, snapshot(1))
    charts.draw_plot()
    original = next(iter(charts._resource_cache.values()))
    first_batches = tuple(plot.batches)
    context = plot.context
    window = context.window
    other = RNA(scene=context.scene, screen=window.screen)
    context.window_manager.windows.append(other)
    context.window = other
    charts.draw_plot()
    second = list(charts._resource_cache.values())[-1]
    assert second is not original and second.shader is not original.shader
    context.window_manager.windows.remove(window)
    charts._cache_maintenance()
    assert original.shader is None and all(ref() is None for ref in first_batches)
    new_scene = RNA(wfrl_chart_visible=True, wfrl_chart_channel='power', wfrl_selected_turbine='T1')
    other.scene = context.scene = new_scene
    charts.draw_plot()
    assert second.shader is None
    assert len(charts._resource_cache) == 1
    before = charts.raw_history_for(plot.context.scene).snapshot()
    for hook in ('load_pre', 'undo_pre', 'redo_pre'):
        charts.draw_plot()
        for callback in getattr(plot.bpy.app.handlers, hook):
            callback(None)
        assert not charts._resource_cache and not charts._data_cache
        assert charts.raw_history_for(plot.context.scene).snapshot() == before
    for i in range(charts._CACHE_LIMIT + 2):
        context.window = RNA(scene=context.scene, screen=context.window.screen)
        context.window_manager.windows.append(context.window)
        charts.draw_plot()
    assert len(charts._resource_cache) == charts._CACHE_LIMIT
    for i in range(charts._CACHE_LIMIT + 2):
        charts._plot_data('power', f'T{i}')
    assert len(charts._data_cache) == charts._CACHE_LIMIT
    charts.unregister()
    assert not charts._resource_cache and not charts._data_cache
    assert all(ref() is None for ref in plot.batches)
    assert not plot.timers and not plot.handles
    assert all(not getattr(plot.bpy.app.handlers, name) for name in ('load_pre', 'undo_pre', 'redo_pre'))


def test_hidden_small_view_and_gpu_failure_release(plot):
    _record_snapshot(plot, snapshot(1))
    charts.draw_plot()
    resources = next(iter(charts._resource_cache.values()))
    batch = next(command[1] for command in resources.commands if command[0] == 'line')
    def fail(shader):
        raise RuntimeError('GPU context is invalid')
    batch.draw = fail
    with pytest.raises(RuntimeError, match='GPU context'):
        charts.draw_plot()
    assert not charts._resource_cache and resources.shader is None
    assert plot.state.blend == 'NONE' and plot.state.width == 1.
    charts.draw_plot()
    plot.context.region.width = 200
    charts.draw_plot()
    assert not charts._resource_cache
    plot.context.region.width = 1000
    charts.draw_plot()
    plot.context.scene.wfrl_chart_visible = False
    charts._prune_plot_resources(plot.context)
    assert not charts._resource_cache


def test_response_plot_reuses_resources_and_returns_to_telemetry(plot, monkeypatch):
    from wfrl_blender import imbalance
    scene = plot.context.scene
    scene.wfrl_farm_panel_page = 'IMBALANCE'
    scene.wfrl_imbalance_curve = True
    data = charts._PlotData((('B1', (((0., 1.), (360., 2.)),), 'offline'),),
                           ('B1',), ('m',), (0., 360., 1., 2.), 'azimuth / deg',
                           'tip_x', 'ALL', title='Offline blade comparison')
    monkeypatch.setattr(imbalance, 'plot_data', lambda owner: data if owner is scene else None)
    charts.draw_plot()
    resources = next(iter(charts._resource_cache.values()))
    assert resources.data is data
    before = plot.calls['batch']
    charts.draw_plot()
    assert plot.calls['batch'] == before
    assert 'Offline blade comparison' in plot.labels
    scene.wfrl_farm_panel_page = 'RADAR'
    _record_snapshot(plot, snapshot(1))
    charts.draw_plot()
    assert resources.shader is None
    assert next(iter(charts._resource_cache.values())).data.channel == 'power'
    scene.wfrl_farm_panel_page = 'IMBALANCE'
    monkeypatch.setattr(imbalance, 'plot_data', lambda owner: None)
    charts.draw_plot()
    assert not charts._resource_cache
