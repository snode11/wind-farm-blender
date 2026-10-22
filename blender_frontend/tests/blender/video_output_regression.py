"""Installed Blender UI operators: MP4 bytes, real RTSP, view state and cleanup.

Requires WFRL_TEST_OUTPUT, WFRL_VIDEO_SOURCE, WFRL_FFMPEG and WFRL_MEDIAMTX.
Runs against the installed extension, never a source-checkout substitute.
"""
from pathlib import Path
import hashlib
import importlib
import json
import os
import socket
import subprocess
import time

import bpy

MODULE = 'bl_ext.user_default.wfrl_blender'
if MODULE not in bpy.context.preferences.addons:
    bpy.ops.preferences.addon_enable(module=MODULE)
addon = importlib.import_module(MODULE)
service = importlib.import_module(MODULE + '.video_output')
panel = importlib.import_module(MODULE + '.panels.video_output')
prefs = bpy.context.preferences.addons[MODULE].preferences
out = Path(os.environ['WFRL_TEST_OUTPUT']); out.mkdir(parents=True, exist_ok=True)
source = Path(os.environ['WFRL_VIDEO_SOURCE']).resolve()
prefs.camera_video_file = str(source)
prefs.video_ffmpeg = os.environ['WFRL_FFMPEG']
prefs.video_mediamtx = os.environ['WFRL_MEDIAMTX']
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0)); prefs.video_rtsp_port = sock.getsockname()[1]
addon.load_demo_scene()
scene = bpy.context.scene
scene.wfrl_farm_panel_page = 'VIDEO'
camera, frame, matrix = scene.camera, scene.frame_current, scene.camera.matrix_world.copy()


def wait_until(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, service.stream.message
        panel._tick()
        time.sleep(.05)


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


try:
    target = out / '离线导出.mp4'
    assert bpy.ops.wfrl.video_export('EXEC_DEFAULT', filepath=str(target)) == {'FINISHED'}
    wait_until(lambda: not service.export.active)
    assert not service.export.error, service.export.error
    assert digest(target) == digest(source)
    assert scene.camera == camera and scene.frame_current == frame and scene.camera.matrix_world == matrix
    assert bpy.ops.wfrl.video_stream() == {'FINISHED'}
    wait_until(lambda: service.stream.state != 'STARTING')
    assert service.stream.state == 'RUNNING', service.stream.message
    if not bpy.app.background:
        assert bpy.ops.wfrl.video_copy_url() == {'FINISHED'}
        assert bpy.context.window_manager.clipboard == service.stream.url
    receivers = []
    for count in (40, 20):
        recording = out / f'received-{count}.mp4'
        subprocess.run([prefs.video_ffmpeg, '-v', 'error', '-xerror', '-rtsp_transport', 'tcp',
                        '-i', service.stream.url, '-frames:v', str(count), '-an', '-c:v', 'copy', str(recording)],
                       check=True, capture_output=True, timeout=30)
        subprocess.run([prefs.video_ffmpeg, '-v', 'error', '-xerror', '-i', str(recording),
                        '-f', 'null', '-'], check=True, capture_output=True, timeout=30)
        receivers.append(count)
    server, publisher = service.stream.server, service.stream.publisher
    assert bpy.ops.wfrl.video_stop() == {'FINISHED'}
    assert server.poll() is not None and publisher.poll() is not None
    assert not service.stream.active
    assert scene.camera == camera and scene.frame_current == frame and scene.camera.matrix_world == matrix
    assert bpy.ops.wfrl.video_stream() == {'FINISHED'}
    wait_until(lambda: service.stream.state != 'STARTING')
    assert service.stream.state == 'RUNNING'
    server, publisher = service.stream.server, service.stream.publisher
    panel._before_load(None)
    assert server.poll() is not None and publisher.poll() is not None
    # Registration includes the original frontend and the new output page.
    for name in ('wfrl.farm_flex_view', 'wfrl.clearance_playback', 'wfrl.gimbal_preset',
                 'wfrl.video_export', 'wfrl.video_stream', 'wfrl.video_stop'):
        category, operator = name.split('.')
        getattr(getattr(bpy.ops, category), operator).get_rna_type()
    report = {'status': 'PASS', 'installed_module': addon.__file__, 'blender': bpy.app.version_string,
              'export_bytes_match': True, 'source_sha256': digest(source),
              'actual_rtsp_recordings_decoded': receivers, 'reconnect': True,
              'stop_owned_processes': True, 'stop_on_file_load': True,
              'camera_and_replay_unchanged': True, 'other_frontend_operators_registered': True,
              'windows_runtime_verified': False}
    (out / 'checks.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('VIDEO_OUTPUT_PASS', json.dumps(report, ensure_ascii=False))
finally:
    panel.unregister()
