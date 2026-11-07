"""Batch semantics of backend messages consumed by runtime.tick()."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import charts, runtime
from wfrl_blender.animation import KinematicState
from wfrl_blender.state import FrontendState
from test_animation_math import snapshot


@pytest.fixture
def backend(monkeypatch):
    bpy = NS(context=NS(scene=None, window_manager=NS(windows=[])), data=NS(objects={}))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    batch = []
    client = NS(negotiated_capabilities=frozenset(), session_id='s', last_error='', status='CONNECTED',
                progress='Connected', poll=lambda: list(batch), send=lambda message: None,
                close=lambda: None)
    ui = FrontendState(mode='interactive_training', connection='CONNECTED', run_status='RUNNING',
                       session_id='s', run_id='r1', capabilities={'pause', 'single_step'})
    initial = snapshot()
    state = KinematicState()
    state.snapshot = initial
    recorded = NS(curves=[], snapshots=[])
    monkeypatch.setattr(charts, 'record_curve', lambda message, scene=None: recorded.curves.append(message))
    monkeypatch.setattr(charts, 'record_snapshot',
                        lambda message, scene=None: recorded.snapshots.append(message))
    monkeypatch.setattr(charts, 'clear', lambda *args, **kwargs: None)
    for name, value in dict(_client=client, _state=ui, _pending_command=None, _pending_command_sequence=None,
                            _pending_step=None, _pending_lifecycle_sequence=None, _last_tick=None,
                            _last_display_state=None, _last_async_display_state=None, _health=None,
                            _live_scene=None, _backend_scene=None, kinematics=state,
                            _desired_mode='interactive_training', build_live_scene=lambda scene: None).items():
        monkeypatch.setattr(runtime, name, value)
    monkeypatch.setattr(runtime, 'safety_events', type(runtime.safety_events)(maxlen=256))
    monkeypatch.setattr(runtime, 'backend_events', type(runtime.backend_events)(maxlen=256))
    return NS(batch=batch, ui=ui, initial=initial, recorded=recorded)


def message(kind, sequence, payload):
    return dict(type=kind, session_id='s', sequence=sequence, payload=payload)


def rejection(sequence, command_sequence=None):
    payload = dict(code='COMMAND_REJECTED', message='rejected', fatal=False)
    if command_sequence is not None:
        payload['command_sequence'] = command_sequence
    return message('error', sequence, payload)


def test_curve_messages_reach_chart_history(backend):
    curve = message('curve', 2, dict(run_id='r1', points=[]))
    backend.batch.append(curve)
    runtime.tick()
    assert backend.recorded.curves == [curve]


def test_matching_rejection_clears_pending_command_and_reports(backend, monkeypatch):
    monkeypatch.setattr(runtime, '_pending_command', 'pause')
    monkeypatch.setattr(runtime, '_pending_command_sequence', 7)
    backend.batch.append(rejection(2, 7))
    runtime.tick()
    assert runtime._pending_command is None
    assert backend.ui.error == 'rejected' and backend.ui.connection == 'CONNECTED'


def test_rejection_for_another_command_is_ignored(backend, monkeypatch):
    monkeypatch.setattr(runtime, '_pending_command', 'pause')
    monkeypatch.setattr(runtime, '_pending_command_sequence', 7)
    backend.batch.append(rejection(2, 5))
    runtime.tick()
    assert runtime._pending_command == 'pause' and backend.ui.error == ''


def test_unsequenced_rejection_without_pending_command_is_ignored(backend):
    backend.batch.append(rejection(2))
    runtime.tick()
    assert backend.ui.error == '' and backend.ui.connection == 'CONNECTED'


def test_unsequenced_rejection_while_pending_stops_the_batch(backend, monkeypatch):
    monkeypatch.setattr(runtime, '_pending_command', 'pause')
    monkeypatch.setattr(runtime, '_pending_command_sequence', 7)
    newer = snapshot()
    newer['run_id'] = 'r1'
    backend.batch.extend([
        message('snapshot', 2, newer),
        rejection(3),
        message('safety_event', 4, dict(kind='late')),
    ])
    runtime.tick()
    assert backend.ui.connection == 'DISCONNECTED'
    assert backend.ui.error == 'Unmatched command rejection; reconnect to confirm backend state'
    # The earlier snapshot is not applied and later messages are not processed.
    assert runtime.kinematics.snapshot is backend.initial
    assert list(runtime.safety_events) == []
    assert [event['type'] for event in runtime.backend_events] == ['error']


def test_other_errors_are_reported(backend):
    backend.batch.append(message('error', 2, dict(code='WORKER_FAILED', message='worker died', fatal=True)))
    runtime.tick()
    assert backend.ui.error == 'worker died'


def test_run_change_discards_an_earlier_snapshot_of_the_same_batch(backend):
    earlier = snapshot()
    earlier['run_id'] = 'r1'
    backend.batch.extend([
        message('snapshot', 2, earlier),
        message('lifecycle', 3, dict(run_status='RUNNING', mode='interactive_training',
                                     capabilities=['pause', 'single_step'], run_id='r2')),
    ])
    runtime.tick()
    assert backend.ui.run_id == 'r2'
    assert runtime.kinematics.snapshot is None
    assert backend.recorded.snapshots == [backend.batch[0]]


def test_snapshot_for_another_run_is_skipped(backend):
    other = snapshot()
    other['run_id'] = 'r0'
    backend.batch.append(message('snapshot', 2, other))
    runtime.tick()
    assert runtime.kinematics.snapshot is backend.initial
    assert backend.recorded.snapshots == []
