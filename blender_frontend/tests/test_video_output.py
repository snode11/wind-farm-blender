"""Real export and RTSP lifecycle checks for the Blender video controller."""
import ctypes
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from blender_frontend.tests._platform import symlink_or_skip

MODULE = Path(__file__).resolve().parents[1] / 'wfrl_blender/video_output.py'
spec = importlib.util.spec_from_file_location('video_output_service', MODULE)
video_output = importlib.util.module_from_spec(spec)
spec.loader.exec_module(video_output)

WAIT_TIMEOUT = 60


def wait_export(job):
    if job.thread is not None:
        job.thread.join(WAIT_TIMEOUT)
    if job._exit_janitor is not None:
        job._exit_janitor.join(WAIT_TIMEOUT)
    assert not job.active, f'Export worker did not exit: {job.status()!r}'


def wait_stream(stream, predicate=lambda job: not job.active):
    deadline = time.monotonic() + WAIT_TIMEOUT
    while not predicate(stream) and time.monotonic() < deadline:
        if stream._stop_thread is not None and stream._stop_thread.is_alive():
            stream._stop_thread.join(max(0., deadline - time.monotonic()))
        else:
            stream.poll()
            threading.Event().wait(.01)
    assert predicate(stream), (f'RTSP condition not reached: state={stream.state}, '
        f'active={stream.active}, message={stream.message}, '
        f'processes={[(p.pid, p.poll()) for p in (stream.server, stream.publisher) if p is not None]}')


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
        wait_stream(stream)
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
    assert job.state == 'CANCELLED' and not job.error
    assert not target.exists()
    assert source.read_bytes() == b'x' * 100


@pytest.mark.parametrize('cancel', [False, True])
def test_external_destination_during_copy_is_preserved_and_never_reported_success(tmp_path, monkeypatch, cancel):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'original video')
    entered, release = threading.Event(), threading.Event()
    original_open = Path.open
    class HeldReader:
        def __enter__(self):
            self.stream = original_open(source, 'rb')
            return self
        def __exit__(self, *args):
            self.stream.close()
        def fileno(self):
            return self.stream.fileno()
        def read(self, count):
            entered.set()
            assert release.wait(WAIT_TIMEOUT * 2), 'Export reader was not released'
            return self.stream.read(count)
    def held_open(path, *args, **kwargs):
        return HeldReader() if path == source and args == ('rb',) else original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', held_open)
    job = video_output.Export()
    job.start(source, target)
    try:
        assert entered.wait(WAIT_TIMEOUT), f'Export reader did not start: {job.status()!r}'
        replacement = tmp_path / 'replacement.mp4'
        replacement.write_bytes(b'preserve external video')
        os.replace(replacement, target)
        if cancel:
            job.cancelled.set()
    finally:
        release.set()
        wait_export(job)
    assert target.read_bytes() == b'preserve external video'
    assert (job.state == 'CANCELLED' if cancel else bool(job.error)) and not job.output_path
    assert sorted(p.name for p in tmp_path.iterdir()) == ['output.mp4', 'source.mp4']


@pytest.mark.skipif(sys.platform not in {'darwin', 'linux'}, reason='Native exclusive rename required')
def test_export_can_commit_on_filesystems_without_hard_links(tmp_path, monkeypatch):
    function = 'renamex_np' if sys.platform == 'darwin' else 'renameat2'
    if not hasattr(ctypes.CDLL(None), function):
        pytest.skip('Native exclusive rename unavailable')
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'complete video')
    def unsupported(*args, **kwargs):
        raise OSError('Filesystem does not support hard links')
    monkeypatch.setattr(video_output.os, 'link', unsupported)
    job = video_output.Export()
    job.start(source, target)
    wait_export(job)
    assert not job.error
    assert target.read_bytes() == b'complete video'


