"""Host contracts for complete, exclusive animation-frame publication."""
import importlib
import json
from pathlib import Path
import runpy
import signal
import struct
import sys
from types import SimpleNamespace as NS
import zlib

import pytest

from wfrl_blender import animation_render as service, animation_render_worker as worker
from wfrl_blender import video_output


def png_bytes():
    def chunk(kind, data):
        return (struct.pack('!I', len(data)) + kind + data
                + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\x11\x22\x33\xff'))
            + chunk(b'IEND', b''))


PNG = png_bytes()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    class Settings(NS):
        clamps = {}

        def __setattr__(self, key, value):
            super().__setattr__(key, self.clamps.get(key, lambda item: item)(value))

    class Scene(Settings):
        def get(self, _key):
            return None

    image = Settings(views_format='INDIVIDUAL', file_format='PNG', color_depth='8')
    render = Settings(use_compositing=False, use_multiview=False, views_format='STEREO_3D',
                      views=[], image_settings=image, resolution_x=32, resolution_y=32,
                      resolution_percentage=100, fps=24, fps_base=1., filepath='')
    scene = Scene(name='source', camera=NS(name='camera'), render=render,
                  frame_current=1, view_settings=NS(exposure=0.))

    def frame_path(*, frame, view=''):
        suffix = next((item.file_suffix for item in render.views if item.name == view), '')
        return f'{render.filepath}{frame:04d}{suffix}.png'

    render.frame_path = frame_path
    handlers = []
    signals = {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.default_int_handler}
    initial_signals = dict(signals)

    def set_signal(number, handler):
        if handler is None:
            raise TypeError('signal handler must be SIG_IGN, SIG_DFL, or a callable object')
        previous = signals[number]
        signals[number] = handler
        return previous

    monkeypatch.setattr(signal, 'getsignal', signals.__getitem__)
    monkeypatch.setattr(signal, 'signal', set_signal)
    state = NS(scene=scene, handlers=handlers, signals=signals, initial_signals=initial_signals,
               root=tmp_path, output=tmp_path / 'frames', render_calls=0,
               write=None, after_frame=None, swallow_callbacks=False, callback_errors=[],
               operator_result={'FINISHED'}, manifests=[])

    def render_animation(**kwargs):
        assert kwargs == dict(animation=True, scene='source')
        state.render_calls += 1
        for frame in range(scene.frame_start, scene.frame_end + 1, scene.frame_step):
            scene.frame_current = frame
            views = service.png_output_views(scene)[1]
            paths = [Path(frame_path(frame=frame, view=view)) for view in views or ('',)]
            if state.write is None:
                for path in paths:
                    path.write_bytes(PNG)
            elif state.write(frame, paths) is False:
                return state.operator_result
            for handler in tuple(handlers):
                try:
                    handler(scene)
                except BaseException as exc:
                    if not state.swallow_callbacks:
                        raise
                    state.callback_errors.append(exc)
            if state.after_frame:
                state.after_frame(frame)
        return state.operator_result

    monkeypatch.setitem(sys.modules, 'bpy', NS(
        data=NS(scenes=NS(get=lambda name: scene if name == scene.name else None)),
        app=NS(handlers=NS(render_write=handlers)),
        ops=NS(wm=NS(open_mainfile=lambda **kwargs: None),
               render=NS(render=render_animation))))
    addon = NS(register=lambda: None)
    spec = NS(name='_wfrl_animation_worker', loader=NS(exec_module=lambda module: None))
    monkeypatch.setattr(worker.importlib.util, 'spec_from_file_location', lambda *args, **kwargs: spec)
    monkeypatch.setattr(worker.importlib.util, 'module_from_spec', lambda spec: addon)
    monkeypatch.setitem(sys.modules, spec.name, addon)
    modules = {'animation_render': service, 'video_output': video_output,
               'manual_control': NS(pause_all=lambda: None),
               'frontend_diagnostics': NS(build_information=lambda: {})}
    original_import = importlib.import_module
    monkeypatch.setattr(importlib, 'import_module', lambda name, *args, **kwargs:
                        modules[name.rsplit('.', 1)[-1]] if name.startswith(spec.name + '.')
                        else original_import(name, *args, **kwargs))
    monkeypatch.setattr(service, '_pid_identity', lambda _pid: 'fixture-process')
    original_write = service.write_json

    def write_manifest(path, record):
        original_write(path, record)
        state.manifests.append(json.loads(Path(path).read_text(encoding='utf-8')))

    monkeypatch.setattr(service, 'write_json', write_manifest)
    state.job = dict(job_id='dj-animation', total_frames=2, addon_root=str(tmp_path / 'addon'),
                     snapshot=str(tmp_path / 'scene.blend'), scene_name='source',
                     frame_start=1, frame_end=2, frame_step=1)
    state.job_path = tmp_path / 'job.json'

    def run(**changes):
        state.job.update(changes)
        state.job_path.write_text(json.dumps(state.job), encoding='utf-8')
        return worker.run(state.job_path)

    state.run = run
    yield state
    assert not handlers
    assert signals == initial_signals


