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


def test_mutated_frame_cannot_create_unreadable_complete_recording(tmp_path):
    sink = recording.Recorder(tmp_path / 'mutated')
    source = observation(10)
    source.spatial['world_from_sensor'] = [[0]]
    with pytest.raises(ValueError, match='world transform'):
        sink.append(source, 0.)
    sink.close()
    sink.close()
    assert sink.state == 'FAILED' and sink.rows == 0
    assert sink.index.closed
    assert not list((sink.path / 'frames').iterdir())
    assert (sink.path / 'INCOMPLETE').exists()
    with pytest.raises(ValueError, match='incomplete'):
        recording.Replay(sink.path)


@pytest.mark.parametrize('step', ['manifest', 'marker'])
@pytest.mark.parametrize('outcome', ['delay', 'failure'])
def test_close_reports_complete_only_after_manifest_and_marker_are_durable(tmp_path, monkeypatch, step, outcome):
    import threading
    sink = recording.Recorder(tmp_path / f'{step}-{outcome}')
    sink.append(observation(1), 0.)
    entered, release = threading.Event(), threading.Event()
    marker = sink.path / 'INCOMPLETE'
    unlink, manifest = Path.unlink, sink._manifest

    def hold():
        entered.set()
        assert release.wait(15)
        if outcome == 'failure':
            raise OSError(f'injected {step} failure')

    def held_manifest(state=None):
        if step == 'manifest' and state == 'COMPLETE':
            hold()
        return manifest(state)

    def held_unlink(path, *args, **kwargs):
        if step == 'marker' and path == marker:
            hold()
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(sink, '_manifest', held_manifest)
    monkeypatch.setattr(Path, 'unlink', held_unlink)
    errors = []

    def close():
        try:
            sink.close()
        except OSError as exc:
            errors.append(exc)

    writer = threading.Thread(target=close)
    writer.start()
    try:
        assert entered.wait(15)
        assert sink.state == 'FINALIZING'
        assert marker.exists()
        with pytest.raises(ValueError, match='incomplete'):
            recording.Replay(sink.path)
        with pytest.raises(RuntimeError, match='not recording'):
            sink.append(observation(2), 1.)
    finally:
        release.set()
        writer.join(15)
    assert not writer.is_alive()
    saved = json.loads((sink.path / 'manifest.json').read_bytes())
    if outcome == 'delay':
        assert not errors and sink.state == 'COMPLETE' and saved['state'] == 'COMPLETE'
        assert not marker.exists() and recording.Replay(sink.path).frame(0).points['x'][0] == 1
    else:
        assert len(errors) == 1 and sink.state == 'FAILED' and saved['state'] == 'FAILED'
        assert 'injected' in saved['error'] and marker.exists()
        with pytest.raises(ValueError, match='incomplete'):
            recording.Replay(sink.path)
