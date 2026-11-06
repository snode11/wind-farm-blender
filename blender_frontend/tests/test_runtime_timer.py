import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl_blender import charts, farm_flex, live_scene, runtime
from wfrl_blender.state import FrontendState


@pytest.fixture
def timer(monkeypatch):
    state = FrontendState(connection='CONNECTED', run_status='DRAINING', session_id='s')
    client = SimpleNamespace(progress='Connected', negotiated_capabilities=frozenset(), session_id='s', last_error='', status='CONNECTED',
                             poll=Mock(return_value=[]), close=Mock())
    motion = SimpleNamespace(snapshot=None, rotor_angles={}, advance=Mock(), apply_objects=Mock())
    job = SimpleNamespace(error=None, path=None, status='WRITING', poll=Mock())
    health = SimpleNamespace(poll=Mock(return_value=[]))
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=Mock())
    scene = SimpleNamespace(objects={}, get=lambda *_args: None)
    bpy = SimpleNamespace(data=SimpleNamespace(objects={}), context=SimpleNamespace(scene=scene,
        window_manager=SimpleNamespace(windows=[SimpleNamespace(scene=scene, screen=SimpleNamespace(
            areas=[area], is_animation_playing=True))])))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    for name, value in dict(_state=state, _client=client, kinematics=motion, _health=health,
                            _last_tick=None, _last_async_display_state=None, _pending_command=None,
                            _pending_lifecycle_sequence=None, _tick_failures=set()).items():
        monkeypatch.setattr(runtime, name, value, raising=False)
    monkeypatch.setattr(charts, 'export_job', job)
    wake = Mock()
    monkeypatch.setattr(live_scene, 'animate_illustrative_wake', wake)
    return SimpleNamespace(state=state, client=client, steps={
        'advance': motion.advance, 'objects': motion.apply_objects, 'wake': wake,
        'export': job.poll, 'health': health.poll, 'redraw': area.tag_redraw})


@pytest.mark.parametrize('step,error_type', [
    ('advance', AttributeError), ('objects', ReferenceError), ('wake', ReferenceError),
    ('export', AttributeError), ('health', ReferenceError), ('redraw', ReferenceError)])
def test_unexpected_timer_failure_disconnects_and_keeps_timer(timer, step, error_type):
    timer.steps[step].side_effect = error_type('RNA is unavailable')
    assert runtime.tick() == .05
    assert timer.state.connection == 'DISCONNECTED'
    assert not timer.state.confirmed
    assert 'RNA is unavailable' in timer.state.error
    assert 'backend state is unconfirmed' in timer.state.error
    timer.client.close.assert_called_once_with()


@pytest.mark.parametrize('connection', ['LOCAL DEMO', 'OFFLINE RESULTS'])
@pytest.mark.parametrize('step', ['redraw', 'export'])
@pytest.mark.parametrize('confirmed', [True, False])
def test_local_timer_failure_preserves_connection_and_session(timer, monkeypatch, connection, step, confirmed):
    monkeypatch.setattr(runtime, '_client', None)
    monkeypatch.setattr(farm_flex, 'is_active', lambda scene: False)
    timer.state.connection, timer.state.confirmed = connection, confirmed
    session = (timer.state.session_id, timer.state.run_id)
    timer.steps[step].side_effect = ReferenceError('RNA is unavailable')
    assert runtime.tick() == .05
    assert timer.steps[step].call_count == (2 if step == 'redraw' else 1)
    timer.steps[step].assert_called_with()
    assert timer.state.connection == connection
    assert timer.state.confirmed is confirmed
    assert (timer.state.session_id, timer.state.run_id) == session
    assert timer.state.error == 'Runtime polling failed: RNA is unavailable'
    timer.client.close.assert_not_called()


@pytest.mark.parametrize('error_type', [AttributeError, ReferenceError])
def test_unexpected_message_failure_keeps_timer(timer, monkeypatch, error_type):
    timer.client.poll.return_value = [dict(type='lifecycle', session_id='s', sequence=2,
        payload=dict(run_status='STOPPED', mode='demo', capabilities=[]))]
    monkeypatch.setattr(timer.state, 'lifecycle', Mock(side_effect=error_type('missing RNA')))
    assert runtime.tick() == .05
    assert timer.state.connection == 'DISCONNECTED'
    assert not timer.state.confirmed
    timer.client.close.assert_called_once_with()


