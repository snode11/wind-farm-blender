"""Backend cleanup truth remains independent of the run outcome."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.messages import FrameDecoder, ProtocolError, encode_message, validate_message
from wfrl.blender_bridge import __main__ as bridge_main
from wfrl.blender_bridge.server import BridgeServer


SPEC = importlib.util.spec_from_file_location(
    'cleanup_launcher', Path(__file__).resolve().parents[2] / 'scripts/blender/wfrl_launcher.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def frame(session, kind='hello_ack', **changes):
    payload = session.synchronization_state()
    if kind == 'hello_ack':
        payload.update(selected_version=1, resumed=True)
    else:
        payload.update(reason=None)
    payload.update(changes)
    message = dict(protocol_version=1, type=kind, session_id=session.session_id,
                   sequence=payload['state_revision'], payload=payload)
    body = json.dumps(message).encode()
    return struct.pack('>I', len(body)) + body


def client_for(monkeypatch, frames):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.recv.side_effect = [*frames, b'']
    monkeypatch.setattr(launcher.socket, 'create_connection', Mock(return_value=client))
    return client


@pytest.mark.parametrize('worker_alive', [True, False])
def test_failed_backend_state_requires_actual_cleanup_in_launcher(monkeypatch, worker_alive):
    session = BackendSession()
    session._trainer = SimpleNamespace(running=worker_alive, stop=lambda timeout: None)
    session.lifecycle('FAILED', 'Controlled backend failure')
    state = session.synchronization_state()
    assert state['worker_alive'] is worker_alive
    assert state['cleanup_confirmed'] is not worker_alive
    client = client_for(monkeypatch, [frame(session)])
    assert launcher.request_protocol_stop(18765, session.session_id, .1) is not worker_alive
    assert client.sendall.call_count == (2 if worker_alive else 1)


def test_cleanup_completion_retains_failed_result_and_publishes_new_revision(monkeypatch):
    session = BackendSession()
    trainer = SimpleNamespace(running=True, stop=lambda timeout: None)
    session._trainer = trainer
    session.lifecycle('RUNNING')
    session.stop(0)
    session._stop_worker.join(1)
    assert not session._stop_worker.is_alive()
    before = session.synchronization_state()
    assert before['run_status'] == 'FAILED'
    assert before['cleanup_confirmed'] is False
    ack = frame(session)
    session.poll()
    trainer.running = False
    events = session.poll()
    terminal = [payload for kind, payload in events if kind == 'lifecycle']
    assert terminal[-1]['run_status'] == 'FAILED'
    assert terminal[-1]['cleanup_confirmed'] is True
    assert terminal[-1]['worker_alive'] is False
    assert terminal[-1]['state_revision'] > before['state_revision']
    client_for(monkeypatch, [ack, frame(session, 'lifecycle')])
    assert launcher.request_protocol_stop(18765, session.session_id, .1)


@pytest.mark.parametrize('refresh', ['poll', 'synchronization_state'])
def test_cleanup_revision_retains_failure_reason_until_explicit_replacement_or_reset(refresh):
    session = BackendSession()
    session._trainer = SimpleNamespace(running=True)
    diagnosis = 'Stop timeout: formal backend was force terminated'
    session.lifecycle('FAILED', diagnosis)
    session.poll()
    session._trainer.running = False
    if refresh == 'synchronization_state':
        session.synchronization_state()
    events = session.poll()
    latest = [payload for kind, payload in events if kind == 'lifecycle'][-1]
    assert latest['cleanup_confirmed'] is True
    assert latest['reason'] == diagnosis
    session.command(dict(command='session.sync', arguments={}))
    assert session.poll()[-1][1]['reason'] == diagnosis
    session.lifecycle('FAILED', 'Replacement failure diagnosis')
    assert session.poll()[-1][1]['reason'] == 'Replacement failure diagnosis'
    session.lifecycle('FAILED', None)
    assert session.poll()[-1][1]['reason'] is None
    session.lifecycle('FAILED', diagnosis)
    session.command(dict(command='run.reset', arguments={}))
    latest = session.poll()[-1][1]
    assert (latest['run_status'], latest['reason']) == ('READY', None)


def test_normal_stopped_backend_proves_cleanup(monkeypatch):
    session = BackendSession()
    session.lifecycle('RUNNING')
    session.stop(0)
    state = session.synchronization_state()
    assert (state['run_status'], state['cleanup_confirmed'], state['worker_alive']) == ('STOPPED', True, False)
    client_for(monkeypatch, [frame(session)])
    assert launcher.request_protocol_stop(18765, session.session_id, .1)


def test_legacy_failed_handshake_is_not_cleanup_proof(monkeypatch):
    session = BackendSession()
    session.lifecycle('FAILED', 'Legacy failure')
    body = json.loads(frame(session)[4:])
    body['payload'].pop('cleanup_confirmed', None)
    body['payload'].pop('worker_alive', None)
    encoded = json.dumps(body).encode()
    client = client_for(monkeypatch, [struct.pack('>I', len(encoded)) + encoded])
    assert not launcher.request_protocol_stop(18765, session.session_id, .1)
    assert client.sendall.call_count == 2


def test_dead_worker_does_not_confirm_cleanup_while_drain_is_still_running():
    session = BackendSession()
    entered, release = threading.Event(), threading.Event()
    def stop(timeout):
        session._trainer.running = False
        entered.set()
        release.wait(2)
    session._trainer = SimpleNamespace(running=True, stop=stop)
    session.lifecycle('FAILED', 'Controlled backend failure')
    session.stop(0)
    try:
        assert entered.wait(1)
        state = session.synchronization_state()
        assert state['worker_alive'] is False
        assert state['cleanup_confirmed'] is False
    finally:
        release.set()
        session._stop_worker.join(1)
    assert session.synchronization_state()['cleanup_confirmed'] is True


@pytest.mark.parametrize('changes', [
    dict(cleanup_confirmed=True, worker_alive=True),
    dict(cleanup_confirmed='yes', worker_alive=False),
    dict(cleanup_confirmed=False), dict(worker_alive=False),
])
def test_protocol_rejects_invalid_cleanup_proof(changes):
    payload = dict(run_status='FAILED', reason=None, mode='demo', capabilities=[], **changes)
    with pytest.raises(ProtocolError):
        validate_message(dict(protocol_version=1, type='lifecycle', session_id='s', sequence=1, payload=payload))


def test_bridge_shutdown_keeps_supervising_until_failed_worker_is_clean(monkeypatch):
    session = BackendSession()
    session._trainer = SimpleNamespace(running=True, stop=lambda timeout: None)
    session.lifecycle('FAILED', 'Controlled backend failure')
    polls = []
    closed = []
    class Server:
        port = 18765
        closed = False
        def __init__(self, *args):
            pass
        def poll(self):
            polls.append(session.alive())
            if len(polls) == 1:
                raise KeyboardInterrupt
            if len(polls) == 4:
                session._trainer.running = False
            session.tick()
        def serve_forever(self):
            self.poll()
        def close(self):
            closed.append(session.synchronization_state())
            self.closed = True
            session.stop(0)
    monkeypatch.setattr(bridge_main, 'BackendSession', lambda: session)
    monkeypatch.setattr(bridge_main, 'BridgeServer', Server)
    monkeypatch.setattr(bridge_main.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['wfrl.blender_bridge'])
    bridge_main.main()
    assert len(polls) == 4
    assert closed[-1]['run_status'] == 'FAILED'
    assert closed[-1]['cleanup_confirmed'] is True


@pytest.mark.parametrize('signal_timing', ['before_loop', 'sleep', 'poll'])
def test_main_closes_command_gate_before_poll_dispatch_after_shutdown_signal(monkeypatch, signal_timing):
    session = BackendSession()
    handlers, dispatched, polls = {}, [], []
    queued = [signal_timing == 'before_loop']
    class Server:
        port = 18765
        closed = False
        shutdown_requested = False
        def __init__(self, *args):
            pass
        def poll(self):
            polls.append(self.shutdown_requested)
            if signal_timing == 'poll':
                queued[0] = True
                handlers[bridge_main.signal.SIGTERM](bridge_main.signal.SIGTERM, None)
            if queued[0] and not self.shutdown_requested:
                dispatched.extend(['run.reset', 'run.start'])
            queued[0] = False
        def close(self):
            self.closed = True
    def register(signum, handler):
        handlers[signum] = handler
        if signal_timing == 'before_loop' and signum == bridge_main.signal.SIGTERM:
            handler(signum, None)
    def sleep(_seconds):
        assert signal_timing == 'sleep'
        queued[0] = True
        handlers[bridge_main.signal.SIGTERM](bridge_main.signal.SIGTERM, None)
    monkeypatch.setattr(bridge_main, 'BackendSession', lambda: session)
    monkeypatch.setattr(bridge_main, 'BridgeServer', Server)
    monkeypatch.setattr(bridge_main.signal, 'signal', register)
    monkeypatch.setattr(bridge_main.time, 'sleep', sleep)
    monkeypatch.setattr(sys, 'argv', ['wfrl.blender_bridge'])
    bridge_main.main()
    assert dispatched == []
    assert len(polls) == (2 if signal_timing == 'sleep' else 1)
    if signal_timing != 'poll':
        assert polls[-1] is True


def test_shutting_down_bridge_rejects_new_work_and_keeps_cleanup_commands_available():
    session = BackendSession()
    command = Mock(wraps=session.command)
    session.command = command
    server = BridgeServer(port=0, session=session)
    server.shutdown_requested = True
    decoder, received = FrameDecoder(), []
    client = socket.create_connection(('127.0.0.1', server.port))
    client.setblocking(False)
    def send(kind, sequence, payload):
        client.sendall(encode_message(dict(protocol_version=1, type=kind,
            session_id='' if kind == 'hello' else session.session_id,
            sequence=sequence, payload=payload)))
    def until(predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            assert time.monotonic() < deadline
            server.poll()
            try:
                received.extend(decoder.feed(client.recv(65536)))
            except BlockingIOError:
                pass
            time.sleep(.001)
    try:
        send('hello', 0, dict(supported_versions=[1], capabilities=[], resume_session_id=None))
        until(lambda: any(row['type'] == 'hello_ack' for row in received))
        send('command', 1, dict(command='run.start', arguments={}))
        until(lambda: any(row['type'] == 'error' for row in received))
        command.assert_not_called()
        assert 'shutdown' in next(row['payload']['message'] for row in received if row['type'] == 'error').lower()
        send('command', 2, dict(command='run.stop', arguments={}))
        send('command', 3, dict(command='session.sync', arguments={}))
        until(lambda: command.call_count == 2)
        assert [call.args[0]['command'] for call in command.call_args_list] == ['run.stop', 'session.sync']
    finally:
        client.close()
        server.close()
