"""Single-owner handover preserves command watermarks and bounds waiting sockets."""
from collections import deque
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge import server as server_module
from wfrl.blender_bridge.messages import FrameDecoder, encode_message
from wfrl.blender_bridge.server import BridgeServer


class QueuedListener:
    """Make socketpair endpoints available to accept in a controlled order."""

    def __init__(self):
        self.pending = deque()
        self.pairs = []

    def connect(self):
        connection, peer = socket.socketpair()
        peer.setblocking(False)
        self.pending.append(connection)
        self.pairs.append((connection, peer))
        return connection, peer

    def accept(self):
        if not self.pending:
            raise BlockingIOError()
        return self.pending.popleft(), ('127.0.0.1', 0)

    def close(self):
        while self.pending:
            self.pending.popleft().close()


@pytest.fixture
def bridge(monkeypatch):
    clock = SimpleNamespace(now=100.)
    monkeypatch.setattr(server_module, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    server = BridgeServer(port=0, handshake_timeout=5.)
    server.listener.close()
    listener = server.listener = QueuedListener()
    try:
        yield server, listener, clock
    finally:
        server.close()
        for connection, peer in listener.pairs:
            connection.close()
            peer.close()


def hello(session_id=None):
    return encode_message(dict(
        protocol_version=1, type='hello', session_id='', sequence=0,
        payload=dict(supported_versions=[1], capabilities=[], resume_session_id=session_id)))


def command(session_id, sequence, name='session.sync'):
    return encode_message(dict(
        protocol_version=1, type='command', session_id=session_id, sequence=sequence,
        payload=dict(command=name, arguments={})))


def receive(peer):
    data = peer.recv(65536)
    assert data, 'Connection closed before the expected frame'
    messages = FrameDecoder().feed(data)
    assert len(messages) == 1
    return messages[0]


def connected_owner(server, listener):
    connection, peer = listener.connect()
    peer.sendall(hello())
    server.poll()
    assert receive(peer)['type'] == 'hello_ack'
    return connection, peer


@pytest.mark.parametrize('name,event', [('session.sync', 'lifecycle'), ('channel.set', 'error')])
def test_reconnect_drains_unread_command_before_resumed_handshake(bridge, monkeypatch, name, event):
    server, listener, _ = bridge
    old, peer = connected_owner(server, listener)
    session_id = server.session.session_id
    guard = server.guard
    dispatch = Mock(wraps=server.session.command)
    monkeypatch.setattr(server.session, 'command', dispatch)

    frame = command(session_id, 7, name)
    peer.sendall(frame)
    peer.shutdown(socket.SHUT_WR)
    replacement, resumed = listener.connect()
    resumed.sendall(hello(session_id))
    # Data precedes EOF, and the replacement is already available to accept.
    assert old.recv(len(frame), socket.MSG_PEEK) == frame
    server.poll()
    assert replacement.fileno() != -1, 'Reconnect was closed before the old command and EOF were drained'
    server.poll()
    ack = receive(resumed)
    assert (ack['type'], ack['session_id'], ack['payload']['resumed']) == ('hello_ack', session_id, True)
    assert ack['payload']['last_command_sequence'] == 7
    assert server.guard is guard
    dispatch.assert_called_once_with(dict(command=name, arguments={}))

    server.poll()
    retained = receive(resumed)
    assert retained['type'] == event
    if event == 'error':
        assert retained['payload']['code'] == 'COMMAND_REJECTED'
        assert retained['payload']['command_sequence'] == 7

    resumed.sendall(command(session_id, 8))
    server.poll()
    assert receive(resumed)['type'] == 'lifecycle'
    assert server.guard.last_sequence == 8 and dispatch.call_count == 2
    resumed.sendall(command(session_id, 8))
    server.poll()
    assert server.client is None and replacement.fileno() == -1
    assert server.guard.last_sequence == 8 and dispatch.call_count == 2


def test_waiting_connection_expires_without_interrupting_live_owner(bridge):
    server, listener, clock = bridge
    old, peer = connected_owner(server, listener)
    session_id = server.session.session_id
    replacement, resumed = listener.connect()
    resumed.sendall(hello(session_id) + command(session_id, 99))
    server.poll()
    assert replacement.fileno() != -1
    assert server.client is old and server.guard.last_sequence == -1

    clock.now += server.handshake_timeout - .1
    peer.sendall(command(session_id, 1))
    server.poll()
    assert receive(peer)['type'] == 'lifecycle'
    assert replacement.fileno() != -1
    assert server.client is old and server.guard.last_sequence == 1

    clock.now += .1
    server.poll()
    assert replacement.fileno() == -1
    assert server.client is old and server.guard.last_sequence == 1


def test_reconnect_waits_across_bounded_reads_before_reporting_watermark(bridge):
    server, listener, _ = bridge
    old, peer = connected_owner(server, listener)
    session_id = server.session.session_id
    frames = [command(session_id, sequence) for sequence in range(1, 7)]

    class OneFramePerRead:
        def recv(self, size, flags=0):
            return old.recv(min(size, len(frames[0])), flags)

        def __getattr__(self, name):
            return getattr(old, name)

    owner = server.client = OneFramePerRead()
    peer.sendall(b''.join(frames))
    peer.shutdown(socket.SHUT_WR)
    replacement, resumed = listener.connect()
    resumed.sendall(hello(session_id))
    server.poll()
    assert server.client is owner and server.guard.last_sequence == 4
    assert replacement.fileno() != -1
    server.poll()
    assert server.client is None and server.guard.last_sequence == 6
    server.poll()
    ack = receive(resumed)
    assert ack['type'] == 'hello_ack' and ack['payload']['resumed']
    assert ack['payload']['last_command_sequence'] == 6


def test_socket_error_keeps_waiting_connection_available(bridge):
    server, listener, _ = bridge
    old, _ = connected_owner(server, listener)
    replacement, resumed = listener.connect()
    resumed.sendall(hello(server.session.session_id))
    failed_owner = Mock(wraps=old)
    failed_owner.recv.side_effect = ConnectionResetError('Peer reset the connection')
    server.client = failed_owner
    server.poll()
    assert old.fileno() == -1 and replacement.fileno() != -1
    server.poll()
    assert receive(resumed)['payload']['resumed']
    assert server.client is replacement


def test_waiting_time_counts_toward_handshake_timeout(bridge):
    server, listener, clock = bridge
    _, peer = connected_owner(server, listener)
    replacement, _ = listener.connect()
    server.poll()
    assert replacement.fileno() != -1
    clock.now += server.handshake_timeout - .1
    peer.shutdown(socket.SHUT_WR)
    server.poll()
    server.poll()
    assert server.client is replacement and not server.ready
    clock.now += .1
    server.poll()
    assert server.client is None and replacement.fileno() == -1


def test_close_releases_waiting_connection(bridge):
    server, listener, _ = bridge
    old, _ = connected_owner(server, listener)
    replacement, _ = listener.connect()
    server.poll()
    assert replacement.fileno() != -1
    server.close()
    assert old.fileno() == replacement.fileno() == -1


@pytest.mark.parametrize('resume,code', [('unrelated-session', 'UNKNOWN_SESSION'), (None, 'RESUME_REQUIRED')])
def test_waiting_connection_still_checks_resume_identity(bridge, monkeypatch, resume, code):
    server, listener, _ = bridge
    _, peer = connected_owner(server, listener)
    session_id, guard = server.session.session_id, server.guard
    monkeypatch.setattr(server.session, 'alive', lambda: True)
    monkeypatch.setattr(server.session, 'stop', lambda: None)
    replacement, resumed = listener.connect()
    resumed.sendall(hello(resume))
    server.poll()
    assert replacement.fileno() != -1
    peer.shutdown(socket.SHUT_WR)
    server.poll()
    server.poll()
    reply = receive(resumed)
    assert reply['type'] == 'error'
    assert reply['payload']['code'] == code and reply['payload']['fatal']
    assert reply['payload']['rejected_session_id'] == resume
    assert server.session.session_id == session_id and server.guard is guard
    assert replacement.fileno() == -1
