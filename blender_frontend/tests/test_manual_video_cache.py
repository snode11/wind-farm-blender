"""Nonblocking video status, authoritative actions and controller lifecycle without bpy."""
import importlib.util
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest


class Scene(dict):
    name = 'manual'
    session_uid = 1
    wfrl_manual_trial = NS(configuration='confirmed trial')


class Layout:
    def __init__(self):
        self.labels = []
        self.operators = []
        self.columns = []
        self.enabled = True

    def box(self):
        return self

    def column(self):
        column = Layout()
        self.columns.append(column)
        return column

    def label(self, *, text, **_kwargs):
        self.labels.append(text)

    def prop(self, *_args):
        pass

    def operator(self, name, **_kwargs):
        self.operators.append(name)
        return NS()


@pytest.fixture
def controller(tmp_path, monkeypatch):
    package = ModuleType('_manual_video_cache_tests')
    package.__path__ = [str(Path(__file__).parents[1] / 'wfrl_blender')]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    response = dict(state='RENDERING', active=True, final=False, completed_frames=0, total_frames=10)
    calls = []

    def query(path):
        calls.append((path, threading.get_ident()))
        return dict(response)

    jobs = ModuleType(package.__name__ + '.manual_video_job')
    jobs.query = Mock(side_effect=query)
    jobs.require_supervision = Mock()
    jobs.read_json = Mock(return_value=dict(scene_name='manual', scene_owner='owner'))
    jobs.write_json = Mock()
    jobs.cancel = Mock(return_value=dict(state='CANCELLED', active=False, final=True))
    monkeypatch.setitem(sys.modules, jobs.__name__, jobs)
    control = ModuleType(package.__name__ + '.manual_control')
    control.is_manual = lambda scene: True
    control.trial_configuration = lambda scene: ({}, NS(rpm=9, duration_s=8))
    control.validate_scene = Mock(return_value=(True, ''))
    monkeypatch.setitem(sys.modules, control.__name__, control)
    render = ModuleType(package.__name__ + '.manual_video_render')
    render.frame_count = lambda *_args: 10
    monkeypatch.setitem(sys.modules, render.__name__, render)

    handlers = ModuleType('bpy.app.handlers')
    handlers.persistent = lambda function: function
    for name in ('undo_post', 'redo_post', 'save_pre', 'load_pre', 'load_post'):
        setattr(handlers, name, [])
    timers = set()
    bpy = ModuleType('bpy')
    bpy.app = NS(handlers=handlers, timers=NS(
        is_registered=lambda fn: fn in timers,
        register=lambda fn, **_kwargs: timers.add(fn), unregister=timers.remove))
    bpy.types = NS(Operator=type('Operator', (), dict(is_registered=False)), Scene=Scene)
    bpy.props = NS(StringProperty=lambda **_kwargs: None, FloatProperty=lambda **_kwargs: None)
    bpy.utils = NS(register_class=lambda cls: setattr(cls, 'is_registered', True),
                   unregister_class=lambda cls: setattr(cls, 'is_registered', False))
    scene = Scene(wfrl_manual_owner='owner', wfrl_manual_video_job=str(tmp_path / 'job.json'))
    scene.wfrl_manual_video_hold_s = 7
    bpy.data = NS(scenes=[scene], filepath='source.blend')
    redraw = Mock()
    area = NS(type='VIEW_3D', tag_redraw=redraw)
    bpy.context = NS(scene=scene, window_manager=NS(windows=[NS(screen=NS(areas=[area]))]))
    bpy.path = NS(abspath=str)
    bpy.ops = NS(wm=NS(save_as_mainfile=Mock(side_effect=AssertionError('unexpected snapshot'))))
    extras = ModuleType('bpy_extras.io_utils')
    extras.ExportHelper = type('ExportHelper', (), {})
    for name, module in {'bpy': bpy, 'bpy.app.handlers': handlers, 'bpy_extras.io_utils': extras}.items():
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location(
        package.__name__ + '.manual_video', Path(package.__path__[0]) / 'manual_video.py')
    ui = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, ui)
    spec.loader.exec_module(ui)
    path = scene['wfrl_manual_video_job']
    ui._JOBS[path] = dict(directory=tmp_path, scene_name=scene.name)
    ui._SCENE_JOBS[scene.session_uid] = path
    runtime = NS(ui=ui, bpy=bpy, scene=scene, jobs=jobs, response=response, path=path,
                 calls=calls, redraw=redraw, timers=timers, releases=[], control=control, spec=spec)
    yield runtime
    worker = ui._QUERIES._thread
    ui.unregister()
    for release in runtime.releases:
        release.set()
    if worker is not None:
        worker.join(timeout=2)
        assert not worker.is_alive(), 'test query did not release its daemon'


