"""Optional diagnostics tolerate unavailable metadata without leaking errors."""
import sys
from types import SimpleNamespace as NS

import pytest

from wfrl_blender import frontend_diagnostics as diagnostics


class Layout:
    def __init__(self):
        self.labels = []

    def label(self, *, text, **kwargs):
        self.labels.append(text)

    def prop(self, *args, **kwargs):
        pass

    def operator(self, *args, **kwargs):
        pass


@pytest.fixture
def context(stub_module):
    class Scene(dict):
        wfrl_diagnostics_sampling = False

    scene = Scene(wfrl_farm_flex_path='/tmp/data', wfrl_clearance_time_s=2.)
    stub_module('wfrl_blender.farm_flex', is_active=lambda _: False)
    stub_module('wfrl_blender.playback', is_playing=lambda _: False)
    stub_module('wfrl_blender.panels.ui_helpers',
                message=lambda layout, text, **_: layout.label(text=text))
    return NS(scene=scene, window=None, area=None)


def test_unreadable_build_identity_does_not_break_snapshot_or_panel(context, monkeypatch):
    def unavailable():
        raise OSError('private-token=do-not-copy-this')

    monkeypatch.setattr(diagnostics, 'build_information', unavailable)
    info = diagnostics.snapshot(context)
    assert info['source_path'] == '/tmp/data'
    assert info['build'] is None and info['version'] is None
    assert info['packaged'] is None
    assert 'do-not-copy-this' not in repr(info)
    layout = Layout()
    diagnostics.draw_panel(layout, context)
    assert any('不可用' in text for text in layout.labels)


def test_diagnostic_panel_preserves_unknown_timeline_rate(context, monkeypatch):
    monkeypatch.setattr(diagnostics, 'snapshot', lambda _: dict(
        version='1', packaged=False, build='abc', module='wfrl_blender', source_path=None,
        source_hz=30., timeline_hz=None, viewport_fps=None))
    layout = Layout()
    diagnostics.draw_panel(layout, context)
    assert any('30 Hz' in text and '未知' in text for text in layout.labels)


def test_diagnostic_draw_callback_contains_deleted_context_errors(monkeypatch):
    class DeletedScene:
        def as_pointer(self):
            return 3

        @property
        def frame_current(self):
            raise ReferenceError('removed scene')

    context = NS(window=NS(as_pointer=lambda: 1), area=NS(as_pointer=lambda: 2),
                 scene=DeletedScene())
    monkeypatch.setitem(sys.modules, 'bpy', NS(context=context))
    diagnostics._draw()


def test_stale_draw_handle_cannot_prevent_diagnostics_unregistration(monkeypatch):
    def removed(*_):
        raise ReferenceError('draw handler is already removed')

    load_pre = [diagnostics._on_load]
    bpy = NS(_wfrl_diagnostics_handle=object(),
             _wfrl_diagnostics_load_handler=diagnostics._on_load,
             types=NS(SpaceView3D=NS(draw_handler_remove=removed)),
             app=NS(handlers=NS(load_pre=load_pre)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    diagnostics.unregister()
    assert load_pre == []
    assert not hasattr(bpy, '_wfrl_diagnostics_handle')


def test_draw_removal_failure_still_removes_load_handler_and_can_retry(monkeypatch):
    def failed(*_):
        raise RuntimeError('temporary removal failure')

    handle = object()
    load_pre = [diagnostics._on_load]
    bpy = NS(_wfrl_diagnostics_handle=handle,
             _wfrl_diagnostics_load_handler=diagnostics._on_load,
             types=NS(SpaceView3D=NS(draw_handler_remove=failed)),
             app=NS(handlers=NS(load_pre=load_pre)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    diagnostics.unregister()
    assert load_pre == []
    assert bpy._wfrl_diagnostics_handle is handle
    bpy.types.SpaceView3D.draw_handler_remove = lambda *_: None
    diagnostics.unregister()
    assert not hasattr(bpy, '_wfrl_diagnostics_handle')


def test_optional_diagnostics_registration_failure_does_not_abort_frontend(monkeypatch):
    def failed(*_):
        raise RuntimeError('draw registration failed')

    load_pre = []
    bpy = NS(types=NS(SpaceView3D=NS(draw_handler_add=failed)),
             app=NS(handlers=NS(load_pre=load_pre)))
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'bpy.app.handlers', NS(persistent=lambda fn: fn))
    diagnostics.register()
    assert load_pre == []
    assert not hasattr(bpy, '_wfrl_diagnostics_handle')
    assert diagnostics._ERROR == 'RuntimeError'
