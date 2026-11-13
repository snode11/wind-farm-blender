"""Exclusive layout publication and caller-owned capture UI resources."""
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


@pytest.fixture
def output(frontend_module, monkeypatch):
    module = frontend_module('wfrl_blender.panels.custom_camera_output')
    monkeypatch.setattr(module.bpy.path, 'abspath', lambda path: path)
    return module


@pytest.mark.parametrize('failure', ['serialization', 'write'])
def test_layout_export_failure_leaves_no_partial_target(output, tmp_path, monkeypatch, failure):
    target = tmp_path / 'layout.json'
    payload = {'value': float('nan')} if failure == 'serialization' else {'value': 1}
    monkeypatch.setattr(output.core, 'layout_dict', lambda _scene: payload)
    if failure == 'write':
        def fail_dump(_payload, stream, **_kwargs):
            stream.write('{')
            raise OSError('injected layout disk failure')
        monkeypatch.setattr(output.json, 'dump', fail_dump)
    operator = output.WFRL_OT_CustomLayoutExport()
    operator.filepath, operator.report = str(target), lambda *_args: None
    assert operator.execute(NS(scene=object())) == {'CANCELLED'}
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_layout_export_preserves_existing_file(output, tmp_path, monkeypatch):
    target = tmp_path / 'layout.json'
    target.write_text('previous result', encoding='utf-8')
    monkeypatch.setattr(output.core, 'layout_dict', lambda _scene: {'new': True})
    operator = output.WFRL_OT_CustomLayoutExport()
    operator.filepath, operator.report = str(target), lambda *_args: None
    assert operator.execute(NS(scene=object())) == {'CANCELLED'}
    assert target.read_text(encoding='utf-8') == 'previous result'
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize('error', [ReferenceError('removed RNA'), RuntimeError('handler not found')])
def test_removed_draw_handler_does_not_leak_capture_timer(output, error):
    removed = []
    operator = output.WFRL_OT_CustomCapture()
    operator.handle, operator.timer = object(), object()
    timer = operator.timer
    operator.wm = NS(event_timer_remove=removed.append)
    output.bpy.types.SpaceView3D.draw_handler_remove.side_effect = error
    operator.remove_timer()
    assert operator.handle is None and operator.timer is None
    assert removed == [timer]


def test_unknown_draw_release_failure_keeps_retry_handle_but_removes_timer(output):
    removed = []
    operator = output.WFRL_OT_CustomCapture()
    operator.handle, operator.timer = object(), object()
    handle, timer = operator.handle, operator.timer
    operator.wm = NS(event_timer_remove=removed.append)
    output.bpy.types.SpaceView3D.draw_handler_remove.side_effect = RuntimeError('GPU busy')
    with pytest.raises(RuntimeError, match='GPU busy'):
        operator.remove_timer()
    assert operator.handle is handle and operator.timer is None
    assert removed == [timer]
    output.bpy.types.SpaceView3D.draw_handler_remove.side_effect = None
    operator.remove_timer()
    assert operator.handle is None and removed == [timer]


def test_capture_does_not_fall_back_to_an_unrelated_viewport(output, monkeypatch):
    other = NS(type='VIEW_3D', regions=[NS(type='WINDOW')], tag_redraw=lambda: None)
    source = NS(type='IMAGE_EDITOR')
    calls = []
    scene = object()
    context = NS(area=other, scene=scene, window=NS(scene=scene, screen=NS(areas=[source, other])),
                 window_manager=NS(event_timer_add=lambda *_args, **_kwargs: object(),
                                   modal_handler_add=lambda _op: None),
                 temp_override=lambda **kwargs: nullcontext())
    monkeypatch.setattr(output, 'requested_times', lambda _scene: None)
    monkeypatch.setattr(output.capture, 'require_preflight', lambda *_args: None)
    monkeypatch.setattr(output.capture, 'Capture', lambda *args: calls.append(args) or NS(closed=False))
    operator = output.WFRL_OT_CustomCapture()
    operator.area, operator.directory, operator.report = source, '/tmp', lambda *_args: None
    assert operator.execute(context) == {'CANCELLED'}
    assert calls == []


def test_capture_uses_invoking_window_when_file_selector_has_other_context(output, monkeypatch):
    redraws, captures, timer_windows, modal_windows = [], [], [], []
    scene = object()
    area = NS(type='VIEW_3D', regions=[NS(type='WINDOW')], tag_redraw=lambda: redraws.append('owner'))
    window = NS(scene=scene, screen=NS(areas=[area]))
    browser = NS(type='FILE_BROWSER')
    browser_window = NS(scene=object(), screen=NS(areas=[browser]))
    manager = NS(windows=[window, browser_window],
                 event_timer_add=lambda _interval, **kwargs: timer_windows.append(kwargs['window']) or object(),
                 modal_handler_add=lambda _op: modal_windows.append(context.window),
                 event_timer_remove=lambda _timer: None)
    context = NS(scene=browser_window.scene, area=browser, window=browser_window, window_manager=manager)

    @contextmanager
    def override(**values):
        saved = dict(context.__dict__)
        context.__dict__.update(values)
        context.scene = context.window.scene
        try:
            yield
        finally:
            context.__dict__.clear()
            context.__dict__.update(saved)

    context.temp_override = override
    monkeypatch.setattr(output, 'requested_times', lambda _scene: None)
    monkeypatch.setattr(output.capture, 'require_preflight', lambda *_args: None)
    monkeypatch.setattr(output.capture, 'Capture', lambda ctx, *_args:
                        captures.append((ctx.window, ctx.area, ctx.scene)) or NS(closed=False))
    operator = output.WFRL_OT_CustomCapture()
    operator.area, operator.window, operator.scene = area, window, scene
    operator.directory, operator.report = '/tmp', lambda *_args: None
    assert operator.execute(context) == {'RUNNING_MODAL'}
    assert captures == [(window, area, scene)]
    assert timer_windows == [window] and redraws == ['owner']
    assert modal_windows == [window]
    assert context.window is browser_window and context.area is browser
    operator.remove_timer()


def test_capture_modal_registration_failure_releases_its_timer(output, monkeypatch):
    removed, finished = [], []
    scene = object()
    area = NS(type='VIEW_3D', regions=[NS(type='WINDOW')], tag_redraw=lambda: None)
    window = NS(scene=scene, screen=NS(areas=[area]))
    manager = NS(windows=[window], event_timer_add=lambda *_args, **_kwargs: object(),
                 modal_handler_add=lambda _op: False, event_timer_remove=removed.append)
    context = NS(scene=scene, area=area, window=window, window_manager=manager,
                 temp_override=lambda **_kwargs: nullcontext())
    job = NS(closed=False, finish=lambda **_kwargs: finished.append(True))
    monkeypatch.setattr(output, 'requested_times', lambda _scene: None)
    monkeypatch.setattr(output.capture, 'require_preflight', lambda *_args: None)
    monkeypatch.setattr(output.capture, 'Capture', lambda *_args: job)
    operator = output.WFRL_OT_CustomCapture()
    operator.directory, operator.report = '/tmp', lambda *_args: None
    assert operator.execute(context) == {'CANCELLED'}
    assert finished == [True] and len(removed) == 1
    assert operator.timer is None
