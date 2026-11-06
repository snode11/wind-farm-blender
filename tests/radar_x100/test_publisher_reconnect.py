"""Real localhost recovery preserves display age and the separate durable queue."""
from dataclasses import replace
import select
import socket
import threading
import time
import numpy as np
import pytest
from wfrl.radar_x100 import acquisition, channel, recording, schema


def frame(sequence, *, empty=False, session='boot'):
    return schema.RadarFrame('X100', session, sequence, 'TEST', 'NEAR',
                             np.zeros(0 if empty else 1, dtype=schema.POINT_DTYPE))


def poll_until(client, predicate, timeout=2.):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        client.poll(time_budget_s=1.)
        if predicate():
            return
        if client.error:
            pytest.fail(client.error)
        remaining = deadline - time.monotonic()
        if client.closed or remaining <= 0 or not select.select([client.socket], [], [], remaining)[0]:
            break
    pytest.fail('Local display did not reach the expected state')


def delivery_signal(fanout):
    delivered = threading.Event()
    finish = fanout.finish_delivery

    def signal(delivery):
        finish(delivery)
        delivered.set()

    fanout.finish_delivery = signal
    return delivered


@pytest.mark.parametrize('empty', [False, True])
def test_sparse_explicit_reconnect_needs_only_one_new_frame(tmp_path, empty):
    sink = recording.Recorder(tmp_path / 'record')
    fanout = acquisition.Fanout(sink)
    publisher = acquisition.LocalPublisher(fanout, port=0)
    a, b = channel.LocalClient(publisher.port), None
    try:
        fanout.submit(frame(0), 0.)
        poll_until(a, lambda: a.state.streams.get('NEAR') is not None)
        a.close()
        b = channel.LocalClient(publisher.port)
        fanout.submit(frame(1, empty=empty), .1)
        poll_until(b, lambda: b.state.streams.get('NEAR') is not None)
        assert b.state.streams['NEAR'].frame.sequence == 1
        assert b.state.status('NEAR') == ('VALID_EMPTY' if empty else 'VALID')
    finally:
        a.close()
        if b:
            b.close()
        publisher.close()
        fanout.close()
    replay = recording.Replay(sink.path)
    assert [replay.frame(i).sequence for i in range(len(replay.rows))] == [0, 1]
    assert not publisher.worker.is_alive()


def test_pending_connection_takes_over_without_waiting_for_old_sender_error():
    fanout = acquisition.Fanout()
    publisher = acquisition.LocalPublisher(fanout, port=0)
    a, b = channel.LocalClient(publisher.port), None
    try:
        fanout.submit(frame(0), 0.)
        poll_until(a, lambda: a.state.peer is not None)
        b = channel.LocalClient(publisher.port)
        poll_until(a, lambda: a.closed)
        fanout.submit(frame(1), .1)
        poll_until(b, lambda: b.state.streams.get('NEAR') is not None)
        assert b.state.streams['NEAR'].frame.sequence == 1
        poll_until(a, lambda: a.closed)
    finally:
        a.close()
        if b:
            b.close()
        publisher.close()
        fanout.close()


def test_failed_send_retries_single_frame_with_original_age(monkeypatch):
    failed = threading.Event()
    encode = channel.encode_frame
    def fail_once(frame, **kwargs):
        if not failed.is_set():
            failed.set()
            raise OSError('injected socket write failure')
        return encode(frame, **kwargs)
    monkeypatch.setattr(channel, 'encode_frame', fail_once)
    fanout = acquisition.Fanout()
    delivered = delivery_signal(fanout)
    publisher = acquisition.LocalPublisher(fanout, port=0)
    a, b = channel.LocalClient(publisher.port), None
    stamp = time.monotonic() - 10.
    source = frame(1)
    try:
        fanout.submit_display(source, received_monotonic=stamp)
        assert failed.wait(2)
        poll_until(a, lambda: a.closed)
        b = channel.LocalClient(publisher.port)
        poll_until(b, lambda: b.state.streams.get('NEAR') is not None)
        stream = b.state.streams['NEAR']
        assert stream.received_monotonic == stamp
        assert stream.frame.metadata() == source.metadata()
        assert b.state.status('NEAR') == 'STALE'
        assert delivered.wait(2)
        assert publisher.sent == 1
    finally:
        a.close()
        if b:
            b.close()
        publisher.close()
        fanout.close()


