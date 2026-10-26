"""Failure boundaries for native regression orchestration and result reporting."""
import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('wfrl_regression_runner', ROOT / 'scripts/blender/run_regressions.py')
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def spec(name):
    return runner.select_tests([name])[0]


def execution(mode='background'):
    return dict(status='PASS' if mode == 'background' else 'WAITING_FOR_CALLBACKS',
                returned_normally=True, background=mode == 'background', final=mode == 'background')


def evaluate(item, *, report=None, code=0, transport=None, marker=None, timeout=False, changed=False):
    return runner.evaluate(item, 'run', code, timeout, transport or execution(item.mode), report,
                           item.marker if marker is None else marker, changed)


def test_suite_has_unique_scripts_and_explicit_window_contracts():
    selected = runner.select_tests(None)
    assert len({item.script for item in selected}) == len(selected)
    assert all((runner.TEST_ROOT / item.script).is_file() for item in selected)
    assert {item.script for item in selected if item.mode == 'visible'} == {
        'shared_context_switch_regression.py', 'native_camera_playback_regression.py',
        'nrel_editor_playback_regression.py', 'frontend_sessions_window.py'}
    assert spec('animation_render_regression').package == 'synthetic_then_builtin_mappo'
    assert spec('animation_render_regression').report == 'result.json'
    assert spec('nrel_defect_editor_regression').phases == ('save', 'reopen')
    assert spec('rebuild_mesh_regression').output_kind == 'directory'
    assert spec('rebuild_mesh_regression').report == 'checks.json'


@pytest.mark.parametrize('name', ['shared_context_switch_regression', 'native_camera_playback_regression',
                                  'nrel_editor_playback_regression'])
@pytest.mark.parametrize('status', ['RUNNING', 'FAIL', 'MISSING_OR_INVALID_JSON'])
def test_window_zero_exit_never_passes_incomplete_or_failed_json(name, status):
    assert evaluate(spec(name), report={'status': status})[0] == 'FAIL'


def test_window_requires_visible_bootstrap_and_final_marker():
    item = spec('shared_context_switch_regression')
    assert evaluate(item, report={'status': 'PASS'}, transport=execution('background'))[0] == 'FAIL'
    assert evaluate(item, report={'status': 'PASS'}, marker='')[0] == 'FAIL'
    assert evaluate(item, report={'status': 'PASS'}) == ('PASS', [])


@pytest.mark.parametrize('status', ['SKIP', 'NOT_RUN'])
def test_script_can_explicitly_report_incomplete_coverage(status):
    item = spec('native_camera_playback_regression')
    assert evaluate(item, report={'status': status, 'reason': 'No GUI'}, marker='')[0] == status
    assert runner.aggregate(['PASS', status]) == 'NOT_RUN'


def test_physical_failure_cannot_be_hidden_by_exit_zero_or_pass_status():
    item = spec('tip_deflection_regression')
    report = dict(status='PASS', software_checks='PASS', physical_gate='FAILED_GATE')
    assert evaluate(item, report=report)[0] == 'FAIL'
    report['physical_gate'] = 'PASS'
    assert evaluate(item, report=report) == ('PASS', [])


def test_console_only_requires_normal_completion_and_final_marker():
    item = spec('three_cameras_regression')
    assert evaluate(item) == ('PASS', [])
    early = dict(status='FAIL', final=True, background=True, returned_normally=False)
    assert evaluate(item, transport=early)[0] == 'FAIL'
    assert evaluate(item, marker='')[0] == 'FAIL'
    assert evaluate(item, code=1)[0] == 'FAIL'
    assert evaluate(item, timeout=True)[0] == 'FAIL'
    assert evaluate(item, changed=True)[0] == 'FAIL'


def test_early_farm_report_does_not_replace_last_lifecycle_assertions():
    item = spec('farm_flex_regression')
    assert evaluate(item, report={'status': 'PASS'}, marker='FARM_FLEX_REGRESSION_PASS')[0] == 'FAIL'
    assert evaluate(item, report={'status': 'PASS'}) == ('PASS', [])


