"""Down export timing, decoded media and exclusive pair publication."""
import ast
from fractions import Fraction
import json
import math
import os
import queue
from pathlib import Path
import shutil
from types import SimpleNamespace
import subprocess
import tempfile
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from blender_frontend.tests._platform import symlink_or_skip

SOURCE = Path(__file__).resolve().parents[1] / 'wfrl_blender/manual_down.py'
WAIT_TIMEOUT = 60


def helpers():
    names = {'pose_at', '_output_paths', '_publish_pair', '_verify_video', '_plan', '_frame_handler',
             '_publish_async', '_finalize_files', '_finish'}
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    namespace = dict(Path=Path, Fraction=Fraction, json=json, math=math, os=os,
                     shutil=shutil, tempfile=tempfile, subprocess=subprocess, queue=queue, threading=threading)
    from wfrl_blender.manual_motion import pitch_at
    namespace['pitch_at'] = pitch_at
    exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                 and node.name in names], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace


@pytest.fixture
def blade_labels():
    names = {'_stations', 'current_blade', 'update_labels', '_set_hidden'}
    source = SOURCE.with_name('manual_down_camera.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    down = SimpleNamespace(_STATIONS={}, LABEL_SPAN=.5)
    namespace = dict(_down=down, np=np, Vector=tuple, math=math, DEFAULT_MODEL='test',
                     manual_control=SimpleNamespace(is_manual=lambda scene: True,
                                                    other_scene_objects=lambda scene: set()))
    exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef)
                                 and node.name in names], type_ignores=[]), str(source), 'exec'), namespace)
    for name in names:
        setattr(down, name, namespace[name])

    class Vertices(list):
        def foreach_get(self, attribute, values):
            assert attribute == 'co'
            values[:] = np.asarray(self).ravel()

    vertices = Vertices([(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)])
    mesh = SimpleNamespace(vertices=vertices, as_pointer=lambda: 7,
                           get=lambda key, default=None: default)
    entry = SimpleNamespace(**{f'blade{index}_object': SimpleNamespace(data=mesh)
                               for index in range(1, 4)})
    camera = dict(wfrl_turbine='T1', wfrl_label_aspect=1.)
    scene = SimpleNamespace(wfrl_manual_blade_labels=True,
                            render=SimpleNamespace(resolution_x=64, resolution_y=64))
    objects = {role: SimpleNamespace(hide_viewport=False, hide_render=False)
               for role in ('Legend.Back', 'Legend.B1', 'Legend.B2', 'Legend.B3')}
    world = MagicMock()
    world.inverted.return_value = world
    world.__matmul__.return_value = world
    world.angle.return_value = 0.
    down._world = lambda obj: world
    down._down_cameras = lambda scene: [camera]
    down._entry = lambda scene, turbine: entry
    down._label_children = lambda scene: {}
    down._label_objects = lambda scene, camera, **kwargs: objects
    return SimpleNamespace(down=down, mesh=mesh, vertices=vertices, entry=entry,
                           camera=camera, scene=scene, objects=objects)


@pytest.mark.parametrize('coordinates', [[], [(0, 0, 0), (1, 0, 0)],
                                        [(0, 0, 0), (0, 0, 1)]],
                         ids=['empty-mesh', 'zero-span', 'no-sampled-stations'])
@pytest.mark.parametrize('empty_cache', [False, True])
def test_blade_number_repeatedly_reports_missing_stations(blade_labels, coordinates, empty_cache):
    runtime = blade_labels
    runtime.vertices[:] = coordinates
    if empty_cache:
        runtime.down._STATIONS[(runtime.mesh.as_pointer(), len(coordinates), 'test')] = []
    for _ in range(2):
        with pytest.raises(ValueError, match='叶片缺少编号定位截面'):
            runtime.down.current_blade(runtime.camera, runtime.entry)
    assert not runtime.down._STATIONS


