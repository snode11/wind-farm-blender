"""Failure boundaries for native regression orchestration and result reporting."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import os
import subprocess
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('wfrl_regression_runner', ROOT / 'scripts/blender/run_regressions.py')
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)
WAIT_TIMEOUT = 60
REVIEW_WINDOWS = (
    'blade_model_window', 'nrel_defect_window_regression',
    'radar_x100_window', 'radar_x100_output_window',
)


def spec(name):
    return runner.select_tests([name])[0]


def execution(mode='background'):
    return dict(status='PASS' if mode == 'background' else 'WAITING_FOR_CALLBACKS',
                returned_normally=True, background=mode == 'background', final=mode == 'background')


def evaluate(item, *, report=None, code=0, transport=None, marker=None, timeout=False, changed=False):
    return runner.evaluate(item, 'run', code, timeout, transport or execution(item.mode), report,
                           item.marker if marker is None else marker, changed)


def script_function(name, function, namespace):
    """Load helper definitions for host checks without executing a native script."""
    path = runner.TEST_ROOT / (name + '.py')
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[function]


def test_suite_has_unique_scripts_and_explicit_window_contracts():
    selected = runner.select_tests(None)
    assert len({item.script for item in selected}) == len(selected)
    assert all((runner.TEST_ROOT / item.script).is_file() for item in selected)
    assert {item.script for item in selected if item.mode == 'visible'} == {
        'shared_context_switch_regression.py', 'native_camera_playback_regression.py',
        'nrel_editor_playback_regression.py', 'frontend_sessions_window.py',
        'radar_x100_estimation_window.py', 'box_surface_install_window.py', 'camera_gpu_calibration.py',
        'chart_render_cache_window.py', 'custom_camera_exit_regression.py',
        'custom_camera_preview_performance.py', 'demo_presentation_regression.py', 'farm_split_view_window.py',
        'frontend_fixes_window.py', 'manual_down_viewport_regression.py', 'manual_playback_window.py',
        'manual_video_window_regression.py', 'native_camera_views_regression.py', 'overlay_draw_smoke.py',
        'stacked_cameras_window.py', 'three_camera_recommended_window.py', 'three_cameras_window_regression.py',
        'blade_model_window.py', 'clearance_gui_acceptance.py', 'custom_camera_capture_window.py',
        'dual_beam_window.py', 'frontend_layout_regression.py', 'manual_control_window.py',
        'manual_pitch_trial_window.py', 'native_camera_enabled_regression.py', 'native_camera_proxy_window.py',
        'native_camera_reopen_regression.py', 'native_camera_save_regression.py', 'nrel_defect_window_regression.py',
        'radar_x100_output_lifecycle.py', 'radar_x100_output_window.py', 'radar_x100_window.py'}
    assert dict(spec('three_camera_recommended_window').environment) == {
        'WFRL_TEST_QUIT': '1', 'WFRL_CHECK_DEFAULT_ONLY': '1'}
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


@pytest.mark.parametrize('name', [
    'blade_model_regression', 'clearance_acceptance_regression', 'clearance_edit_mode_regression',
    'clearance_ux_regression', 'custom_camera_edit_binding_regression', 'landscape_geometry_regression',
    'live_contracts_regression', 'manual_down_regression', 'manual_geometry_compatibility_regression',
    'manual_model_regression', 'manual_video_lifecycle_regression', 'manual_video_render_regression',
    'nacelle_camera_intrinsics_regression', 'nacelle_camera_regression', 'nrel_defect_ui_state_regression',
])
def test_background_execution_contract_requires_completion(name):
    item = spec(name)
    assert item.mode == 'background' and item.phases == ('run',)
    assert item.timeout_s > 0 and item.scope and item.package
    report = {'status': 'PASS'} if item.report else None
    assert evaluate(item, report=report) == ('PASS', [])
    assert evaluate(item, report=report, marker='')[0] == 'FAIL'
    assert evaluate(item, report=report, code=1)[0] == 'FAIL'


def test_ci_selection_can_only_run_registered_background_checks():
    names = (ROOT / 'configs/ci-native-tests.txt').read_text().split()
    selected = runner.select_tests(names)
    assert selected
    assert all(item.mode == 'background' for item in selected)


def test_x100_ci_checks_require_native_reports_and_completion_markers():
    names = (ROOT / 'configs/ci-native-tests.txt').read_text().split()
    selected = {item.script: item for item in runner.select_tests(names)}
    for name in ('radar_x100_analysis.py', 'radar_x100_mount_presets.py',
                 'radar_x100_configuration.py', 'radar_x100_binding_replay.py'):
        item = selected[name]
        assert item.mode == 'background'
        assert evaluate(item, report={'status': 'PASS'}) == ('PASS', [])
        assert evaluate(item, report=None)[0] == 'FAIL'
        assert evaluate(item, report={'status': 'PASS'}, marker='')[0] == 'FAIL'


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


@pytest.mark.parametrize(('report', 'expected'), [
    ({'passed': True}, 'PASS'),
    ({'passed': False}, 'FAIL'),
    ({}, 'FAIL'),
    ({'passed': None}, 'FAIL'),
    ({'passed': 1}, 'FAIL'),
    ({'passed': 0}, 'FAIL'),
    ({'passed': 'true'}, 'FAIL'),
    ({'passed': 'false'}, 'FAIL'),
    ({'status': 'PASS', 'passed': True}, 'PASS'),
    ({'status': 'PASS', 'passed': False}, 'FAIL'),
    ({'status': 'ERROR', 'passed': True}, 'FAIL'),
    ({'status': 'RUNNING', 'passed': True}, 'FAIL'),
    ({'status': 'SKIP', 'passed': True}, 'FAIL'),
    ({'status': None, 'passed': True}, 'FAIL'),
])
def test_boolean_report_normalization_requires_strict_final_boolean(report, expected):
    original = dict(report)
    assert evaluate(spec('clearance_gui_acceptance'), report=report)[0] == expected
    assert report == original


def test_boolean_report_normalization_requires_explicit_registration_and_completion():
    assert evaluate(spec('native_camera_playback_regression'), report={'passed': True})[0] == 'FAIL'
    item = spec('clearance_gui_acceptance')
    assert evaluate(item, report={'passed': True, 'final': False})[0] == 'FAIL'
    assert evaluate(item, report={'passed': True}, marker='')[0] == 'FAIL'
    assert evaluate(item, report={'passed': True}, code=1)[0] == 'FAIL'
    assert evaluate(item, report={'passed': True}, timeout=True)[0] == 'FAIL'
    assert evaluate(item, report={'passed': True}, transport=execution('background'))[0] == 'FAIL'


@pytest.mark.parametrize('name', REVIEW_WINDOWS)
def test_review_window_contract_requires_exit_and_final_callback_evidence(name):
    item = spec(name)
    assert item.mode == 'visible'
    assert dict(item.environment)['WFRL_TEST_QUIT'] == '1'
    assert evaluate(item, report=None)[0] == 'FAIL'
    assert evaluate(item, report={'status': 'RUNNING', 'final': False})[0] == 'FAIL'
    assert evaluate(item, report={'status': 'PASS', 'final': False})[0] == 'FAIL'
    complete = {'status': 'PASS', 'final': True}
    assert evaluate(item, report=complete, marker='')[0] == 'FAIL'
    assert evaluate(item, report=complete, timeout=True)[0] == 'FAIL'
    assert evaluate(item, report=complete, code=1)[0] == 'FAIL'
    assert evaluate(item, report=complete) == ('PASS', [])


@pytest.mark.parametrize('name', REVIEW_WINDOWS)
@pytest.mark.parametrize('status', ['PASS', 'FAIL', 'ERROR'])
@pytest.mark.parametrize('quit_setting', [None, '0', '1'])
def test_review_window_finishes_report_and_marker_before_optional_exit(
        name, status, quit_setting, tmp_path, capsys):
    from wfrl_blender import atomic_json

    item = spec(name)
    report_path = tmp_path / item.report
    marker = item.marker.removesuffix('PASS') + status
    environment = {} if quit_setting is None else {'WFRL_TEST_QUIT': quit_setting}
    result = dict(status=status, checks={'probe': True})
    at_exit = []

    def quit_blender():
        assert json.loads(report_path.read_text(encoding='utf-8')) == dict(result, final=True)
        at_exit.append(capsys.readouterr().out)
        assert at_exit[-1].startswith(marker + ' ')

    quit_operation = Mock(side_effect=quit_blender)
    finish = script_function(name, 'finish', dict(
        OUT=tmp_path, out=tmp_path, atomic_json=atomic_json, json=json,
        os=SimpleNamespace(environ=environment),
        bpy=SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(quit_blender=quit_operation)))))
    finish(result)

    assert quit_operation.call_count == int(quit_setting == '1')
    report = json.loads(report_path.read_text(encoding='utf-8'))
    log = ''.join(at_exit) + capsys.readouterr().out
    assert log.startswith(marker + ' ')
    assert report['final'] is True
    assert evaluate(item, report=report, marker=log)[0] == ('PASS' if status == 'PASS' else 'FAIL')


@pytest.mark.parametrize('name', REVIEW_WINDOWS)
def test_review_window_write_failure_cannot_quit_or_print_completion(name, tmp_path, capsys):
    quit_operation = Mock()
    writer = Mock(side_effect=OSError('Report write failed'))
    finish = script_function(name, 'finish', dict(
        OUT=tmp_path, out=tmp_path, atomic_json=SimpleNamespace(write_json=writer), json=json,
        os=SimpleNamespace(environ={'WFRL_TEST_QUIT': '1'}),
        bpy=SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(quit_blender=quit_operation)))))
    with pytest.raises(OSError, match='Report write failed'):
        finish({'status': 'PASS'})
    quit_operation.assert_not_called()
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('installed', [False, True])
def test_dual_beam_registers_source_and_preserves_enabled_installation(installed, tmp_path):
    module = 'bl_ext.isolated.wfrl_blender' if installed else 'wfrl_blender'
    addon_root = tmp_path if installed else runner.ADDON_ROOT
    addon = SimpleNamespace(__file__=str(addon_root / '__init__.py'), register=Mock())
    paths = ['/existing']
    namespace = dict(module=module, ROOT=ROOT, Path=Path,
        sys=SimpleNamespace(path=paths), importlib=SimpleNamespace(import_module=Mock(return_value=addon)),
        os=SimpleNamespace(environ={'WFRL_EXPECTED_ADDON_ROOT': str(addon_root)}),
        bpy=SimpleNamespace(context=SimpleNamespace(preferences=SimpleNamespace(addons={module: object()}))))
    load_addon = script_function('dual_beam_window', 'load_addon', namespace)
    assert load_addon() is addon
    assert addon.register.call_count == int(not installed)
    assert paths == (['/existing'] if installed else [str(ROOT / 'blender_frontend'), str(ROOT), '/existing'])
    addon.register.reset_mock()
    namespace['os'].environ['WFRL_EXPECTED_ADDON_ROOT'] = str(tmp_path / 'wrong-source')
    with pytest.raises(AssertionError):
        load_addon()
    addon.register.assert_not_called()
    if installed:
        namespace['os'].environ['WFRL_EXPECTED_ADDON_ROOT'] = str(addon_root)
        namespace['bpy'].context.preferences.addons.clear()
        with pytest.raises(AssertionError, match='must be enabled'):
            load_addon()


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
    inherited = dict(PATH='/bin', WFRL_TEST_KEEP_OPEN='1', WFRL_TEST_QUIT='0', WFRL_TEST_RUNTIME='/foreign',
                     WFRL_FARM_LAUNCHER='/foreign', WFRL_NREL_DEFECT_PHASE='reopen',
                     WFRL_NATIVE_PLAYBACK_MANUAL_SPACE='1', BLENDER_USER_CONFIG='/user',
                     PYTHONPATH='/foreign')
    first = runner.isolated_environment(tmp_path / 'first', tmp_path / 'result.json', 'run', inherited)
    second = runner.isolated_environment(tmp_path / 'second', tmp_path / 'result.json', 'save', inherited)
    for name in ('WFRL_TEST_KEEP_OPEN', 'WFRL_TEST_QUIT', 'WFRL_TEST_RUNTIME', 'WFRL_FARM_LAUNCHER',
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


@pytest.mark.parametrize('has_sigkill', [False, True], ids=['windows', 'posix'])
@pytest.mark.parametrize('term_stops', [False, True], ids=['force-required', 'term-sufficient'])
def test_stop_process_uses_available_force_termination(monkeypatch, has_sigkill, term_stops):
    signals = SimpleNamespace(SIGTERM=15, SIGKILL=9)
    monkeypatch.setattr(runner, 'signal', signals)
    if not has_sigkill:
        monkeypatch.delattr(signals, 'SIGKILL')
    alive = True

    def signal_owned(signum):
        nonlocal alive
        if term_stops or signum == 9:
            alive = False

    def kill():
        nonlocal alive
        alive = False

    owner = Mock()
    owner.signal.side_effect = signal_owned
    owner.alive.side_effect = lambda: alive
    process = Mock()
    process.kill.side_effect = kill

    runner.stop_process(process, owner, grace=0)

    assert owner.signal.call_args_list == ([call(15), call(9)]
                                          if has_sigkill and not term_stops else [call(15)])
    assert process.kill.call_count == int(not has_sigkill and not term_stops)
    process.wait.assert_called_once_with(timeout=0)
    assert not alive


def test_stop_without_sigkill_rejects_unconfirmed_cleanup(monkeypatch):
    monkeypatch.setattr(runner, 'signal', SimpleNamespace(SIGTERM=15))
    owner, process = Mock(), Mock()
    owner.alive.return_value = True

    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        runner.stop_process(process, owner, grace=0)

    owner.signal.assert_called_once_with(15)
    process.kill.assert_called_once_with()
    process.wait.assert_not_called()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-session regression')
@pytest.mark.parametrize('parent_mode', ['exit_on_term', 'ignore_term', 'already_exited'])
def test_stop_reaps_owned_descendants_after_launcher_exit(tmp_path, parent_mode, monkeypatch):
    import psutil

    child_script = tmp_path / 'child.py'
    ready = tmp_path / 'child.pid'
    child_script.write_text('import os,signal,sys,threading\nfrom pathlib import Path\n'
        'signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
        'ready=Path(sys.argv[1]); temporary=ready.with_suffix(".tmp")\n'
        'temporary.write_text(str(os.getpid()), encoding="ascii"); temporary.replace(ready)\n'
        'threading.Event().wait()\n', encoding='utf-8')
    parent_script = tmp_path / 'parent.py'
    parent_script.write_text('import subprocess,sys,threading,signal\n'
        'if sys.argv[3] == "ignore_term": signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
        'subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n'
        'if sys.argv[3] != "already_exited": threading.Event().wait()\n', encoding='utf-8')
    owner = runner.OwnedProcesses(env_key='WFRL_TEST_REGRESSION_TOKEN')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import threading;threading.Event().wait()'])
    process = owner.start([sys.executable, str(parent_script), str(child_script), str(ready), parent_mode],
                          cwd=tmp_path, env=os.environ)
    child = None
    try:
        deadline = time.monotonic() + WAIT_TIMEOUT
        while not ready.exists():
            assert time.monotonic() < deadline, (
                f'Child readiness timed out: parent_mode={parent_mode}, parent_exit={process.poll()}, path={ready}')
            threading.Event().wait(.01)
        child = psutil.Process(int(ready.read_text(encoding='ascii')))
        if parent_mode == 'already_exited':
            process.wait(timeout=WAIT_TIMEOUT)
        signalled, clock = [None], [0.]
        original_signal = owner.signal

        def signal_owner(signum):
            original_signal(signum)
            signalled[0] = signum

        def monotonic():
            # Expire the ignored TERM grace; KILL and reaping keep a real time budget.
            if signalled[0] == runner.signal.SIGTERM:
                clock[0] += WAIT_TIMEOUT
                return clock[0]
            return time.monotonic()

        monkeypatch.setattr(owner, 'signal', signal_owner)
        monkeypatch.setattr(runner, 'time', SimpleNamespace(monotonic=monotonic, sleep=time.sleep))
        runner.stop_process(process, owner, grace=WAIT_TIMEOUT)
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
        process.wait(timeout=WAIT_TIMEOUT)
        unrelated.terminate()
        unrelated.wait(timeout=WAIT_TIMEOUT)
