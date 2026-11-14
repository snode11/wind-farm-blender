"""Delivery, retirement and source ages across bridge queue boundaries."""
from types import SimpleNamespace
from unittest.mock import Mock
import socket

import pytest

from wfrl.blender_bridge import backend_session, messages, server as server_module
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.messages import FrameDecoder
from wfrl.blender_bridge.server import BridgeServer
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.training_progress import interactive_progress
from test_disconnect_progress import scene, snapshot
from test_reconnect import bridge, command, connected_owner, hello, receive


@pytest.fixture
def delivery(monkeypatch):
    clock = SimpleNamespace(now=10.)
    timer = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(server_module, 'time', timer)
    monkeypatch.setattr(backend_session, 'time', timer)
    server = BridgeServer(port=0, session=BackendSession(scene()))
    wire = bytearray()
    socket = Mock()
    socket.recv.side_effect = BlockingIOError
    socket.send.side_effect = lambda data: (wire.extend(data), len(data))[1]
    server.client = socket
    server.ready = True
    server._incoming = []
    server.negotiated_capabilities = frozenset({'training_stats_v1'})
    monkeypatch.setattr(server_module, 'select', SimpleNamespace(select=lambda *args: ([], [server.client], [])))
    try:
        yield server, clock, wire, socket
    finally:
        server.close()


def assert_pending_bytes(server):
    assert server.pending_bytes == sum(len(item['frame']) - item['offset'] for item in server.pending)


def drain(server):
    for _ in range(30):
        server._queue_session_events()
        server._flush()
        assert_pending_bytes(server)
        if (not server.pending and not server.backlog and not server._deferred
                and not len(server.session._events) and server.session._latest is None):
            return
    pytest.fail('Send queues did not drain')


def test_long_command_rejection_is_bounded_and_session_resumes(bridge, monkeypatch):
    server, listener, _ = bridge
    _, peer = connected_owner(server, listener)
    original = server.session.command
    reason = 'Invalid option: ' + '\u62d2\U0001f6ab\n"' * messages.MAX_FRAME_BYTES
    monkeypatch.setattr(server.session, 'command', Mock(side_effect=ValueError(reason)))
    peer.sendall(command(server.session.session_id, 7))
    server.poll()
    assert server.client is not None
    rejected = receive(peer)
    assert rejected['payload']['code'] == 'COMMAND_REJECTED'
    assert rejected['payload']['command_sequence'] == 7
    assert rejected['payload']['message'].startswith('Invalid option: ')
    assert len(rejected['payload']['message']) <= 4096
    assert not server.backlog
    peer.shutdown(socket.SHUT_WR)
    server.poll()
    _, resumed = listener.connect()
    resumed.sendall(hello(server.session.session_id))
    server.poll()
    assert receive(resumed)['payload']['last_command_sequence'] == 7
    monkeypatch.setattr(server.session, 'command', original)
    resumed.sendall(command(server.session.session_id, 8))
    server.poll()
    assert receive(resumed)['type'] == 'lifecycle'


@pytest.mark.parametrize('limit', ['frame', 'queue'])
def test_unsendable_reliable_event_cannot_block_later_delivery(delivery, limit):
    server, _, wire, _ = delivery
    padding = 'x' * (messages.MAX_FRAME_BYTES + 1 if limit == 'frame' else 2048)
    if limit == 'queue':
        server.max_queue_bytes = 1024
    server.session.emit('error', dict(code='UNSENDABLE', message='bad record', fatal=False, detail=padding))
    server.session.emit('error', dict(code='FOLLOWING', message='deliverable', fatal=False))
    server.poll()
    assert not any(event[1].get('code') == 'UNSENDABLE' for event in server.backlog)
    assert server.client is not None
    drain(server)
    received = FrameDecoder().feed(wire)
    assert any(item['payload'].get('code') == 'FOLLOWING' for item in received)
    assert not server.backlog


