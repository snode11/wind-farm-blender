import sys
from types import SimpleNamespace
import pytest
from wfrl_blender import runtime, charts
from wfrl_blender.animation import KinematicState
from wfrl_blender.state import FrontendState
from wfrl_blender.wake import WakeFrameBuffer
from wfrl_blender.training import TrainingDashboard


@pytest.fixture
def local_runtime(monkeypatch):
    draws = []
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: draws.append(1))
    screen = SimpleNamespace(areas=[area], is_animation_playing=False)
    scene = {}
    bpy = SimpleNamespace(data=SimpleNamespace(objects={}), context=SimpleNamespace(scene=scene,
        window_manager=SimpleNamespace(windows=[SimpleNamespace(scene=scene, screen=screen)])))
    clock = [0.]
    job = SimpleNamespace(status='WRITING', poll=lambda: None)
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    for name, value in dict(_client=None, _health=None, _last_tick=None, _last_async_display_state=None,
                            _last_display_state=None, _backend_scene=None,
                            _state=FrontendState(connection='LOCAL DEMO'),
                            kinematics=KinematicState(clock=lambda: clock[0]), wake_buffer=WakeFrameBuffer(),
                            health_results={}).items():
        monkeypatch.setattr(runtime, name, value, raising=False)
    monkeypatch.setattr(charts, 'export_job', job)
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    return SimpleNamespace(draws=draws, screen=screen, scene=scene, job=job, clock=clock)


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS'])
@pytest.mark.parametrize('kind', ['empty', 'demo', 'manual', 'clearance_replay'])
def test_idle_local_scenes_skip_redraw(local_runtime, connection, kind):
    runtime._state.connection = connection
    local_runtime.scene['wfrl_scene_kind'] = kind
    for _ in range(2):
        assert runtime.tick() == .05
    assert runtime._state.error == ''
    assert local_runtime.draws == []


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS'])
@pytest.mark.parametrize('status', ['COMPLETE', 'FAILED'])
def test_export_status_change_redraws_once(local_runtime, connection, status):
    runtime._state.connection = connection
    local_runtime.job.poll = lambda: setattr(local_runtime.job, 'status', status)
    assert runtime.tick() == .05
    assert local_runtime.draws == [1]
    assert runtime.tick() == .05
    assert local_runtime.draws == [1]


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS'])
def test_health_results_redraw_once(local_runtime, monkeypatch, connection):
    runtime._state.connection = connection
    result = SimpleNamespace(component='python', status='READY')
    results = iter([[result], []])
    monkeypatch.setattr(runtime, '_health', SimpleNamespace(poll=lambda: next(results)))
    assert runtime.tick() == .05
    assert runtime.health_results['python'] is result
    assert local_runtime.draws == [1]
    assert runtime.tick() == .05
    assert local_runtime.draws == [1]


def test_export_completion_polled_elsewhere_and_later_label_are_redrawn(local_runtime):
    assert runtime.tick() == .05
    assert local_runtime.draws == []
    local_runtime.job.status = 'COMPLETE'
    assert runtime.tick() == .05
    assert local_runtime.draws == [1]
    local_runtime.scene['wfrl_history_export_status'] = 'COMPLETE / result.json'
    assert runtime.tick() == .05
    assert local_runtime.draws == [1, 1]
    assert runtime.tick() == .05
    assert local_runtime.draws == [1, 1]


def test_window_recording_status_redraws_when_changed(local_runtime):
    assert runtime.tick() == .05
    assert local_runtime.draws == []
    for status in ('RECORDING / 1 frame', 'RECORDING / 2 frames', 'COMPLETE / 2 frames'):
        local_runtime.scene['wfrl_capture_status'] = status
        assert runtime.tick() == .05
    assert local_runtime.draws == [1, 1, 1]
    assert runtime.tick() == .05
    assert local_runtime.draws == [1, 1, 1]


def test_async_status_in_other_window_is_visible(local_runtime):
    other_scene = {'name': 'other'}
    other_draws = []
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: other_draws.append(1))
    other_window = SimpleNamespace(scene=other_scene, screen=SimpleNamespace(areas=[area]))
    sys.modules['bpy'].context.window_manager.windows.append(other_window)
    assert runtime.tick() == .05
    other_scene['wfrl_capture_status'] = 'COMPLETE'
    assert runtime.tick() == .05
    assert local_runtime.draws == []
    assert other_draws == [1]


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS'])
def test_native_playback_does_not_add_idle_timer_redraws(local_runtime, connection):
    runtime._state.connection = connection
    local_runtime.screen.is_animation_playing = True
    assert runtime.tick() == .05
    assert runtime.tick() == .05
    assert local_runtime.draws == []


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS', 'CONNECTED'])
def test_idle_backend_client_redraws_once(local_runtime, monkeypatch, connection):
    runtime._state.connection = connection
    monkeypatch.setattr(runtime, '_client', SimpleNamespace(
        poll=lambda: [], last_error='', status='CONNECTED'))
    assert runtime.tick() == .05
    assert runtime.tick() == .05
    assert runtime._state.error == ''
    assert local_runtime.draws == [1]


