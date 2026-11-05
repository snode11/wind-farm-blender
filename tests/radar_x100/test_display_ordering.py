"""Display continuity survives slot consumption while recordings keep arrival order."""
import time
import numpy as np
from wfrl.radar_x100 import acquisition, channel, recording, schema


def frame(sequence, sensor='X100', session='boot'):
    return schema.RadarFrame(sensor, session, sequence, 'TEST', 'NEAR',
                             np.zeros(1, dtype=schema.POINT_DTYPE))


def poll_until(client, predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        client.poll()
        assert client.error is None
        assert time.monotonic() < deadline
        time.sleep(.005)


def test_consumed_watermark_survives_reconnect_and_preserves_recording(tmp_path):
    sink = recording.Recorder(tmp_path/'arrival-order')
    fanout = acquisition.Fanout(sink)
    publisher = acquisition.LocalPublisher(fanout, port=0)
    first, second = channel.LocalClient(publisher.port), None
    try:
        fanout.submit(frame(1), 0.)
        poll_until(first, lambda: first.state.streams.get('NEAR') is not None)
        first.close()
        second = channel.LocalClient(publisher.port)
        fanout.submit(frame(0), .1)
        for _ in range(20):
            second.poll()
            time.sleep(.005)
        assert second.state.streams.get('NEAR') is None
        fanout.submit(frame(2), .2)
        poll_until(second, lambda: second.state.streams.get('NEAR') is not None)
        assert second.state.streams['NEAR'].frame.sequence == 2
    finally:
        first.close()
        if second:
            second.close()
        publisher.close()
        fanout.close()
    replay = recording.Replay(sink.path)
    assert sink.state == 'COMPLETE'
    assert [replay.frame(i).sequence for i in range(3)] == [1, 0, 2]


def test_delivery_tokens_are_single_use_and_preserve_receipt_time():
    fanout = acquisition.Fanout()
    try:
        source = frame(1)
        assert fanout.submit_display(source, received_monotonic=3.)
        first = fanout.begin_delivery()
        assert not fanout.submit_display(source, received_monotonic=9.)
        assert fanout.submit_display(first[0], restoring=True, restore_token=first[2], received_monotonic=99.)
        second = fanout.begin_delivery()
        assert second[1] == 3.
        assert not fanout.submit_display(first[0], restoring=True, restore_token=first[2])
        assert fanout.submit_display(second[0], restoring=True, restore_token=second[2])
        third = fanout.begin_delivery()
        fanout.finish_delivery(third)
        assert not fanout.submit_display(third[0], restoring=True, restore_token=third[2])
        assert fanout.take_latest() is None
    finally:
        fanout.close()


def test_retired_session_and_inflight_delivery_cannot_reenter():
    fanout = acquisition.Fanout()
    try:
        fanout.submit_display(frame(9))
        held = fanout.begin_delivery()
        assert not fanout.submit_display(frame(0, session='new'))
        assert fanout.begin_session('X100', 'new', 'TEST')
        assert fanout.submit_display(frame(0, session='new'))
        assert not fanout.begin_session('X100', 'boot', 'TEST')
        assert not fanout.submit_display(held[0], restoring=True, restore_token=held[2])
        assert not fanout.submit_display(frame(10))
        assert fanout.take_latest().session_id == 'new'
        assert fanout.begin_session('X100', 'new', 'TEST')
        assert not fanout.submit_display(frame(0, session='new'))
    finally:
        fanout.close()


def test_display_admission_limit_does_not_drop_recording(tmp_path):
    sink = recording.Recorder(tmp_path/'bounded')
    fanout = acquisition.Fanout(sink, display_capacity=2)
    try:
        for index in range(3):
            fanout.submit(frame(1, sensor=str(index)), index*.1)
            if index < 2:
                assert fanout.take_latest() is not None
        assert fanout.take_latest() is None
        assert len(fanout.watermarks) == len(fanout.sources) == 2
        assert not fanout.submit_display(frame(0, sensor='0'))
    finally:
        fanout.close()
    assert sink.state == 'COMPLETE'
    replay = recording.Replay(sink.path)
    assert [replay.frame(i).sensor_id for i in range(3)] == ['0', '1', '2']


def test_payload_eviction_keeps_watermark_and_close_invalidates_delivery():
    fanout = acquisition.Fanout()
    for index in range(9):
        fanout.submit_display(frame(4, sensor=str(index)))
    assert len(fanout.latest) == 8 and len(fanout.watermarks) == 9
    assert not fanout.submit_display(frame(3, sensor='0'))
    held = fanout.begin_delivery()
    fanout.close()
    assert not fanout.submit_display(held[0], restoring=True, restore_token=held[2])
