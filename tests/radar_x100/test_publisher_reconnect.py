"""Real localhost recovery preserves display age and the separate durable queue."""
from dataclasses import replace
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
        client.poll()
        if predicate():
            return
        if client.error:
            pytest.fail(client.error)
        time.sleep(.005)
    pytest.fail('Local display did not reach the expected state')


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
        time.sleep(.1)
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
        time.sleep(.03)
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
            time.sleep(.05)
            b.poll()
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