def test_fatal_flush_freezes_observations_but_keeps_tick_and_reliable_events(delivery, monkeypatch):
    server, _, wire, socket = delivery
    tick = Mock(wraps=server.session.tick)
    monkeypatch.setattr(server.session, 'tick', tick)
    server.session.emit('error', dict(code='FATAL', message='close this connection', fatal=True))
    server.session.emit('training_stats', interactive_progress(snapshot(1), 'run', 2))
    server.session.lifecycle('READY', 'retain after fatal')
    socket.send.side_effect = lambda data: (wire.extend(data[:7]), min(7, len(data)))[1]
    server.poll()
    assert server.close_after_flush
    assert [item['kind'] for item in server.pending] == ['error']
    socket.send.side_effect = lambda data: (wire.extend(data), len(data))[1]
    safety = next(SnapshotAdapter(scene(), server.session.session_id).safety_events(snapshot(2, safety=True)))
    server.session.emit('safety_event', safety)
    before = tick.call_count
    for step in range(2, 6):
        server.session.emit('snapshot', SnapshotAdapter(scene(), server.session.session_id).encode(snapshot(step)))
        server.session.emit('training_stats', interactive_progress(snapshot(step), 'run', 2))
        server.poll()
        assert_pending_bytes(server)
        if server.client is None:
            break
    assert server.client is None
    assert tick.call_count > before
    assert [item['type'] for item in FrameDecoder().feed(wire)] == ['error']
    wire.clear()
    server.client = socket
    server.ready = True
    drain(server)
    received = FrameDecoder().feed(wire)
    assert any(item['type'] == 'lifecycle' for item in received)
    assert any(item['type'] == 'safety_event' and item['payload']['event_id'] == safety['event_id'] for item in received)


def test_oversize_notification_survives_backpressure_and_is_delivered_once(delivery, monkeypatch):
    server, _, wire, socket = delivery
    server.max_queue_bytes = 500
    server._send('error', dict(code='FIRST', message='x' * 100, fatal=False))
    socket.send.side_effect = lambda data: (wire.extend(data[:7]), min(7, len(data)))[1]
    server._flush()
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1500)
    payload = interactive_progress(snapshot(1), 'run', 2)
    server._send('training_stats', payload)
    server._send('training_stats', payload)
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1024 * 1024)
    socket.send.side_effect = lambda data: (wire.extend(data), len(data))[1]
    drain(server)
    received = FrameDecoder().feed(wire)
    assert [item['payload']['code'] for item in received] == ['FIRST', 'FRAME_TOO_LARGE']


def test_oversize_notification_retries_when_it_cannot_yet_be_retained(delivery, monkeypatch):
    server, _, wire, _ = delivery
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1500)
    payload = interactive_progress(snapshot(1), 'run', 2)
    server.max_queue_bytes = 200
    server._send('training_stats', payload)
    assert not server.pending and not server.backlog
    server.max_queue_bytes = 500
    server._send('training_stats', payload)
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1024 * 1024)
    drain(server)
    assert [item['payload']['code'] for item in FrameDecoder().feed(wire)] == ['FRAME_TOO_LARGE']


@pytest.mark.parametrize('replace', [False, True])
def test_dropped_deferred_observation_removes_only_its_own_version(delivery, monkeypatch, replace):
    server, clock, _, _ = delivery
    server.max_queue_bytes = 1000
    oversized = dict(interactive_progress(snapshot(2), 'run', 2), padding='x' * 4096)
    server._send('training_stats', oversized)
    key = ('training_stats', 'progress')
    assert key in server._deferred
    newer = interactive_progress(snapshot(4), 'run', 2), clock.now + 1.
    report = server._report_oversized
    def report_and_replace(*args):
        report(*args)
        if replace:
            server._deferred[key] = newer
    monkeypatch.setattr(server, '_report_oversized', report_and_replace)
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1500)
    server.max_queue_bytes = 2 * 1024 * 1024
    server._queue_session_events()
    if replace:
        assert server._deferred[key] is newer
    else:
        assert key not in server._deferred


