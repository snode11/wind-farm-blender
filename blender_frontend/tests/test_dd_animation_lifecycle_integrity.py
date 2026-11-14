"""Animation recovery and UI cleanup retain ownership until the worker exits."""
from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import animation_render as service, animation_render_ui as ui


def record(**changes):
    return dict(dict(schema='wfrl.animation-render.v1', job_id='dd-job', state='RUNNING',
                     completed_frames=0, total_frames=2, worker_pid=12345), **changes)


@pytest.mark.parametrize('invalid', ['partial_complete', 'job_list', 'manifest_list'])
def test_recovery_rejects_invalid_saved_results_without_rewriting(tmp_path, invalid):
    manifest = record(state='COMPLETE', completed_frames=2)
    configuration = dict(manifest)
    if invalid == 'partial_complete':
        manifest['completed_frames'] = 1
    elif invalid == 'job_list':
        configuration = []
    else:
        manifest = []
    service.write_json(tmp_path / 'manifest.json', manifest)
    service.write_json(tmp_path / 'job.json', configuration)
    before = (tmp_path / 'manifest.json').read_bytes()
    job = service.RenderJob()
    assert not job.recover(tmp_path)
    assert job.state == 'IDLE' and job.directory is None
    assert (tmp_path / 'manifest.json').read_bytes() == before


@pytest.mark.parametrize('invalid_state', [[], {}, None, 'UNKNOWN'])
def test_invalid_progress_cannot_break_process_finalization(tmp_path, invalid_state):
    job = service.RenderJob()
    job.directory, job.total_frames, job._record = tmp_path, 2, record()
    job.process = Mock(poll=Mock(return_value=0))
    service.write_json(tmp_path / 'manifest.json', record(state=invalid_state))
    job.poll()
    assert job.state == 'FAILED' and not job.active and job.error


def test_directory_collision_preserves_previous_job_and_existing_files(tmp_path, monkeypatch):
    job = service.RenderJob()
    previous = tmp_path / 'previous'
    job.directory, job.state, job._record = previous, 'COMPLETE', record(state='COMPLETE')
    target = tmp_path / 'animation-aaaaaaaaaaaa'
    target.mkdir()
    existing = target / 'manifest.json'
    existing.write_text('existing result', encoding='utf-8')
    monkeypatch.setattr(service.uuid, 'uuid4', lambda: NS(hex='a' * 32))
    snapshot = Mock()
    with pytest.raises(FileExistsError):
        job.start(tmp_path, blender=sys.executable, addon_root=Path(service.__file__).parent,
                  scene_name='source', frame_start=1, frame_end=2, frame_step=1,
                  snapshot_writer=snapshot)
    assert job.directory == previous and job.state == 'COMPLETE'
    assert existing.read_text(encoding='utf-8') == 'existing result'
    snapshot.assert_not_called()


def test_frame_count_overflow_does_not_allocate_or_replace_job(tmp_path):
    job = service.RenderJob()
    previous = tmp_path / 'previous'
    job.directory, job.state = previous, 'COMPLETE'
    with pytest.raises(OverflowError):
        job.start(tmp_path / 'new', blender=sys.executable, addon_root=Path(service.__file__).parent,
                  scene_name='source', frame_start=1, frame_end=10 ** 100, frame_step=1,
                  snapshot_writer=Mock())
    assert job.directory == previous and job.state == 'COMPLETE'
    assert not (tmp_path / 'new').exists()


def test_snapshot_cannot_reenter_and_replace_starting_job(tmp_path, monkeypatch):
    job = service.RenderJob()
    process = Mock(poll=Mock(return_value=None))
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *_args, **_kwargs: process)
    arguments = dict(blender=sys.executable, addon_root=Path(service.__file__).parent,
                     scene_name='source', frame_start=1, frame_end=2, frame_step=1)

    def snapshot(path):
        with pytest.raises(ValueError, match='已有动画'):
            job.start(tmp_path / 'nested', snapshot_writer=lambda nested: nested.touch(), **arguments)
        path.touch()

    directory = job.start(tmp_path / 'outer', snapshot_writer=snapshot, **arguments)
    assert directory.parent == tmp_path / 'outer'
    assert not (tmp_path / 'nested').exists()


def test_recovery_cannot_replace_job_during_snapshot(tmp_path, monkeypatch):
    saved = tmp_path / 'saved'
    saved.mkdir()
    completed = record(state='COMPLETE', completed_frames=2)
    service.write_json(saved / 'manifest.json', completed)
    service.write_json(saved / 'job.json', completed)
    before = (saved / 'manifest.json').read_bytes()
    job = service.RenderJob()
    monkeypatch.setattr(service.subprocess, 'Popen', lambda *_args, **_kwargs: Mock(poll=Mock(return_value=None)))

    def snapshot(path):
        assert not job.recover(saved)
        path.touch()

    directory = job.start(tmp_path / 'new', blender=sys.executable,
                          addon_root=Path(service.__file__).parent, scene_name='source',
                          frame_start=1, frame_end=2, frame_step=1, snapshot_writer=snapshot)
    assert directory.parent == tmp_path / 'new'
    assert (saved / 'manifest.json').read_bytes() == before


@pytest.fixture
def running_job(tmp_path, monkeypatch):
    job = service.RenderJob()
    job.directory, job.total_frames, job._record, job.state = tmp_path, 2, record(), 'RUNNING'
    job.process = Mock(poll=Mock(return_value=None))
    service.write_json(tmp_path / 'manifest.json', job._record)
    monkeypatch.setattr(service, 'job', job)
    return job


