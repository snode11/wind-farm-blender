"""Pull a live field-camera stream with FFmpeg and keep only the newest frame.

FFmpeg connects to the stream on a worker thread, decodes it, letterboxes it to
a fixed RGBA size and writes raw frames to a pipe. The worker keeps only the
latest complete frame, so a display that redraws slower than the stream shows
the current picture instead of accumulating delay. No Blender imports: the
view module uploads frames and draws them. Frames are converted to float
RGBA on the worker thread, since Blender textures take only float initial data
and the conversion would otherwise run on the UI thread.
"""
from __future__ import annotations

import atexit
from collections import deque
import os
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import numpy as np

SCHEMES = ('rtmp', 'rtmps', 'rtsp', 'rtsps', 'http', 'https', 'srt')
SIZES = {'1280x720': (1280, 720), '1920x1080': (1920, 1080), '960x540': (960, 540)}
RECONNECT_S = 3.0
TIMEOUT_US = 10_000_000
_CHECK_S = .05
_TERMINATE_S = .35
_KILL_S = 1.0
_PROCESS_LOCK = globals().get('_PROCESS_LOCK') or threading.RLock()
_PROCESSES = globals().get('_PROCESSES', set())
_EXITING = globals().get('_EXITING', False)


def check_url(url):
    """Return the stripped stream URL or raise ValueError with a user-facing reason."""
    url = (url or '').strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValueError('流地址格式无效') from None
    if parts.scheme.lower() not in SCHEMES:
        raise ValueError('流地址须以 ' + ' / '.join(s + '://' for s in SCHEMES) + ' 开头')
    if not parts.hostname:
        raise ValueError('流地址缺少主机名')
    try:
        _ = parts.port
    except ValueError:
        raise ValueError('流地址端口无效') from None
    if parts.scheme.lower().startswith('rtmp') and len([p for p in parts.path.split('/') if p]) < 2:
        raise ValueError('RTMP 地址缺少流名称，例如 rtmp://主机:1935/rtp/设备ID_通道ID')
    return url


def display_url(url):
    """URL without user info or query, for on-screen labels."""
    parts = urlsplit(url)
    host = parts.hostname or ''
    if parts.port:
        host += f':{parts.port}'
    return f'{parts.scheme}://{host}{parts.path}'


_URL_IN_TEXT = re.compile(r'''(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<netloc>[^\s/?#'"]*)'''
                          r'''(?P<path>[^\s?#'"]*)(?P<tail>[?#][^\s'"]*)?''')
_TRAILING_PUNCTUATION = re.compile(r'[.,:;!)\]]*$')


def scrub_text(text, url=''):
    """``text`` with ``url`` shown as ``display_url``, user info removed from every URL
    and the query dropped from stream URLs, for diagnostics shown on screen."""
    if '://' in url:
        try:
            text = text.replace(url, display_url(url))
        except ValueError:
            pass

    def scrub(match):
        tail = match['tail'] or ''
        if match['scheme'].lower() in SCHEMES:
            # FFmpeg ends sentences right after the URL; keep that punctuation.
            tail = tail[_TRAILING_PUNCTUATION.search(tail).start():]
        return f"{match['scheme']}://{match['netloc'].rpartition('@')[2]}{match['path']}{tail}"

    return _URL_IN_TEXT.sub(scrub, text)


def command(ffmpeg, url, size):
    width, height = size
    scheme = urlsplit(url).scheme.lower()
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-fflags', 'nobuffer', '-flags', 'low_delay', '-rw_timeout', str(TIMEOUT_US)]
    if scheme in ('rtsp', 'rtsps'):
        args += ['-rtsp_transport', 'tcp']
    # Rows are flipped so the bottom-up GPU texture shows the image upright.
    video_filter = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                    f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,vflip')
    return args + ['-i', url, '-map', '0:v:0', '-an', '-vf', video_filter,
                   '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1']


def _read_exact(pipe, count):
    data = bytearray(count)
    view = memoryview(data)
    done = 0
    while done < count:
        got = pipe.readinto(view[done:])
        if not got:
            return None
        done += got
    return data


def _spawn(args, options):
    # Publish the owned child before exit cleanup can pass the spawn gate.
    with _PROCESS_LOCK:
        if _EXITING:
            return None
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, bufsize=0, **options)
        _PROCESSES.add(process)
        return process


def _process_running(process):
    if process is None:
        return False
    with _PROCESS_LOCK:
        try:
            if process.poll() is None:
                return True
        except OSError:
            return True
        _PROCESSES.discard(process)
        return False


def _signal_and_wait(process, signal, timeout):
    try:
        signal()
    except OSError:
        pass
    try:
        process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _close_processes_at_exit():
    global _EXITING
    _EXITING = True
    # A daemon reader may be blocked in a pipe read. Reap the child here using
    # the same lock that protects spawn and the worker's process control.
    with _PROCESS_LOCK:
        for process in tuple(_PROCESSES):
            if _process_running(process):
                _signal_and_wait(process, process.kill, _KILL_S)
            if _process_running(process):
                print(f'WFRL could not reap live FFmpeg process {process.pid} during exit.', file=sys.__stderr__)


_previous_exit_callback = globals().get('_EXIT_CALLBACK')
if _previous_exit_callback is not None:
    atexit.unregister(_previous_exit_callback)
