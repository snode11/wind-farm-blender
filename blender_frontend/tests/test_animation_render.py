"""Owned background render processes, progress and cancellation without Blender."""
import importlib
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

WAIT_TIMEOUT = 60


@pytest.fixture
def renderer(tmp_path, python_tool):
    executable = python_tool(tmp_path / 'blender-fixture', '''import json,os,sys,threading
from pathlib import Path
path=Path(sys.argv[-1]); job=json.loads(path.read_text(encoding='utf-8')); root=path.parent
(root/'runtime-profile.json').write_text(json.dumps(dict(config=os.environ.get('BLENDER_USER_CONFIG'))), encoding='utf-8')
mode=os.environ.get('WFRL_TEST_RENDER_MODE','complete')
total=len(range(job['frame_start'],job['frame_end']+1,job['frame_step']))
record=dict(job_id=job['job_id'],state='RUNNING',completed_frames=1,total_frames=total)
def write():
    temp=root/'fixture.tmp'; temp.write_text(json.dumps(record), encoding='utf-8'); temp.replace(root/'manifest.json')
write()
if mode=='wait': threading.Event().wait()
record.update(state='COMPLETE',completed_frames=total if mode!='partial' else 1)
write()
raise SystemExit(9 if mode=='fail' else 0)
''', mode=0o700)
    addon = tmp_path / 'addon'
    addon.mkdir()
    (addon / '__init__.py').write_text('')
    (addon / 'animation_render_worker.py').write_text('')
    return executable, addon


def start(tmp_path, renderer):
    module = importlib.import_module('wfrl_blender.animation_render')
    job = module.RenderJob()
    executable, addon = renderer
    job.start(tmp_path / 'output', blender=executable, addon_root=addon, scene_name='source',
              frame_start=2, frame_end=6, frame_step=2,
              snapshot_writer=lambda path: path.write_bytes(b'NON-BLEND snapshot fixture'))
    return job


