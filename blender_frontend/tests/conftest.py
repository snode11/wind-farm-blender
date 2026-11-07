"""Host-test fixtures for Blender-facing frontend modules."""
import importlib
import os
import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest

from blender_frontend.tests._platform import python_tool_popen, write_python_tool


@pytest.fixture
def python_tool(monkeypatch):
    scripts = set()
    if os.name == 'nt':
        monkeypatch.setattr(subprocess, 'Popen', python_tool_popen(scripts))

    def create(path, body, *, mode=0o755):
        script = write_python_tool(path, body, mode=mode)
        scripts.add(script)
        return script

    return create


# Real empty classes, so operator, panel and property-group classes define normally.
BASE_TYPES = ('Operator', 'Panel', 'PropertyGroup', 'UIList', 'Menu', 'Header',
              'AddonPreferences', 'Gizmo', 'GizmoGroup', 'Macro')
FAKE_MODULES = ('mathutils', 'mathutils.geometry', 'mathutils.bvhtree', 'gpu', 'gpu.types',
                'gpu.shader', 'gpu.state', 'gpu_extras', 'gpu_extras.batch', 'gpu_extras.presets',
                'bmesh', 'blf', 'bpy_extras', 'bpy_extras.view3d_utils', 'aud')


def fake_blender_modules():
    """Return ``sys.modules`` entries for a Blender API whose calls are ``MagicMock``s."""
    bpy = MagicMock(name='bpy')
    bpy.types = MagicMock(name='bpy.types')
    for name in BASE_TYPES:
        setattr(bpy.types, name, type(name, (), {}))
    bpy.app.handlers.persistent = lambda function: function
    modules = {'bpy': bpy, 'bpy.types': bpy.types, 'bpy.props': bpy.props, 'bpy.app': bpy.app,
               'bpy.app.handlers': bpy.app.handlers, 'bpy.utils': bpy.utils}
    modules.update((name, MagicMock(name=name)) for name in FAKE_MODULES)
    return modules


@pytest.fixture
def frontend_module(monkeypatch):
    """Import frontend modules against a fake ``bpy``; they leave ``sys.modules`` after the test.

    Only ``wfrl_blender`` modules bind the fake; third-party imports such as NumPy stay loaded.
    """
    before = set(sys.modules)
    for name, module in fake_blender_modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    yield importlib.import_module
    # Undo first: a later undo would put replaced fake-bound modules back.
    monkeypatch.undo()
    for name in {name for name in set(sys.modules) - before
                 if name == 'wfrl_blender' or name.startswith('wfrl_blender.')}:
        module = sys.modules.pop(name)
        parent, _, child = name.rpartition('.')
        if getattr(sys.modules.get(parent), child, None) is module:
            delattr(sys.modules[parent], child)


@pytest.fixture
def stub_module(monkeypatch):
    """Replace a frontend module in ``sys.modules`` and on its parent package for one test."""
    def stub(name, **attributes):
        module = types.ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, child = name.rpartition('.')
        if parent in sys.modules:
            monkeypatch.setattr(sys.modules[parent], child, module, raising=False)
        return module
    return stub
