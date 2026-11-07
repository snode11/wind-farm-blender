"""File export and owned RTSP processes, independent of Blender's UI thread."""
from __future__ import annotations

import atexit
import ctypes
import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def video_path(value):
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != '.mp4':
        raise ValueError('请选择已有的 MP4 相机视频')
    return path


def executable(value, name):
    if value:
        path = Path(value).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise ValueError(f'{name} 路径不存在，请在视频输出设置中重新选择')
    path = shutil.which(name)
    if path:
        return path
    raise ValueError(f'未找到 {name}，请在视频输出设置中选择可执行文件')


def server_config(port, lan=False):
    if not 1024 <= int(port) <= 65535:
        raise ValueError('RTSP 端口必须在 1024–65535 之间')
    host = '0.0.0.0' if lan else '127.0.0.1'
    return (f'logLevel: info\nrtspAddress: {host}:{int(port)}\nrtspTransports: [tcp]\n'
            'writeQueueSize: 8192\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\n'
            'moq: no\napi: no\nmetrics: no\npprof: no\nplayback: no\n'
            'authMethod: internal\nauthInternalUsers:\n'
            '  - user: any\n    ips: [127.0.0.1, "::1"]\n    permissions:\n'
            '      - action: publish\n        path: windfarm/camera1\n'
            '  - user: any\n    ips: []\n    permissions:\n'
            '      - action: read\n        path: windfarm/camera1\n'
            'paths:\n  windfarm/camera1:\n    source: publisher\n    overridePublisher: no\n')


def _spawn(command, log):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log,
                            stderr=subprocess.STDOUT, **options)


def _tail(handle):
    size = os.fstat(handle.fileno()).st_size
    # Logs are diagnostics, not permanent frame archives.
    offset = max(0, size - 8192)
    if hasattr(os, 'pread'):
        data = os.pread(handle.fileno(), 8192, offset)
    else:
        # A separate open keeps the child process's shared write offset intact.
        with open(handle.name, 'rb') as reader:
            reader.seek(offset)
            data = reader.read(8192)
    text = data.decode('utf-8', errors='replace')
    if size > 1024 * 1024:
        handle.seek(0)
        handle.truncate()
    return text


class Stream:
    def __init__(self):
        self.server = self.publisher = None
        self.directory = None
        self.logs = []
        self.state = 'STOPPED'
        self.message = 'RTSP 未启动'
        self.url = ''
        self._stop_thread = None
        self._stop_result = None

    def _settle_stop(self):
        if self._stop_result is not None and (
                self._stop_thread is None or not self._stop_thread.is_alive()):
            self._state, self.message = self._stop_result
            self._stop_result = None

    @property
    def state(self):
        self._settle_stop()
        return self._state

    @state.setter
    def state(self, value):
        self._state = value

    @property
    def active(self):
        self._settle_stop()
        return (self._state == 'STOPPING'
                or (self._stop_thread is not None and self._stop_thread.is_alive())
                or self.server is not None or self.publisher is not None)

    def start(self, video, ffmpeg='', mediamtx='', port=8554, lan=False):
        if self.active:
            raise RuntimeError('RTSP 已在运行，请先停止')
        path = video_path(video)
        ffmpeg = executable(ffmpeg, 'ffmpeg')
        mediamtx = executable(mediamtx, 'mediamtx')
        config = server_config(port, lan)
        self.url = f'rtsp://127.0.0.1:{int(port)}/windfarm/camera1'
        self.command = [ffmpeg, '-hide_banner', '-loglevel', 'warning', '-nostdin',
                        '-re', '-stream_loop', '-1', '-i', str(path), '-map', '0:v:0',
                        '-an', '-c:v', 'copy', '-progress', 'pipe:1',
                        '-rtsp_transport', 'tcp', '-f', 'rtsp', self.url]
        self.directory = tempfile.TemporaryDirectory(prefix='wfrl-rtsp-')
        config_path = Path(self.directory.name) / 'mediamtx.yml'
        try:
            config_path.write_text(config, encoding='utf-8')
            for _ in range(2):
                self.logs.append(tempfile.NamedTemporaryFile(mode='w+b', dir=self.directory.name,
                                                             suffix='.log', delete=False))
            self.server = _spawn([mediamtx, str(config_path)], self.logs[0])
        except BaseException:
            self._release()
            raise
        self.state, self.message = 'STARTING', '正在启动 RTSP…'
        self.deadline = time.monotonic() + 15

    def poll(self):
        if not self.active or self.state == 'STOPPING':
            return
        if self.state == 'ERROR':
            if all(process is None or process.poll() is not None
                   for process in (self.publisher, self.server)):
                self._release()
            return
        server_log = _tail(self.logs[0])
        if self.server.poll() is not None:
            return self.fail('RTSP 服务退出；检查端口是否被占用。' + server_log[-600:])
        listening = 'listener opened on' in server_log or 'started with listeners on' in server_log
        if self.publisher is None and listening:
            try:
                self.publisher = _spawn(self.command, self.logs[1])
            except OSError as exc:
                return self.fail(str(exc))
        if self.publisher is not None:
            publisher_log = _tail(self.logs[1])
            if self.publisher.poll() is not None:
                return self.fail('推流失败：' + publisher_log[-600:])
            if 'is publishing to path' in server_log and 'frame=' in publisher_log:
                self.state, self.message = 'RUNNING', '正在循环输出所选视频'
        if self.state == 'STARTING' and time.monotonic() > self.deadline:
            self.fail('RTSP 启动超时，请检查视频格式和工具路径')

    def fail(self, message):
        self.stop(error=message)

    def stop(self, *, error=''):
        if not self.active or self.state == 'STOPPING':
            return
        self.state, self.message = 'STOPPING', error or '正在停止 RTSP…'
        self._stop_thread = threading.Thread(target=self._stop_processes, args=(error,),
                                             name='wfrl-rtsp-stop', daemon=True)
        self._stop_thread.start()

    def _stop_processes(self, error):
        processes = [process for process in (self.publisher, self.server)
                     if process is not None and process.poll() is None]
        for method in ('terminate', 'kill'):
            for process in processes:
                try:
                    getattr(process, method)()
                except OSError:
                    pass  # A failed signal must never prevent stopping the other process.
            deadline = time.monotonic() + 2
            for process in processes:
                try:
                    process.wait(timeout=max(0., deadline - time.monotonic()))
                except (OSError, subprocess.TimeoutExpired):
                    pass
            processes = [process for process in processes if process.poll() is None]
            if not processes:
                break
        if processes:
            self._stop_result = 'ERROR', error + ' RTSP 进程未退出，仍保留进程控制。'
            return
        try:
            self._release()
        except OSError as exc:
            error = f'{error} RTSP 临时文件清理失败：{exc}'.strip()
        self._stop_result = ('ERROR', error) if error else ('STOPPED', 'RTSP 已停止')

    def _release(self):
        for log in self.logs:
            log.close()
        self.logs = []
        try:
            if self.directory:
                self.directory.cleanup()
        finally:
            self.directory = None
            self.publisher = self.server = None

    def close(self):
        if self.state == 'STOPPING' and self._stop_thread is not None:
            self._stop_thread.join(timeout=4.1)
        elif self.active:
            self._stop_processes('')
        self._settle_stop()