@pytest.mark.parametrize('new', [frame(2), frame(0, session='new-session')])
def test_retry_never_overwrites_a_newer_pending_observation(new):
    fanout = acquisition.Fanout()
    try:
        old = frame(1)
        fanout.submit_display(old, received_monotonic=1.)
        held = fanout.take_latest_with_time()
        if new.session_id != old.session_id:
            fanout.begin_session(new.sensor_id, new.session_id, new.source_kind)
        fanout.submit_display(new, received_monotonic=2.)
        fanout.submit_display(held[0], received_monotonic=held[1], restoring=True)
        actual, stamp = fanout.take_latest_with_time()
        assert actual.identity == new.identity and stamp == 2.
        assert not fanout.latest_received
    finally:
        fanout.close()


def test_session_change_requires_an_explicit_new_connection():
    fanout = acquisition.Fanout()
    publisher = acquisition.LocalPublisher(fanout, port=0)
    a, b = channel.LocalClient(publisher.port), None
    try:
        fanout.submit(frame(0), 0.)
        poll_until(a, lambda: a.state.streams.get('NEAR') is not None)
        fanout.begin_session('X100', 'new-session', 'TEST')
        fanout.submit(frame(0, session='new-session'), .1)
        poll_until(a, lambda: a.closed)
        b = channel.LocalClient(publisher.port)
        poll_until(b, lambda: b.state.streams.get('NEAR') is not None)
        assert b.state.peer['session_id'] == 'new-session'
        assert b.state.streams['NEAR'].frame.sequence == 0
    finally:
        a.close()
        if b:
            b.close()
        publisher.close()
        fanout.close()


def test_retry_does_not_evict_a_new_observation_when_display_budget_is_full():
    fanout = acquisition.Fanout()
    try:
        old = frame(1)
        for index in range(8):
            fanout.submit_display(replace(frame(index), sensor_id=str(index)), received_monotonic=2.)
        fanout.submit_display(old, received_monotonic=1., restoring=True)
        assert len(fanout.latest) == len(fanout.latest_received) == 8
        actual = [fanout.take_latest_with_time() for _ in range(8)]
        assert {entry[0].sensor_id for entry in actual} == {str(index) for index in range(8)}
        assert all(stamp == 2. for _, stamp in actual)
    finally:
        fanout.close()


def test_continuous_input_and_reconnect_leave_complete_recording_independent(tmp_path):
    sink = recording.Recorder(tmp_path/'continuous')
    fanout = acquisition.Fanout(sink)
    publisher = acquisition.LocalPublisher(fanout, port=0)
    a, b = channel.LocalClient(publisher.port), None
    try:
        fanout.submit(frame(0), 0.)
        poll_until(a, lambda: a.state.streams.get('NEAR') is not None)
        a.close()
        b = channel.LocalClient(publisher.port)
        for sequence in range(1, 6):
            fanout.submit(frame(sequence), sequence*.05)
            poll_until(b, lambda: b.state.streams.get('NEAR') is not None
                       and b.state.streams['NEAR'].frame.sequence == sequence)
        poll_until(b, lambda: b.state.streams.get('NEAR') is not None and b.state.streams['NEAR'].frame.sequence == 5)
        assert b.state.status('NEAR') == 'VALID'
    finally:
        a.close()
        if b:
            b.close()
        publisher.close()
        fanout.close()
    replay = recording.Replay(sink.path)
    assert [replay.frame(i).sequence for i in range(len(replay.rows))] == list(range(6))


class SocketProxy:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self.wrapped, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.wrapped.close()


def wrap_publisher_accept(monkeypatch, wrap_client):
    socket_factory = socket.socket
    servers = []

    class Server(SocketProxy):
        def accept(self):
            client, address = self.wrapped.accept()
            return wrap_client(client), address

    def factory(*args, **kwargs):
        created = socket_factory(*args, **kwargs)
        if not servers:
            server = Server(created)
            servers.append(server)
            return server
        return created

    monkeypatch.setattr(socket, 'socket', factory)


