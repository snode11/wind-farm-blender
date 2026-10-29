"""Short native chart cache and window ownership check, with optional CPU comparison.

Use an independent --factory-startup process and a new WFRL_TEST_OUTPUT directory.
WFRL_CHART_BASELINE may name an unchanged charts.py for a matched draw comparison.
The synthetic 600-point signals test rendering only, not physical results or FPS.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from time import monotonic, perf_counter
import traceback

import bpy
import gpu_extras.batch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
from wfrl_blender import charts

OUT = Path(os.environ['WFRL_TEST_OUTPUT']).resolve()
OUT.mkdir(parents=True, exist_ok=False)
assert '--factory-startup' in sys.argv and not bpy.app.background and not bpy.data.filepath
assert Path(charts.__file__).resolve().parent == ROOT / 'blender_frontend/wfrl_blender'
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
baseline = None
if os.environ.get('WFRL_CHART_BASELINE'):
    spec = importlib.util.spec_from_file_location('wfrl_blender._chart_render_cache_baseline',
                                                 os.environ['WFRL_CHART_BASELINE'])
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    baseline.history = charts.history_for(bpy.context.scene)

state = {'deadline': monotonic() + 30., 'seen': set(), 'metrics': {}, 'error': None}
draw = charts.draw_plot


def measured(module, count):
    metrics = dict(batch_builds=0, vertices_built=0, history_reads=0, segment_scans=0, axis_scans=0)
    geometry = []
    chart_history = charts.history_for(bpy.context.scene)
    originals = (gpu_extras.batch.batch_for_shader, chart_history.points, module.segments, module.axis_ticks)
    def batch(shader, kind, attributes):
        metrics['batch_builds'] += 1
        metrics['vertices_built'] += len(attributes['pos'])
        geometry.append((kind, tuple(attributes['pos'])))
        return originals[0](shader, kind, attributes)
    def points(*args):
        metrics['history_reads'] += 1
        return originals[1](*args)
    def segments(*args):
        metrics['segment_scans'] += 1
        return originals[2](*args)
    def axes(*args):
        metrics['axis_scans'] += 1
        return originals[3](*args)
    gpu_extras.batch.batch_for_shader = batch
    chart_history.points, module.segments, module.axis_ticks = points, segments, axes
    try:
        callback = draw if module is charts else module.draw_plot
        started = perf_counter()
        for _ in range(count):
            callback()
        metrics.update(draws=count, cpu_ms=(perf_counter() - started) * 1000)
    finally:
        gpu_extras.batch.batch_for_shader = originals[0]
        chart_history.points, module.segments, module.axis_ticks = originals[1:]
    return metrics, geometry


def probe():
    if state['error']:
        return
    try:
        if not state['metrics']:
            charts.invalidate_render_cache()
            cold, geometry = measured(charts, 1)
            warm, _ = measured(charts, 20)
            assert cold['batch_builds'] > 0
            assert all(warm[name] == 0 for name in
                       ('batch_builds', 'vertices_built', 'history_reads', 'segment_scans', 'axis_scans'))
            state['metrics'].update(cold=cold, cached=warm)
            if baseline:
                previous, old_geometry = measured(baseline, 20)
                assert old_geometry[:len(geometry)] == geometry
                state['metrics'].update(baseline=previous, same_geometry=True)
        else:
            draw()
        state['seen'].add(bpy.context.window.as_pointer())
    except Exception:
        state['error'] = traceback.format_exc()


def finish(error=None):
    charts.unregister()
    charts.draw_plot = draw
    assert not charts._resource_cache and not charts._data_cache
    assert not bpy.app.timers.is_registered(charts._cache_maintenance)
    assert all(charts.invalidate_render_cache not in getattr(bpy.app.handlers, name)
               for name in ('load_pre', 'undo_pre', 'redo_pre'))
    del bpy.types.Scene.wfrl_selected_turbine
    result = dict(status='FAIL' if error else 'PASS', error=error, blender=bpy.app.version_string,
                  source=charts.__file__, source_sha256=hashlib.sha256(Path(charts.__file__).read_bytes()).hexdigest(),
                  metrics=state['metrics'], separate_window_batches=state.get('separate_window_batches', False),
                  closed_window_released=state.get('closed_window_released', False),
                  unregister_released=True, scope='Synthetic draw callback CPU and allocations; no viewport FPS')
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print('CHART_RENDER_CACHE_WINDOW_' + result['status'], json.dumps(result), flush=True)
    bpy.ops.wm.quit_blender()


def poll():
    try:
        if state['error']:
            raise RuntimeError(state['error'])
        if monotonic() > state['deadline']:
            raise TimeoutError('Chart draw callbacks did not complete')
        main = state['main']
        if main.as_pointer() not in state['seen']:
            return .1
        if 'other' not in state:
            state['main_resources'] = next(iter(charts._resource_cache.values()))
            with bpy.context.temp_override(window=main):
                before = {window.as_pointer() for window in bpy.context.window_manager.windows}
                bpy.ops.wm.window_new()
            state['other'] = next(window for window in bpy.context.window_manager.windows
                                  if window.as_pointer() not in before)
            return .1
        other = state['other']
        if other.as_pointer() not in state['seen']:
            return .1
        resources = next(value for key, value in charts._resource_cache.items() if key[0] == other.as_pointer())
        first_batches = [command[1] for command in state['main_resources'].commands if command[0] == 'line']
        other_batches = [command[1] for command in resources.commands if command[0] == 'line']
        assert first_batches and other_batches and all(a is not b for a, b in zip(first_batches, other_batches))
        state['separate_window_batches'] = True
        other_batches.clear()
        with bpy.context.temp_override(window=other):
            bpy.ops.wm.window_close()
        charts._cache_maintenance()
        assert resources.shader is None and resources.commands == ()
        state['closed_window_released'] = True
        assert charts.raw_history_for(scene).snapshot() == state['raw']
        finish()
    except Exception:
        finish(traceback.format_exc())
    return None


bpy.types.Scene.wfrl_selected_turbine = bpy.props.StringProperty(default='ALL')
charts.draw_plot = probe
charts.register()
scene = bpy.context.scene
scene.wfrl_chart_visible = True
scene.wfrl_chart_channel = 'power'
for step in range(600):
    turbines = [dict(turbine_id=f'T{index+1}', channels={'power': dict(
        value=(step % 100) * .02 + index, unit='MW', fidelity='DIRECT', validity='valid',
        provenance={'source': 'synthetic chart fixture'})}) for index in range(3)]
    charts.record_snapshot(dict(type='snapshot', session_id='chart-render-cache', sequence=step+1,
        payload=dict(mode='replay', run_id='synthetic', step=step, turbines=turbines, farm={},
                     timestamp=dict(value=step * .1, timebase='simulation_seconds'))), scene=scene)
state.update(main=bpy.context.window, raw=charts.raw_history_for(scene).snapshot())
for area in bpy.context.screen.areas:
    if area.type == 'VIEW_3D':
        area.tag_redraw()
bpy.app.timers.register(poll, first_interval=.1)
