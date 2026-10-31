"""Replay validates the same bytes that it decodes, within writer and reader budgets."""
import io
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