@pytest.fixture
def ui_runtime(monkeypatch, stub_module):
    class Scene(dict):
        __hash__ = object.__hash__
        camera, name, frame_current, frame_subframe = object(), 'source', 7, .25
        frame_start, frame_end, frame_step = 2, 12, 2
        render = NS(use_compositing=False, use_multiview=False, filepath='original-output',
                    image_settings=NS(file_format='OPEN_EXR', color_depth='32'))

    scene, timers = Scene(), {}
    app_handlers = NS(load_pre=[], load_post=[], undo_pre=[], redo_pre=[])
    stub_module('bpy.app.handlers', persistent=lambda f: f)
    stub_module('wfrl_blender.viewport', tag_redraw=lambda *_: None)
    stub_module('wfrl_blender.custom_camera_capture', active=lambda: False)
    stub_module('wfrl_blender.native_camera_views', capture_quality=lambda _: nullcontext())
    timer_api = NS(is_registered=lambda f: f in timers,
                   register=lambda f, **kwargs: timers.__setitem__(f, kwargs),
                   unregister=lambda f: timers.pop(f))
    bpy = NS(context=NS(scene=scene), app=NS(timers=timer_api, handlers=app_handlers,
                                            binary_path=sys.executable),
             data=NS(libraries=NS(write=lambda path, *_args, **_kwargs: Path(path).touch())),
             path=NS(abspath=str))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setattr(ui, '_SOURCE_SCENE', scene)
    monkeypatch.setattr(ui, '_LAST_DISPLAY', None)
    monkeypatch.setattr(ui, '_render_settings', lambda _: {})
    return NS(scene=scene, timers=timers, bpy=bpy, handlers=app_handlers)


@pytest.mark.parametrize('signal', ['terminate', 'kill'])
def test_stop_signal_failure_retains_polling_and_visible_error(running_job, ui_runtime, monkeypatch, signal):
    job, process = running_job, running_job.process
    getattr(process, signal).side_effect = PermissionError('signal denied')
    if signal == 'terminate':
        job.cancel()
    else:
        job._cancel_at = 0.
        monkeypatch.setattr(service.time, 'monotonic', lambda: 3.)
    assert ui._poll() == .25
    assert job.active and job.state == 'CANCELLING'
    assert 'signal denied' in job.error
    assert json.loads((job.directory / 'manifest.json').read_text(encoding='utf-8'))['state'] == 'RUNNING'


def test_unregister_retains_cleanup_timer_until_process_exit(running_job, ui_runtime):
    job, process, r = running_job, running_job.process, ui_runtime
    process.wait.side_effect = subprocess.TimeoutExpired('owned-worker', 2)
    ui.register()
    ui.unregister()
    assert job.active and job.state == 'CANCELLING'
    assert ui._SOURCE_SCENE is None
    assert ui._poll in r.timers and r.timers[ui._poll]['persistent']
    assert ui.restore not in r.timers
    assert all(not handlers for handlers in vars(r.handlers).values())
    process.poll.return_value = -9
    assert ui._poll() is None
    assert not job.active and job.state == 'CANCELLED'


def test_file_close_keeps_read_only_recovery_polling(running_job, ui_runtime, monkeypatch):
    job, r = running_job, ui_runtime
    job.process, job._recovered_pid, job.state = None, 12345, 'RECOVERING'
    alive = [True]
    monkeypatch.setattr(service, '_pid_alive', lambda *_: alive[0])
    r.timers[ui._poll] = {}
    ui.close()
    assert ui._poll in r.timers and r.timers[ui._poll]['persistent']
    assert job.active and ui._SOURCE_SCENE is None
    alive[0] = False
    assert ui._poll() is None
    assert not job.active and job.state == 'FAILED'


def test_timer_registration_failure_prevents_worker_launch(tmp_path, ui_runtime, monkeypatch):
    job, process = service.RenderJob(), Mock(poll=Mock(return_value=None))
    monkeypatch.setattr(service, 'job', job)
    launch = Mock(return_value=process)
    monkeypatch.setattr(service.subprocess, 'Popen', launch)
    monkeypatch.setattr(ui_runtime.bpy.app.timers, 'register', Mock(side_effect=RuntimeError('timer unavailable')))
    with pytest.raises(RuntimeError, match='timer unavailable'):
        ui.start(NS(scene=ui_runtime.scene), tmp_path)
    assert launch.call_count == 0
    assert not job.active
    scene = ui_runtime.scene
    assert (scene.frame_start, scene.frame_end, scene.frame_step, scene.frame_current, scene.frame_subframe) == (2, 12, 2, 7, .25)
    assert scene.render.filepath == 'original-output'
    assert scene.render.image_settings.file_format == 'OPEN_EXR'


def test_transient_display_failure_retries_without_losing_monitor(running_job, ui_runtime, monkeypatch):
    class FlakyScene(dict):
        frame_current = 1
        fail = True

        def __setitem__(self, key, value):
            if self.fail:
                raise RuntimeError('scene display unavailable')
            super().__setitem__(key, value)

    scene = FlakyScene()
    monkeypatch.setattr(ui, '_SOURCE_SCENE', scene)
    assert ui._poll() == .25
    assert running_job.active
    assert 'scene display unavailable' in running_job.error
    scene.fail = False
    assert ui._poll() == .25
    assert scene['wfrl_animation_render_directory'] == str(running_job.directory)
    assert scene['wfrl_animation_render_status']
