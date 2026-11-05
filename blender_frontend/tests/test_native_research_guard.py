"""Installed test guards must distinguish missing modules from broken imports."""
import importlib.util
from pathlib import Path
import sys

import pytest


@pytest.fixture
def research_guard(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        'native_guard_helpers', Path(__file__).parent / 'blender' / '_native.py')
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    package = tmp_path / 'bl_ext' / 'guard_test' / 'addon'
    package.mkdir(parents=True)
    for directory in (package, package.parent, package.parent.parent):
        (directory / '__init__.py').write_text('', encoding='utf-8')
    monkeypatch.syspath_prepend(str(tmp_path))
    # Isolate the temporary package from any other host test's module stubs.
    for name in list(sys.modules):
        if name == 'bl_ext' or name.startswith('bl_ext.'):
            monkeypatch.delitem(sys.modules, name)
    yield helpers.assert_research_texture_absent, package
    for name in list(sys.modules):
        if name == 'bl_ext' or name.startswith('bl_ext.'):
            sys.modules.pop(name, None)


def test_installed_guard_accepts_absent_research_module(research_guard):
    guard, _package = research_guard
    guard('bl_ext.guard_test.addon')


@pytest.mark.parametrize('body', ['', 'import nonexistent_research_dependency\n'])
def test_installed_guard_rejects_present_research_module_without_importing(research_guard, body):
    guard, package = research_guard
    (package / 'split_surface_texture.py').write_text(body, encoding='utf-8')
    with pytest.raises(AssertionError, match='must not ship'):
        guard('bl_ext.guard_test.addon')
    assert 'bl_ext.guard_test.addon.split_surface_texture' not in sys.modules


def test_absence_guard_does_not_skip_source_research_checks(research_guard):
    guard, _package = research_guard
    with pytest.raises(AssertionError, match='installed extension'):
        guard('wfrl_blender')