def wait(job, predicate=None):
    if predicate is None:
        try:
            job.process.wait(timeout=WAIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            diagnostic = f'{job.state} / {job.error} / {job._log_tail()}'
            job.close()
            pytest.fail(f'Render process did not exit: {diagnostic}')
        job.poll()
        assert not job.active, f'Render still active: {job.state} / {job.error}'
        return
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline:
        job.poll()
        if predicate(job):
            return
        threading.Event().wait(.02)
    raise AssertionError(f'Render did not reach expected state: {job.state} / {job.error}; '
                         f'frames={job.completed_frames}/{job.total_frames}; log={job._log_tail()}')


def test_background_job_finishes_in_its_own_output_folder(tmp_path, renderer):
    target = tmp_path / 'output'
    target.mkdir()
    existing = target / 'frame_0002.png'
    existing.write_bytes(b'user data')
    job = start(tmp_path, renderer)
    wait(job)
    assert job.state == 'COMPLETE'
    assert job.completed_frames == job.total_frames == 3
    assert existing.read_bytes() == b'user data'
    assert job.directory.parent == target
    assert json.loads((job.directory / 'job.json').read_text(encoding='utf-8'))['scene_name'] == 'source'
    assert json.loads((job.directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'COMPLETE'
    profile = json.loads((job.directory / 'runtime-profile.json').read_text(encoding='utf-8'))['config']
    assert str(job.directory) in str(profile)


@pytest.mark.parametrize('mode', ['fail', 'partial'])
def test_completion_requires_successful_process_and_all_frames(tmp_path, renderer, monkeypatch, mode):
    monkeypatch.setenv('WFRL_TEST_RENDER_MODE', mode)
    job = start(tmp_path, renderer)
    wait(job)
    assert job.state == 'FAILED'
    assert job.error
    assert json.loads((job.directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'FAILED'


@pytest.mark.parametrize('initial', ['running', 'missing', 'incomplete_json'])
def test_poll_rereads_final_manifest_after_process_exit(tmp_path, initial):
    module = importlib.import_module('wfrl_blender.animation_render')
    job = module.RenderJob()
    job.directory = tmp_path
    job.total_frames = 3
    job._record = dict(job_id='render-fixture', state='RUNNING', completed_frames=0, total_frames=3)
    manifest = tmp_path / 'manifest.json'
    if initial == 'running':
        manifest.write_text(json.dumps(dict(job._record, completed_frames=2)))
    elif initial == 'incomplete_json':
        manifest.write_text('{')

    class CompletingProcess:
        def poll(self):
            manifest.write_text(json.dumps(dict(job_id='render-fixture', state='COMPLETE',
                                                completed_frames=3, total_frames=3)))
            return 0

    job.process = CompletingProcess()
    job.poll()

    assert job.state == 'COMPLETE'
    assert job.completed_frames == 3
    assert not job.active
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    assert saved['state'] == 'COMPLETE'
    assert saved['completed_frames'] == 3


def test_cancel_is_nonblocking_and_only_stops_owned_process(tmp_path, renderer, monkeypatch):
    monkeypatch.setenv('WFRL_TEST_RENDER_MODE', 'wait')
    job = start(tmp_path, renderer)
    try:
        wait(job, lambda current: current.completed_frames == 1)
        with monkeypatch.context() as patch:
            patch.setattr(job.process, 'wait', Mock(side_effect=AssertionError('cancel waited for the process')))
            job.cancel()
            assert job._cancel_at is not None and job.state == 'CANCELLING'
        wait(job)
        assert job.state == 'CANCELLED'
        assert job.completed_frames == 1
        assert json.loads((job.directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'CANCELLED'
        job.cancel()
    finally:
        job.close()


def test_snapshot_failure_never_launches_renderer(tmp_path, renderer):
    module = importlib.import_module('wfrl_blender.animation_render')
    job = module.RenderJob()
    executable, addon = renderer

    def unavailable(_path):
        raise OSError('snapshot unavailable')

    with pytest.raises(OSError, match='snapshot unavailable'):
        job.start(tmp_path / 'output', blender=executable, addon_root=addon, scene_name='source',
                  frame_start=1, frame_end=2, frame_step=1, snapshot_writer=unavailable)
    assert not job.active
    assert job.state == 'FAILED'
    assert json.loads((job.directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'FAILED'


def test_new_render_cannot_replace_an_active_job(tmp_path, renderer, monkeypatch):
    monkeypatch.setenv('WFRL_TEST_RENDER_MODE', 'wait')
    job = start(tmp_path, renderer)
    try:
        directory = job.directory
        executable, addon = renderer
        with pytest.raises(ValueError):
            job.start(tmp_path / 'other', blender=executable, addon_root=addon, scene_name='other',
                      frame_start=1, frame_end=2, frame_step=1,
                      snapshot_writer=lambda path: path.write_bytes(b'other'))
        assert job.directory == directory
    finally:
        job.close()


def test_compositor_file_outputs_are_rejected_recursively_before_render():
    module = importlib.import_module('wfrl_blender.animation_render')
    output = NS(type='OUTPUT_FILE', mute=False)
    tree = NS(nodes=[NS(type='GROUP', mute=False, node_tree=NS(nodes=[output]))])
    scene = NS(render=NS(use_compositing=True), compositing_node_group=tree)
    with pytest.raises(ValueError, match='文件输出'):
        module.validate_output_nodes(scene)
    output.mute = True
    module.validate_output_nodes(scene)
    output.mute = False
    scene.render.use_compositing = False
    module.validate_output_nodes(scene)


@pytest.mark.parametrize(('depth', 'expected'), [('8', '8'), ('10', '16'), ('12', '16'), ('16', '16'), ('32', '16')])
def test_png_snapshot_uses_supported_depth_without_mutating_source(depth, expected):
    from wfrl_blender.animation_render_ui import _render_settings

    class Scene(dict):
        pass

    scene = Scene()
    image = NS(color_mode='RGBA', color_depth=depth, compression=15)
    scene.render = NS(image_settings=image)
    scene.display_settings = NS()
    scene.view_settings = NS()
    scene.eevee = NS()

    settings = _render_settings(scene)

    assert settings['image_settings'] == dict(color_mode='RGBA', color_depth=expected, compression=15)
    assert image.color_depth == depth


def test_manual_png_snapshot_normalizes_depth_without_changing_source():
    from wfrl_blender.animation_render_ui import _render_settings

    class Scene(dict):
        pass

    scene = Scene(wfrl_scene_kind='manual')
    scene.render = NS(image_settings=NS(color_mode='RGBA', color_depth='32', compression=15))
    scene.display_settings = scene.view_settings = scene.eevee = NS()

    assert _render_settings(scene)['image_settings']['color_depth'] == '16'
    assert scene.render.image_settings.color_depth == '32'



def test_png_preflight_checks_enabled_view_suffixes_and_packed_eyes():
    from wfrl_blender.animation_render import png_output_views

    left = NS(name='left', use=True, file_suffix='_L')
    right = NS(name='right', use=True, file_suffix='_R')
    image = NS(views_format='INDIVIDUAL')
    render = NS(use_multiview=True, views_format='STEREO_3D', views=[left, right], image_settings=image)
    scene = NS(render=render)
    assert png_output_views(scene) == ('INDIVIDUAL', ('left', 'right'))
    right.file_suffix = '_l'
    with pytest.raises(ValueError, match='唯一'):
        png_output_views(scene)
    right.use = False
    assert png_output_views(scene) == ('INDIVIDUAL', ('left',))
    image.views_format = 'STEREO_3D'
    with pytest.raises(ValueError, match='左右双眼'):
        png_output_views(scene)
    right.use = True
    assert png_output_views(scene) == ('STEREO_3D', ())


@pytest.mark.parametrize('failed', [False, True])
def test_worker_manifest_is_not_terminal_until_owned_process_exits(tmp_path, monkeypatch, failed):
    from wfrl_blender import animation_render as service, animation_render_worker as worker

    record = dict(job_id='worker-fixture', total_frames=1, addon_root=str(tmp_path / 'addon'),
                  snapshot=str(tmp_path / 'scene.blend'), scene_name='source',
                  frame_start=1, frame_end=1, frame_step=1)
    job_path = tmp_path / 'job.json'
    job_path.write_text(json.dumps(record), encoding='utf-8')
    handlers = []
    scene = NS(name='source', camera=NS(name='camera'), frame_current=1,
               get=lambda key: None, view_settings=NS(exposure=0.),
               render=NS(use_compositing=False, use_multiview=False, fps=24, fps_base=1.,
                         image_settings=NS(),
                         frame_path=lambda **kwargs: str(tmp_path / 'frames/frame_0001.png')))

    def render(**kwargs):
        if failed:
            raise RuntimeError('injected render failure')
        (tmp_path / 'frames/frame_0001.png').write_bytes(b'PNG fixture')
        for callback in tuple(handlers):
            callback(scene)
        return {'FINISHED'}

    monkeypatch.setitem(sys.modules, 'bpy', NS(
        data=NS(scenes=NS(get=lambda name: scene)),
        app=NS(handlers=NS(render_write=handlers)),
        ops=NS(wm=NS(open_mainfile=lambda **kwargs: None), render=NS(render=render))))
    addon = NS(register=lambda: None)
    spec = NS(name='_wfrl_animation_worker', loader=NS(exec_module=lambda module: None))
    monkeypatch.setattr(worker.importlib.util, 'spec_from_file_location', lambda *args, **kwargs: spec)
    monkeypatch.setattr(worker.importlib.util, 'module_from_spec', lambda spec: addon)
    monkeypatch.setitem(sys.modules, spec.name, addon)
    modules = {'animation_render': service, 'manual_control': NS(pause_all=lambda: None),
               'frontend_diagnostics': NS(build_information=lambda: {})}
    monkeypatch.setattr(importlib, 'import_module', lambda name: modules[name.rsplit('.', 1)[-1]])
    job = service.RenderJob()
    job.directory, job.total_frames, job._record = tmp_path, 1, dict(record)
    job.state = 'RUNNING'
    process = Mock()
    process.poll.return_value = None
    job.process = process

    if failed:
        with pytest.raises(RuntimeError, match='injected render failure'):
            worker.run(job_path)
    else:
        worker.run(job_path)

    assert not handlers
    manifest = tmp_path / 'manifest.json'
    saved = json.loads(manifest.read_text(encoding='utf-8'))
    assert saved['state'] not in {'COMPLETE', 'FAILED', 'CANCELLED'}
    job.poll()
    assert job.active and job.state == 'RUNNING'
    process.poll.return_value = 1 if failed else 0
    job.poll()
    assert not job.active
    assert job.state == ('FAILED' if failed else 'COMPLETE')
    assert json.loads(manifest.read_text(encoding='utf-8'))['state'] == job.state


def test_launch_failure_keeps_ui_polling_until_spawned_process_exits(tmp_path, renderer, monkeypatch):
    from wfrl_blender import animation_render as service, animation_render_ui as ui
    from wfrl_blender import custom_camera_capture

    job = service.RenderJob()
    monkeypatch.setattr(service, 'job', job)
    monkeypatch.setattr(custom_camera_capture, 'active', lambda: False)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.native_camera_views',
                        NS(capture_quality=lambda scene: nullcontext()))
    monkeypatch.setattr(ui, '_SOURCE_SCENE', None)
    monkeypatch.setattr(ui, '_LAST_DISPLAY', None)
    monkeypatch.setattr(ui, '_render_settings', lambda scene: {})
    published, timers = [], set()
    monkeypatch.setattr(ui, '_publish', lambda: published.append(job.state))
    scene = NS(camera=object(), name='source', frame_start=1, frame_end=1, frame_step=1,
               get=lambda key: None,
               frame_current=1, render=NS(use_compositing=False, use_multiview=False))
    monkeypatch.setitem(sys.modules, 'bpy', NS(
        app=NS(binary_path=str(renderer[0]), timers=NS(is_registered=lambda f: f in timers,
                    register=lambda f, **kwargs: timers.add(f))),
        path=NS(abspath=lambda value: value),
        data=NS(libraries=NS(write=lambda path, *args, **kwargs: Path(path).touch()))))
    # Blender scenes are hashable datablocks in the snapshot writer's set.
    class ContextScene(NS):
        __hash__ = object.__hash__
    scene = ContextScene(**vars(scene))
    process = Mock()
    process.poll.return_value = None
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *args, **kwargs: process)
    original_open = Path.open

    @contextmanager
    def failing_log(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as log:
            yield log
        raise OSError('injected log close failure')

    monkeypatch.setattr(Path, 'open', lambda path, *args, **kwargs:
        failing_log(path, *args, **kwargs) if path.name == 'render.log' and args == ('wb',)
        else original_open(path, *args, **kwargs))
    with pytest.raises(OSError, match='injected log close failure'):
        ui.start(NS(scene=scene), tmp_path / 'output')
    assert job.active and job.state == 'CANCELLING'
    assert ui._poll in timers and published == ['CANCELLING']
    process.terminate.assert_called_once_with()
    manifest = job.directory / 'manifest.json'
    assert json.loads(manifest.read_text(encoding='utf-8'))['state'] not in {'FAILED', 'COMPLETE'}
    process.poll.return_value = 0
    assert ui._poll() is None
    assert not job.active and job.state == 'FAILED'
    assert 'injected log close failure' in job.error
    assert published[-1] == 'FAILED'


@pytest.mark.parametrize('code', [0, 1])
@pytest.mark.parametrize('cancelled', [False, True])
def test_failing_worker_is_finalized_as_failed_only_after_exit(tmp_path, monkeypatch, code, cancelled):
    from wfrl_blender import animation_render as service
    job = service.RenderJob()
    job.directory, job.total_frames = tmp_path, 1
    job._record = dict(job_id='failing-fixture', state='FAILING', completed_frames=1,
                       total_frames=1, error='render failed')
    job.state = 'RUNNING'
    manifest = tmp_path / 'manifest.json'
    service.write_json(manifest, job._record)
    process = Mock()
    process.poll.return_value = None
    job.process = process
    writes = []
    write_json = service.write_json

    def write_terminal(path, record):
        assert job.process is None and not job.active
        assert process.poll() is not None
        writes.append(dict(record))
        write_json(path, record)

    monkeypatch.setattr(service, 'write_json', write_terminal)
    job.poll()
    assert job.active and not writes
    if cancelled:
        job._cancel_at = time.monotonic()
    process.poll.return_value = code
    job.poll()
    assert not job.active and job.state == 'FAILED'
    assert writes[-1]['state'] == 'FAILED' and 'render failed' in job.error


@pytest.mark.parametrize('worker_alive', [False, True])
def test_reopened_scene_recovers_failing_worker_without_stale_busy_state(
        tmp_path, renderer, monkeypatch, worker_alive):
    from wfrl_blender import animation_render as service, animation_render_ui as ui
    from wfrl_blender import custom_camera_capture

    directory = tmp_path / 'orphan'
    directory.mkdir()
    record = dict(schema='wfrl.animation-render.v1', job_id='orphan-fixture', state='FAILING',
                  completed_frames=0, total_frames=1, error='orphan render failed', worker_pid=12345)
    service.write_json(directory / 'manifest.json', record)
    service.write_json(directory / 'job.json', dict(record, state='STARTING'))
    job = service.RenderJob()
    monkeypatch.setattr(service, 'job', job)
    alive, probes = [worker_alive], []

    def process_alive(pid, identity=None):
        probes.append(pid)
        return alive[0]

    monkeypatch.setattr(service, '_pid_alive', process_alive, raising=False)
    monkeypatch.setattr(ui, '_SOURCE_SCENE', None)
    monkeypatch.setattr(ui, '_LAST_DISPLAY', None)
    monkeypatch.setattr(custom_camera_capture, 'active', lambda: False)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.native_camera_views',
                        NS(capture_quality=lambda scene: nullcontext()))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.viewport', NS(tag_redraw=lambda scenes: None))
    monkeypatch.setattr(ui, '_render_settings', lambda scene: {})

    class Scene(dict):
        __hash__ = object.__hash__
        camera, name, frame_current = object(), 'restored-scene', 1
        frame_start = frame_end = frame_step = 1
        render = NS(use_compositing=False, use_multiview=False)

    scene = Scene(wfrl_animation_render_directory=str(directory),
                  wfrl_animation_render_status='后台渲染 0/1 帧')
    timers = set()
    monkeypatch.setitem(sys.modules, 'bpy', NS(
        context=NS(scene=scene),
        data=NS(scenes=[scene], libraries=NS(write=lambda path, *args, **kwargs: Path(path).touch())),
        path=NS(abspath=lambda value: value),
        app=NS(binary_path=str(renderer[0]), timers=NS(is_registered=lambda f: f in timers,
                    register=lambda f, **kwargs: timers.add(f)))))
    ui.restore()
    assert probes and probes[-1] == 12345
    if worker_alive:
        assert job.active and job.state not in {'FAILED', 'COMPLETE'}
        assert ui._poll in timers
        with pytest.raises(ValueError, match='已有动画'):
            ui.start(NS(scene=scene), tmp_path / 'retry')
        assert json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'FAILING'
        alive[0] = False
        assert ui._poll() is None
    assert job.state == 'FAILED' and not job.active
    assert scene['wfrl_animation_render_status'] == '后台渲染失败'
    assert json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'FAILED'
    assert 'orphan render failed' in job.error
    new_directory = ui.start(NS(scene=scene), tmp_path / 'retry')
    try:
        wait(job)
        assert new_directory != directory and job.state == 'COMPLETE'
    finally:
        job.close()


def test_recovery_does_not_wait_for_a_reused_worker_pid(monkeypatch):
    from wfrl_blender import animation_render as service
    monkeypatch.setattr(service, '_pid_identity', lambda pid: 'replacement-process', raising=False)
    assert not service._pid_alive(12345, 'original-worker')


def test_worker_liveness_tracks_real_process_exit():
    from wfrl_blender import animation_render as service
    with subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read()'],
                          stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        try:
            identity = service._pid_identity(process.pid)
            assert identity and service._pid_alive(process.pid, identity)
        finally:
            process.communicate(timeout=WAIT_TIMEOUT)
        assert not service._pid_alive(process.pid, identity)