def wait_snapshot(controller, predicate):
    queries = controller.ui._QUERIES
    with queries._condition:
        assert queries._condition.wait_for(
            lambda: predicate(*queries.snapshot(controller.path)), timeout=3), 'status was not delivered'
    return queries.snapshot(controller.path)


def stall(controller, result=None):
    entered, release = threading.Event(), threading.Event()
    controller.releases.append(release)

    def query(path):
        assert threading.get_ident() != threading.main_thread().ident
        entered.set()
        assert release.wait(5), 'test query was not released'
        return dict(controller.response if result is None else result)

    controller.jobs.query.side_effect = query
    return entered, release


def immediate(function):
    started = time.monotonic()
    result = function()
    elapsed = time.monotonic() - started
    assert elapsed < .05, f'cached UI call took {elapsed:.3f} s'
    return result


def test_stalled_query_keeps_all_ui_paths_pending_and_nonblocking(controller):
    ui, scene = controller.ui, controller.scene
    entered, _release = stall(controller)
    record = immediate(lambda: ui.status(scene))
    assert entered.wait(2)
    assert record['active'] is True and record['final'] is False and record['state'] == 'QUERYING'
    assert immediate(lambda: ui.active(scene)) is True
    layout = Layout()
    immediate(lambda: ui.draw(layout, scene))
    assert not layout.columns[0].enabled
    assert any('正在查询' in text for text in layout.labels)
    assert immediate(lambda: ui.WFRL_OT_ManualVideo.poll(controller.bpy.context)) is False
    assert immediate(ui._poll) == .5
    controller.redraw.assert_not_called()


def test_repeated_polls_keep_one_inflight_query(controller):
    entered, _release = stall(controller)
    assert controller.ui._poll() == .5
    assert entered.wait(2)
    worker = controller.ui._QUERIES._thread
    for _ in range(30):
        assert immediate(controller.ui._poll) == .5
        assert immediate(lambda: controller.ui.active(controller.scene))
    assert controller.jobs.query.call_count == 1
    assert controller.ui._QUERIES._thread is worker and worker.daemon


def test_final_complete_drops_tracking_and_redraws_once(controller):
    final = dict(state='COMPLETE', active=False, final=True, completed_frames=10,
                 total_frames=10, output_mp4='/output.mp4')
    entered, release = stall(controller, final)
    controller.ui.status(controller.scene)
    assert entered.wait(2)
    release.set()
    wait_snapshot(controller, lambda record, obtained: record == final and obtained is not None)
    assert controller.ui._poll() is None
    assert not controller.ui._JOBS
    controller.redraw.assert_called_once_with()
    assert controller.ui.status(controller.scene) == final
    assert controller.ui._poll() is None
    controller.redraw.assert_called_once_with()


def test_identical_records_do_not_redraw_but_progress_does(controller):
    ui = controller.ui
    ui.status(controller.scene)
    _record, first = wait_snapshot(controller, lambda record, _time: record is not None)
    assert ui._poll() == .5
    controller.redraw.assert_called_once_with()
    controller.response['heartbeat'] = 'not displayed'
    wait_snapshot(controller, lambda record, obtained: obtained is not None and obtained > first)
    assert ui._poll() == .5
    controller.redraw.assert_called_once_with()
    controller.response['completed_frames'] = 2
    wait_snapshot(controller, lambda record, _time: record and record.get('completed_frames') == 2)
    assert ui._poll() == .5
    assert controller.redraw.call_count == 2
    assert ui._poll() == .5
    assert controller.redraw.call_count == 2


