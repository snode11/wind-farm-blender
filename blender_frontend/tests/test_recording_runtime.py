"""Exercise production recording methods with Blender-shaped timer events."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def recording(frontend_module, monkeypatch):
    def make(clock, screenshot):
        panel = frontend_module('wfrl_blender.panels.presentation')
        timers = set()
        monkeypatch.setattr(panel, 'time', SimpleNamespace(monotonic=lambda: clock[0], time_ns=lambda: 1))
        monkeypatch.setattr(panel, 'bpy', SimpleNamespace(
            ops=SimpleNamespace(screen=SimpleNamespace(screenshot=screenshot)),
            app=SimpleNamespace(timers=SimpleNamespace(is_registered=lambda f: f in timers,
                register=lambda f, **_kwargs: timers.add(f), unregister=timers.remove))))
        monkeypatch.setattr(panel, '_ACTIVE', None)
        monkeypatch.setattr(panel, '_directory', lambda scene: scene.directory)
        return panel.WFRL_OT_CaptureRecording, panel
    return make


def recording_context(scene, manager):
    window = SimpleNamespace(scene=scene)
    manager.windows = [window]
    return SimpleNamespace(scene=scene, window=window, window_manager=manager)


class Scene(dict):
    frame_current = 7
    wfrl_capture_fps = 1
    wfrl_capture_duration = .1


def test_short_recording_has_initial_frame_and_accepts_real_timer_shape(recording, tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    assert recorder.execute(context) == {'RUNNING_MODAL'}
    assert screenshot.call_count == 1
    unrelated = recorder._directory / 'manifest.json.tmp'
    unrelated.write_bytes(b'unrelated file')
    clock[0] = 1.01
    assert recorder.modal(context, SimpleNamespace(type='TIMER')) == {'FINISHED'}
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] == 'COMPLETE'
    assert len(manifest['frames']) == 1
    assert scene.frame_current == 7
    assert unrelated.read_bytes() == b'unrelated file'
    assert set(recorder._directory.iterdir()) == {unrelated, recorder._directory / 'manifest.json'}
    assert namespace._ACTIVE is None
    manager.event_timer_remove.assert_called_once()


def test_timer_jitter_preserves_capture_grid(recording, tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    scene.wfrl_capture_fps = 10
    scene.wfrl_capture_duration = 2.0
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    assert recorder.execute(context) == {'RUNNING_MODAL'}
    jitter = [.0005, .003, .001, .004, .002]
    for k in range(1, 22):
        clock[0] = k * .1 + jitter[k % len(jitter)]
        if recorder.modal(context, SimpleNamespace(type='TIMER')) == {'FINISHED'}:
            break
    else:
        pytest.fail('Recording did not finish at its duration')

    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] == 'COMPLETE'
    assert len(manifest['frames']) == 20
    elapsed = [frame['elapsed_s'] for frame in manifest['frames']]
    assert all(previous < current for previous, current in zip(elapsed, elapsed[1:]))
    assert elapsed == pytest.approx([0.0] + [
        k * .1 + jitter[k % len(jitter)] for k in range(1, 20)])
    assert namespace._ACTIVE is None


def test_late_timer_skips_missed_grid_points_without_catch_up(recording, tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    scene.wfrl_capture_fps = 10
    scene.wfrl_capture_duration = 1.0
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    assert recorder.execute(context) == {'RUNNING_MODAL'}
    assert screenshot.call_count == 1
    for timestamp, count in [(.1, 2), (.35, 3), (.35, 3), (.399, 3), (.401, 4)]:
        clock[0] = timestamp
        assert recorder.modal(context, SimpleNamespace(type='TIMER')) == {'PASS_THROUGH'}
        assert screenshot.call_count == count

    recorder.cancel(context)
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert [frame['elapsed_s'] for frame in manifest['frames']] == [0.0, .1, .35, .401]
    assert namespace._ACTIVE is None


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
@pytest.mark.parametrize('field', ['elapsed_s', 'duration_s', 'requested_fps'])
def test_nonfinite_manifest_keeps_destination_and_releases_recording(recording, tmp_path, field, value):
    cls, namespace = recording([0.], Mock(return_value={'FINISHED'}))
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    assert recorder.execute(context) == {'RUNNING_MODAL'}
    target = recorder._directory / 'manifest.json'
    previous = b'{"status": "saved"}\n'
    target.write_bytes(previous)
    if field == 'elapsed_s':
        recorder._frames[0]['elapsed_s'] = value
    elif field == 'duration_s':
        recorder._duration = value
    else:
        recorder._fps = value

    recorder.cancel(context)

    assert scene['wfrl_capture_status'].startswith('Manifest write failed: ')
    assert target.read_bytes() == previous
    assert list(recorder._directory.iterdir()) == [target]
    assert namespace._ACTIVE is None
    assert recorder._closed
    manager.event_timer_remove.assert_called_once()


def test_foreign_timer_cannot_accelerate_capture_and_failure_cleans_up(recording, tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
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
    assert namespace._ACTIVE is None
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
def test_removed_source_finalizes_recording_without_scene_access(recording, tmp_path, termination):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
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
    assert namespace._ACTIVE is None
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] in {'FAILED', 'CANCELLED'}
    assert len(manifest['frames']) == 1
    recorder.cancel(context)
    manager.event_timer_remove.assert_called_once()


def test_removed_timer_does_not_prevent_manifest_or_release(recording, tmp_path):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(return_value=object()),
        event_timer_remove=Mock(side_effect=ReferenceError('timer removed')), modal_handler_add=Mock())
    context = recording_context(scene, manager)
    recorder.execute(context)
    recorder.cancel(context)
    assert namespace._ACTIVE is None
    assert json.loads((recorder._directory / 'manifest.json').read_text())['status'] == 'CANCELLED'


@pytest.mark.parametrize('interruption', ['window_closed', 'scene_switched', 'file_load'])
def test_recording_lifecycle_interruptions_cancel_and_release(recording, tmp_path, interruption):
    clock, screenshot = [0.0], Mock(return_value={'FINISHED'})
    cls, namespace = recording(clock, screenshot)
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
        namespace._stop_recording(None)
    else:
        if interruption == 'window_closed':
            manager.windows.clear()
        else:
            context.window.scene = Scene(other=True)
        assert namespace._watch_recording() is None
    assert namespace._ACTIVE is None
    assert screenshot.call_count == 1
    manifest = json.loads((recorder._directory / 'manifest.json').read_text())
    assert manifest['status'] == 'CANCELLED'
    assert len(manifest['frames']) == 1
    recorder.modal(context, SimpleNamespace(type='ESC'))
    manager.event_timer_remove.assert_called_once()


def test_recording_start_failure_releases_ownership_and_saves_failure(recording, tmp_path):
    cls, namespace = recording([0.], Mock(return_value={'FINISHED'}))
    recorder = cls()
    recorder.report = Mock()
    scene = Scene()
    scene.directory = tmp_path
    manager = SimpleNamespace(event_timer_add=Mock(side_effect=RuntimeError('window unavailable')),
                              event_timer_remove=Mock(), modal_handler_add=Mock())
    assert recorder.execute(recording_context(scene, manager)) == {'CANCELLED'}
    assert namespace._ACTIVE is None
    assert json.loads((recorder._directory / 'manifest.json').read_text())['status'] == 'FAILED'
