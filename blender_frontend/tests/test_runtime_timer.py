import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl_blender import charts, farm_flex, live_scene, runtime
from wfrl_blender.state import FrontendState


@pytest.fixture
def timer(monkeypatch):
    state = FrontendState(connection='CONNECTED', run_status='DRAINING', session_id='s')
    client = SimpleNamespace(session_id='s', last_error='', status='CONNECTED',
                             poll=Mock(return_value=[]), close=Mock())
    motion = SimpleNamespace(snapshot=None, advance=Mock(), apply_objects=Mock())
    job = SimpleNamespace(status='WRITING', poll=Mock())
    health = SimpleNamespace(poll=Mock(return_value=[]))
    area = SimpleNamespace(type='VIEW_3D', tag_redraw=Mock())
    bpy = SimpleNamespace(data=SimpleNamespace(objects={}), context=SimpleNamespace(scene={},
        window_manager=SimpleNamespace(windows=[SimpleNamespace(screen=SimpleNamespace(
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
