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


@pytest.mark.parametrize('count,point_count', [(1, 1), (2, 1), (1, 0), (2, 0)])
def test_duplicate_fragment_can_supply_terminal_evidence(count, point_count):
    source = replace(observation(), points=np.zeros(point_count, dtype=schema.POINT_DTYPE))
    assembler = acquisition.Assembler()
    for index in range(count):
        assert assembler.push(acquisition.Fragment(source, index, count, source.points), now=0.) is None
    result = assembler.push(acquisition.Fragment(source, 0, count, source.points, terminal=True), now=.01)
    assert result is not None and len(result.points) == count * point_count
    assert result.completeness == 'COMPLETE' and not assembler.pending
    assert assembler.push(acquisition.Fragment(source, 0, count, source.points, terminal=True), now=.02) is None
    assert assembler.counters == dict(duplicate=2, timeout=0, corrupt=0, overflow=0)


def test_duplicate_false_does_not_revoke_terminal_evidence():
    source = observation()
    assembler = acquisition.Assembler()
    assert assembler.push(acquisition.Fragment(source, 0, 2, source.points, terminal=True), now=0.) is None
    assert assembler.push(acquisition.Fragment(source, 0, 2, source.points), now=.01) is None
    assert assembler.push(acquisition.Fragment(source, 1, 2, source.points), now=.02) is not None


@pytest.mark.parametrize('retirement', ['conflict', 'timeout', 'CORRUPT'])
def test_terminal_upgrade_cannot_revive_retired_frame(retirement):
    source = observation()
    assembler = acquisition.Assembler()
    assert assembler.push(acquisition.Fragment(source, 0, 1, source.points), now=0.) is None
    if retirement == 'timeout':
        assembler.expire(.3)
    else:
        points = source.points.copy()
        points['x'] = 20
        template = replace(source, completeness='CORRUPT') if retirement == 'CORRUPT' else source
        assert assembler.push(acquisition.Fragment(template, 0, 1, points, terminal=True), now=.01) is None
    assert assembler.push(acquisition.Fragment(source, 0, 1, source.points, terminal=True), now=.31) is None
    assert not assembler.pending
    assert assembler.counters['timeout' if retirement == 'timeout' else 'corrupt'] == 1


@pytest.mark.parametrize('terminal', [1, 'true', None])
def test_nonboolean_duplicate_cannot_supply_terminal_evidence(terminal):
    source = observation()
    assembler = acquisition.Assembler()
    assert assembler.push(acquisition.Fragment(source, 0, 1, source.points), now=0.) is None
    assert assembler.push(acquisition.Fragment(source, 0, 1, source.points, terminal=terminal), now=.01) is None
    assert assembler.counters['corrupt'] == 1
    assert assembler.push(acquisition.Fragment(source, 0, 1, source.points, terminal=True), now=.02) is not None


def test_assembly_timeout_boundary_retires_frame():
    source = observation()
    assembler = acquisition.Assembler(timeout_s=.25)
    assert assembler.push(acquisition.Fragment(source, 0, 2, source.points), now=0.) is None
    assert assembler.push(acquisition.Fragment(source, 1, 2, source.points, True), now=.25) is None
    assert not assembler.pending
    assert assembler.counters == dict(duplicate=1, timeout=1, corrupt=0, overflow=0)


@pytest.mark.parametrize('capacity', [True, 1.5])
def test_queue_and_assembler_capacities_require_integers(capacity):
    with pytest.raises(ValueError, match='budget'):
        acquisition.Assembler(max_pending=capacity)
    with pytest.raises(ValueError, match='budget'):
        acquisition.Fanout(record_capacity=capacity)


@pytest.mark.parametrize('now', [float('nan'), float('inf')])
def test_assembly_rejects_nonfinite_time_without_retaining_fragments(now):
    source = observation()
    assembler = acquisition.Assembler()
    with pytest.raises(ValueError, match='time'):
        assembler.push(acquisition.Fragment(source, 0, 2, source.points), now=now)
    assert not assembler.pending and not assembler.sessions


