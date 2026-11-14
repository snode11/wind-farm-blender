"""Replaceable observations cannot consume reliable delivery capacity."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge import server as server_module
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.messages import FrameDecoder
from wfrl.blender_bridge.server import BridgeServer
from wfrl.blender_bridge.training_progress import interactive_progress
from test_disconnect_progress import scene, snapshot


@pytest.fixture
def controlled_server(monkeypatch):
    server = BridgeServer(port=0, session=BackendSession(scene()))
    clock = SimpleNamespace(now=10.)
    wire = bytearray()
    server.client = Mock(send=lambda data: (wire.extend(data), len(data))[1])
    server.negotiated_capabilities = frozenset({'training_stats_v1'})
    monkeypatch.setattr(server_module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(server_module, 'select', SimpleNamespace(select=lambda *args: ([], [server.client], [])))
    try:
        yield server, clock, wire
    finally:
        server.close()


def test_reliable_event_defers_unsent_training_statistics(controlled_server):
    server, _, wire = controlled_server
    payload = interactive_progress(snapshot(1), 'run', 2)
    server._send('training_stats', payload)
    server.max_queue_bytes = server.pending_bytes
    server._send('error', dict(code='RELIABLE', message='must arrive first', fatal=False))
    assert [item['kind'] for item in server.pending] == ['error']
    assert ('training_stats', 'iteration_stats') in server._deferred
    server._flush()
    server._queue_session_events()
    server._flush()
    messages = FrameDecoder().feed(wire)
    assert [m['type'] for m in messages] == ['error', 'training_stats']
    assert messages[1]['payload']['stats'] == payload['stats']
    assert server.pending_bytes == 0


def test_age_growth_defers_observation_without_disconnecting(controlled_server):
    server, clock, wire = controlled_server
    server._send('training_stats', interactive_progress(snapshot(2), 'run', 2))
    server.max_queue_bytes = server.pending_bytes
    clock.now = 10.125
    server._flush()
    assert not wire and not server.pending
    assert server.pending_bytes == 0
    assert ('training_stats', 'progress') in server._deferred
    server._send('error', dict(code='RELIABLE', message='still connected', fatal=False))
    server._flush()
    assert FrameDecoder().feed(wire)[0]['payload']['code'] == 'RELIABLE'
    server.client.close.assert_not_called()


def test_server_close_releases_listener_when_session_stop_raises():
    session = BackendSession()
    server = BridgeServer(port=0, session=session)
    session.stop = Mock(side_effect=ValueError('stop failed'))
    try:
        with pytest.raises(ValueError, match='stop failed'):
            server.close()
        assert server.listener.fileno() == -1
        server.close()
        session.stop.assert_called_once_with()
    finally:
        server.listener.close()


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), -1.])
def test_invalid_handshake_timeout_rejected_before_listening(timeout):
    server = None
    try:
        with pytest.raises(ValueError, match='handshake_timeout'):
            server = BridgeServer(port=0, handshake_timeout=timeout)
    finally:
        if server is not None:
            server.close()


def test_oversized_observation_is_skipped_with_one_nonfatal_error(controlled_server, monkeypatch):
    from wfrl.blender_bridge import messages
    server, _, wire = controlled_server
    payload = interactive_progress(snapshot(3), 'run', 2)
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1500)
    before = server.sequence
    server._send('training_stats', payload)
    server._send('training_stats', payload)          # same run: reported only once
    assert [item['kind'] for item in server.pending] == ['error']
    assert server.sequence == before + 1             # skipped frames consume no sequence
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1024 * 1024)
    server._flush()
    error = FrameDecoder().feed(wire)[0]['payload']
    assert error['code'] == 'FRAME_TOO_LARGE' and error['fatal'] is False
    assert not server.close_after_flush
    server.client.close.assert_not_called()
    server._send('training_stats', payload)          # within the limit it is delivered again
    assert [item['kind'] for item in server.pending] == ['training_stats']