@pytest.mark.parametrize('field,value', [
    ('state', 'ENCODING'), ('total_frames', 20), ('error', 'query error'),
    ('output_mp4', '/another.mp4'), ('final', True), ('active', False)])
def test_displayed_fields_trigger_redraw(controller, field, value):
    controller.ui.status(controller.scene)
    wait_snapshot(controller, lambda record, _time: record is not None)
    controller.ui._poll()
    controller.response[field] = value
    wait_snapshot(controller, lambda record, _time: record and record.get(field) == value)
    controller.ui._poll()
    assert controller.redraw.call_count == 2


@pytest.mark.parametrize('boundary', ['load', 'after_load', 'unregister', 'reload'])
def test_old_generation_is_discarded_and_worker_is_reused(controller, boundary):
    ui = controller.ui
    old_entered, old_release = threading.Event(), threading.Event()
    new_entered, new_release = threading.Event(), threading.Event()
    controller.releases.extend([old_release, new_release])
    count = 0

    def query(_path):
        nonlocal count
        count += 1
        if count == 1:
            old_entered.set()
            assert old_release.wait(5)
            return dict(state='COMPLETE', active=False, final=True, output_mp4='/old.mp4')
        new_entered.set()
        assert new_release.wait(5)
        return dict(controller.response)

    controller.jobs.query.side_effect = query
    ui.status(controller.scene)
    assert old_entered.wait(2)
    queries, worker = ui._QUERIES, ui._QUERIES._thread
    generation = queries._generation

    def reset():
        if boundary == 'load':
            ui._before_load(None)
            ui._after_load(None)
        elif boundary == 'after_load':
            ui._after_load(None)
        elif boundary == 'unregister':
            ui.unregister()
            ui.register()
        else:
            controller.spec.loader.exec_module(ui)
            ui.register()

    immediate(reset)
    assert ui._QUERIES is queries and queries._generation > generation
    assert ui.status(controller.scene)['state'] == 'QUERYING'
    assert queries._thread is worker
    assert controller.jobs.query.call_count == 1
    old_release.set()
    assert new_entered.wait(2)
    assert queries.snapshot(controller.path) == (None, None)
    assert ui.status(controller.scene)['state'] == 'QUERYING'
    assert ui._poll() == .5
    controller.redraw.assert_not_called()
    new_release.set()
    wait_snapshot(controller, lambda record, _time: record == controller.response)
    assert ui._poll() == .5
    assert ui.active(controller.scene)


@pytest.mark.parametrize('cached', [None, dict(state='COMPLETE', active=False, final=True)])
@pytest.mark.parametrize('tracked', [False, True])
def test_launch_authoritatively_refuses_duplicate_with_empty_or_stale_cache(controller, cached, tracked):
    if not tracked:
        controller.ui._JOBS.clear()
    if cached is not None:
        controller.ui._QUERIES.store(controller.path, cached)
        assert not controller.ui.active(controller.scene)
    with pytest.raises(ValueError, match='已有视频'):
        controller.ui.launch(controller.scene, '/duplicate.mp4')
    assert (controller.path, threading.get_ident()) in controller.calls
    controller.bpy.ops.wm.save_as_mainfile.assert_not_called()
    controller.control.validate_scene.assert_not_called()
    assert controller.path in controller.ui._JOBS or controller.path in controller.ui._RESOLVING
    assert controller.ui._poll in controller.timers