def test_worker_append_exception_fails_recording_and_preserves_display(tmp_path):
    sink = recording.Recorder(tmp_path / 'worker-error')
    sink.append(observation(), 0.)
    entered, release = threading.Event(), threading.Event()

    def broken_append(frame, stamp):
        entered.set()
        assert release.wait(2)
        raise OSError('injected append failure')

    sink.append = broken_append
    fanout = acquisition.Fanout(sink)
    try:
        fanout.submit(observation(1), .1)
        assert entered.wait(2)
        fanout.submit(observation(2), .2)
        release.set()
        fanout.worker.join(2)
        assert not fanout.worker.is_alive()
        assert sink.state == 'FAILED' and sink.rows == 1
        assert fanout.failure == sink.error == 'injected append failure'
        assert fanout.record_failures == 1 and fanout.queue.empty()
        assert fanout.queue.unfinished_tasks == 0
        assert (sink.path / 'INCOMPLETE').exists()
        with pytest.raises(ValueError, match='incomplete'):
            recording.Replay(sink.path)
        fanout.submit(observation(3), .3)
        assert fanout.take_latest().sequence == 3
    finally:
        release.set()
        fanout.close()


def test_worker_close_exception_preserves_failure_reason(tmp_path):
    sink = recording.Recorder(tmp_path / 'close-error')
    sink.append(observation(), 0.)

    def broken_close():
        raise OSError('injected close failure')

    sink.close = broken_close
    fanout = acquisition.Fanout(sink)
    fanout.close()
    fanout.close()
    assert not fanout.worker.is_alive()
    assert sink.state == 'FAILED'
    assert sink.error == fanout.failure == 'injected close failure'
    assert (sink.path / 'INCOMPLETE').exists()


def test_request_close_drains_accepted_queue_and_rejects_new_submissions(tmp_path):
    sink = recording.Recorder(tmp_path / 'drain')
    entered, release = threading.Event(), threading.Event()
    append = sink.append

    def held_append(frame, stamp):
        entered.set()
        assert release.wait(2)
        append(frame, stamp)

    sink.append = held_append
    fanout = acquisition.Fanout(sink)
    try:
        fanout.submit(observation(0), 0.)
        assert entered.wait(2)
        fanout.submit(observation(1), .1)
        held = fanout.begin_delivery()
        fanout.request_close()
        with pytest.raises(RuntimeError, match='closing'):
            fanout.submit(observation(2), .2)
        assert not fanout.submit_display(held[0], restoring=True, restore_token=held[2])
        assert fanout.begin_delivery() is None and fanout.take_latest() is None
        assert not fanout.deliveries and not fanout.latest_received
    finally:
        release.set()
        fanout.close()
    replay = recording.Replay(sink.path)
    assert [replay.frame(i).sequence for i in range(2)] == [0, 1]
    assert fanout.queue.unfinished_tasks == 0


def test_display_submission_is_independent_of_producer_mutation():
    fanout = acquisition.Fanout()
    try:
        source = observation()
        assert fanout.submit_display(source)
        source.provenance['calibration']['id'] = 'changed'
        source.points.flags.writeable = True
        source.points['x'] = 99
        stored = fanout.take_latest()
        assert stored.provenance['calibration']['id'] == 'original'
        assert stored.points['x'][0] == 10
    finally:
        fanout.close()


@pytest.mark.parametrize('stamp', [float('nan'), float('inf'), True])
def test_invalid_display_time_does_not_consume_sequence(stamp):
    fanout = acquisition.Fanout()
    try:
        source = observation()
        with pytest.raises(ValueError, match='receipt time'):
            fanout.submit_display(source, received_monotonic=stamp)
        assert not fanout.watermarks and not fanout.latest
        assert fanout.submit_display(source, received_monotonic=1.)
    finally:
        fanout.close()