def test_partial_send_disconnect_retries_complete_frame_with_original_age(monkeypatch):
    failed = threading.Event()

    class PartialSender(SocketProxy):
        calls = 0

        def sendall(self, raw):
            self.calls += 1
            if self.calls == 2 and not failed.is_set():
                self.wrapped.sendall(raw[:len(raw) // 2])
                failed.set()
                raise OSError('injected partial send')
            self.wrapped.sendall(raw)

    wrap_publisher_accept(monkeypatch, PartialSender)
    fanout = acquisition.Fanout()
    delivered = delivery_signal(fanout)
    publisher = acquisition.LocalPublisher(fanout, port=0)
    first, second = channel.LocalClient(publisher.port), None
    stamp = time.monotonic() - 10.
    try:
        fanout.submit_display(frame(1), received_monotonic=stamp)
        assert failed.wait(2)
        poll_until(first, lambda: first.closed)
        assert 'Truncated radar packet' in first.error
        assert not first.state.streams
        second = channel.LocalClient(publisher.port)
        poll_until(second, lambda: second.state.streams.get('NEAR') is not None)
        received = second.state.streams['NEAR']
        assert received.frame.sequence == 1 and received.received_monotonic == stamp
        assert second.state.status('NEAR') == 'STALE'
        assert delivered.wait(2)
        assert publisher.sent == 1 and publisher.errors == 1
    finally:
        first.close()
        if second:
            second.close()
        publisher.close()
        fanout.close()


def test_publisher_close_interrupts_send_and_joins_worker(monkeypatch):
    entered, shutdown, release = threading.Event(), threading.Event(), threading.Event()
    clients = []

    class BlockedSender(SocketProxy):
        def sendall(self, raw):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('blocked test send')
            raise OSError('send interrupted')

        def shutdown(self, how):
            try:
                self.wrapped.shutdown(how)
            finally:
                shutdown.set()
                release.set()

    def wrap(client):
        wrapped = BlockedSender(client)
        clients.append(wrapped)
        return wrapped

    wrap_publisher_accept(monkeypatch, wrap)
    fanout = acquisition.Fanout()
    publisher = acquisition.LocalPublisher(fanout, port=0)
    client = channel.LocalClient(publisher.port)
    try:
        fanout.submit(frame(1), 0.)
        assert entered.wait(2)
        publisher.close()
        assert shutdown.is_set() and not publisher.worker.is_alive()
        assert publisher.server.fileno() == clients[0].fileno() == -1
        assert fanout.take_latest().sequence == 1
        publisher.close()
    finally:
        release.set()
        client.close()
        publisher.close()
        fanout.close()


def test_publisher_bind_failure_closes_owned_socket(monkeypatch):
    with socket.socket() as occupied:
        occupied.settimeout(2)
        occupied.bind(('127.0.0.1', 0))
        occupied.listen(1)
        socket_factory = socket.socket
        created = []

        def factory(*args, **kwargs):
            created.append(socket_factory(*args, **kwargs))
            return created[-1]

        monkeypatch.setattr(socket, 'socket', factory)
        fanout = acquisition.Fanout()
        try:
            with pytest.raises(OSError):
                acquisition.LocalPublisher(fanout, port=occupied.getsockname()[1])
            assert len(created) == 1 and created[0].fileno() == -1
        finally:
            for owned in created:
                owned.close()
            fanout.close()


def test_publisher_close_joins_idle_accept_worker():
    fanout = acquisition.Fanout()
    publisher = acquisition.LocalPublisher(fanout, port=0)
    try:
        publisher.close()
        assert not publisher.worker.is_alive() and publisher.server.fileno() == -1
    finally:
        publisher.close()
        fanout.close()


def test_client_connect_setup_failure_closes_owned_socket(monkeypatch):
    created = []
    socket_factory = socket.socket

    def factory(*args, **kwargs):
        created.append(socket_factory(*args, **kwargs))
        return created[-1]

    monkeypatch.setattr(socket, 'socket', factory)
    try:
        with pytest.raises(OverflowError):
            channel.LocalClient(65536)
        assert len(created) == 1 and created[0].fileno() == -1
    finally:
        for owned in created:
            owned.close()