def test_blade_labels_report_missing_stations_and_recover(blade_labels):
    runtime = blade_labels
    for _ in range(2):
        runtime.down.update_labels(runtime.scene)
        assert runtime.camera.get('wfrl_label_error') == '叶片缺少编号定位截面'
        assert all(obj.hide_viewport and obj.hide_render for obj in runtime.objects.values())
    assert not runtime.down._STATIONS

    runtime.vertices[1] = (0, 0, .5)
    runtime.down.update_labels(runtime.scene)
    assert 'wfrl_label_error' not in runtime.camera
    for role, obj in runtime.objects.items():
        assert obj.hide_viewport == obj.hide_render == (role not in ('Legend.Back', 'Legend.B1'))
    stations = runtime.down._stations(runtime.mesh)
    assert stations[0] == (0., 0., .5)
    assert runtime.down._stations(runtime.mesh) is stations


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('completed', [False, True])
def test_camera_failure_wins_over_cancel_and_blocks_both_publication_paths(asynchronous, completed):
    namespace = helpers()
    scene = SimpleNamespace(as_pointer=lambda: 8)
    job = dict(source_key=7, failure='camera mismatch', temp='/private-media')
    called = []
    namespace.update(_EXPORT={8:job}, _LAST={}, _drop_scene=lambda scene: called.append('drop'),
        _publish_async=lambda *args: called.append('publish'), _finalize_files=lambda *args: called.append('publish'),
        _render_camera=None, _render_complete=None, _render_cancel=None,
        bpy=SimpleNamespace(app=SimpleNamespace(handlers=SimpleNamespace(
            frame_change_pre=[], render_post=[], render_complete=[], render_cancel=[]))))
    with pytest.raises(RuntimeError, match='camera mismatch'):
        namespace['_finish'](scene,completed=completed,asynchronous=asynchronous)
    assert called == ['drop'] and namespace['_LAST'][7]['state'] == 'FAILED'


@pytest.mark.parametrize('asynchronous', [False, True])
def test_camera_frame_coverage_is_required_before_publishing(asynchronous):
    namespace = helpers()
    scene = SimpleNamespace(as_pointer=lambda: 8)
    job = dict(source_key=7, temp='/private-media', plan={'frames':2}, camera_frames={2})
    called = []
    namespace.update(_EXPORT={8:job},_LAST={},_drop_scene=lambda scene:called.append('drop'),
        _check_export_camera=lambda *args:None, _publish_async=lambda *args:called.append('publish'),
        _finalize_files=lambda *args:called.append('publish'), _render_camera=None,
        _render_complete=None,_render_cancel=None,
        bpy=SimpleNamespace(app=SimpleNamespace(handlers=SimpleNamespace(
            frame_change_pre=[],render_post=[],render_complete=[],render_cancel=[]))))
    with pytest.raises(RuntimeError,match='全部输出帧'):
        namespace['_finish'](scene,completed=True,asynchronous=asynchronous)
    assert called == ['drop'] and namespace['_LAST'][7]['state'] == 'FAILED'


def test_pose_clamps_pitch_and_wraps_rotor():
    function = helpers()['pose_at']
    item = dict(azimuth_deg=359, rpm=10, pitch_start_deg=[0, 20, -3],
                pitch_target_deg=[10, -10, -3], pitch_rate_deg_s=5)
    assert function(item, 1) == (59, [5, 15, -3])
    assert function(item, 100) == (239, [10, -10, -3])


def test_pair_preflight_rejects_either_destination_and_dangling_links(tmp_path):
    function = helpers()['_output_paths']
    target = tmp_path / 'down.mp4'
    assert function(str(target)) == (target, target.with_suffix('.json'))
    for path in (target, target.with_suffix('.json')):
        symlink_or_skip(path, tmp_path / 'missing')
        with pytest.raises(FileExistsError):
            function(str(target))
        path.unlink()


