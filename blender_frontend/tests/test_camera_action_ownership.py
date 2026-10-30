"""Camera actions keep another scene's active editing session untouched."""
import ast
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

PANELS = Path(__file__).parents[1] / 'wfrl_blender/panels'


def execute_body(filename, class_name, namespace):
    tree = ast.parse((PANELS / filename).read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == class_name)
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == 'execute')
    exec(compile(ast.Module(body=[method], type_ignores=[]), filename, 'exec'), namespace)
    return namespace['execute']


@pytest.mark.parametrize('action', ['CANCEL', 'CONFIRM', 'LEFT', 'RIGHT', 'AIM_LEFT', 'AIM_RIGHT'])
def test_box_action_rejects_another_scene(action):
    owner, other = object(), object()
    transaction = SimpleNamespace(finish=Mock())
    active = SimpleNamespace(scene=owner, transaction=transaction, cleanup=Mock(),
                             wm=SimpleNamespace(wfrl_box_spin=0., wfrl_box_aim=0.))
    shutdown = Mock()
    execute = execute_body('stacked_camera_rig.py', 'WFRL_OT_BoxAction',
                           {'_ACTIVE': active, 'shutdown': shutdown})
    result = execute(SimpleNamespace(action=action, report=Mock()), SimpleNamespace(scene=other))
    assert result == {'CANCELLED'}
    shutdown.assert_not_called()
    transaction.finish.assert_not_called()
    active.cleanup.assert_not_called()
    assert active.wm.wfrl_box_spin == active.wm.wfrl_box_aim == 0.


def test_selecting_a_slot_does_not_switch_another_scenes_observation(monkeypatch):
    capture = ModuleType('wfrl_blender.custom_camera_capture')
    capture.active = lambda: False
    monkeypatch.setitem(sys.modules, capture.__name__, capture)
    active = SimpleNamespace(scene=object(), stage='WATCH', closed=False,
                             select_slot=Mock(), camera=object())
    execute = execute_body('custom_cameras.py', 'WFRL_OT_CustomAction',
                           {'__package__': 'wfrl_blender.panels', '_ACTIVE': active,
                            '_VIEWING': {'WATCH', 'LAYOUT'}, '_redraw_if_changed': Mock()})
    wm = SimpleNamespace(wfrl_custom_slot=1)
    context = SimpleNamespace(scene=object(), window_manager=wm,
                              area=SimpleNamespace(tag_redraw=Mock()))
    result = execute(SimpleNamespace(action='SLOT', slot=2, report=Mock()), context)
    assert result == {'FINISHED'}
    assert wm.wfrl_custom_slot == 2
    active.select_slot.assert_not_called()


def test_capture_cancel_rejects_another_scene():
    owner, other = object(), object()
    active = SimpleNamespace(scene=owner, request_cancel=Mock())
    capture = SimpleNamespace(active=lambda: True, _ACTIVE=active)
    execute = execute_body('custom_camera_output.py', 'WFRL_OT_CustomCaptureCancel', {'capture': capture})
    result = execute(SimpleNamespace(report=Mock()), SimpleNamespace(scene=other))
    assert result == {'CANCELLED'}
    active.request_cancel.assert_not_called()