@pytest.mark.parametrize('platform', ['nt', 'posix'])
@pytest.mark.parametrize('field', ['st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'])
def test_export_stat_fstat_identity_difference(tmp_path, monkeypatch, platform, field):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    content = b'unchanged video bytes'
    source.write_bytes(content)
    original = source.stat()
    fstat = os.fstat
    source_checks = []

    def changed_stat(fd):
        info = fstat(fd)
        if (info.st_dev, info.st_ino) == (original.st_dev, original.st_ino):
            source_checks.append(fd)
            fields = {name: getattr(info, name) for name in
                      ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_mode')}
            fields[field] += 1
            return SimpleNamespace(**fields)
        return info

    monkeypatch.setattr(video_output, 'os', SimpleNamespace(**{
        **vars(os), 'name': platform, 'fstat': changed_stat}))
    job = video_output.Export()
    job._source_identity = job._identity(original)
    job.total_bytes = len(content)

    job._copy(source, target)

    assert source_checks
    assert source.read_bytes() == content
    if platform == 'nt' and field == 'st_ctime_ns':
        assert job.state == 'SUCCEEDED' and not job.error
        assert target.read_bytes() == content
        assert job.done_bytes == job.total_bytes
        assert set(tmp_path.iterdir()) == {source, target}
    else:
        assert job.state == 'FAILED' and '源视频在导出期间发生变化' in job.error
        assert set(tmp_path.iterdir()) == {source}


@pytest.mark.skipif(os.name != 'posix', reason='POSIX content-change ctime semantics')
def test_posix_export_rejects_content_change_with_restored_mtime(tmp_path):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'x' * 100)
    original = source.stat()
    job = video_output.Export()
    job._source_identity = job._identity(original)
    job.total_bytes = original.st_size
    source.write_bytes(b'y' * 100)
    os.utime(source, ns=(original.st_atime_ns, original.st_mtime_ns))
    changed = source.stat()
    assert changed.st_size == original.st_size and changed.st_mtime_ns == original.st_mtime_ns
    assert changed.st_ctime_ns != original.st_ctime_ns

    job._copy(source, target)

    assert job.state == 'FAILED' and '源视频在导出期间发生变化' in job.error
    assert source.read_bytes() == b'y' * 100
    assert set(tmp_path.iterdir()) == {source}


@pytest.mark.parametrize('changed_size', [0, 50, 200])
def test_export_rejects_a_source_changed_after_start(tmp_path, monkeypatch, changed_size):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'x' * 100)
    target = tmp_path / 'output.mp4'
    job = video_output.Export()
    proceed = threading.Event()
    original_copy = job._copy

    def copy_after_source_change(source, target):
        assert proceed.wait(WAIT_TIMEOUT * 2), 'Source change did not release export worker'
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
                    '-t', '2', '-c:v', 'libx264', '-g', '20', '-bf', '0', str(source)],
                   check=True, capture_output=True, timeout=WAIT_TIMEOUT * 2)
    stream = video_output.Stream()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]

    def start():
        stream.start(source, ffmpeg, os.environ['WFRL_MEDIAMTX'], port=port)
        wait_stream(stream, lambda job: job.state not in {'STARTING', 'STOPPING'})
        assert stream.state == 'RUNNING', stream.message

    try:
        start()
        with pytest.raises(RuntimeError, match='先停止'):
            stream.start(source, ffmpeg, os.environ['WFRL_MEDIAMTX'], port=port)
        subprocess.run([ffmpeg, '-v', 'error', '-xerror', '-rtsp_transport', 'tcp', '-i', stream.url,
                        '-frames:v', '65', '-f', 'null', '-'], check=True, capture_output=True,
                       timeout=WAIT_TIMEOUT * 2)
        server, publisher = stream.server, stream.publisher
        directory = Path(stream.directory.name)
        publisher.kill(); publisher.wait(timeout=WAIT_TIMEOUT)
        stream.poll()
        wait_stream(stream)
        assert stream.state == 'ERROR' and not stream.active
        assert server.poll() is not None and not directory.exists()
        start()
        server, publisher = stream.server, stream.publisher
        stream.stop()
        wait_stream(stream)
        assert server.poll() is not None and publisher.poll() is not None
        assert stream.directory is None
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', port)); busy.listen()
            stream.start(source, ffmpeg, os.environ['WFRL_MEDIAMTX'], port=port)
            wait_stream(stream)
            assert not stream.active and stream.state == 'ERROR'
            assert busy.getsockname()[1] == port
    finally:
        stream.close()