@pytest.mark.skipif(os.name == 'nt', reason='Pair publication requires POSIX hard links')
def test_pair_publication_preserves_collisions_and_retains_master(tmp_path, monkeypatch):
    function = helpers()['_publish_pair']
    master = tmp_path / 'encoded.mp4'
    master.write_bytes(b'encoded master')
    target = tmp_path / 'final.mp4'
    sidecar = target.with_suffix('.json')
    sidecar.write_text('existing')
    with pytest.raises(FileExistsError):
        function(master, target, {'samples': [1]})
    assert sidecar.read_text() == 'existing'
    assert not target.exists()
    sidecar.unlink()
    original = os.link
    def race(source, destination, **kwargs):
        if Path(destination) == target:
            target.write_bytes(b'racing owner')
        return original(source, destination, **kwargs)
    monkeypatch.setattr(os, 'link', race)
    with pytest.raises(FileExistsError):
        function(master, target, {'samples': [1]})
    assert target.read_bytes() == b'racing owner'
    assert not sidecar.exists()
    assert master.read_bytes() == b'encoded master'


@pytest.mark.skipif(os.name == 'nt', reason='Pair publication requires POSIX hard links')
def test_pair_publication_writes_complete_bytes(tmp_path):
    function = helpers()['_publish_pair']
    master, target = tmp_path / 'encoded.mp4', tmp_path / 'final.mp4'
    master.write_bytes(b'encoded master')
    function(master, target, {'samples': [1, 2]})
    assert target.read_bytes() == master.read_bytes()
    assert json.loads(target.with_suffix('.json').read_text()) == {'samples': [1, 2]}


def test_verification_requires_actual_count_size_fps_and_full_decode(monkeypatch, tmp_path):
    function = helpers()['_verify_video']
    master = tmp_path / 'master.mp4'
    result = dict(streams=[dict(codec_name='h264', width=64, height=36, avg_frame_rate='25/1')],
                  frames=[dict(best_effort_timestamp_time='0.0'), dict(best_effort_timestamp_time='0.04')])
    commands = []
    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=json.dumps(result) if command[0] == 'probe' else '', returncode=0)
    monkeypatch.setattr(subprocess, 'run', run)
    assert function(master, 2, 25, (64, 36), 'probe', 'codec')['frame_count'] == 2
    assert commands[-1][0] == 'codec' and '-xerror' in commands[-1]
    for field, value in [('width', 62), ('height', 34), ('avg_frame_rate', '24/1')]:
        old = result['streams'][0][field]
        result['streams'][0][field] = value
        with pytest.raises(ValueError):
            function(master, 2, 25, (64, 36), 'probe', 'codec')
        result['streams'][0][field] = old
    result['frames'].pop()
    with pytest.raises(ValueError):
        function(master, 2, 25, (64, 36), 'probe', 'codec')


def test_plan_animates_only_selected_turbine_from_actual_snapshot():
    namespace = helpers()
    entries = [SimpleNamespace(turbine_id=tid, rpm=10, pitch_start_b1_deg=0,
                pitch_start_b2_deg=0, pitch_start_b3_deg=0) for tid in ('T1', 'T2', 'T3')]
    namespace['manual_control'] = SimpleNamespace(target_pitch=lambda entry, index: 20)
    scene = SimpleNamespace(wfrl_manual_pitch_rate_deg_s=5, wfrl_manual_turbines=entries)
    poses = {tid: dict(azimuth_deg=30, individual_pitch=True, pitch_b1_deg=2,
             pitch_b2_deg=3, pitch_b3_deg=4) for tid in ('T1', 'T2', 'T3')}
    plan = namespace['_plan'](scene, 25, 2, True, 'T2', poses)
    for item in plan['turbines']:
        if item['turbine'] == 'T2':
            assert item['rpm'] == 10 and item['pitch_start_deg'] == [0, 0, 0]
        else:
            assert item['rpm'] == 0 and item['pitch_start_deg'] == item['pitch_target_deg'] == [2, 3, 4]


