"""Exercise production recording methods with Blender-shaped timer events."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).parents[1] / 'wfrl_blender/panels/presentation.py'


def recording_class(clock, screenshot):
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WFRL_OT_CaptureRecording')
    node.bases = []
    from wfrl_blender.presentation import recording_schedule
    timers = set()
    namespace = dict(time=SimpleNamespace(monotonic=lambda: clock[0], time_ns=lambda: 1),
                     bpy=SimpleNamespace(ops=SimpleNamespace(screen=SimpleNamespace(screenshot=screenshot)),
                         app=SimpleNamespace(timers=SimpleNamespace(is_registered=lambda f: f in timers,
                             register=lambda f, **_kwargs: timers.add(f), unregister=timers.remove))),
                     json=json, _ACTIVE=None, recording_schedule=recording_schedule,
                     _directory=lambda scene: scene.directory)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {'_watch_recording', '_stop_recording', '_ensure_recording_watch'}]
    exec(compile(ast.Module(body=functions + [node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['WFRL_OT_CaptureRecording'], namespace


def recording_context(scene, manager):
    window = SimpleNamespace(scene=scene)
    manager.windows = [window]
    return SimpleNamespace(scene=scene, window=window, window_manager=manager)


class Scene(dict):
    frame_current = 7
    wfrl_capture_fps = 1
    wfrl_capture_duration = .1


def test_short_recording_has_initial_frame_and_accepts_real_timer_shape(tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording_class(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    assert recorder.execute(context) == {'RUNNING_MODAL'}
    assert screenshot.call_count == 1
    clock[0] = 1.01
    assert recorder.modal(context, SimpleNamespace(type='TIMER')) == {'FINISHED'}
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] == 'COMPLETE'
    assert len(manifest['frames']) == 1
    assert scene.frame_current == 7
    assert namespace['_ACTIVE'] is None
    manager.event_timer_remove.assert_called_once()


def test_foreign_timer_cannot_accelerate_capture_and_failure_cleans_up(tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording_class(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    scene.wfrl_capture_duration = 3
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    recorder.execute(context)
    clock[0] = .05
    recorder.modal(context, SimpleNamespace(type='TIMER'))
    assert screenshot.call_count == 1
    screenshot.side_effect = RuntimeError('capture failed')
    clock[0] = 1.0
    assert recorder.modal(context, SimpleNamespace(type='TIMER')) == {'CANCELLED'}
    assert namespace['_ACTIVE'] is None
    assert 'FAILED' in scene['wfrl_capture_status']


class RemovedScene(Scene):
    removed = False

    def __getattribute__(self, name):
        if name == 'frame_current' and self.removed:
            raise ReferenceError('StructRNA of type Scene has been removed')
        return super().__getattribute__(name)

    def __setitem__(self, key, value):
        if self.removed:
            raise ReferenceError('StructRNA of type Scene has been removed')
        super().__setitem__(key, value)


@pytest.mark.parametrize('termination', ['tick', 'tick_after_deadline', 'cancel'])
def test_removed_source_finalizes_recording_without_scene_access(tmp_path, termination):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording_class(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = RemovedScene()
    scene.directory = tmp_path
    scene.wfrl_capture_duration = 3
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    recorder.execute(context)
    scene.removed = True
    if termination in {'tick', 'tick_after_deadline'}:
        clock[0] = 1 if termination == 'tick' else 3.1
        assert recorder.modal(context, SimpleNamespace(type='TIMER')) == {'CANCELLED'}
    else:
        recorder.cancel(context)
    assert namespace['_ACTIVE'] is None
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] in {'FAILED', 'CANCELLED'}
    assert len(manifest['frames']) == 1
    recorder.cancel(context)
    manager.event_timer_remove.assert_called_once()


def test_removed_timer_does_not_prevent_manifest_or_release(tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording_class(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
        event_timer_remove=Mock(side_effect=ReferenceError('timer removed')), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    recorder.execute(context)
    recorder.cancel(context)
    assert namespace['_ACTIVE'] is None
    assert json.loads((recorder._directory / 'manifest.json').read_text())['status'] == 'CANCELLED'


@pytest.mark.parametrize('interruption', ['window_closed', 'scene_switched', 'file_load'])
def test_recording_lifecycle_interruptions_cancel_and_release(tmp_path, interruption):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording_class(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    scene.wfrl_capture_duration = 3
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    assert recorder.execute(context) == {'RUNNING_MODAL'}
    if interruption == 'file_load':
        namespace['_stop_recording'](None)
    else:
        if interruption == 'window_closed':
            manager.windows.clear()
        else:
            context.window.scene = Scene(other=True)
        assert namespace['_watch_recording']() is None
    assert namespace['_ACTIVE'] is None
    assert screenshot.call_count == 1
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] == 'CANCELLED'
    assert len(manifest['frames']) == 1
    recorder.modal(context, SimpleNamespace(type='ESC'))
    manager.event_timer_remove.assert_called_once()


def test_recording_start_failure_releases_ownership_and_saves_failure(tmp_path):
    cls, namespace = recording_class([0.], Mock(return_value={'FINISHED'}))
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(side_effect=RuntimeError('window unavailable')),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    assert recorder.execute(recording_context(scene, manager)) == {'CANCELLED'}
    assert namespace['_ACTIVE'] is None
    assert json.loads((recorder._directory / 'manifest.json').read_text())['status'] == 'FAILED'
