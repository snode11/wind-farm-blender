"""A failed output cleanup must not strand other canvases or radar sessions."""
from types import SimpleNamespace

import pytest

from wfrl_blender.radar_x100 import runtime


def test_draw_handler_failure_still_releases_canvas_frame_and_gpu(frontend_module, monkeypatch):
    output = frontend_module('wfrl_blender.radar_x100.output')
    released = []
    item = SimpleNamespace(window_id=7, frame=object(), data_key=object(), source_key=object(),
        release_gpu=lambda: released.append(True), alive=lambda: (None, None))
    monkeypatch.setattr(output, '_WINDOWS', {7: item})
    monkeypatch.setattr(output, '_HANDLE', object())
    output.bpy.types.SpaceImageEditor.draw_handler_remove.side_effect = RuntimeError('handler cleanup failed')
    with pytest.raises(RuntimeError, match='handler cleanup failed'):
        output.close(item)
    assert released == [True]
    assert item.frame is item.data_key is item.source_key is None
    assert item.xy.shape == (0, 2) and item.colors.shape == (0, 4)


def test_close_all_attempts_every_canvas_and_unregisters_timers_after_failure(frontend_module, monkeypatch):
    output = frontend_module('wfrl_blender.radar_x100.output')
    items = [SimpleNamespace(window_id=1), SimpleNamespace(window_id=2)]
    closed, timers = [], {output.tick, output.discard_saved_windows}
    monkeypatch.setattr(output, '_WINDOWS', {item.window_id: item for item in items})
    def close(item):
        closed.append(item.window_id)
        output._WINDOWS.pop(item.window_id)
        if item.window_id == 1:
            raise RuntimeError('first canvas cleanup failed')
    monkeypatch.setattr(output, 'close', close)
    output.bpy.app.timers.is_registered.side_effect = lambda callback: callback in timers
    output.bpy.app.timers.unregister.side_effect = timers.remove
    with pytest.raises(RuntimeError, match='first canvas cleanup failed'):
        output.close_all()
    assert closed == [1, 2] and not timers and not output._WINDOWS


def test_close_all_retries_a_remaining_handler_without_live_canvases(frontend_module, monkeypatch):
    output = frontend_module('wfrl_blender.radar_x100.output')
    handle = object()
    monkeypatch.setattr(output, '_WINDOWS', {})
    monkeypatch.setattr(output, '_HANDLE', handle)
    output.bpy.app.timers.is_registered.return_value = False
    output.close_all()
    assert output._HANDLE is None
    output.bpy.types.SpaceImageEditor.draw_handler_remove.assert_called_once_with(handle, 'WINDOW')


@pytest.mark.parametrize('entry', ['load', 'unregister'])
def test_output_cleanup_failure_does_not_skip_session_and_handler_teardown(frontend_module, monkeypatch, stub_module, entry):
    bpy = frontend_module('bpy')
    events = []
    def failed_output():
        raise RuntimeError('output window cleanup failed')
    stub_module('wfrl_blender.radar_x100.output', close_all=failed_output, on_load_post=lambda _: None)
    def close(**kwargs):
        events.append('session')
        return True
    scene = {}
    session = SimpleNamespace(close=close, scene=scene)
    monkeypatch.setattr(runtime, '_SESSIONS', {1: session})
    monkeypatch.setattr(runtime, '_RESTORE_ATTEMPTED', {1})
    monkeypatch.setattr(runtime, '_DRAWN', {1: object()})
    monkeypatch.setattr(runtime, '_SHUTTING_DOWN', False)
    if entry == 'load':
        runtime.on_load_pre(None)
        assert session.restore_after_close
    else:
        stub_module('wfrl_blender.panels.radar_x100', unregister_properties=lambda: events.append('properties'))
        monkeypatch.setattr(runtime.view, 'unregister', lambda: events.append('draw handler'))
        bpy.app.handlers.load_pre = [runtime.on_load_pre]
        bpy.app.handlers.undo_pre = [runtime.on_load_pre]
        bpy.app.handlers.redo_pre = [runtime.on_load_pre]
        bpy.app.handlers.load_post = []
        bpy.app.timers.is_registered.return_value = True
        with pytest.raises(RuntimeError, match='output window cleanup failed'):
            runtime.unregister()
        bpy.app.timers.unregister.assert_called_once_with(runtime.tick)
        assert events == ['session', 'draw handler', 'properties']
        assert not bpy.app.handlers.load_pre and not bpy.app.handlers.undo_pre and not bpy.app.handlers.redo_pre
    assert 'session' in events
    assert not runtime._SESSIONS and not runtime._RESTORE_ATTEMPTED and not runtime._DRAWN
