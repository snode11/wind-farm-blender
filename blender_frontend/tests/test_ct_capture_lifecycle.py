"""Window capture publishes complete frames and retains failed cleanup ownership."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from blender_frontend.tests.test_recording_runtime import Scene, recording, recording_context


@pytest.mark.parametrize('failure', ['cancel', 'exception'])
@pytest.mark.parametrize('kind', ['screenshot', 'recording'])
def test_capture_failure_never_leaves_partial_final_png(recording, tmp_path, failure, kind):
    def screenshot(*, filepath):
        Path(filepath).write_bytes(b'partial PNG')
        if failure == 'exception':
            raise RuntimeError('screen capture failed')
        return {'CANCELLED'}

    _cls, panel = recording([0.], Mock(side_effect=screenshot))
    scene = Scene()
    scene.directory = tmp_path
    manager = NS(event_timer_add=Mock(return_value=object()), event_timer_remove=Mock(),
                 modal_handler_add=Mock())
    context = recording_context(scene, manager)
    operator = (panel.WFRL_OT_CaptureScreenshot() if kind == 'screenshot'
                else panel.WFRL_OT_CaptureRecording())
    operator.report = Mock()

    assert operator.execute(context) == {'CANCELLED'}
    assert not list(tmp_path.rglob('*.png'))
    assert not list(tmp_path.rglob('.wfrl-*'))
    assert panel._ACTIVE is None
    assert operator.report.call_args.args[0] == {'ERROR'}
    if kind == 'recording':
        manifest = json.loads((operator._directory / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['frames'] == [] and manifest['status'] == 'FAILED'


def test_recording_cleanup_failure_retains_timer_until_retry(recording, tmp_path):
    def screenshot(*, filepath):
        Path(filepath).write_bytes(b'complete PNG')
        return {'FINISHED'}

    cls, panel = recording([0.], Mock(side_effect=screenshot))
    scene = Scene()
    scene.directory, scene.name = tmp_path, 'Source'
    scene.wfrl_capture_duration = 3.
    manager = NS(event_timer_add=Mock(return_value=object()),
                 event_timer_remove=Mock(side_effect=RuntimeError('timer still busy')),
                 modal_handler_add=Mock())
    context = recording_context(scene, manager)
    operator = cls()
    operator.report = Mock()
    assert operator.execute(context) == {'RUNNING_MODAL'}

    operator.cancel(context)
    assert panel._ACTIVE is operator and operator._timer is not None
    assert 'timer still busy' in scene['wfrl_capture_status']
    assert panel.status_for(scene).stage == 'CANCELLING'
    other = cls()
    other.report = Mock()
    assert other.execute(context) == {'CANCELLED'}
    manager.event_timer_remove.side_effect = None
    assert panel._watch_recording() is None
    assert panel._ACTIVE is None and operator._timer is None
    assert operator._scene is None and operator._window is None
    assert not panel.bpy.app.timers.is_registered(panel._watch_recording)


def test_recording_status_survives_deleted_scene_before_watchdog(recording, tmp_path):
    class RemovedScene(Scene):
        removed = False
        name = 'Source'

        def __getattribute__(self, name):
            if name in {'name', 'frame_current'} and self.removed:
                raise ReferenceError('Scene removed')
            return super().__getattribute__(name)

        def __setitem__(self, key, value):
            if self.removed:
                raise ReferenceError('Scene removed')
            super().__setitem__(key, value)

    def screenshot(*, filepath):
        Path(filepath).write_bytes(b'complete PNG')
        return {'FINISHED'}

    cls, panel = recording([0.], Mock(side_effect=screenshot))
    scene = RemovedScene()
    scene.directory = tmp_path
    manager = NS(event_timer_add=Mock(return_value=object()), event_timer_remove=Mock(),
                 modal_handler_add=Mock())
    context = recording_context(scene, manager)
    operator = cls()
    operator.report = Mock()
    assert operator.execute(context) == {'RUNNING_MODAL'}
    scene.removed = True

    status = panel.status_for(Scene(other=True))
    assert status.active and status.stage == 'CANCELLING'
    assert status.scene_name == 'Source'
    assert panel._watch_recording() is None
    assert panel._ACTIVE is None


def test_recording_manifest_failure_cannot_finish_successfully(recording, monkeypatch, tmp_path):
    clock = [0.]
    cls, panel = recording(clock, Mock(return_value={'FINISHED'}))
    scene = Scene()
    scene.directory = tmp_path
    manager = NS(event_timer_add=Mock(return_value=object()), event_timer_remove=Mock(),
                 modal_handler_add=Mock())
    context = recording_context(scene, manager)
    operator = cls()
    operator.report = Mock()
    assert operator.execute(context) == {'RUNNING_MODAL'}
    monkeypatch.setattr(panel, 'write_json', Mock(side_effect=OSError('disk full')))
    clock[0] = 1.

    assert operator.modal(context, NS(type='TIMER')) == {'CANCELLED'}
    assert panel._ACTIVE is None and operator._timer is None
    assert 'disk full' in operator.report.call_args.args[1]
    assert scene['wfrl_capture_status'].startswith('Manifest write failed:')


def test_presentation_failure_restores_workspace_and_all_view_chrome(frontend_module):
    panel = frontend_module('wfrl_blender.panels.presentation')

    class FailingSpace:
        show_region_toolbar = True
        show_region_tool_header = True

        @property
        def show_gizmo(self):
            return True

        @show_gizmo.setter
        def show_gizmo(self, enabled):
            if not enabled:
                raise RuntimeError('view became unavailable')

    normal = NS(show_region_toolbar=True, show_region_tool_header=False, show_gizmo=True)
    failing = FailingSpace()
    areas = [NS(type='VIEW_3D', spaces=NS(active=space), tag_redraw=Mock())
             for space in (normal, failing)]
    context = NS(scene=Scene(), workspace={}, screen=NS(areas=areas))
    operator = panel.WFRL_OT_PresentationMode()
    operator.report = Mock()

    assert operator.execute(context) == {'CANCELLED'}
    assert context.workspace == {} and context.scene == {}
    assert vars(normal) == dict(show_region_toolbar=True, show_region_tool_header=False, show_gizmo=True)
    assert failing.show_region_toolbar and failing.show_region_tool_header and failing.show_gizmo
    assert 'view became unavailable' in operator.report.call_args.args[1]


def test_recording_cleanup_retry_survives_file_load(recording, monkeypatch, tmp_path):
    cls, panel = recording([0.], Mock(return_value={'FINISHED'}))
    timers = {}
    monkeypatch.setattr(panel.bpy.app.timers, 'is_registered', lambda fn: fn in timers)
    monkeypatch.setattr(panel.bpy.app.timers, 'register',
                        lambda fn, **kwargs: timers.__setitem__(fn, kwargs.get('persistent', False)))
    monkeypatch.setattr(panel.bpy.app.timers, 'unregister', lambda fn: timers.pop(fn))
    scene = Scene()
    scene.directory = tmp_path
    manager = NS(event_timer_add=Mock(return_value=object()),
                 event_timer_remove=Mock(side_effect=RuntimeError('timer busy during load')),
                 modal_handler_add=Mock())
    operator = cls()
    operator.report = Mock()
    assert operator.execute(recording_context(scene, manager)) == {'RUNNING_MODAL'}

    panel._stop_recording()
    for fn, persistent in list(timers.items()):
        if not persistent:
            del timers[fn]
    assert panel._ACTIVE is operator and panel._watch_recording in timers
    manager.event_timer_remove.side_effect = ReferenceError('old window manager removed')
    assert panel._watch_recording() is None
    assert panel._ACTIVE is None and not timers
