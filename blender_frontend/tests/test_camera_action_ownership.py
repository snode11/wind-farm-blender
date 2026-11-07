"""Camera actions keep another scene's active editing session untouched."""
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize('action', ['CANCEL', 'CONFIRM', 'LEFT', 'RIGHT', 'AIM_LEFT', 'AIM_RIGHT'])
def test_box_action_rejects_another_scene(frontend_module, monkeypatch, action):
    panel = frontend_module('wfrl_blender.panels.stacked_camera_rig')
    owner, other = object(), object()
    transaction = SimpleNamespace(finish=Mock())
    active = SimpleNamespace(scene=owner, transaction=transaction, cleanup=Mock(),
                             wm=SimpleNamespace(wfrl_box_spin=0., wfrl_box_aim=0.))
    shutdown = Mock()
    monkeypatch.setattr(panel, '_ACTIVE', active)
    monkeypatch.setattr(panel, 'shutdown', shutdown)
    result = panel.WFRL_OT_BoxAction.execute(SimpleNamespace(action=action, report=Mock()),
                                             SimpleNamespace(scene=other))
    assert result == {'CANCELLED'}
    shutdown.assert_not_called()
    transaction.finish.assert_not_called()
    active.cleanup.assert_not_called()
    assert active.wm.wfrl_box_spin == active.wm.wfrl_box_aim == 0.


def test_selecting_a_slot_does_not_switch_another_scenes_observation(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.custom_cameras')
    capture = ModuleType('wfrl_blender.custom_camera_capture')
    capture.active = lambda: False
    monkeypatch.setitem(sys.modules, capture.__name__, capture)
    monkeypatch.setattr(sys.modules['wfrl_blender'], 'custom_camera_capture', capture)
    active = SimpleNamespace(scene=object(), stage='WATCH', closed=False,
                             select_slot=Mock(), camera=object())
    monkeypatch.setattr(panel, '_ACTIVE', active)
    monkeypatch.setattr(panel, '_redraw_if_changed', Mock())
    wm = SimpleNamespace(wfrl_custom_slot=1)
    context = SimpleNamespace(scene=object(), window_manager=wm,
                              area=SimpleNamespace(tag_redraw=Mock()))
    result = panel.WFRL_OT_CustomAction.execute(SimpleNamespace(action='SLOT', slot=2, report=Mock()), context)
    assert result == {'FINISHED'}
    assert wm.wfrl_custom_slot == 2
    active.select_slot.assert_not_called()


def test_capture_cancel_rejects_another_scene(frontend_module, monkeypatch):
    panel = frontend_module('wfrl_blender.panels.custom_camera_output')
    owner, other = object(), object()
    active = SimpleNamespace(scene=owner, request_cancel=Mock())
    monkeypatch.setattr(panel, 'capture', SimpleNamespace(active=lambda: True, _ACTIVE=active))
    result = panel.WFRL_OT_CustomCaptureCancel.execute(SimpleNamespace(report=Mock()),
                                                        SimpleNamespace(scene=other))
    assert result == {'CANCELLED'}
    active.request_cancel.assert_not_called()