def _commit_export(source, destination):
    """Publish complete bytes under an exclusive target name on the same filesystem."""
    if os.name == 'nt':
        os.rename(source, destination)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    function = None
    if sys.platform == 'darwin' and hasattr(libc, 'renamex_np'):
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        args = (os.fsencode(source), os.fsencode(destination), 0x00000004)
    elif sys.platform.startswith('linux') and hasattr(libc, 'renameat2'):
        function = libc.renameat2
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        args = (-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if function is not None:
        function.restype = ctypes.c_int
        if function(*args) == 0:
            return
        code = ctypes.get_errno()
        if code not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP}:
            raise OSError(code, os.strerror(code), str(destination))
    os.link(source, destination, follow_symlinks=False)


class Export:
    def __init__(self):
        self.thread = None
        self.state = 'IDLE'
        self._state_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.done_bytes = self.total_bytes = 0
        self.message = '导出所选视频为 MP4'
        self.error = ''
        self.source_path = self.output_path = ''
        self._source_identity = None
        self._staging = None
        self._exit_janitor = None
        self._result = None

    @staticmethod
    def _identity(stat):
        identity = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        return identity if os.name == 'nt' else (*identity, stat.st_ctime_ns)

    def _workers_active(self):
        return any(worker is not None and worker.is_alive()
                   for worker in (self.thread, self._exit_janitor))

    def _settle(self):
        with self._state_lock:
            if self._result is not None and not self._workers_active():
                self._state, self.message, self.error, self.output_path = self._result
                self._result = None

    @property
    def state(self):
        self._settle()
        return self._state

    @state.setter
    def state(self, value):
        self._state = value

    @property
    def active(self):
        self._settle()
        return self._workers_active()

    def start(self, video, output):
        if self.active:
            raise RuntimeError('已有视频正在导出')
        source, target = video_path(video), Path(os.path.abspath(Path(output).expanduser()))
        if target.suffix.lower() != '.mp4':
            raise ValueError('输出文件必须使用 .mp4 后缀')
        if target == source or os.path.lexists(target):
            raise FileExistsError('目标文件已存在，请另选文件名；不会覆盖源视频')
        if not target.parent.is_dir():
            raise ValueError('输出目录不存在')
        identity = self._identity(source.stat())
        self.cancelled.clear()
        self.done_bytes, self.total_bytes = 0, identity[2]
        self.message, self.error, self.state = '正在导出 MP4…', '', 'RUNNING'
        self.source_path, self.output_path = str(source), ''
        self._source_identity = identity
        self._staging = dict(owned={}, ready=threading.Event())
        if os.name == 'posix':
            self._exit_janitor = threading.Thread(target=self._cleanup_on_exit,
                                                 args=(self._staging,),
                                                 name='wfrl-export-cleanup', daemon=True)
            self._exit_janitor.start()
        self.thread = threading.Thread(target=self._copy, args=(source, target), daemon=True)
        try:
            self.thread.start()
        except Exception:
            self._staging['ready'].set()
            raise

    def _copy(self, source, target):
        stage = self._staging
        directory = None
        owned = stage['owned'] if stage is not None else {}
        terminal, message, error, output = 'FAILED', '', '', ''
        try:
            if self.cancelled.is_set():
                raise InterruptedError
            # A private directory identifies incomplete copies even after a bounded shutdown.
            directory = Path(tempfile.mkdtemp(prefix='.wfrl-video-export-', dir=target.parent))
            owned[directory] = directory.lstat()
            if stage is not None:
                stage['directory'] = directory
            if self.cancelled.is_set():
                raise InterruptedError
            marker, staging = directory / 'owner.json', directory / 'video.part'
            with marker.open('x', encoding='utf-8') as handle:
                owned[marker] = os.fstat(handle.fileno())
                json.dump(dict(source=str(source), destination=str(target)), handle)
            if self.cancelled.is_set():
                raise InterruptedError
            with source.open('rb') as incoming, staging.open('xb') as outgoing:
                owned[staging] = os.fstat(outgoing.fileno())
                if (self._source_identity is not None and
                        self._identity(os.fstat(incoming.fileno())) != self._source_identity):
                    raise ValueError('源视频在导出期间发生变化，请等待源文件写入完成后重试')
                while block := incoming.read(4 * 1024 * 1024):
                    if self.cancelled.is_set():
                        raise InterruptedError
                    outgoing.write(block)
                    self.done_bytes += len(block)
                if self.cancelled.is_set():
                    raise InterruptedError
                outgoing.flush()
                os.fsync(outgoing.fileno())
                if self._source_identity is not None and (
                        self._identity(os.fstat(incoming.fileno())) != self._source_identity
                        or self._identity(source.stat()) != self._source_identity
                        or self.done_bytes != self.total_bytes):
                    raise ValueError('源视频在导出期间发生变化，请等待源文件写入完成后重试')
            with self._state_lock:
                if self.cancelled.is_set():
                    raise InterruptedError
                self.state, self.message = 'COMMITTING', '正在提交 MP4…'
            _commit_export(staging, target)
            terminal, message, output = 'SUCCEEDED', '已导出：' + str(target), str(target)
        except InterruptedError:
            terminal, message = 'CANCELLED', '导出已取消'
        except Exception as exc:
            message = error = str(exc)
        finally:
            try:
                self._discard_staging(directory, owned)
            except OSError as exc:
                if terminal == 'SUCCEEDED':
                    message += '；暂存清理失败：' + str(exc)
                else:
                    terminal, error = 'FAILED', f'暂存清理失败：{exc}'
                    message = error
            with self._state_lock:
                self._result = terminal, message, error, output
            if stage is not None:
                stage['ready'].set()

    @classmethod
    def _discard_staging(cls, directory, owned):
        if directory is None or not cls._is_owned(directory, owned):
            return
        for path in (directory / 'video.part', directory / 'owner.json'):
            if cls._is_owned(path, owned):
                path.unlink(missing_ok=True)
        if cls._is_owned(directory, owned):
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass

    @classmethod
    def _cleanup_on_exit(cls, stage):
        stage['ready'].wait()
        if stage.get('exit'):
            try:
                cls._discard_staging(stage.get('directory'), stage['owned'].copy())
            except OSError:
                pass  # Preserve any entries whose ownership or removal could not be established.

    @staticmethod
    def _is_owned(path, owned):
        expected = owned.get(path)
        if expected is None:
            return False
        try:
            current = path.lstat()
        except FileNotFoundError:
            return False
        return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)

    def stop(self, timeout=.05):
        with self._state_lock:
            worker = self.thread
            if self._workers_active() and self._state == 'RUNNING':
                self.cancelled.set()
                self.state, self.message = 'CANCELLING', '取消已请求，等待文件 I/O 结束'
        if worker is not None:
            worker.join(timeout=max(0., timeout))
        return not self.active

    def close(self):
        self.stop(timeout=0)
        with self._state_lock:
            if self._state != 'CANCELLING' or self._exit_janitor is None:
                return
            self._staging['exit'] = True
            self._staging['ready'].set()
            janitor = self._exit_janitor
        janitor.join(timeout=.2)

    def status(self):
        self._settle()
        with self._state_lock:
            return (self._state, self._workers_active(), self.done_bytes, self.total_bytes,
                    self.error, self.output_path, self.message)


stream = globals().get('stream') or Stream()
export = globals().get('export') or Export()


def shutdown():
    stream.stop()
    export.stop(timeout=0)


def _close():
    export.close()
    stream.close()


atexit.register(_close)


def status_for(scene=None):
    """Immutable values for global file-copy and streaming consumers."""
    return (*export.status(), stream.state, stream.active, stream.message, stream.url)