def test_top_level_pass_cannot_hide_failed_check():
    item = spec('native_camera_playback_regression')
    assert evaluate(item, report={'status': 'PASS', 'checks': {'clock': {'status': 'FAIL'}}})[0] == 'FAIL'
    assert evaluate(item, report={'status': 'PASS', 'final': False})[0] == 'FAIL'


def test_profiles_packages_and_output_are_explicit_and_isolated(tmp_path):
    inherited = dict(PATH='/bin', WFRL_TEST_KEEP_OPEN='1', WFRL_TEST_RUNTIME='/foreign',
                     WFRL_FARM_LAUNCHER='/foreign', WFRL_NREL_DEFECT_PHASE='reopen',
                     WFRL_NATIVE_PLAYBACK_MANUAL_SPACE='1', BLENDER_USER_CONFIG='/user',
                     PYTHONPATH='/foreign')
    first = runner.isolated_environment(tmp_path / 'first', tmp_path / 'result.json', 'run', inherited)
    second = runner.isolated_environment(tmp_path / 'second', tmp_path / 'result.json', 'save', inherited)
    for name in ('WFRL_TEST_KEEP_OPEN', 'WFRL_TEST_RUNTIME', 'WFRL_FARM_LAUNCHER',
                 'WFRL_NATIVE_PLAYBACK_MANUAL_SPACE', 'PYTHONPATH'):
        assert name not in first
    assert first['BLENDER_USER_CONFIG'] != second['BLENDER_USER_CONFIG']
    assert Path(first['TMPDIR']).is_dir()
    assert first['WFRL_FARM_FLEX_PACKAGE'] == str(runner.ADDON_ROOT / 'assets/mappo')
    assert first['WFRL_DUAL_PACKAGE'] == str(runner.ADDON_ROOT / 'assets/dual_beam')
    assert first['WFRL_TEST_OUTPUT'] == str(tmp_path / 'result.json')
    assert 'WFRL_NREL_DEFECT_PHASE' not in first
    assert second['WFRL_NREL_DEFECT_PHASE'] == 'save'


def test_unknown_and_duplicate_scripts_are_rejected():
    with pytest.raises(ValueError, match='empty'):
        runner.select_tests([])
    with pytest.raises(ValueError, match='Unknown'):
        runner.select_tests(['../user_scene.py'])
    with pytest.raises(ValueError, match='Duplicate'):
        runner.select_tests(['tower_flex_regression', 'tower_flex_regression.py'])


def test_failed_save_cannot_make_batch_pass():
    assert runner.aggregate(['FAIL', 'NOT_RUN']) == 'FAIL'
    assert runner.aggregate(['PASS', 'PASS']) == 'PASS'
    assert runner.aggregate([]) == 'NOT_RUN'


def test_existing_output_is_preserved(tmp_path):
    sentinel = tmp_path / 'user-data.txt'
    sentinel.write_text('preserve')
    with pytest.raises(SystemExit) as error:
        runner.main(['--output', str(tmp_path), '--blender', sys.executable])
    assert error.value.code == 2
    assert sentinel.read_text() == 'preserve'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['user-data.txt']


def test_explicit_mode_skip_is_reported_without_launching(tmp_path, monkeypatch):
    import json

    def unexpected_launch(*args):
        raise AssertionError('Excluded visible test was launched')

    monkeypatch.setattr(runner, 'run_invocation', unexpected_launch)
    output = tmp_path / 'results'
    code = runner.main(['--tests', 'shared_context_switch_regression', '--mode', 'background',
                        '--output', str(output), '--scratch-root', str(tmp_path / 'scratch'),
                        '--blender', sys.executable])
    result = json.loads((output / 'result.json').read_text())
    assert code == 2 and result['final'] and result['status'] == 'NOT_RUN'
    assert result['counts'] == {'SKIP': 1}
    assert result['tests'][0]['invocations'] == []
