"""Offscreen Down encoder process ownership and failure reporting."""
import sys
import threading
import time

import pytest


@pytest.fixture
def manual_down(frontend_module, stub_module):
    stub_module('bpy_extras.io_utils', ExportHelper=type('ExportHelper', (), {}),
                ImportHelper=type('ImportHelper', (), {}))
    return frontend_module('wfrl_blender.manual_down')


def _encoder(script):
    return [sys.executable, '-c', script]


def _no_encoder_threads():
    return not any(thread.name == 'wfrl-down-encoder' for thread in threading.enumerate())


def test_encoder_reports_ffmpeg_reason_when_it_exits_mid_stream(manual_down):
    script = "import sys; sys.stderr.write('Unknown encoder libx264\\n'); sys.exit(1)"
    chunk = b'\0' * (1 << 20)
    started = time.monotonic()
    with pytest.raises(RuntimeError) as failure:
        with manual_down._offscreen_encoder(_encoder(script), job={}) as send:
            for _ in range(10_000):
                send(chunk)
    message = str(failure.value)
    assert 'Unknown encoder libx264' in message and '（1）' in message
    assert isinstance(failure.value.__cause__, OSError)
    assert time.monotonic() - started < 10
    assert _no_encoder_threads()


def test_encoder_reports_ffmpeg_reason_after_reading_all_input(manual_down):
    script = ("import sys; sys.stdin.buffer.read(); "
              "sys.stderr.write('No space left on device\\n'); sys.exit(1)")
    with pytest.raises(RuntimeError) as failure:
        with manual_down._offscreen_encoder(_encoder(script), job={}) as send:
            send(b'frame' * 1000)
    message = str(failure.value)
    assert 'No space left on device' in message and '（1）' in message
    assert _no_encoder_threads()


def test_encoder_finishes_after_ffmpeg_consumes_every_frame(manual_down, tmp_path):
    output = tmp_path / 'frames.bin'
    script = ("import sys; open(sys.argv[1], 'wb').write(sys.stdin.buffer.read())")
    frames = [bytes([index]) * 4096 for index in range(8)]
    with manual_down._offscreen_encoder([*_encoder(script), str(output)], job={}) as send:
        for frame in frames:
            send(frame)
    assert output.read_bytes() == b''.join(frames)
    assert _no_encoder_threads()
