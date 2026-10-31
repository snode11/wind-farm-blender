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