@pytest.mark.parametrize('block_at', ['read', 'fsync'])
def test_stop_is_bounded_and_keeps_worker_and_staging_until_io_finishes(tmp_path, monkeypatch, block_at):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'owned video')
    entered, release = threading.Event(), threading.Event()
    original_open, original_fsync = Path.open, video_output.os.fsync
    class HeldReader:
        def __enter__(self):
            self.stream = original_open(source, 'rb')
            return self
        def __exit__(self, *args): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, count):
            entered.set()
            assert release.wait(WAIT_TIMEOUT * 2), 'Export reader was not released'
            return self.stream.read(count)
    if block_at == 'read':
        monkeypatch.setattr(Path, 'open', lambda p, *a, **kw:
            HeldReader() if p == source and a == ('rb',) else original_open(p, *a, **kw))
    else:
        def fsync(fd):
            entered.set()
            assert release.wait(WAIT_TIMEOUT * 2), 'Export fsync was not released'
            original_fsync(fd)
        monkeypatch.setattr(video_output.os, 'fsync', fsync)
    job = video_output.Export()
    job.start(source, target)
    try:
        assert entered.wait(WAIT_TIMEOUT), f'Export did not reach {block_at}: {job.status()!r}'
        worker = job.thread
        join = Mock(wraps=worker.join)
        monkeypatch.setattr(worker, 'join', join)
        assert not job.stop()
        join.assert_called_once_with(timeout=.05)
        assert job.state == 'CANCELLING' and job.thread is worker and job.active
        assert list(tmp_path.glob('.wfrl-video-export-*'))
        with pytest.raises(RuntimeError): job.start(source, target)
    finally:
        release.set()
        wait_export(job)
    assert job.state == 'CANCELLED' and not job.error and not target.exists()
    assert not list(tmp_path.glob('.wfrl-video-export-*'))



def test_stream_stop_is_nonblocking_and_releases_server_when_publisher_times_out():
    stream = video_output.Stream()
    server, publisher = stream.server, stream.publisher = Mock(), Mock()
    entered, release = threading.Event(), threading.Event()
    server.poll.return_value = publisher.poll.return_value = None
    server.terminate.side_effect = lambda: setattr(server.poll, 'return_value', 0)

    def wait_for_publisher(timeout):
        entered.set()
        assert release.wait(WAIT_TIMEOUT * 2), 'Publisher wait was not released'
        raise subprocess.TimeoutExpired('ffmpeg', timeout)

    publisher.wait.side_effect = wait_for_publisher
    try:
        stream.stop()
        assert entered.wait(WAIT_TIMEOUT), f'RTSP stop worker did not start: {stream.state} / {stream.message}'
        assert stream.state == 'STOPPING' and stream.active
        server.terminate.assert_called_once_with()
    finally:
        release.set()
    wait_stream(stream, lambda job: job.state == 'ERROR')
    publisher.kill.assert_called_once_with()
    assert stream.active and stream.publisher is publisher
    publisher.poll.return_value = 0
    wait_stream(stream)


def test_export_stop_cannot_cancel_an_already_claimed_commit(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'complete video')
    entered, release = threading.Event(), threading.Event()
    commit = video_output._commit_export

    def held_commit(staging, target):
        entered.set()
        assert release.wait(WAIT_TIMEOUT * 2), 'Export commit was not released'
        commit(staging, target)

    monkeypatch.setattr(video_output, '_commit_export', held_commit)
    job = video_output.Export()
    job.start(source, target)
    try:
        assert entered.wait(WAIT_TIMEOUT), f'Export did not reach commit: {job.status()!r}'
        assert not job.stop(timeout=0)
        assert job.state == 'COMMITTING' and not job.output_path and not target.exists()
    finally:
        release.set()
        wait_export(job)
    assert job.state == 'SUCCEEDED' and job.output_path == str(target)
    assert job.message == '已导出：' + str(target)
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX can unlink an open staging file')
def test_normal_process_exit_unlinks_owned_export_with_blocked_fsync(tmp_path):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'complete video')
    script = """
import importlib.util, os, sys, threading
spec = importlib.util.spec_from_file_location('video_output', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
entered = threading.Event()
def held_fsync(_fd):
    entered.set()
    threading.Event().wait()
module.os.fsync = held_fsync
module.export.start(sys.argv[2], sys.argv[3])
assert entered.wait(60), 'Export worker did not reach fsync before process exit'
"""
    subprocess.run([sys.executable, '-c', script, str(MODULE), str(source), str(target)],
                   check=True, capture_output=True, timeout=WAIT_TIMEOUT * 2)
    assert not target.exists()
    assert not list(tmp_path.glob('.wfrl-video-export-*'))