def test_cancel_caches_verified_result_and_discards_older_query(controller):
    entered, release = stall(controller)
    controller.ui.status(controller.scene)
    assert entered.wait(2)
    worker = controller.ui._QUERIES._thread
    result = controller.ui.cancel(controller.scene)
    controller.jobs.cancel.assert_called_once_with(controller.path)
    assert result['state'] == 'CANCELLED'
    assert controller.ui.status(controller.scene) == result
    assert controller.ui._poll() is None
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert controller.ui.status(controller.scene) == result
    assert not controller.ui._JOBS


def test_completed_saved_path_is_not_added_to_polling_jobs(controller):
    ui = controller.ui
    ui._JOBS.clear()
    ui._SCENE_JOBS.clear()
    final = dict(state='COMPLETE', active=False, final=True, output_mp4='/output.mp4')
    entered, release = stall(controller, final)
    assert ui.status(controller.scene)['state'] == 'QUERYING'
    assert entered.wait(2)
    assert not ui._JOBS and controller.path in ui._RESOLVING
    release.set()
    wait_snapshot(controller, lambda record, _time: record == final)
    assert ui._poll() is None
    assert ui.status(controller.scene) == final
    assert not ui._JOBS and not ui._RESOLVING
    assert ui._poll() is None
    controller.redraw.assert_called_once_with()


def test_cached_scene_binding_survives_undo_and_rejects_copy(controller):
    controller.ui.status(controller.scene)
    wait_snapshot(controller, lambda record, _time: record is not None)
    controller.scene.pop('wfrl_manual_video_job')
    controller.ui._restore_job_paths(None)
    assert controller.scene['wfrl_manual_video_job'] == controller.path
    assert controller.ui.active(controller.scene)
    copy = Scene(controller.scene)
    copy.name, copy.session_uid = 'copy', 2
    assert not controller.ui.active(copy)


def test_active_observes_background_completion_without_timer_calls(controller):
    assert controller.ui.active(controller.scene)
    wait_snapshot(controller, lambda record, _time: record is not None)
    controller.response.update(state='COMPLETE', active=False, final=True, output_mp4='/output.mp4')
    wait_snapshot(controller, lambda record, _time: record and record.get('final'))
    assert not controller.ui.active(controller.scene)
    assert controller.ui.status(controller.scene)['state'] == 'COMPLETE'
    assert controller.path in controller.ui._JOBS
    assert controller.ui._poll() is None


@pytest.mark.parametrize('state', ['COMPLETE', 'FAILED', 'CANCELLED', 'LOST'])
@pytest.mark.parametrize('final', [False, True])
def test_draw_only_labels_confirmed_terminal_results(controller, state, final):
    controller.ui._QUERIES.store(controller.path, dict(
        state=state, final=final, active=False, output_mp4='/output.mp4'))
    layout = Layout()
    controller.ui.draw(layout, controller.scene)
    labels = {'COMPLETE': '视频已完成', 'FAILED': '视频生成失败，见输出目录 process.log',
              'CANCELLED': '视频生成已取消'}
    for terminal, label in labels.items():
        assert (label in layout.labels) == (final and state == terminal)
    assert ('wm.path_open' in layout.operators) == (final and state == 'COMPLETE')


@pytest.mark.parametrize('error', [ValueError, RuntimeError])
def test_query_error_is_conservatively_lost_and_keeps_polling(controller, error):
    controller.jobs.query.side_effect = error('unreadable job')
    controller.ui.status(controller.scene)
    record, _obtained = wait_snapshot(controller, lambda record, _time: record is not None)
    assert record == dict(state='LOST', active=True, final=False, error='unreadable job')
    assert controller.ui._poll() == .5
    assert controller.ui.active(controller.scene)


def test_inactive_lost_job_stops_polling_without_final_result(controller):
    controller.response.update(state='LOST', active=False, final=False, error='no live lease')
    controller.ui.status(controller.scene)
    wait_snapshot(controller, lambda record, _time: record is not None)
    assert controller.ui._poll() is None
    assert not controller.ui._JOBS
    assert controller.ui.status(controller.scene)['state'] == 'LOST'
    assert controller.ui.status(controller.scene)['final'] is False