def manifest(runtime):
    return json.loads((runtime.root / 'manifest.json').read_text(encoding='utf-8'))


def files(runtime):
    return sorted(path.name for path in runtime.output.iterdir())


@pytest.mark.parametrize('mode', ['mono', 'individual', 'packed'])
def test_dj_frames_are_private_until_complete_and_keep_output_protocol(runtime, mode):
    r = runtime
    if mode != 'mono':
        r.scene.render.use_multiview = True
        r.scene.render.views = [NS(name='left', use=True, file_suffix='_L'),
                                NS(name='right', use=True, file_suffix='_R')]
        if mode == 'packed':
            r.scene.render.image_settings.views_format = 'STEREO_3D'
    names = []

    def write(frame, paths):
        suffixes = ('_L', '_R') if mode == 'individual' else ('',)
        for path, suffix in zip(paths, suffixes):
            final = r.output / f'frame_{frame:04d}{suffix}.png'
            assert path.parent == final.parent and path != final
            path.write_bytes(PNG[:-12])
            assert not final.exists()
            assert manifest(r)['completed_frames'] == len(names)
            path.write_bytes(PNG)

    def published(frame):
        names.append(frame)
        assert manifest(r)['completed_frames'] == len(names)
        assert not any(path.name.startswith('.') for path in r.output.iterdir())

    r.write, r.after_frame = write, published
    r.run(frame_start=2, frame_end=6, frame_step=2, total_frames=3)
    saved = manifest(r)
    assert saved['state'] == 'RENDERED'
    assert saved['completed_frames'] == 3
    expected = []
    for frame in (2, 4, 6):
        if mode == 'individual':
            expected.append(dict(frame=frame, views={view: f'frames/frame_{frame:04d}{suffix}.png'
                                                     for view, suffix in [('left', '_L'), ('right', '_R')]}))
        else:
            expected.append(dict(frame=frame, file=f'frames/frame_{frame:04d}.png'))
    assert saved['frames'] == expected
    assert all(path.read_bytes() == PNG for path in r.output.iterdir())
    job = service.RenderJob()
    job.directory, job.total_frames, job._record = r.root, 3, dict(r.job)
    job.process, job.state = NS(poll=lambda: 0), 'RUNNING'
    job.poll()
    assert job.state == 'COMPLETE' and not job.active


