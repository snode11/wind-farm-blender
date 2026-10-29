"""Failure boundaries for native regression orchestration and result reporting."""
import importlib.util
from pathlib import Path
import sys
import os
import subprocess
import time
from dataclasses import replace

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


def test_manual_control_and_home_checks_have_background_completion_contracts():
    names = ('home_entry_smoke', 'manual_control_regression', 'manual_control_state_regression',
             'manual_rate_pitch_regression', 'manual_rotor_conflict_regression',
             'manual_pitch_trial_regression', 'manual_pitch_trial_undo_regression',
             'manual_pose_undo_regression', 'manual_scene_recovery_regression',
             'manual_scene_link_compatibility_regression')
    for name in names:
        item = spec(name)
        assert item.mode == 'background'
        assert (runner.TEST_ROOT / item.script).is_file()
        assert evaluate(item, report={'status': 'PASS'} if item.report else None) == ('PASS', [])
        assert evaluate(item, report={'status': 'PASS'} if item.report else None, marker='')[0] == 'FAIL'


def test_ci_selection_can_only_run_registered_background_checks():
    names = (ROOT / 'configs/ci-native-tests.txt').read_text().split()
    selected = runner.select_tests(names)
    assert selected
    assert all(item.mode == 'background' for item in selected)


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


@pytest.mark.parametrize('name', ['home_entry_smoke', 'native_camera_playback_regression'])
def test_uncaught_callback_overrides_success_and_skip(name):
    item = spec(name)
    log = item.marker + '\nTraceback (most recent call last):\n  File "panel.py", line 5, in draw\nRuntimeError: callback failed\n'
    assert evaluate(item, report={'status': 'PASS'} if item.report else None, marker=log)[0] == 'FAIL'
    assert evaluate(item, report={'status': 'SKIP'}, marker=log)[0] == 'FAIL'
    allowed = replace(item, expected_exceptions=(('RuntimeError: callback failed', 1),))
    assert evaluate(allowed, report={'status': 'PASS'} if item.report else None, marker=log) == ('PASS', [])
    assert evaluate(allowed, marker=log + log)[0] == 'FAIL'
    assert evaluate(allowed, marker=log.replace('RuntimeError:', 'ValueError:'))[0] == 'FAIL'


@pytest.mark.parametrize('text', [
    'The phrase Traceback (most recent call last): describes an exception.',
    '{"error": "Traceback (most recent call last):\\n  File \\"old.py\\", line 1\\nRuntimeError: historical"}',
    'Traceback (most recent call last):\nThis paragraph explains Python diagnostics.',
])
def test_prose_and_escaped_history_are_not_execution_diagnostics(text):
    assert evaluate(spec('home_entry_smoke'), marker='HOME_ENTRY_SMOKE_PASS\n' + text) == ('PASS', [])


def test_blender_prefixed_and_incomplete_tracebacks_fail():
    item = spec('home_entry_smoke')
    for tail in ('RuntimeError: callback failed\n', ''):
        log = item.marker + '\nError: Python: Traceback (most recent call last):\n  File "panel.py", line 5, in draw\n' + tail
        assert evaluate(item, marker=log)[0] == 'FAIL'


@pytest.mark.parametrize('nested', [False, True])
def test_real_exception_group_and_chained_tracebacks_fail(nested):
    import traceback
    failures = [RuntimeError('first callback'), ValueError('second callback')]
    if nested:
        failures[1] = ExceptionGroup('nested', [failures[1], KeyError('third callback')])
    try:
        try:
            raise LookupError('initial callback')
        except LookupError as exc:
            raise ExceptionGroup('callback errors', failures) from exc
    except ExceptionGroup as exc:
        diagnostic = ''.join(traceback.format_exception(exc))
    for name in ('home_entry_smoke', 'native_camera_playback_regression'):
        item = spec(name)
        assert evaluate(item, report={'status': 'PASS'} if item.report else None,
                        marker=item.marker + '\n' + diagnostic)[0] == 'FAIL'
    parsed = runner.traceback_exceptions(diagnostic)
    assert 'RuntimeError: first callback' in parsed and 'ValueError: second callback' in parsed
    assert any('ExceptionGroup:' in item for item in parsed)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-session regression')
@pytest.mark.parametrize('parent_mode', ['exit_on_term', 'ignore_term', 'already_exited'])
def test_stop_reaps_owned_descendants_after_launcher_exit(tmp_path, parent_mode):
    import psutil

    child_script = tmp_path / 'child.py'
    ready = tmp_path / 'child.pid'
    child_script.write_text('import os,signal,sys,time\nfrom pathlib import Path\n'
        'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
        'Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(120)\n')
    parent_script = tmp_path / 'parent.py'
    parent_script.write_text('import subprocess,sys,time,signal\n'
        'if sys.argv[3] == "ignore_term": signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
        'subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n'
        'if sys.argv[3] != "already_exited": time.sleep(120)\n')
    owner = runner.OwnedProcesses(env_key='WFRL_TEST_REGRESSION_TOKEN')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'])
    process = owner.start([sys.executable, str(parent_script), str(child_script), str(ready), parent_mode],
                          cwd=tmp_path, env=os.environ)
    child = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert time.monotonic() < deadline
            time.sleep(.01)
        child = psutil.Process(int(ready.read_text()))
        if parent_mode == 'already_exited':
            process.wait(timeout=5)
        runner.stop_process(process, owner, grace=.5)
        assert process.returncode is not None
        assert not owner.alive()
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert unrelated.poll() is None
    finally:
        if child is not None:
            try: child.kill()
            except psutil.NoSuchProcess: pass
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)