def test_retained_kinematics_keeps_redraw(local_runtime):
    runtime.kinematics.snapshot = {'turbines': []}
    assert runtime.tick() == .05
    assert local_runtime.draws == [1]
    assert runtime.tick() == .05
    assert local_runtime.draws == [1]


def backend(local_runtime, monkeypatch, status='PAUSED'):
    runtime._state.connection = 'CONNECTED'
    runtime._state.run_status = status
    monkeypatch.setattr(runtime, '_backend_scene', local_runtime.scene, raising=False)
    monkeypatch.setattr(runtime, '_client', SimpleNamespace(
        poll=lambda: [], last_error='', status='CONNECTED'))
    record = dict(value=1., unit='MW', fidelity='DIRECT', validity='valid',
                  source_age_seconds=0., stale_after_seconds=2.)
    runtime.kinematics.apply_snapshot(dict(turbines=[dict(turbine_id='T1',
        channels={'power': record, 'rotor_speed': dict(record, unit='rpm')})], farm={}))


def test_telemetry_expiry_redraws_without_new_snapshot(local_runtime, monkeypatch):
    backend(local_runtime, monkeypatch)
    runtime.tick()
    for at in (.5, 1.99, 2., 3.):
        local_runtime.clock[0] = at
        runtime.tick()
    assert local_runtime.draws == [1, 1]


def test_rotor_motion_refreshes_only_while_fresh(local_runtime, monkeypatch):
    backend(local_runtime, monkeypatch, 'RUNNING')
    runtime.tick()
    local_runtime.clock[0] = .5
    runtime.tick()
    assert runtime.kinematics.rotor_angles['T1'] > 0
    assert local_runtime.draws == [1, 1]
    local_runtime.clock[0] = 2.
    runtime.tick()
    local_runtime.clock[0] = 3.
    runtime.tick()
    assert local_runtime.draws == [1, 1, 1]


def test_backend_refresh_preserves_other_scene_viewports(local_runtime, monkeypatch):
    backend(local_runtime, monkeypatch)
    other_draws = []
    other = SimpleNamespace(scene={'name': 'other'}, screen=SimpleNamespace(areas=[
        SimpleNamespace(type='VIEW_3D', tag_redraw=lambda: other_draws.append(1))]))
    sys.modules['bpy'].context.window_manager.windows.append(other)
    runtime.tick()
    local_runtime.clock[0] = 2.
    runtime.tick()
    assert local_runtime.draws == [1, 1]
    assert other_draws == []


def test_training_age_and_expiry_remain_visible_without_poll_rate_redraw(local_runtime, monkeypatch):
    from test_training_dashboard import message
    dashboard = TrainingDashboard(clock=lambda: local_runtime.clock[0])
    dashboard.reset('run')
    dashboard.ingest(message(), negotiated=True)
    monkeypatch.setattr(runtime, 'training_dashboard', dashboard)
    runtime._state.connection = 'CONNECTED'
    monkeypatch.setattr(runtime, '_client', SimpleNamespace(poll=lambda: [], last_error='',
        status='CONNECTED', negotiated_capabilities={'training_stats_v1'}))
    runtime.tick()
    local_runtime.clock[0] = .05
    runtime.tick()
    assert local_runtime.draws == [1]
    local_runtime.clock[0] = 1.
    runtime.tick()
    assert local_runtime.draws == [1, 1]
    local_runtime.clock[0] = 27.
    runtime.tick()
    assert dashboard.metric('mean_power')['validity'] == 'stale'
    assert local_runtime.draws == [1, 1, 1]


def test_poll_error_is_redrawn_in_idle_local_scene(local_runtime, monkeypatch):
    def fail():
        raise RuntimeError('health polling unavailable')
    monkeypatch.setattr(runtime, '_health', SimpleNamespace(poll=fail))
    assert runtime.tick() == .05
    assert 'health polling unavailable' in runtime._state.error
    assert local_runtime.draws == [1]