def test_timer_traceback_is_printed_once_per_distinct_failure(timer, capsys):
    timer.steps['export'].side_effect = [ReferenceError('missing RNA'),
        ReferenceError('missing RNA'), AttributeError('missing RNA')]
    for _ in range(3):
        assert runtime.tick() == .05
    assert capsys.readouterr().err.count('Traceback (most recent call last):') == 2


def test_timer_survives_client_close_failure(timer):
    timer.steps['objects'].side_effect = ReferenceError('missing RNA')
    timer.client.close.side_effect = RuntimeError('close failed')
    assert runtime.tick() == .05
    assert timer.state.connection == 'DISCONNECTED'
    assert not timer.state.confirmed


def test_lifecycle_is_processed_before_transport_error_disconnect(timer, monkeypatch):
    timer.client.poll.return_value = [dict(type='lifecycle', session_id='s', sequence=2,
        payload=dict(run_status='STOPPED', mode='demo', capabilities=[]))]
    timer.client.last_error = 'inbound sequence must increase'
    observed = []
    disconnect = timer.state.disconnect

    def disconnect_after_lifecycle(error):
        observed.append(timer.state.run_status)
        disconnect(error)

    monkeypatch.setattr(timer.state, 'disconnect', disconnect_after_lifecycle)
    assert runtime.tick() == .05
    assert observed == ['STOPPED']
    assert timer.state.connection == 'DISCONNECTED'
    assert timer.state.error == timer.client.last_error


def test_known_message_error_keeps_existing_diagnostic(timer):
    timer.client.poll.return_value = [dict(type='lifecycle', session_id='s', sequence=2,
        payload=dict(run_status='RUNNING', mode='demo', capabilities=[]))]
    assert runtime.tick() == .05
    assert timer.state.error.startswith('Invalid backend data or scene: ')
    timer.client.close.assert_called_once_with()


def test_transport_cleanup_failure_still_invalidates_wake(timer, monkeypatch, caplog):
    timer.steps['export'].side_effect = KeyError('broken export state')
    timer.client.close.side_effect = RuntimeError('close failed')
    clear = Mock()
    monkeypatch.setattr(runtime, '_clear_backend_wake', clear)
    assert runtime.tick() == .05
    clear.assert_called_once_with('stale')
    assert 'broken export state' in timer.state.error
    assert 'transport cleanup failed' in caplog.text and 'close failed' in caplog.text


def test_invalid_backend_error_survives_transport_cleanup_failure(timer, caplog):
    timer.client.poll.side_effect = ValueError('corrupt backend frame')
    timer.client.close.side_effect = RuntimeError('close failed')
    assert runtime.tick() == .05
    assert timer.state.error == 'Invalid backend data or scene: corrupt backend frame'
    assert 'transport cleanup failed' in caplog.text


@pytest.mark.parametrize('error_type', [OSError, ValueError])
def test_unwritable_traceback_is_recorded_in_timer_state(timer, monkeypatch, error_type):
    timer.steps['export'].side_effect = TypeError('invalid export state')
    monkeypatch.setattr(runtime.traceback, 'print_exc', Mock(side_effect=error_type('stderr closed')))
    assert runtime.tick() == .05
    assert 'invalid export state' in timer.state.error
    assert 'traceback unavailable: stderr closed' in timer.state.error


@pytest.mark.parametrize('target', ['_clear_backend_wake', '_redraw_viewports'])
def test_timer_cleanup_failure_is_logged_without_replacing_primary(timer, monkeypatch, caplog, target):
    timer.steps['export'].side_effect = TypeError('invalid export state')
    monkeypatch.setattr(runtime, target, Mock(side_effect=KeyError('broken cleanup state')))
    assert runtime.tick() == .05
    assert 'invalid export state' in timer.state.error
    assert 'broken cleanup state' in caplog.text