def test_plan_current_pose_start_and_full_decode_failure(monkeypatch, tmp_path):
    namespace = helpers()
    entry = SimpleNamespace(turbine_id='T1', rpm=8)
    namespace['manual_control'] = SimpleNamespace(target_pitch=lambda entry, index: 30)
    scene = SimpleNamespace(wfrl_manual_pitch_rate_deg_s=4, wfrl_manual_turbines=[entry])
    pose = dict(azimuth_deg=123, individual_pitch=False, pitch_deg=7)
    plan = namespace['_plan'](scene, 30, 2, False, 'T1', {'T1': pose})
    assert plan['turbines'][0]['pitch_start_deg'] == [7,7,7]
    assert plan['turbines'][0]['pitch_target_deg'] == [30,30,30]
    result = dict(streams=[dict(codec_name='h264', width=64, height=36, avg_frame_rate='25/1')],
                  frames=[dict(best_effort_timestamp_time='0.0')])
    def fail_decode(command, **kwargs):
        if command[0] == 'codec':
            raise subprocess.CalledProcessError(1, command, stderr='corrupt frame')
        return SimpleNamespace(stdout=json.dumps(result))
    monkeypatch.setattr(subprocess, 'run', fail_decode)
    with pytest.raises(subprocess.CalledProcessError):
        namespace['_verify_video'](tmp_path / 'corrupt.mp4', 1, 25, (64,36), 'probe', 'codec')


@pytest.mark.parametrize('state', [{'cancelled': True}, {'failure': 'native render failed'}])
def test_cancelled_or_failed_frame_handler_avoids_removed_control_rna(state):
    namespace = helpers()
    scene = SimpleNamespace(as_pointer=lambda: 7)
    namespace['_EXPORT'] = {7: state}
    def forbidden(*args, **kwargs):
        raise AssertionError('Terminal job accessed pose controls')
    namespace['apply_frame'] = forbidden
    namespace['_frame_handler'](scene)



@pytest.mark.parametrize('verification_fails', [False, True])
def test_down_async_finish_verifies_and_publishes_off_the_main_thread(tmp_path, verification_fails):
    namespace = helpers()
    timers, published = [], []
    source = SimpleNamespace(as_pointer=lambda: 7)
    namespace.update(_PUBLISHING={}, _LAST={}, bpy=SimpleNamespace(app=SimpleNamespace(
        timers=SimpleNamespace(register=lambda callback, **_: timers.append(callback)))))
    temporary = tmp_path / 'temporary'
    temporary.mkdir()
    master = temporary / 'down.mp4'
    master.touch()
    target = tmp_path / 'out.mp4'
    entered, release = threading.Event(), threading.Event()
    main_thread = threading.get_ident()

    def verify(path, frames, fps, size, ffprobe, ffmpeg):
        assert threading.get_ident() != main_thread and path == master
        entered.set()
        assert release.wait(WAIT_TIMEOUT * 2), 'Down verification worker was not released'
        if verification_fails:
            raise ValueError('verification rejected')
        return {'full_decode': True}

    def publish(path, destination, metadata):
        assert threading.get_ident() != main_thread
        published.append((path, destination, metadata))

    namespace.update(_verify_video=verify, _publish_pair=publish)
    namespace['_publish_async'](source, 7, (str(temporary), str(target), dict(frames=1, fps=25),
                                           (64, 36), 'probe', 'codec'))
    try:
        assert entered.wait(WAIT_TIMEOUT), f'Down verification did not start: {namespace["_LAST"]!r}'
        assert timers[0]() == .2 and not namespace['_LAST']
    finally:
        release.set()
    pending = namespace['_PUBLISHING'][7][1]
    try:
        status = pending.get(timeout=WAIT_TIMEOUT)
    except queue.Empty:
        pytest.fail(f'Down publication did not finish: {namespace["_LAST"]!r}')
    pending.put(status)
    assert timers[0]() is None and not namespace['_PUBLISHING']
    assert namespace['_LAST'][7] == status
    if verification_fails:
        assert status == dict(state='FAILED', error='verification rejected', temporary=str(temporary))
        assert not published and master.exists()
    else:
        assert status == dict(state='COMPLETE', path=str(target))
        assert published[0][1:] == (target, dict(frames=1, fps=25, verification={'full_decode': True}))
        assert not temporary.exists()
