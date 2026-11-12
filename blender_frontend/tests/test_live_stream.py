import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

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


@pytest.mark.parametrize('url', ['rtsp://127.0.0.1:bad/camera', 'http://127.0.0.1:70000/camera'])
def test_check_url_rejects_invalid_ports(url):
    with pytest.raises(ValueError, match='端口'):
        live_stream.check_url(url)


def test_display_url_hides_credentials_and_query():
    assert live_stream.display_url('rtmp://user:pw@host:1935/rtp/a?sign=secret') == 'rtmp://host:1935/rtp/a'


_SECRET_URL = 'rtsp://admin:S3cretPw@127.0.0.1:9/Streaming/Channels/101?token=abc123'


@pytest.mark.parametrize(('line', 'expected'), [
    ('[in#0 @ 0x600] Error opening input: Connection refused',
     '[in#0 @ 0x600] Error opening input: Connection refused'),
    (f'Error opening input file {_SECRET_URL}.',
     'Error opening input file rtsp://127.0.0.1:9/Streaming/Channels/101.'),
    ('Error opening input files: Connection refused', 'Error opening input files: Connection refused'),
    (f'{_SECRET_URL}: Connection refused', 'rtsp://127.0.0.1:9/Streaming/Channels/101: Connection refused'),
    # FFmpeg may print a re-encoded form of the address that differs from the entered one.
    ('rtsp://admin:S3cret%40Pw@cam.local/live?token=abc123: Connection refused',
     'rtsp://cam.local/live: Connection refused'),
    ('Error opening input file https://cam.local/live.flv?token=abc123.',
     'Error opening input file https://cam.local/live.flv.'),
    ('srt://cam.local:9000?passphrase=topsecret1234: Connection timed out',
     'srt://cam.local:9000: Connection timed out'),
    ('Unable to open resource: no stream', 'Unable to open resource: no stream'),
    ('[tcp @ 0x700] Connection to tcp://127.0.0.1:9?timeout=0 failed: Connection refused',
     '[tcp @ 0x700] Connection to tcp://127.0.0.1:9?timeout=0 failed: Connection refused'),
])
def test_scrub_text_removes_credentials_and_stream_queries(line, expected):
    assert live_stream.scrub_text(line, _SECRET_URL) == expected
    assert live_stream.scrub_text(line) == expected


def test_stream_diagnostics_and_status_hide_credentials():
    line = f'{_SECRET_URL}: Connection refused'
    stream = live_stream.LiveStream(sys.executable, _SECRET_URL, (1, 1), args=[
        sys.executable, '-c', f'import sys; sys.stderr.write({line!r} + "\\n")'])
    try:
        deadline = time.monotonic() + 5
        while stream.state != 'RECONNECTING' and time.monotonic() < deadline:
            time.sleep(.025)
        diagnostics, (state, message) = stream.diagnostics(), stream.status()
    finally:
        stream.stop()
        stream.thread.join(timeout=5)
    assert state == 'RECONNECTING'
    assert diagnostics == ('rtsp://127.0.0.1:9/Streaming/Channels/101: Connection refused',)
    assert message.endswith('rtsp://127.0.0.1:9/Streaming/Channels/101: Connection refused')
    for secret in ('S3cretPw', 'abc123', 'admin'):
        assert secret not in ''.join(diagnostics) + message


def test_command_adds_tcp_only_for_rtsp_and_flips_rows():
    rtmp = live_stream.command('ffmpeg', 'rtmp://h:1935/rtp/a', (1280, 720))
    rtsp = live_stream.command('ffmpeg', 'rtsp://h/a', (1280, 720))
    assert '-rtsp_transport' not in rtmp and rtsp[rtsp.index('-rtsp_transport') + 1] == 'tcp'
    vf = rtmp[rtmp.index('-vf') + 1]
    assert vf.endswith(',vflip') and 'pad=1280:720' in vf
    assert rtmp[-5:] == ['-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1']
    assert rtmp[rtmp.index('-i') + 1] == 'rtmp://h:1935/rtp/a'


