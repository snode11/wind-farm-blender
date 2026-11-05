"""Shared setup for native Blender test scripts.

A script appends its own directory to ``sys.path`` and imports this module as
``native``. ``WFRL_ADDON_MODULE`` selects the add-on (the workspace source by
default), ``WFRL_EXPECTED_ADDON_ROOT`` pins where it was loaded from, and
``WFRL_TEST_OUTPUT`` names the output directory.
"""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]


def assert_research_texture_absent(module):
    """Reject a research module in an installation without executing its code."""
    import importlib.util
    assert module.startswith('bl_ext.'), 'Expected an installed extension module'
    name = module + '.split_surface_texture'
    assert importlib.util.find_spec(name) is None, (
        'Installed package must not ship research-only split_surface_texture')


def addon_module():
    """Return the add-on module name; the source default puts the workspace on ``sys.path``."""
    module = os.environ.get('WFRL_ADDON_MODULE', 'wfrl_blender')
    if module == 'wfrl_blender':
        sys.path[:0] = [str(ROOT / 'blender_frontend'), str(ROOT)]
    return module


def check_addon_root(addon):
    """When ``WFRL_EXPECTED_ADDON_ROOT`` is set, the add-on must have been loaded from it."""
    expected = os.environ.get('WFRL_EXPECTED_ADDON_ROOT')
    if expected:
        assert Path(addon.__file__).resolve().parent == Path(expected).resolve(), addon.__file__


def output_directory(*, new=True, resolve=False):
    """Create and return ``WFRL_TEST_OUTPUT``; ``new`` rejects a directory that already exists."""
    output = Path(os.environ['WFRL_TEST_OUTPUT'])
    if resolve:
        output = output.resolve()
    if new:
        assert not output.exists(), 'WFRL_TEST_OUTPUT must name a new directory'
    output.mkdir(parents=True, exist_ok=not new)
    return output
