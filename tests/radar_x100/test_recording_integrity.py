"""Replay validates the same bytes that it decodes, within writer and reader budgets."""
import io
import json
from pathlib import Path
import numpy as np
import pytest
from wfrl.radar_x100 import ply, recording, schema


def observation(x):
    points = np.zeros(1, dtype=schema.POINT_DTYPE)
    points['x'] = x
    return schema.RadarFrame('X100', 'boot', 0, 'TEST', 'NEAR', points)


def test_replay_reads_each_ply_payload_once(tmp_path, monkeypatch):
    sink = recording.Recorder(tmp_path / 'single-read')
    sink.append(observation(10), 0.)
    sink.close()
    replay = recording.Replay(sink.path)
    path = sink.path / replay.rows[0]['file']
    expected = path.read_bytes()
    changed = ply.encode_ply(observation(99).points)
    opened = []
    original = Path.open
    def replaced_file(file, *args, **kwargs):
        if file == path and (args[0] if args else kwargs.get('mode')) == 'rb':
            opened.append(file)
            return io.BytesIO(changed if len(opened) == 1 else expected)
        return original(file, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', replaced_file)
    with pytest.raises(ValueError, match='digest'):
        replay.frame(0)
    assert len(opened) == 1


def test_recording_cannot_close_complete_after_index_budget_exceeded(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, 'MAX_INDEX_BYTES', 1, raising=False)
    sink = recording.Recorder(tmp_path / 'budget')
    with pytest.raises(ValueError, match='index|budget'):
        sink.append(observation(10), 0.)
    sink.close()
    assert sink.state == 'FAILED' and (sink.path / 'INCOMPLETE').exists()


def test_failed_final_manifest_replace_records_failure_without_reusing_staging(tmp_path, monkeypatch):
    sink = recording.Recorder(tmp_path / 'failed-close')
    sink.append(observation(10), 0.)
    manifest = sink.path / 'manifest.json'
    unrelated = sink.path / 'manifest.json.partial'
    unrelated.write_text('unowned file')
    replace = recording.os.replace
    staging = []
    failure = OSError('final manifest replace failed')

    def fail_once(source, destination):
        if destination == manifest:
            staging.append(source)
            if len(staging) == 1:
                raise failure
        return replace(source, destination)

    monkeypatch.setattr(recording.os, 'replace', fail_once)
    with pytest.raises(OSError) as raised:
        sink.close()
    assert raised.value is failure
    assert len(set(staging)) == 2 and all(not path.exists() for path in staging)
    assert unrelated.read_text() == 'unowned file'
    assert (sink.path / 'INCOMPLETE').exists()
    saved = json.loads(manifest.read_bytes())
    assert (sink.state, saved['state'], saved['error'], saved['frames']) == ('FAILED', 'FAILED', str(failure), 1)
    with pytest.raises(ValueError, match='incomplete'):
        recording.Replay(sink.path)