@pytest.mark.parametrize('failure', ['exception', 'interrupt', 'exit', 'cancelled'])
def test_dj_failed_frame_is_removed_and_previous_complete_frame_survives(runtime, failure):
    r = runtime

    def write(frame, paths):
        paths[0].write_bytes(PNG if frame == 1 else PNG[:30])
        if frame == 2:
            if failure == 'cancelled':
                r.operator_result = {'CANCELLED'}
                return False
            raise {'exception': OSError('disk full'), 'interrupt': KeyboardInterrupt(),
                   'exit': SystemExit(9)}[failure]

    r.write = write
    expected = {'exception': OSError, 'interrupt': KeyboardInterrupt,
                'exit': SystemExit, 'cancelled': RuntimeError}[failure]
    with pytest.raises(expected):
        r.run()
    assert files(r) == ['frame_0001.png']
    assert (r.output / 'frame_0001.png').read_bytes() == PNG
    assert manifest(r)['completed_frames'] == 1
    assert manifest(r)['state'] == ('RUNNING' if failure in {'interrupt', 'exit'} else 'FAILING')


@pytest.mark.parametrize('payload', [b'', b'not a PNG', PNG[:-1], PNG[:-12],
                                    PNG[:45] + bytes([PNG[45] ^ 1]) + PNG[46:], PNG + b'extra'],
                         ids=['empty', 'not_png', 'truncated_crc', 'missing_end', 'bad_crc', 'trailing_bytes'])
def test_dj_nonempty_or_truncated_png_is_never_published(runtime, payload):
    runtime.write = lambda frame, paths: paths[0].write_bytes(payload)
    with pytest.raises(RuntimeError, match='PNG|渲染帧'):
        runtime.run(frame_end=1, total_frames=1)
    assert files(runtime) == []
    assert manifest(runtime)['completed_frames'] == 0
    assert manifest(runtime)['state'] == 'FAILING'


def test_dj_png_sync_failure_is_not_counted(runtime, monkeypatch):
    original_sync = worker.os.fsync

    def fail_png(descriptor):
        if worker.os.fstat(descriptor).st_size == len(PNG):
            raise OSError('PNG sync failed')
        return original_sync(descriptor)

    monkeypatch.setattr(worker.os, 'fsync', fail_png)
    with pytest.raises(OSError, match='PNG sync failed'):
        runtime.run(frame_end=1, total_frames=1)
    assert files(runtime) == [] and manifest(runtime)['completed_frames'] == 0


def test_dj_atomic_publish_failure_preserves_previous_frame(runtime, monkeypatch):
    commit = video_output._commit_export

    def fail_second(source, destination):
        if destination.name == 'frame_0002.png':
            raise OSError('publication failed')
        commit(source, destination)

    monkeypatch.setattr(video_output, '_commit_export', fail_second)
    with pytest.raises(OSError, match='publication failed'):
        runtime.run()
    assert files(runtime) == ['frame_0001.png']
    assert manifest(runtime)['completed_frames'] == 1


@pytest.mark.parametrize('race', [False, True])
def test_dj_existing_complete_frame_is_never_overwritten(runtime, monkeypatch, race):
    r = runtime
    existing = r.output / 'frame_0001.png'
    preserved = PNG + b'existing owner'
    if race:
        commit = video_output._commit_export

        def racing_commit(source, destination):
            destination.write_bytes(preserved)
            commit(source, destination)

        monkeypatch.setattr(video_output, '_commit_export', racing_commit)
    else:
        def write(_frame, paths):
            existing.write_bytes(preserved)
            paths[0].write_bytes(PNG)

        r.write = write
    with pytest.raises(FileExistsError):
        r.run(frame_end=1, total_frames=1)
    assert existing.read_bytes() == preserved
    assert files(r) == [existing.name]
    assert manifest(r)['completed_frames'] == 0


