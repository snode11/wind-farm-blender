"""Real export and RTSP lifecycle checks for the Blender video controller."""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
from unittest.mock import Mock

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'wfrl_blender/video_output.py'
spec = importlib.util.spec_from_file_location('video_output_service', MODULE)
video_output = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_output)


def wait_export(job):
    deadline = time.monotonic() + 5
    while job.active and time.monotonic() < deadline:
        time.sleep(.01)
    assert not job.active


@pytest.mark.skipif(not hasattr(os, 'pread'), reason='os.pread required')
@pytest.mark.parametrize('size', [128, 16384])
def test_log_tail_preserves_shared_file_offset(tmp_path, size):
    content = b'x' * (size - 4) + b'tail'
    with (tmp_path / 'child.log').open('w+b') as handle:
        handle.write(content)
        handle.flush()
        handle.seek(11)
        assert video_output._tail(handle) == content[-8192:].decode()
        assert os.lseek(handle.fileno(), 0, os.SEEK_CUR) == 11


@pytest.mark.skipif(not hasattr(os, 'pread'), reason='os.pread required')
def test_large_log_tail_resets_shared_offset_before_truncation(tmp_path):
    content = b'x' * (1024 * 1024) + b'tail'
    with (tmp_path / 'child.log').open('w+b') as handle:
        handle.write(content)
        handle.flush()
        assert video_output._tail(handle) == content[-8192:].decode()
        assert os.fstat(handle.fileno()).st_size == 0
        assert os.lseek(handle.fileno(), 0, os.SEEK_CUR) == 0
        os.write(handle.fileno(), b'next log')
        assert os.pread(handle.fileno(), 8192, 0) == b'next log'


def test_stream_without_pread_keeps_polling_and_releases_owned_processes(tmp_path, monkeypatch):
    monkeypatch.delattr(video_output.os, 'pread', raising=False)
    monkeypatch.setattr(video_output, 'executable', lambda value, name: name)
    processes = []

    def spawn(command, log):
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)
        processes.append(process)
        if len(processes) == 1:
            log.write(b'listener opened on :8554\nis publishing to path windfarm/camera1\n')
        else:
            log.write(b'frame=1\n')
        log.flush()
        return process

    monkeypatch.setattr(video_output, '_spawn', spawn)
    source = tmp_path / 'video.mp4'
    source.touch()
    stream = video_output.Stream()
    try:
        stream.start(source)
        directory = Path(stream.directory.name)
        server_position = stream.logs[0].tell()
        stream.poll()
        assert stream.state == 'RUNNING', stream.message
        assert stream.logs[0].tell() == server_position
        stream.logs[1].write(b'frame=2\n')
        stream.logs[1].flush()
        assert video_output._tail(stream.logs[1]) == 'frame=1\nframe=2\n'
    finally:
        stream.stop()
    assert len(processes) == 2
    for process in processes:
        process.terminate.assert_called_once_with()
    assert not directory.exists() and not stream.active


def test_export_preserves_bytes_and_refuses_overwrite(tmp_path):
    source = tmp_path / '原始相机.mp4'
    source.write_bytes(os.urandom(100000))
    destination = tmp_path / '离线视频.mp4'
    job = video_output.Export()
    job.start(source, destination)
    wait_export(job)
    assert not job.error
    assert source.read_bytes() == destination.read_bytes()
    with pytest.raises(FileExistsError):
        job.start(source, source)
    with pytest.raises(FileExistsError):
        job.start(source, destination)
    assert source.read_bytes() == destination.read_bytes()


def test_cancel_removes_partial_export(tmp_path, monkeypatch):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'x' * 100)
    target = tmp_path / 'output.mp4'
    job = video_output.Export()
    job.cancelled.set()
    job._copy(source, target)
    assert job.error
    assert not target.exists()
    assert source.read_bytes() == b'x' * 100


@pytest.mark.parametrize('changed_size', [0, 50, 200])
def test_export_rejects_a_source_changed_after_start(tmp_path, monkeypatch, changed_size):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'x' * 100)
    target = tmp_path / 'output.mp4'
    job = video_output.Export()
    proceed = threading.Event()
    original_copy = job._copy

    def copy_after_source_change(source, target):
        assert proceed.wait(5)
        original_copy(source, target)

    monkeypatch.setattr(job, '_copy', copy_after_source_change)
    job.start(source, target)
    try:
        source.write_bytes(b'y' * changed_size)
    finally:
        proceed.set()
    wait_export(job)
    assert job.error, 'A changed source must not be reported as a successful export'
    assert not target.exists()
    assert source.read_bytes() == b'y' * changed_size


def test_invalid_tools_or_video_never_start_server(tmp_path):
    stream = video_output.Stream()
    with pytest.raises(ValueError):
        stream.start(tmp_path / 'missing.mp4')
    source = tmp_path / 'video.mp4'; source.touch()
    with pytest.raises(ValueError, match='路径不存在'):
        stream.start(source, ffmpeg=str(tmp_path / 'missing'))
    assert not stream.active and stream.directory is None


@pytest.mark.skipif(not os.environ.get('WFRL_MEDIAMTX'), reason='Set WFRL_MEDIAMTX for actual RTSP')
def test_stream_decode_failure_cleanup_restart_and_busy_port(tmp_path):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('FFmpeg required')
    source = tmp_path / '相机 video.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=128x72:rate=20',
                    '-t', '2', '-c:v', 'libx264', '-g', '20', '-bf', '0', str(source)], check=True)
    stream = video_output.Stream()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]

    def start():
        stream.start(source, ffmpeg, os.environ['WFRL_MEDIAMTX'], port=port)
        deadline = time.monotonic() + 10
        while stream.state == 'STARTING' and time.monotonic() < deadline:
            stream.poll(); time.sleep(.05)
        assert stream.state == 'RUNNING', stream.message

    try:
        start()
        with pytest.raises(RuntimeError, match='先停止'):
            stream.start(source, ffmpeg, os.environ['WFRL_MEDIAMTX'], port=port)
        subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-rtsp_transport', 'tcp', '-i', stream.url,
                        '-frames:v', '65', '-f', 'null', '-'], check=True, capture_output=True, timeout=15)
        server, publisher = stream.server, stream.publisher
        directory = Path(stream.directory.name)
        publisher.kill(); publisher.wait(timeout=5)
        stream.poll()
        assert stream.state == 'ERROR' and not stream.active
        assert server.poll() is not None and not directory.exists()
        start()
        server, publisher = stream.server, stream.publisher
        stream.stop()
        assert server.poll() is not None and publisher.poll() is not None
        assert stream.directory is None
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', port)); busy.listen()
            stream.start(source, ffmpeg, os.environ['WFRL_MEDIAMTX'], port=port)
            deadline = time.monotonic() + 5
            while stream.active and time.monotonic() < deadline:
                stream.poll(); time.sleep(.05)
            assert not stream.active and stream.state == 'ERROR'
            assert busy.getsockname()[1] == port
    finally:
        stream.stop()
