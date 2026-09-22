"""File export and owned RTSP processes, independent of Blender's UI thread."""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import subprocess
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
    handle.seek(max(0, size - 8192))
    text = handle.read().decode('utf-8', errors='replace')
    if size > 1024 * 1024:
        handle.seek(0); handle.truncate()
    return text


class Stream:
    def __init__(self):
        self.server = self.publisher = None
        self.directory = None
        self.logs = []
        self.state = 'STOPPED'
        self.message = 'RTSP 未启动'
        self.url = ''

    @property
    def active(self):
        return self.server is not None

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
        config_path.write_text(config, encoding='utf-8')
        self.logs = [tempfile.TemporaryFile(mode='w+b') for _ in range(2)]
        try:
            self.server = _spawn([mediamtx, str(config_path)], self.logs[0])
        except BaseException:
            self.stop()
            raise
        self.state, self.message = 'STARTING', '正在启动 RTSP…'
        self.deadline = time.monotonic() + 15

    def poll(self):
        if not self.active:
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
        self.stop()
        self.state, self.message = 'ERROR', message

    def stop(self):
        for process in (self.publisher, self.server):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2)
        self.publisher = self.server = None
        for log in self.logs:
            log.close()
        self.logs = []
        if self.directory:
            self.directory.cleanup()
            self.directory = None
        self.state, self.message = 'STOPPED', 'RTSP 已停止'


class Export:
    def __init__(self):
        self.thread = None
        self.cancelled = threading.Event()
        self.done_bytes = self.total_bytes = 0
        self.message = '导出所选视频为 MP4'
        self.error = ''

    @property
    def active(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, video, output):
        if self.active:
            raise RuntimeError('已有视频正在导出')
        source, target = video_path(video), Path(output).expanduser().resolve()
        if target.suffix.lower() != '.mp4':
            raise ValueError('输出文件必须使用 .mp4 后缀')
        if target == source or target.exists():
            raise FileExistsError('目标文件已存在，请另选文件名；不会覆盖源视频')
        if not target.parent.is_dir():
            raise ValueError('输出目录不存在')
        self.cancelled.clear()
        self.done_bytes, self.total_bytes = 0, source.stat().st_size
        self.message, self.error = '正在导出 MP4…', ''
        self.thread = threading.Thread(target=self._copy, args=(source, target), daemon=True)
        self.thread.start()

    def _copy(self, source, target):
        owned = False
        try:
            with source.open('rb') as incoming, target.open('xb') as outgoing:
                owned = True
                while block := incoming.read(4 * 1024 * 1024):
                    if self.cancelled.is_set():
                        raise InterruptedError('导出已取消')
                    outgoing.write(block)
                    self.done_bytes += len(block)
            if self.cancelled.is_set():
                raise InterruptedError('导出已取消')
            self.message = '已导出：' + str(target)
        except Exception as exc:
            if owned:
                target.unlink(missing_ok=True)
            self.error = str(exc)
            self.message = self.error

    def stop(self):
        self.cancelled.set()
        if self.thread:
            self.thread.join()


stream = Stream()
export = Export()


def shutdown():
    stream.stop()
    export.stop()


atexit.register(shutdown)
