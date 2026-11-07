"""Blender subprocess entry points disable bytecode writes before loading workspace code."""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINTS = (
    'scripts/blender/render_manual_video.py',
    'blender_frontend/wfrl_blender/animation_render_worker.py',
    'scripts/blender/accept_frontend.py',
    'blender_frontend/tests/blender/demo_playback_benchmark.py',
    'projects/gw184-single/tests/defect_cli_regression.py',
    'scripts/blender/gw184_single.py',
)
WORKSPACE_MODULES = {'wfrl_blender', 'wfrl', 'gw184', 'defect_render_guard'}
SCRIPT_LOADERS = {'importlib.import_module', 'import_module', '__import__',
                  'addon_utils.enable', 'runpy.run_path'}


def _loads_workspace_code(node):
    if isinstance(node, ast.Import):
        return any(alias.name.partition('.')[0] in WORKSPACE_MODULES for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.level > 0 or (node.module or '').partition('.')[0] in WORKSPACE_MODULES
    if isinstance(node, ast.Call):
        return (ast.unparse(node.func) in SCRIPT_LOADERS
                or isinstance(node.func, ast.Attribute) and node.func.attr == 'exec_module')
    return False


def _disables_bytecode(node):
    return (isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant) and node.value.value is True
            and any(ast.unparse(target) == 'sys.dont_write_bytecode' for target in node.targets))


def _assert_bytecode_disabled(tree, source):
    nodes = list(ast.walk(tree))
    first_load = min(((node.lineno, node.col_offset) for node in nodes if _loads_workspace_code(node)),
                     default=None)
    assert first_load is not None, f'{source}: no workspace code load found'
    assert any(_disables_bytecode(node) and (node.lineno, node.col_offset) < first_load for node in nodes), (
        f'{source}:{first_load[0]}: set sys.dont_write_bytecode = True before loading workspace code'
    )


@pytest.mark.parametrize('entry', ENTRY_POINTS)
def test_blender_subprocess_disables_bytecode(entry):
    tree = ast.parse((ROOT / entry).read_text(encoding='utf-8'), filename=entry)
    _assert_bytecode_disabled(tree, entry)


def test_regression_bootstrap_disables_bytecode():
    runner = ROOT / 'scripts/blender/run_regressions.py'
    tree = ast.parse(runner.read_text(encoding='utf-8'), filename=str(runner))
    bootstrap = next(ast.literal_eval(node.value) for node in tree.body
                     if isinstance(node, ast.Assign)
                     and any(ast.unparse(target) == 'BOOTSTRAP' for target in node.targets))
    source = 'scripts/blender/run_regressions.py BOOTSTRAP'
    _assert_bytecode_disabled(ast.parse(bootstrap, filename=source), source)
