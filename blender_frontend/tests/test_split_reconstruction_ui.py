"""Same-source UI entry points require a matching optional result payload."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

from wfrl_blender import split_mappo_texture


@pytest.fixture
def ui(monkeypatch):
    bpy = ModuleType('bpy')
    bpy.types = NS(Operator=object)
    props = ModuleType('bpy.props')
    props.IntProperty = props.EnumProperty = lambda **kwargs: None
    handlers = ModuleType('bpy.app.handlers')
    handlers.persistent = lambda function: function
    for name, value in [('bpy', bpy), ('bpy.props', props),
                        ('bpy.app.handlers', handlers), ('blf', ModuleType('blf'))]:
        monkeypatch.setitem(sys.modules, name, value)
    path = Path(split_mappo_texture.__file__).with_name('split_reconstruction_ui.py')
    spec = importlib.util.spec_from_file_location('wfrl_blender._split_ui_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    core = NS(available=lambda scene: True, enabled=lambda scene: True,
              texture_adapter=lambda scene: NS(active=lambda target: False))
    monkeypatch.setattr(module, '_core', lambda: core)
    return NS(module=module, core=core, context=NS(scene={}))


@pytest.mark.parametrize('available', [True, False])
def test_same_source_entry_requires_its_result(ui, monkeypatch, available):
    monkeypatch.setattr(split_mappo_texture, 'available', lambda scene: available)
    assert ui.module.WFRL_OT_SplitReconSameSource.poll(ui.context) is available


def test_missing_result_disables_entry_but_keeps_exit_from_active_texture(ui, monkeypatch):
    monkeypatch.setattr(split_mappo_texture, 'available', lambda scene: False)
    assert not ui.module.WFRL_OT_SplitReconSwitchMode.poll(ui.context)
    ui.core.texture_adapter = lambda scene: NS(active=lambda target: True)
    assert ui.module.WFRL_OT_SplitReconSwitchMode.poll(ui.context)


def test_available_result_does_not_enable_switch_outside_comparison(ui, monkeypatch):
    monkeypatch.setattr(split_mappo_texture, 'available', lambda scene: True)
    ui.core.enabled = lambda scene: False
    assert not ui.module.WFRL_OT_SplitReconSwitchMode.poll(ui.context)