_EXIT_CALLBACK = atexit.register(_close_processes_at_exit)


class LiveStream:
    """Supervise FFmpeg and its pipe readers until they have all exited.

    ``stop`` requests shutdown without waiting. ``active`` includes spawn,
    reconnect and shutdown, including any reader that still owns a pipe.
    ``frame`` is the newest bottom-up RGBA frame as flat float32 in 0..1."""

    def __init__(self, ffmpeg, url, size=SIZES['1280x720'], *, args=None):
        self.url, self.size = url, tuple(size)
        self.args = args or command(ffmpeg, url, self.size)
        self._status = ('CONNECTING', '正在连接…')
        self._cleanup_error = self._read_error = ''
        self.frame, self.sequence, self.fps = None, 0, 0.0
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.process = None
        self.errors = deque(maxlen=20)
        self._readers = []
        self.thread = threading.Thread(target=self._run, name='wfrl-live-stream', daemon=True)
        self.thread.start()

    def latest(self):
        with self.lock:
            return self.sequence, self.frame

    def diagnostics(self):
        with self.lock:
            return tuple(self.errors)

    @property
    def active(self):
        return (self.thread.is_alive() or _process_running(self.process)
                or any(reader.is_alive() for reader in self._readers))

    def status(self):
        if self.stopping.is_set():
            if self.active:
                return 'STOPPING', self._cleanup_error or '正在停止现场视频…'
            return 'STOPPED', '已停止'
        return ('ERROR', self._cleanup_error) if self._cleanup_error else self._status

    @property
    def state(self):
        return self.status()[0]

    @property
    def message(self):
        return self.status()[1]

    def _drain(self, pipe):
        for line in iter(pipe.readline, b''):
            # FFmpeg names the input URL in its errors; keep only the on-screen form.
            text = scrub_text(line.decode('utf-8', errors='replace').strip(), self.url)
            with self.lock:
                self.errors.append(text)

    def _frames(self, pipe):
        frame_bytes = self.size[0] * self.size[1] * 4
        window = deque(maxlen=30)
        while not self.stopping.is_set():
            data = _read_exact(pipe, frame_bytes)
            if data is None:
                return
            pixels = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
            pixels *= 1.0 / 255.0
            now = time.monotonic()
            window.append(now)
            with self.lock:
                if self.stopping.is_set():
                    return
                self.frame, self.sequence = pixels, self.sequence + 1
                self.fps = (len(window) - 1) / (now - window[0]) if len(window) > 1 and now > window[0] else 0.0
                self._status = ('LIVE', '正在播放')

    def _read_pipe(self, read, pipe):
        try:
            read(pipe)
        except Exception as exc:
            self._read_error = scrub_text(f'读取 FFmpeg 管道失败：{exc}', self.url)

    def _run(self):
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        while not self.stopping.is_set():
            try:
                process = _spawn(self.args, options)
            except Exception as exc:
                self._status = ('ERROR', scrub_text(f'无法启动 FFmpeg：{exc}', self.url))
                return
            if process is None:
                return
            self.process = process
            # Errors describe this connection attempt only; a reconnect must not report an old one.
            with self.lock:
                self.errors.clear()
            self._read_attempt(process)
            if self._read_error:
                self._status = ('ERROR', self._read_error)
                return
            if self.stopping.is_set():
                return
            errors = self.diagnostics()
            detail = errors[-1] if errors else f'FFmpeg 退出码 {process.returncode}'
            self._status = ('RECONNECTING', f'连接中断，{RECONNECT_S:g} s 后重连：{detail}')
            self.fps = 0.0
            self.stopping.wait(RECONNECT_S)

    def _read_attempt(self, process):
        self._readers = []
        try:
            if not self.stopping.is_set() and not _EXITING:
                for read, pipe, name in ((self._frames, process.stdout, 'frames'),
                                         (self._drain, process.stderr, 'stderr')):
                    reader = threading.Thread(target=self._read_pipe, args=(read, pipe),
                                              name=f'wfrl-live-{name}', daemon=True)
                    self._readers.append(reader)
                    reader.start()
                while self._readers[0].is_alive() and not self._read_error:
                    if self.stopping.wait(_CHECK_S):
                        break
        except Exception as exc:
            self._read_error = scrub_text(f'无法启动 FFmpeg 管道读取：{exc}', self.url)
        finally:
            self._end(process)

    def _end(self, process):
        for signal, timeout in ((process.terminate, _TERMINATE_S), (process.kill, _KILL_S)):
            with _PROCESS_LOCK:
                if _EXITING or not _process_running(process):
                    break
                _signal_and_wait(process, signal, timeout)
        while _process_running(process):
            self._cleanup_error = 'FFmpeg 进程未退出，正在等待回收。'
            time.sleep(_CHECK_S)

        deadline = time.monotonic() + _KILL_S
        for reader in self._readers:
            while reader.is_alive():
                reader.join(timeout=_CHECK_S)
                if time.monotonic() >= deadline:
                    self._cleanup_error = 'FFmpeg 读取线程未退出，正在等待回收。'
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        self._cleanup_error = ''

    def stop(self):
        """Request shutdown and drop the visible frame."""
        self.stopping.set()
        with self.lock:
            self.frame = None
