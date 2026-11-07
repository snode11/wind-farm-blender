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

from collections import deque
import os
import subprocess
import threading
import time
from urllib.parse import urlsplit

import numpy as np

SCHEMES = ('rtmp', 'rtmps', 'rtsp', 'rtsps', 'http', 'https', 'srt')
SIZES = {'1280x720': (1280, 720), '1920x1080': (1920, 1080), '960x540': (960, 540)}
RECONNECT_S = 3.0
TIMEOUT_US = 10_000_000


def check_url(url):
    """Return the stripped stream URL or raise ValueError with a user-facing reason."""
    url = (url or '').strip()
    parts = urlsplit(url)
    if parts.scheme.lower() not in SCHEMES:
        raise ValueError('流地址须以 ' + ' / '.join(s + '://' for s in SCHEMES) + ' 开头')
    if not parts.hostname:
        raise ValueError('流地址缺少主机名')
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


class LiveStream:
    """One FFmpeg reader that reconnects after a drop until stopped.

    ``state`` is CONNECTING, LIVE, RECONNECTING, STOPPED or ERROR; ``frame`` is
    the newest bottom-up RGBA frame as a flat float32 array in 0..1 and
    ``sequence`` counts decoded frames."""

    def __init__(self, ffmpeg, url, size=SIZES['1280x720'], *, args=None):
        self.url, self.size = url, tuple(size)
        self.args = args or command(ffmpeg, url, self.size)
        self.state, self.message = 'CONNECTING', '正在连接…'
        self.frame, self.sequence, self.fps = None, 0, 0.0
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.process = None
        self.errors = deque(maxlen=20)
        self.thread = threading.Thread(target=self._run, name='wfrl-live-stream', daemon=True)
        self.thread.start()

    def latest(self):
        with self.lock:
            return self.sequence, self.frame

    def _drain(self, pipe):
        for line in iter(pipe.readline, b''):
            self.errors.append(line.decode('utf-8', errors='replace').strip())

    def _run(self):
        frame_bytes = self.size[0] * self.size[1] * 4
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        while not self.stopping.is_set():
            try:
                process = subprocess.Popen(self.args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, bufsize=0, **options)
            except OSError as exc:
                self.state, self.message = 'ERROR', f'无法启动 FFmpeg：{exc}'
                return
            self.process = process
            threading.Thread(target=self._drain, args=(process.stderr,), daemon=True).start()
            window = deque(maxlen=30)
            while not self.stopping.is_set():
                data = _read_exact(process.stdout, frame_bytes)
                if data is None:
                    break
                pixels = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
                pixels *= 1.0 / 255.0
                now = time.monotonic()
                window.append(now)
                with self.lock:
                    self.frame, self.sequence = pixels, self.sequence + 1
                self.fps = (len(window) - 1) / (now - window[0]) if len(window) > 1 and now > window[0] else 0.0
                self.state, self.message = 'LIVE', '正在播放'
            self._end(process)
            if self.stopping.is_set():
                break
            detail = self.errors[-1] if self.errors else f'FFmpeg 退出码 {process.returncode}'
            self.state, self.message = 'RECONNECTING', f'连接中断，{RECONNECT_S:g} s 后重连：{detail}'
            self.fps = 0.0
            self.stopping.wait(RECONNECT_S)
        self.state, self.message = 'STOPPED', '已停止'

    @staticmethod
    def _end(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except OSError:
                pass

    def stop(self):
        self.stopping.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
        self.thread.join(timeout=5)
        with self.lock:
            self.frame = None
