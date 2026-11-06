"""Native scripts have a registry entry or an explicit, current execution contract."""
import ast
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('wfrl_regression_runner', ROOT / 'scripts/blender/run_regressions.py')
runner = sys.modules.get(SPEC.name)
if runner is None:
    runner = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = runner
    SPEC.loader.exec_module(runner)

OTHER_RUNNERS = {
    'demo_playback_benchmark.py': 'scripts/blender/run_playback_benchmark.py',
    'mappo_full_view_default_window.py': 'scripts/blender/run_installed_windows.py',
    'mappo_same_source_navigation_window.py': 'scripts/blender/run_installed_windows.py',
    'mappo_same_source_texture_reopen.py': 'scripts/blender/run_installed_windows.py',
    'mappo_same_source_texture_window.py': 'scripts/blender/run_installed_windows.py',
    'reconstruction_workflow_window.py': 'scripts/blender/run_installed_windows.py',
    'split_texture_sidebar_window.py': 'scripts/blender/run_installed_windows.py',
    'native_camera_projection_regression.py': 'scripts/blender/validate_frontend_package.py',
    'three_camera_default_regression.py': 'scripts/blender/validate_frontend_package.py',
}

# These scripts have no automated driver and cannot use the source-runtime bootstrap.
MANUAL_ONLY = {
    'launcher_mode_regression.py': (
        'Requires the installed daily extension and an idle Bridge with launcher environment; '
        'wfrl_blender_bootstrap.py does not invoke this regression.'),
    'manual_video_installed_regression.py': (
        'Requires an enabled installed extension, a supervisor Python and two fresh processes '
        'sharing create/recover output; the source runner reaps descendants after each invocation.'),
    'video_output_regression.py': (
        'Requires the installed daily extension, a prepared MP4, FFmpeg and MediaMTX for real RTSP; '
        'the source runner cannot substitute for that installed payload.'),
}


def native_scripts():
    """Underscore-prefixed modules are shared helpers, not executable regressions."""
    return {path.name for path in runner.TEST_ROOT.glob('*.py') if not path.name.startswith('_')}


def registered_scripts():
    return {item.script for item in runner.select_tests(None)}


def undeclared_scripts():
    return native_scripts() - registered_scripts() - OTHER_RUNNERS.keys() - MANUAL_ONLY.keys()


def test_every_native_script_has_an_explicit_execution_contract():
    assert not undeclared_scripts(), sorted(undeclared_scripts())


def test_execution_contracts_are_disjoint_and_have_no_stale_files():
    registered = registered_scripts()
    assert not registered.intersection(OTHER_RUNNERS)
    assert not registered.intersection(MANUAL_ONLY)
    assert not OTHER_RUNNERS.keys() & MANUAL_ONLY.keys()
    assert registered | OTHER_RUNNERS.keys() | MANUAL_ONLY.keys() == native_scripts()
    assert all(reason.strip() for reason in MANUAL_ONLY.values())


@pytest.mark.parametrize(('name', 'driver'), sorted(OTHER_RUNNERS.items()))
def test_other_runner_names_the_script_in_executable_code(name, driver):
    path = ROOT / driver
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=driver)
    # Comments and documentation alone cannot declare an executable entry point.
    prose = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}
    literals = {node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in prose}
    assert name in literals or 'blender_frontend/tests/blender/' + name in literals


@pytest.mark.parametrize('name', ['unlisted_regression.py', 'unlisted_window.py'])
def test_new_scripts_require_explicit_registration_even_if_documented(tmp_path, monkeypatch, name):
    (tmp_path / name).write_text('"""Run this script manually in Blender."""\n', encoding='utf-8')
    (tmp_path / '_shared.py').write_text('', encoding='utf-8')
    monkeypatch.setattr(runner, 'TEST_ROOT', tmp_path)
    assert undeclared_scripts() == {name}