@pytest.mark.parametrize('failure', ['missing_view', 'publish_view'])
def test_dj_multiview_frame_is_counted_only_after_all_views_publish(runtime, monkeypatch, failure):
    r = runtime
    r.scene.render.use_multiview = True
    r.scene.render.views = [NS(name='left', use=True, file_suffix='_L'),
                            NS(name='right', use=True, file_suffix='_R')]
    if failure == 'missing_view':
        r.write = lambda frame, paths: paths[0].write_bytes(PNG)
        expected = RuntimeError
    else:
        commit = video_output._commit_export

        def fail_right(source, destination):
            if destination.name.endswith('_R.png'):
                raise OSError('right view failed')
            commit(source, destination)

        monkeypatch.setattr(video_output, '_commit_export', fail_right)
        expected = OSError
    with pytest.raises(expected):
        r.run(frame_end=1, total_frames=1)
    assert files(r) == []
    assert manifest(r)['completed_frames'] == 0


def test_dj_swallowed_callback_failure_cannot_become_rendered(runtime, monkeypatch):
    r = runtime
    r.swallow_callbacks = True
    write = service.write_json
    failed = False

    def fail_progress(path, record):
        nonlocal failed
        if record['completed_frames'] == 1 and record['state'] == 'RUNNING' and not failed:
            failed = True
            raise OSError('progress write failed')
        return write(path, record)

    monkeypatch.setattr(service, 'write_json', fail_progress)
    with pytest.raises(OSError, match='progress write failed'):
        r.run()
    assert files(r) == ['frame_0001.png']
    assert manifest(r)['state'] == 'FAILING'
    assert manifest(r)['completed_frames'] == 1


def test_dj_callback_failure_is_recorded_before_a_late_cancel(runtime):
    r = runtime
    r.swallow_callbacks = True
    r.write = lambda frame, paths: paths[0].write_bytes(PNG[:-12])

    def before_return(_frame):
        saved = manifest(r)
        assert saved['state'] == 'FAILING'
        assert saved['completed_frames'] == 0 and 'PNG' in saved['error']
        with pytest.raises(RuntimeError, match='PNG'):
            r.signals[signal.SIGTERM](signal.SIGTERM, None)

    r.after_frame = before_return
    with pytest.raises(RuntimeError, match='PNG'):
        r.run()
    assert manifest(r)['state'] == 'FAILING' and files(r) == []


def test_dj_cleanup_attempts_every_temporary_view_and_reports_unremovable_file(runtime, monkeypatch):
    r = runtime
    r.scene.render.use_multiview = True
    r.scene.render.views = [NS(name='left', use=True, file_suffix='_L'),
                            NS(name='right', use=True, file_suffix='_R')]
    blocked = []

    def write(_frame, paths):
        blocked.append(paths[0])
        for path in paths:
            path.write_bytes(PNG[:-12])

    unlink = Path.unlink

    def deny_one(path, *args, **kwargs):
        if path in blocked:
            raise PermissionError('temporary view is busy')
        return unlink(path, *args, **kwargs)

    r.write = write
    monkeypatch.setattr(Path, 'unlink', deny_one)
    glob = Path.glob
    monkeypatch.setattr(Path, 'glob', lambda path, pattern: iter(sorted(glob(path, pattern))))
    with pytest.raises(OSError, match='temporary view is busy'):
        r.run(frame_end=1, total_frames=1)
    assert files(r) == [blocked[0].name]
    assert manifest(r)['state'] == 'FAILING'
    assert 'temporary view is busy' in manifest(r)['error']
    unlink(blocked[0])


