"""Every native script under tests/blender has a declared way to be run."""
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('wfrl_regression_runner', ROOT / 'scripts/blender/run_regressions.py')
runner = sys.modules.get(SPEC.name)
if runner is None:
    runner = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = runner
    SPEC.loader.exec_module(runner)

# Current usage documents and driver scripts that name a script declare its manual entry point.
# Release check records are historical and do not count.
ENTRY_DOCUMENTS = (
    *(ROOT / '.agents/skills/windfarm-blender-dev/references').glob('*.md'),
    *(ROOT / 'docs/blender').glob('*.md'),
    ROOT / 'wfrl/lidar/README.md',
    *(path for path in (ROOT / 'scripts/blender').glob('*.py') if path.name != 'run_regressions.py'),
)
# Scripts awaiting registration, documentation or removal. Entries only leave this set.
UNTRIAGED = frozenset({
    'launcher_mode_regression.py',
})


def native_scripts():
    """Runnable scripts; underscore modules such as ``_native.py`` are shared helpers."""
    return {path.name for path in runner.TEST_ROOT.glob('*.py') if not path.name.startswith('_')}


def registered_scripts():
    return {item.script for item in runner.select_tests(None)}


def manual_entry_points(candidates):
    """Return the scripts in ``candidates`` that have a declared non-runner entry point."""
    texts = [path.read_text(encoding='utf-8') for path in ENTRY_DOCUMENTS]
    return {name for name in candidates if any(name in text for text in texts)}


def test_every_native_script_has_an_entry_point():
    outside = native_scripts() - registered_scripts()
    orphaned = outside - manual_entry_points(outside) - UNTRIAGED
    assert not orphaned, sorted(orphaned)


def test_untriaged_scripts_are_still_orphaned():
    outside = native_scripts() - registered_scripts()
    resolved = UNTRIAGED - (outside - manual_entry_points(outside))
    assert not resolved, f'remove from UNTRIAGED: {sorted(resolved)}'