class _StreamArea(NS):
    @property
    def ui_type(self):
        return self.type

    @ui_type.setter
    def ui_type(self, value):
        self.type = self.spaces.active.type = value


class _TestSpaces(NS):
    def __iter__(self):
        return iter((self.active,))


class _TestScreen(dict):
    def __init__(self, uid, areas):
        super().__init__()
        self.name = f'Screen {uid}'
        self.session_uid, self.areas = uid, list(areas)
        self._pointer = uid + 200

    def as_pointer(self):
        return self._pointer


class _TestImage(dict):
    source, size = 'GENERATED', (1, 1)
    is_dirty = use_fake_user = False
    __hash__ = object.__hash__
    __eq__ = object.__eq__


class _TestImages(list):
    def new(self, *_args, **_kwargs):
        marker = _TestImage()
        self.append(marker)
        return marker


def _stream_area(pointer, x):
    space = NS(type='VIEW_3D', as_pointer=lambda: pointer + 100, ui_mode='VIEW', image=None)
    return _StreamArea(type='VIEW_3D', spaces=_TestSpaces(active=space), regions=[], as_pointer=lambda: pointer,
                       x=x, y=0, width=100, height=80, tag_redraw=Mock())


def _mock_reader(*, active=False):
    return NS(active=active, sequence=0, fps=0., stop=Mock(), diagnostics=lambda: (),
              status=Mock(return_value=('CONNECTING', '正在连接…')))


