import shutil
import socket
import subprocess
import sys
import time

import pytest

from wfrl_blender import live_stream

FFMPEG = shutil.which('ffmpeg')
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason='FFmpeg not on PATH')


def test_check_url_accepts_stream_schemes_and_rejects_missing_stream_name():
    assert live_stream.check_url('  rtmp://wvp.cluing.com:1935/rtp/3402_01  ') == \
        'rtmp://wvp.cluing.com:1935/rtp/3402_01'
    assert live_stream.check_url('rtsp://10.0.0.5:554/live') == 'rtsp://10.0.0.5:554/live'
    with pytest.raises(ValueError, match='流名称'):
        live_stream.check_url('rtmp://wvp.cluing.com:1935/rtp/')
    with pytest.raises(ValueError):
        live_stream.check_url('file:///C:/video.mp4')
    with pytest.raises(ValueError):
        live_stream.check_url('rtmp:///rtp/x')


def test_display_url_hides_credentials_and_query():
    assert live_stream.display_url('rtmp://user:pw@host:1935/rtp/a?sign=secret') == 'rtmp://host:1935/rtp/a'


def test_command_adds_tcp_only_for_rtsp_and_flips_rows():
    rtmp = live_stream.command('ffmpeg', 'rtmp://h:1935/rtp/a', (1280, 720))
    rtsp = live_stream.command('ffmpeg', 'rtsp://h/a', (1280, 720))
    assert '-rtsp_transport' not in rtmp and rtsp[rtsp.index('-rtsp_transport') + 1] == 'tcp'
    vf = rtmp[rtmp.index('-vf') + 1]
    assert vf.endswith(',vflip') and 'pad=1280:720' in vf
    assert rtmp[-5:] == ['-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1']
    assert rtmp[rtmp.index('-i') + 1] == 'rtmp://h:1935/rtp/a'


def _source_args(size, seconds):
    # A synthetic stream through the same decode/scale/letterbox chain as a real URL.
    width, height = size
    return [FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-re',
            '-f', 'lavfi', '-i', f'testsrc=size=640x480:rate=25:duration={seconds}',
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                   f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,vflip',
            '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1']


@needs_ffmpeg
def test_reader_keeps_newest_frame_reconnects_and_stops():
    size = (320, 180)
    stream = live_stream.LiveStream(FFMPEG, 'rtmp://local/test/x', size, args=_source_args(size, 3))
    try:
        deadline = time.monotonic() + 10
        while stream.state != 'LIVE' and time.monotonic() < deadline:
            time.sleep(.05)
        assert stream.state == 'LIVE'
        # FFmpeg's -re passes an initial burst; the 30-frame rate window has
        # left it after about 1.2 s of 25 fps input.
        time.sleep(1.6)
        sequence, frame = stream.latest()
        assert sequence >= 5 and frame.dtype.name == 'float32' and frame.size == 320 * 180 * 4
        assert 15 < stream.fps < 35, stream.fps
        # 640x480 into 320x180 keeps 4:3, so the left and right bars are black.
        assert frame[:4].tolist() == [0.0, 0.0, 0.0, 1.0]
        assert 0.0 <= frame.min() and frame.max() <= 1.0 and frame.max() > 0.5
        # The 3 s source ends: the reader reports the drop and reconnects.
        deadline = time.monotonic() + 10
        while stream.state != 'RECONNECTING' and time.monotonic() < deadline:
            time.sleep(.05)
        assert stream.state == 'RECONNECTING'
        deadline = time.monotonic() + 10
        before = stream.sequence
        while stream.sequence <= before and time.monotonic() < deadline:
            time.sleep(.05)
        assert stream.sequence > before, 'no frames after reconnect'
    finally:
        started = time.monotonic()
        stream.stop()
    assert time.monotonic() - started < 5
    assert stream.state == 'STOPPED' and stream.latest()[1] is None
    assert stream.process.poll() is not None


@needs_ffmpeg
def test_unreachable_stream_reports_reconnecting_with_reason():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'rtmp://127.0.0.1:{port}/rtp/none'
    stream = live_stream.LiveStream(FFMPEG, url, (320, 180))
    try:
        deadline = time.monotonic() + 15
        while stream.state != 'RECONNECTING' and time.monotonic() < deadline:
            time.sleep(.05)
        assert stream.state == 'RECONNECTING' and stream.sequence == 0
        assert '连接中断' in stream.message
    finally:
        stream.stop()
    assert stream.state == 'STOPPED'


@needs_ffmpeg
def test_end_to_end_rtmp_from_local_server():
    """FFmpeg serves RTMP on localhost; the reader pulls it like a field camera."""
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    url = f'rtmp://127.0.0.1:{port}/live/cam'
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    server = subprocess.Popen(
        [FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-re', '-f', 'lavfi',
         '-i', 'testsrc=size=1280x720:rate=25', '-c:v', 'libx264', '-preset', 'ultrafast',
         '-tune', 'zerolatency', '-g', '25', '-f', 'flv', '-listen', '1', url],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
    try:
        time.sleep(1.0)
        stream = live_stream.LiveStream(FFMPEG, url, (640, 360))
        try:
            deadline = time.monotonic() + 15
            while stream.sequence < 25 and time.monotonic() < deadline:
                time.sleep(.05)
            assert stream.state == 'LIVE' and stream.sequence >= 25, (stream.state, stream.message,
                                                                       list(stream.errors))
            assert stream.latest()[1].size == 640 * 360 * 4
        finally:
            stream.stop()
    finally:
        server.kill()
        server.wait(timeout=5)