def test_export_does_not_follow_a_dangling_destination_symlink(tmp_path):
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'complete video')
    symlink_or_skip(target, tmp_path / 'missing.mp4')
    with pytest.raises(FileExistsError):
        video_output.Export().start(source, target)
    assert target.is_symlink() and not (tmp_path / 'missing.mp4').exists()


@pytest.mark.parametrize('boundary', ['copy', 'janitor'])
@pytest.mark.parametrize('terminal', ['SUCCEEDED', 'CANCELLED', 'FAILED'])
def test_export_terminal_waits_for_all_owned_threads(tmp_path, monkeypatch, boundary, terminal):
    if boundary == 'janitor' and os.name != 'posix':
        pytest.skip('POSIX export janitor required')
    source, target = tmp_path / 'source.mp4', tmp_path / 'output.mp4'
    source.write_bytes(b'complete video')
    job = video_output.Export()
    entered, release = threading.Event(), threading.Event()
    original_copy, original_cleanup = job._copy, job._cleanup_on_exit

    def hold_exit():
        entered.set()
        assert release.wait(WAIT_TIMEOUT), 'Export thread exit was not released'

    def copy(source, target):
        if terminal == 'CANCELLED':
            job.cancelled.set()
        original_copy(source, target)
        if boundary == 'copy':
            hold_exit()

    def cleanup(stage):
        original_cleanup(stage)
        if boundary == 'janitor':
            hold_exit()

    if terminal == 'FAILED':
        def fail_commit(*args):
            raise OSError('injected commit failure')
        monkeypatch.setattr(video_output, '_commit_export', fail_commit)
    monkeypatch.setattr(job, '_copy', copy)
    monkeypatch.setattr(job, '_cleanup_on_exit', cleanup)
    job.start(source, target)
    try:
        assert entered.wait(WAIT_TIMEOUT)
        if boundary == 'janitor':
            job.thread.join(WAIT_TIMEOUT)
            assert not job.thread.is_alive()
        assert job.state not in {'SUCCEEDED', 'CANCELLED', 'FAILED'}
        assert job.active and job.status()[1]
        with pytest.raises(RuntimeError, match='正在导出'):
            job.start(source, tmp_path / 'retry.mp4')
    finally:
        release.set()
        job.thread.join(WAIT_TIMEOUT)
        if job._exit_janitor is not None:
            job._exit_janitor.join(WAIT_TIMEOUT)
    assert job.state == terminal and not job.active
    assert not list(tmp_path.glob('.wfrl-video-export-*'))
    job.start(source, tmp_path / 'retry.mp4')
    wait_export(job)


@pytest.mark.parametrize('error', ['', 'injected stream failure'])
def test_stream_terminal_waits_for_stop_thread_exit(tmp_path, monkeypatch, error):
    source = tmp_path / 'video.mp4'
    source.touch()
    monkeypatch.setattr(video_output, 'executable', lambda value, name: name)
    processes = []

    def spawn(*args):
        process = Mock()
        process.poll.return_value = None
        process.terminate.side_effect = lambda: setattr(process.poll, 'return_value', 0)
        processes.append(process)
        return process

    monkeypatch.setattr(video_output, '_spawn', spawn)
    stream = video_output.Stream()
    original_stop = stream._stop_processes
    entered, release = threading.Event(), threading.Event()

    def stop(error):
        original_stop(error)
        entered.set()
        assert release.wait(WAIT_TIMEOUT), 'RTSP stop thread exit was not released'

    monkeypatch.setattr(stream, '_stop_processes', stop)
    stream.start(source)
    directory, logs = Path(stream.directory.name), tuple(stream.logs)
    try:
        stream.stop(error=error)
        assert entered.wait(WAIT_TIMEOUT)
        assert all(process.poll() is not None for process in processes)
        assert all(log.closed for log in logs) and not directory.exists()
        assert stream.state == 'STOPPING' and stream.active
        with pytest.raises(RuntimeError, match='先停止'):
            stream.start(source)
    finally:
        release.set()
        stream._stop_thread.join(WAIT_TIMEOUT)
    assert stream.state == ('ERROR' if error else 'STOPPED') and not stream.active
    try:
        stream.start(source)
        assert stream.active and stream.state == 'STARTING'
    finally:
        stream.close()