@pytest.mark.parametrize('number', [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize('swallowed', [False, True])
def test_dj_catchable_cancel_cleans_staging_without_reporting_failure(runtime, number, swallowed):
    r = runtime

    def write(frame, paths):
        paths[0].write_bytes(PNG if frame == 1 else PNG[:30])
        if frame == 2:
            handler = r.signals[number]
            assert handler != r.initial_signals[number]
            if swallowed:
                try:
                    handler(number, None)
                except SystemExit:
                    pass
            else:
                handler(number, None)

    r.write, r.swallow_callbacks = write, swallowed
    with pytest.raises(SystemExit) as exited:
        r.run()
    assert exited.value.code == 128 + number
    assert files(r) == ['frame_0001.png']
    assert manifest(r)['state'] == 'RUNNING'
    assert manifest(r)['completed_frames'] == 1
    job = service.RenderJob()
    job.directory, job.total_frames, job._record = r.root, 2, dict(r.job)
    job.process, job._cancel_at = NS(poll=lambda: exited.value.code), 1.
    job.poll()
    assert job.state == 'CANCELLED'


@pytest.mark.parametrize('number', [signal.SIGTERM, signal.SIGINT])
def test_dj_unknown_native_signal_handler_is_preserved(runtime, number):
    runtime.signals[number] = runtime.initial_signals[number] = None
    runtime.run()
    assert manifest(runtime)['state'] == 'RENDERED'


@pytest.mark.parametrize('cancelled', [False, True])
def test_dj_script_exit_is_nonzero_after_failure_or_cancellation(runtime, monkeypatch, capsys, cancelled):
    r = runtime

    def write(_frame, paths):
        paths[0].write_bytes(PNG[:30])
        if cancelled:
            r.signals[signal.SIGTERM](signal.SIGTERM, None)
        raise OSError('render disk full')

    r.write = write
    r.job_path.write_text(json.dumps(r.job), encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', [worker.__file__, '--', str(r.job_path)])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(worker.__file__, run_name='__main__')
    assert exited.value.code == (128 + signal.SIGTERM if cancelled else 1)
    assert files(r) == [] and manifest(r)['completed_frames'] == 0
    if not cancelled:
        assert 'render disk full' in capsys.readouterr().err
        assert manifest(r)['state'] == 'FAILING'


@pytest.mark.parametrize('changes', [
    dict(frame_start=2, frame_end=1, total_frames=0),
    dict(frame_start=4, frame_end=1, frame_step=-1, total_frames=2),
    dict(frame_start=True, frame_end=2, total_frames=2),
    dict(frame_step=True), dict(frame_end=1, total_frames=True),
    dict(frame_start=1.0), dict(frame_step=0), dict(total_frames=3),
])
def test_dj_invalid_frame_parameters_fail_before_rendering(runtime, changes):
    with pytest.raises(ValueError, match='帧|范围|步长'):
        runtime.run(**changes)
    assert runtime.render_calls == 0 and not runtime.output.exists()
    assert manifest(runtime)['state'] == 'FAILING'


@pytest.mark.parametrize('key', ['resolution_x', 'resolution_y', 'resolution_percentage'])
@pytest.mark.parametrize('value', [0, -1, True, 1.5, float('nan'), float('inf')])
def test_dj_invalid_resolution_is_rejected_before_blender_can_clamp(runtime, key, value):
    with pytest.raises(ValueError, match='分辨率'):
        runtime.run(render_settings={'render': {key: value}})
    assert runtime.render_calls == 0 and not runtime.output.exists()


@pytest.mark.parametrize('setting', ['frame', 'resolution'])
def test_dj_blender_clamping_cannot_silently_change_requested_output(runtime, setting):
    if setting == 'frame':
        runtime.scene.clamps = dict(frame_start=lambda value: 1, frame_end=lambda value: 1)
        changes = dict(frame_start=2, frame_end=2, total_frames=1)
    else:
        runtime.scene.render.clamps = dict(resolution_x=lambda value: 16)
        changes = dict(render_settings={'render': {'resolution_x': 32}})
    with pytest.raises(ValueError):
        runtime.run(**changes)
    assert runtime.render_calls == 0 and not runtime.output.exists()


@pytest.mark.parametrize('entry', ['directory', 'file'])
def test_dj_existing_output_location_is_not_reused(runtime, entry):
    if entry == 'directory':
        runtime.output.mkdir()
        sentinel = runtime.output / 'frame_0001.png'
    else:
        sentinel = runtime.output
    sentinel.write_bytes(PNG)
    with pytest.raises(FileExistsError):
        runtime.run()
    assert sentinel.read_bytes() == PNG and runtime.render_calls == 0