@pytest.fixture
def stream_view(frontend_module, stub_module, monkeypatch):
    view = frontend_module('wfrl_blender.live_stream_view')
    stub_module('wfrl_blender.manual_down', ffmpeg_executable=lambda context: 'ffmpeg')
    stub_module('wfrl_blender.manual_control', is_manual=lambda scene: True)
    worker = Mock(return_value=_mock_reader())
    monkeypatch.setattr(live_stream, 'LiveStream', worker)
    monkeypatch.setattr(view, '_view_state', lambda space: {})
    left, right = _stream_area(1, 0), _stream_area(2, 107)
    screen = _TestScreen(20, [left])
    workspace = NS(session_uid=30, screens=[screen])
    window = NS(screen=screen, workspace=workspace, as_pointer=lambda: 10)
    bpy = view.bpy
    bpy.context.window_manager.windows = [window]
    bpy.context.preferences.system.ui_scale = 1
    bpy.data.workspaces = [workspace]
    bpy.data.screens = [screen]
    bpy.data.images = _TestImages()
    bpy.data.user_map.return_value = {}
    context = NS(area=left, window=window, scene=object(), window_manager=NS(),
                 temp_override=bpy.context.temp_override)

    def split(**kwargs):
        screen.areas.append(right)
        return {'FINISHED'}

    def close():
        screen.areas.remove(right)
        return {'FINISHED'}

    def join(*, source_xy, target_xy):
        assert source_xy == (50, 40) and target_xy == (157, 40)
        target = next(area for area in window.screen.areas
                      if (area.x + area.width // 2, area.y + area.height // 2) == target_xy)
        window.screen.areas.remove(target)
        return {'FINISHED'}

    bpy.ops.wfrl.manual_down_view.return_value = {'FINISHED'}
    bpy.ops.screen.area_split.side_effect = split
    bpy.ops.screen.area_close.side_effect = close
    bpy.ops.screen.area_join.side_effect = join
    timers = set()
    bpy.app.timers.is_registered.side_effect = lambda fn: fn in timers
    bpy.app.timers.register.side_effect = lambda fn, **kwargs: timers.add(fn)
    bpy.app.timers.unregister.side_effect = timers.remove
    return view, context, worker


@pytest.mark.parametrize(('url', 'size', 'reason'), [
    ('rtsp://127.0.0.1:bad/camera', '1280x720', '端口'),
    ('http://127.0.0.1:70000/camera', '1280x720', '端口'),
    ('rtsp://127.0.0.1:554/camera', 'invalid', '尺寸'),
])
def test_invalid_start_preserves_current_stream(stream_view, url, size, reason):
    view, context, worker = stream_view
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    active = view.session().copy()
    areas = list(context.window.screen.areas)
    context.window_manager.wfrl_live_url = url
    context.window_manager.wfrl_live_size = size
    operator = view.WFRL_OT_LiveStreamStart()
    operator.turbine = 'T1'
    operator.report = Mock()

    assert operator.execute(context) == {'CANCELLED'}
    assert view.session() == active and context.window.screen.areas == areas
    assert reason in operator.report.call_args.args[1]
    active['stream'].stop.assert_not_called()
    view.bpy.ops.wfrl.manual_down_view.assert_called_once()
    view.bpy.ops.screen.area_split.assert_called_once()
    worker.assert_called_once()


def test_start_while_live_refuses_and_keeps_current_stream(stream_view):
    view, context, worker = stream_view
    view.start(context, 'rtsp://127.0.0.1:554/first')
    active = view.session().copy()
    areas = list(context.window.screen.areas)

    with pytest.raises(RuntimeError, match='请先关闭'):
        view.start(context, 'rtsp://127.0.0.1:554/second')

    assert view.session() == active and context.window.screen.areas == areas
    active['stream'].stop.assert_not_called()
    worker.assert_called_once()
    view.bpy.ops.wfrl.manual_down_view.assert_called_once()
    view.bpy.ops.screen.area_split.assert_called_once()
    assert view._HANDLE is not None and view.bpy.app.timers.is_registered(view._tick)


@pytest.mark.parametrize('stage', ['worker', 'timer'])
def test_failed_start_releases_split_and_acquired_resources(stream_view, stage):
    view, context, worker = stream_view
    bpy = view.bpy
    fail = worker if stage == 'worker' else bpy.app.timers.register
    fail.side_effect = RuntimeError(f'{stage} startup failed')

    with pytest.raises(RuntimeError, match=f'{stage} startup failed'):
        view.start(context, 'rtsp://127.0.0.1:554/camera')

    assert view.session() is None and view._HANDLE is None
    assert context.window.screen.areas == [context.area]
    bpy.ops.screen.area_join.assert_called_once()
    if stage == 'timer':
        worker.return_value.stop.assert_called_once()
        bpy.types.SpaceImageEditor.draw_handler_remove.assert_called_once_with(
            bpy.types.SpaceImageEditor.draw_handler_add.return_value, 'WINDOW')


def _show_other_workspace(view, window, areas=()):
    window.screen = _TestScreen(21, areas)
    window.workspace = NS(session_uid=31, screens=[window.screen])
    view.bpy.data.workspaces.append(window.workspace)
    view.bpy.data.screens.append(window.screen)


@pytest.mark.parametrize('repurposed', [None, 'image', 'space', 'mask'])
def test_workspace_return_cleans_only_owned_stream_editor(stream_view, repurposed):
    view, context, worker = stream_view
    window = context.window
    workspace, screen = window.workspace, window.screen
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    right = screen.areas[-1]

    unrelated = _stream_area(right.as_pointer(), right.x)
    unrelated.type = 'IMAGE_EDITOR'
    _show_other_workspace(view, window, [unrelated])
    view._tick()

    assert view.session() is None and view._HANDLE is None
    worker.return_value.stop.assert_called_once()
    assert window.screen.areas == [unrelated]
    assert unrelated.type == 'IMAGE_EDITOR'
    assert len(screen.areas) == 2 and right.type == 'IMAGE_EDITOR'

    if repurposed == 'image':
        right.spaces.active.image = object()
    elif repurposed == 'space':
        right.spaces.active = NS(type='IMAGE_EDITOR', as_pointer=lambda: 103, ui_mode='VIEW', image=None)
    elif repurposed == 'mask':
        right.spaces.active.ui_mode = 'MASK'
    window.workspace, window.screen = workspace, screen
    assert view._tick() is None
    assert screen.areas == ([context.area, right] if repurposed else [context.area])
    if repurposed:
        assert right.type == 'IMAGE_EDITOR'
    assert not view.bpy.data.images and view._LAYOUT_KEY not in screen


def test_restart_in_returning_workspace_merges_before_new_split(stream_view):
    view, context, worker = stream_view
    window = context.window
    workspace, screen = window.workspace, window.screen
    view.start(context, 'rtsp://127.0.0.1:554/first')
    first = view.session()['stream']
    _show_other_workspace(view, window)
    view._tick()
    first.stop.assert_called_once()

    window.workspace, window.screen = workspace, screen
    worker.return_value = _mock_reader()
    view.start(context, 'rtsp://127.0.0.1:554/second')
    assert len(screen.areas) == 2
    assert view._tick() == view._TICK_S
    assert view.session()['stream'] is worker.return_value
    worker.return_value.stop.assert_not_called()

    view.stop()
    assert screen.areas == [context.area]
    assert not view.bpy.app.timers.is_registered(view._tick)


def test_replacement_waits_for_reader_shutdown_and_redraws_status(stream_view):
    view, context, worker = stream_view
    reader = worker.return_value = _mock_reader(active=True)
    reader.stop.side_effect = lambda: setattr(reader.status, 'return_value', ('STOPPING', '等待进程退出'))
    view.start(context, 'rtsp://127.0.0.1:554/first')

    view.stop()
    assert context.window.screen.areas == [context.area] and view._HANDLE is None
    assert view.session()['stream'] is reader and '正在停止' in view.status()[0][1]
    with pytest.raises(RuntimeError, match='正在停止'):
        view.start(context, 'rtsp://127.0.0.1:554/second')
    worker.assert_called_once()
    assert view._tick() == view._RESTORE_S
    context.area.tag_redraw.assert_called_once()

    reader.active = False
    assert view._tick() is None
    assert view.status() == [] and context.area.tag_redraw.call_count == 2


def test_start_is_unavailable_while_a_stream_plays_or_stops(stream_view, monkeypatch):
    view, context, worker = stream_view
    operator = view.WFRL_OT_LiveStreamStart
    message = Mock()
    monkeypatch.setattr(operator, 'poll_message_set', message, raising=False)
    assert operator.poll(context)
    assert not operator.poll(NS(area=None)) and not operator.poll(NS(area=NS(type='IMAGE_EDITOR')))
    message.assert_not_called()

    reader = worker.return_value = _mock_reader(active=True)
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    assert not operator.poll(context)
    assert '正在播放' in message.call_args.args[0]

    view.stop()
    assert view.session()['stream'] is reader
    assert not operator.poll(context)
    assert '正在停止' in message.call_args.args[0]

    reader.active = False
    assert operator.poll(context) and view.session() is None
    assert message.call_count == 2


@pytest.mark.parametrize('parked', [False, True])
@pytest.mark.parametrize('repurposed', [False, True])
def test_load_preserving_ui_recovers_only_owned_stream_editor(stream_view, parked, repurposed):
    view, context, worker = stream_view
    window = context.window
    workspace, screen = window.workspace, window.screen
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    view._tick()
    right = screen.areas[-1]
    if parked:
        _show_other_workspace(view, window)
        view._tick()

    view._on_load_pre()
    # Blender keeps the screen and editor but remaps references to the old file's images.
    screen.session_uid += 100
    replacement = object() if repurposed else None
    right.spaces.active.image = replacement
    view.bpy.data.images.clear()
    view._on_load_post()
    if parked:
        assert view._tick() == view._RESTORE_S
        window.workspace, window.screen = workspace, screen
    assert view._tick() is None

    assert screen.areas == ([context.area, right] if repurposed else [context.area])
    if repurposed:
        assert right.spaces.active.image is replacement
    assert view._LAYOUT_KEY not in screen
    worker.return_value.stop.assert_called_once()


@pytest.mark.parametrize('changed_topology', [False, True])
def test_saved_layout_uses_new_identities_and_preserves_unrelated_areas(stream_view, changed_topology):
    view, context, _worker = stream_view
    window = context.window
    screen = window.screen
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    marker_key = view._LAYOUT_KEY
    saved = dict(screen[marker_key])
    view._on_load_pre()

    left, right = _stream_area(110, 0), _stream_area(120, 107)
    right.ui_type = 'IMAGE_EDITOR'
    reopened = _TestScreen(121, [left, right])
    reopened.name = screen.name
    reopened[marker_key] = saved
    window.screen = reopened
    window.workspace = NS(session_uid=131, screens=[reopened])
    view.bpy.data.screens = [reopened]
    view.bpy.data.images.clear()
    marker = view.bpy.data.images.new('Reloaded marker')
    marker[marker_key] = saved['token']
    right.spaces.active.image = marker
    if changed_topology:
        unrelated = _stream_area(130, 214)
        unrelated.ui_type = 'IMAGE_EDITOR'
        unrelated.spaces.active.image = object()
        reopened.areas.insert(0, unrelated)

    view._on_load_post()
    assert view._tick() is None
    if changed_topology:
        assert reopened.areas == [unrelated, left, right]
        assert unrelated.type == 'IMAGE_EDITOR' and unrelated.spaces.active.image is not None
        assert right.type == 'VIEW_3D' and right.spaces.active.image is None
    else:
        assert reopened.areas == [left]
    assert marker_key not in reopened and not view.bpy.data.images


@pytest.mark.parametrize('teardown', ['load', 'unregister'])
def test_teardown_removes_timer_but_preserves_saved_layout(stream_view, monkeypatch, teardown):
    view, context, _worker = stream_view
    window = context.window
    workspace, screen = window.workspace, window.screen
    view.start(context, 'rtsp://127.0.0.1:554/camera')
    _show_other_workspace(view, window)
    assert view._tick() == view._RESTORE_S
    assert view.bpy.app.timers.is_registered(view._tick)

    if teardown == 'load':
        view._on_load_pre()
    else:
        for cls in view.CLASSES:
            monkeypatch.setattr(cls, 'is_registered', False, raising=False)
        view.unregister()

    assert not view.bpy.app.timers.is_registered(view._tick)
    assert view._LAYOUT_KEY in screen
    window.workspace, window.screen = workspace, screen
    assert view._tick() is None
    assert screen.areas == [context.area]


def _source_args(size, seconds):
    # A synthetic stream through the same decode/scale/letterbox chain as a real URL.
    width, height = size
    return [FFMPEG, '-hide_banner', '-loglevel', 'error', '-nostdin', '-re',
            '-f', 'lavfi', '-i', f'testsrc=size=640x480:rate=25:duration={seconds}',
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
                   f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,vflip',
            '-pix_fmt', 'rgba', '-f', 'rawvideo', 'pipe:1']


@needs_ffmpeg
@pytest.mark.skipif(not hasattr(signal, 'SIGSTOP'), reason='requires POSIX process suspension')
def test_stop_reaps_suspended_ffmpeg_without_waiting_on_stdout():
    size = (64, 48)
    stream = live_stream.LiveStream(FFMPEG, 'test', size, args=_source_args(size, 30))
    try:
        deadline = time.monotonic() + 5
        while not stream.sequence and time.monotonic() < deadline:
            time.sleep(.025)
        assert stream.sequence
        process = stream.process
        os.kill(process.pid, signal.SIGSTOP)
        started = time.monotonic()
        stream.stop()
        assert time.monotonic() - started < 1
        assert stream.state == 'STOPPING' and stream.active
        stream.thread.join(timeout=4)
        assert process.poll() == -signal.SIGKILL
        assert not stream.active and stream.state == 'STOPPED'
    finally:
        if stream.process is not None and stream.process.poll() is None:
            stream.process.kill()
        stream.stop()
        stream.thread.join(timeout=5)


@pytest.mark.skipif(not hasattr(signal, 'SIGSTOP'), reason='requires POSIX process suspension')
@pytest.mark.parametrize('mode', ['active', 'stopping', 'spawning'])
def test_interpreter_exit_kills_and_reaps_owned_process(mode):
    script = '''
import atexit
import os
import signal
import subprocess
import sys
import time

process = None

def report():
    print(process.returncode, flush=True)
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)

atexit.register(report)
from wfrl_blender import live_stream

mode = sys.argv[1]
popen = subprocess.Popen

def suspend_after_spawn(*args, **kwargs):
    global process
    process = popen(*args, **kwargs)
    os.kill(process.pid, signal.SIGSTOP)
    if mode == 'spawning':
        time.sleep(.2)
    return process

live_stream.subprocess.Popen = suspend_after_spawn
stream = live_stream.LiveStream(sys.executable, 'test', (1, 1),
                                args=[sys.executable, '-c', 'import time; time.sleep(30)'])
deadline = time.monotonic() + 5
while not (process if mode == 'spawning' else stream.process):
    assert time.monotonic() < deadline
    time.sleep(.005)
if mode == 'stopping':
    stream.stop()
'''
    env = os.environ | {'PYTHONPATH': str(Path(live_stream.__file__).resolve().parents[1]),
                        'PYTHONDONTWRITEBYTECODE': '1'}
    result = subprocess.run([sys.executable, '-c', script, mode], env=env, capture_output=True,
                            encoding='utf-8', timeout=10, check=True)
    assert result.stdout.strip() == str(-signal.SIGKILL), result.stderr


def test_stop_during_spawn_keeps_ownership_until_new_process_is_reaped(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    popen = subprocess.Popen

    def delayed_spawn(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=10)
        return popen(*args, **kwargs)

    monkeypatch.setattr(live_stream.subprocess, 'Popen', delayed_spawn)
    stream = live_stream.LiveStream(sys.executable, 'test', (1, 1),
                                    args=[sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        assert entered.wait(timeout=5)
        started = time.monotonic()
        stream.stop()
        assert time.monotonic() - started < 1
        assert stream.state == 'STOPPING' and stream.active and stream.process is None
        release.set()
        stream.thread.join(timeout=5)
        assert stream.process.poll() is not None
        assert stream.state == 'STOPPED' and not stream.active
    finally:
        release.set()
        stream.stop()
        stream.thread.join(timeout=5)


def test_read_failure_reaps_live_process(monkeypatch):
    def fail(_pipe, _size):
        raise OSError('injected read failure')

    monkeypatch.setattr(live_stream, '_read_exact', fail)
    stream = live_stream.LiveStream(sys.executable, 'test', (1, 1),
                                    args=[sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        stream.thread.join(timeout=5)
        assert stream.state == 'ERROR' and 'injected read failure' in stream.message
        assert stream.process.poll() is not None and not stream.active
    finally:
        stream.stop()
        stream.thread.join(timeout=5)


def test_stop_retains_ownership_until_stderr_reader_exits(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def delayed_read(_self, _pipe):
        entered.set()
        assert release.wait(timeout=10)

    monkeypatch.setattr(live_stream.LiveStream, '_drain', delayed_read)
    monkeypatch.setattr(live_stream, '_KILL_S', .1)
    stream = live_stream.LiveStream(sys.executable, 'test', (1, 1),
                                    args=[sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        assert entered.wait(timeout=5)
        stream.stop()
        deadline = time.monotonic() + 5
        while '线程未退出' not in stream.message and time.monotonic() < deadline:
            time.sleep(.025)
        assert stream.process.poll() is not None
        assert stream.state == 'STOPPING' and stream.active and '线程未退出' in stream.message
    finally:
        release.set()
        stream.stop()
        stream.thread.join(timeout=5)
    assert not stream.active and stream.state == 'STOPPED'


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
    assert time.monotonic() - started < 1
    stream.thread.join(timeout=5)
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
        stream.thread.join(timeout=5)
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
            stream.thread.join(timeout=5)
    finally:
        server.kill()
        server.wait(timeout=5)
