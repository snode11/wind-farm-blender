"""Corrupt units and concurrent recording boundaries."""
from dataclasses import replace
import threading
import numpy as np
import pytest
from wfrl.radar_x100 import acquisition, channel, recording, schema


def observation(sequence=0):
    points = np.zeros(1, dtype=schema.POINT_DTYPE)
    points['x'] = 10
    return schema.RadarFrame('X100', 'boot', sequence, 'TEST', 'NEAR', points,
                            provenance={'calibration': {'id': 'original'}})


def test_corrupt_template_cannot_become_complete():
    source = observation()
    corrupt = replace(source, points=source.points[:0], completeness='CORRUPT')
    assembler = acquisition.Assembler()
    assert assembler.push(acquisition.Fragment(corrupt, 0, 1, source.points, terminal=True)) is None
    assert assembler.counters['corrupt'] == 1


def test_close_rejects_submission_during_manifest_finalization(tmp_path):
    sink = recording.Recorder(tmp_path / 'closing')
    entered, release = threading.Event(), threading.Event()
    close = sink.close
    def held_close():
        entered.set()
        assert release.wait(3)
        close()
    sink.close = held_close
    fanout = acquisition.Fanout(sink)
    closer = threading.Thread(target=fanout.close)
    closer.start()
    try:
        assert entered.wait(3)
        with pytest.raises(RuntimeError, match='closed|closing'):
            fanout.submit(observation(), 0.)
    finally:
        release.set()
        closer.join(3)
        fanout.close()
    assert fanout.queue.empty()


def test_queued_frame_is_independent_of_producer_mutation(tmp_path):
    sink = recording.Recorder(tmp_path / 'snapshot')
    entered, release = threading.Event(), threading.Event()
    append = sink.append
    def held_append(frame, t):
        entered.set()
        assert release.wait(3)
        append(frame, t)
    sink.append = held_append
    fanout = acquisition.Fanout(sink)
    source = observation()
    fanout.submit(source, 0.)
    try:
        assert entered.wait(3)
        source.provenance['calibration']['id'] = 'changed'
        source.points.flags.writeable = True
        source.points['x'] = 99
    finally:
        release.set()
        fanout.close()
    stored = recording.Replay(sink.path).frame(0)
    assert stored.provenance['calibration']['id'] == 'original'
    assert stored.points['x'][0] == 10


def test_metadata_export_does_not_mutate_observation():
    source = observation()
    source.metadata()['provenance']['calibration']['id'] = 'changed'
    assert source.provenance['calibration']['id'] == 'original'


def test_frame_metadata_cannot_exceed_channel_budget():
    with pytest.raises(ValueError, match='metadata|budget'):
        replace(observation(), provenance={'data': 'x' * channel.MAX_METADATA})


def test_interleaved_frames_finish_once_without_losing_older_pending_frame(tmp_path):
    assembler = acquisition.Assembler()
    sink = recording.Recorder(tmp_path / 'interleaved')
    fanout = acquisition.Fanout(sink)
    outputs = []
    try:
        for sequence, index, now in ((0, 0, 0.), (1, 0, .01), (1, 1, .02), (0, 1, .03)):
            frame = observation(sequence)
            result = assembler.push(acquisition.Fragment(frame, index, 2, frame.points,
                                                         terminal=index == 1), now=now)
            if result is not None:
                outputs.append(result.sequence)
                fanout.submit(result, now)
        assert outputs == [1, 0]
        assert not assembler.pending
        for sequence in (0, 1):
            frame = observation(sequence)
            assert assembler.push(acquisition.Fragment(frame, 0, 2, frame.points), now=.04) is None
        assert assembler.counters == dict(duplicate=2, timeout=0, corrupt=0, overflow=0)
        assert fanout.take_latest().sequence == 1
    finally:
        fanout.close()
    replay = recording.Replay(sink.path)
    assert [replay.frame(i).sequence for i in range(len(replay.rows))] == [1, 0]
    assert replay.times == [.02, .03]


def test_older_timeout_does_not_lower_completed_sequence_watermark():
    assembler = acquisition.Assembler()
    old, new = observation(0), observation(1)
    assert assembler.push(acquisition.Fragment(old, 0, 2, old.points), now=0.) is None
    assert assembler.push(acquisition.Fragment(new, 0, 1, new.points, terminal=True), now=.01)
    assembler.expire(.3)
    assert assembler.push(acquisition.Fragment(new, 0, 1, new.points, terminal=True), now=.31) is None
    assert assembler.counters['timeout'] == 1


@pytest.mark.parametrize('through_fanout', [False, True])
def test_zero_frame_close_reports_failure_and_retains_incomplete_marker(tmp_path, through_fanout):
    sink = recording.Recorder(tmp_path / 'empty')
    if through_fanout:
        acquisition.Fanout(sink).close()
    else:
        with pytest.raises(ValueError, match='no frames'):
            sink.close()
    assert sink.state == 'FAILED'
    assert (sink.path / 'INCOMPLETE').exists()
    assert 'no frames' in sink.error
    with pytest.raises(ValueError, match='incomplete'):
        recording.Replay(sink.path)


def test_single_valid_empty_observation_can_close_and_replay(tmp_path):
    sink = recording.Recorder(tmp_path / 'valid-empty')
    frame = replace(observation(), points=np.zeros(0, dtype=schema.POINT_DTYPE))
    sink.append(frame, 0.)
    sink.close()
    assert sink.state == 'COMPLETE'
    assert recording.Replay(sink.path).frame(0).validity == 'VALID_EMPTY'