def test_safety_age_counts_session_send_and_offline_wait_without_mutating_partial_frame(delivery):
    server, clock, wire, socket = delivery
    event = next(SnapshotAdapter(scene(), server.session.session_id).safety_events(snapshot(1, safety=True)))
    event['data']['source_age_seconds'] = 7.
    server.session.emit('safety_event', event)
    clock.now = 15.
    server._queue_session_events()
    clock.now = 17.
    socket.send.side_effect = BlockingIOError
    with pytest.raises(BlockingIOError):
        server._flush()
    clock.now = 19.
    socket.send.side_effect = lambda data: (wire.extend(data[:7]), min(7, len(data)))[1]
    server._flush()
    partial = server.pending[0]['frame']
    assert FrameDecoder().feed(partial)[0]['payload']['data']['source_age_seconds'] == 16.
    clock.now = 23.
    server._flush()
    assert server.pending[0]['frame'] == partial
    assert_pending_bytes(server)
    clock.now = 25.
    server._disconnect()
    wire.clear()
    clock.now = 40.
    server.client = socket
    server._queue_session_events()
    clock.now = 45.
    socket.send.side_effect = lambda data: (wire.extend(data), len(data))[1]
    drain(server)
    received = FrameDecoder().feed(wire)
    assert len(received) == 1 and received[0]['type'] == 'safety_event'
    assert received[0]['payload']['data']['source_age_seconds'] == 42.
    assert event['data']['source_age_seconds'] == 7.
    assert received[0]['sequence'] > FrameDecoder().feed(partial)[0]['sequence']


def test_safety_backlog_age_survives_resume_backpressure(delivery):
    server, clock, wire, socket = delivery
    server.max_queue_bytes = 1000
    server.session.emit('error', dict(code='FIRST', message='x' * 400, fatal=False))
    event = next(SnapshotAdapter(scene(), server.session.session_id).safety_events(snapshot(1, safety=True)))
    event['data']['source_age_seconds'] = 7.
    server.session.emit('safety_event', event)
    clock.now = 15.
    server.poll()
    assert server.client is None and not wire
    clock.now = 25.
    server.client = socket
    server._queue_session_events()
    assert [item['kind'] for item in server.pending] == ['error']
    clock.now = 30.
    server._queue_session_events()
    server._flush()
    clock.now = 40.
    server._queue_session_events()
    clock.now = 45.
    drain(server)
    received = FrameDecoder().feed(wire)
    assert [item['type'] for item in received] == ['error', 'safety_event']
    assert received[-1]['payload']['data']['source_age_seconds'] == 42.
    assert event['data']['source_age_seconds'] == 7.


@pytest.mark.parametrize('limit', ['frame', 'queue'])
def test_safety_age_growth_beyond_permanent_limit_does_not_enter_backlog(delivery, monkeypatch, limit):
    server, clock, wire, _ = delivery
    event = next(SnapshotAdapter(scene(), server.session.session_id).safety_events(snapshot(1, safety=True)))
    server._send('safety_event', event)
    frame_size = server.pending_bytes
    if limit == 'frame':
        monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', frame_size - 4)
    else:
        server.max_queue_bytes = frame_size
    clock.now += .125
    server._flush()
    assert not server.backlog
    assert all(item['kind'] != 'safety_event' for item in server.pending)
    monkeypatch.setattr(messages, 'MAX_FRAME_BYTES', 1024 * 1024)
    drain(server)
    assert [item['payload']['code'] for item in FrameDecoder().feed(wire)] == ['FRAME_TOO_LARGE']


@pytest.mark.parametrize('reason', ['', ' \n\t', '\ud800invalid UTF-8'])
def test_command_diagnostics_remain_valid_text(delivery, reason):
    server, _, wire, _ = delivery
    server._send('error', dict(code='COMMAND_REJECTED', message=reason, fatal=False, command_sequence=1))
    drain(server)
    received = FrameDecoder().feed(wire)
    assert received[0]['payload']['message'].strip()
    assert received[0]['payload']['command_sequence'] == 1
