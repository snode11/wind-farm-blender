"""Failure boundaries for camera files and capture finalization."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import custom_camera_capture as capture
from blender_frontend.tests.test_custom_camera_capture import capture_runtime, clock_capture


def test_png_write_failure_never_publishes_a_partial_file(tmp_path, monkeypatch):
    target = tmp_path / 'frame.png'
    original_open = Path.open

    class BrokenWrite:
        def __init__(self, path, *args, **kwargs):
            self.stream = original_open(path, *args, **kwargs)

        def __enter__(self):
            self.stream.__enter__()
            return self

        def write(self, data):
            self.stream.write(data[:8])
            self.stream.flush()
            raise OSError('injected PNG disk failure')

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

    monkeypatch.setattr(Path, 'open', lambda path, mode='r', *args, **kwargs:
                        BrokenWrite(path, mode, *args, **kwargs) if mode == 'xb'
                        else original_open(path, mode, *args, **kwargs))
    with pytest.raises(OSError, match='PNG disk failure'):
        capture.write_png(target, 1, 1, bytes(4))
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('failure', ['exception', 'short_write'])
def test_failed_frame_append_preserves_only_committed_groups(clock_capture, monkeypatch, failure):
    job, _calls, _generation = clock_capture
    target = job.path / 'frames.jsonl'
    previous = '{"sample_index": 0, "camera_id": "C1"}\n'
    target.write_text(previous, encoding='utf-8')
    job._writer = NS(thread=NS(is_alive=lambda: False), error=None,
                     cancelled=threading.Event())
    job._pending_records = [{'sample_index': 1, 'camera_id': slot} for slot in ('C1', 'C2')]
    job.in_group = True
    original_open = Path.open

    class BrokenAppend:
        def __init__(self, *args, **kwargs):
            self.stream = original_open(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            self.stream.__enter__()
            return self

        def write(self, data):
            newline = b'\n' if isinstance(data, bytes) else '\n'
            written = self.stream.write(data[:data.index(newline) + 5])
            self.stream.flush()
            if failure == 'short_write':
                return written
            raise OSError('injected frame index failure')

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

    monkeypatch.setattr(Path, 'open', lambda path, mode='r', *args, **kwargs:
                        BrokenAppend(path, mode, *args, **kwargs)
                        if path == target and mode.startswith('a')
                        else original_open(path, mode, *args, **kwargs))
    with pytest.raises(OSError):
        job.step(None)
    assert target.read_text(encoding='utf-8') == previous
    assert job.index == 1 and job.manifest['status'] == 'incomplete'


@pytest.mark.parametrize('resource', ['gpu', 'ui'])
def test_capture_cleanup_retries_without_skipping_other_resources(
        capture_runtime, tmp_path, monkeypatch, resource):
    context, timers = capture_runtime
    job = capture.Capture(context, tmp_path)
    calls, released = [], set()
    blocked = True

    def cleanup(name):
        calls.append(name)
        if name == resource and blocked:
            raise RuntimeError('injected ' + name + ' cleanup failure')
        released.add(name)

    job.render_target = NS(free=lambda: cleanup('gpu'), usage=lambda: {'active_targets': 0})
    job.ui_operator = NS(remove_timer=lambda: cleanup('ui'))
    job.finish(error='capture interrupted')
    assert set(calls) == {'gpu', 'ui'}
    assert not job._closed and capture._ACTIVE is job
    assert len(timers.callbacks) == 1
    assert json.loads((job.path / 'manifest.json').read_text(encoding='utf-8'))['status'] == 'incomplete'
    blocked = False
    callback = next(iter(timers.callbacks))
    assert callback() is None
    assert released == {'gpu', 'ui'} and job.closed
    assert capture._ACTIVE is None and not timers.callbacks
    assert capture.status_for(context.scene).stage == 'FAILED'
    replacement = capture.Capture(context, tmp_path)
    replacement.request_cancel()


def test_manifest_failure_cannot_leave_a_successful_terminal(capture_runtime, tmp_path, monkeypatch):
    context, _timers = capture_runtime
    job = capture.Capture(context, tmp_path)

    def fail_write(*_args, **_kwargs):
        raise OSError('injected terminal manifest failure')

    monkeypatch.setattr(capture, 'write_json', fail_write)
    try:
        job.finish(success=True)
    except OSError:
        pass
    assert job.closed and not capture.active()
    assert job.manifest['status'] == 'incomplete'
    assert 'terminal manifest failure' in job.error
    assert capture.status_for(context.scene).stage == 'FAILED'
    assert json.loads((job.path / 'manifest.json').read_text(encoding='utf-8'))['status'] == 'incomplete'


@pytest.mark.parametrize('filename', ['layout.json', 'manifest.json'])
def test_capture_initialization_failure_leaves_no_published_directory(
        capture_runtime, tmp_path, monkeypatch, filename):
    context, _timers = capture_runtime
    original_write = capture.write_json
    playback = []

    def fail_write(path, payload):
        if Path(path).name == filename:
            raise OSError('injected capture initialization failure')
        original_write(path, payload)

    monkeypatch.setattr(capture, 'write_json', fail_write)
    monkeypatch.setattr(capture.Capture, '_play', lambda _self, playing: playback.append(playing))
    monkeypatch.setattr(capture.playback, 'is_playing', lambda _scene: True)
    with pytest.raises(OSError, match='capture initialization failure'):
        capture.Capture(context, tmp_path)
    assert list(tmp_path.iterdir()) == []
    assert capture._ACTIVE is None and playback == [False, True]


def test_capture_directory_collision_preserves_previous_results(capture_runtime, tmp_path, monkeypatch):
    context, _timers = capture_runtime
    import datetime
    monkeypatch.setattr(capture.uuid, 'uuid4', lambda: NS(hex='a' * 32))
    monkeypatch.setattr(capture, 'datetime', NS(now=lambda: datetime.datetime(2026, 1, 1)))
    previous = tmp_path / 'capture_20260101_000000_aaaaaaaaaa'
    previous.mkdir()
    (previous / 'manifest.json').write_text('old result', encoding='utf-8')
    with pytest.raises(OSError):
        capture.Capture(context, tmp_path)
    assert list(tmp_path.iterdir()) == [previous]
    assert (previous / 'manifest.json').read_text(encoding='utf-8') == 'old result'
