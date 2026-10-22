"""Exercise production recording methods with Blender-shaped timer events."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).parents[1] / 'wfrl_blender/panels/presentation.py'


def recording_class(clock, screenshot):
    tree = ast.parse(SOURCE.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'WFRL_OT_CaptureRecording')
    node.bases = []
    from wfrl_blender.presentation import recording_schedule
    namespace = dict(time=SimpleNamespace(monotonic=lambda: clock[0], time_ns=lambda: 1),
                     bpy=SimpleNamespace(ops=SimpleNamespace(screen=SimpleNamespace(screenshot=screenshot))),
                     json=json, _ACTIVE=None, recording_schedule=recording_schedule,
                     _directory=lambda scene: scene.directory)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['WFRL_OT_CaptureRecording'], namespace


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
    context = SimpleNamespace(scene=scene, window=object(), window_manager=manager)
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
    context = SimpleNamespace(scene=scene, window=object(), window_manager=manager)
    recorder.execute(context)
    clock[0] = .05
    recorder.modal(context, SimpleNamespace(type='TIMER'))
    assert screenshot.call_count == 1
    screenshot.side_effect = RuntimeError('capture failed')
    clock[0] = 1.0
    assert recorder.modal(context, SimpleNamespace(type='TIMER')) == {'CANCELLED'}
    assert namespace['_ACTIVE'] is None
    assert 'FAILED' in scene['wfrl_capture_status']
