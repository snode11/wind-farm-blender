"""Exercise the installed check runner with synthetic profiles and process results."""
import importlib.util
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from blender_frontend.tests._platform import symlink_or_skip


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('wfrl_installed_window_runner',
                                              ROOT / 'scripts/blender/run_installed_windows.py')
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)

TEXTURE = 'mappo_same_source_texture_window.py'
REOPEN = 'mappo_same_source_texture_reopen.py'


@pytest.mark.parametrize('platform', ['nt', 'posix'])
@pytest.mark.parametrize('outcome', ['success', 'timeout', 'interrupt'])
def test_run_process_uses_platform_process_group(tmp_path, monkeypatch, platform, outcome):
    monkeypatch.setattr(runner, 'os', SimpleNamespace(name=platform))
    monkeypatch.setattr(runner.subprocess, 'CREATE_NEW_PROCESS_GROUP', 512, raising=False)
    process = Mock(returncode=0 if outcome == 'success' else -1)
    process.wait.return_value = process.returncode
    if outcome == 'timeout':
        process.wait.side_effect = runner.subprocess.TimeoutExpired('fixture', 7)
    elif outcome == 'interrupt':
        process.wait.side_effect = KeyboardInterrupt
    popen = Mock(return_value=process)
    stop = Mock()
    monkeypatch.setattr(runner.subprocess, 'Popen', popen)
    monkeypatch.setattr(runner, 'stop_process', stop)

    result = runner.run_process(['fixture'], {'FIXTURE': '1'}, tmp_path / 'process.log', 7)

    args, options = popen.call_args
    assert args == (['fixture'],)
    assert options['cwd'] == tmp_path and options['env'] == {'FIXTURE': '1'}
    if platform == 'nt':
        assert options['creationflags'] == 512
        assert 'start_new_session' not in options
    else:
        assert options['start_new_session'] is True
        assert 'creationflags' not in options
    assert result == runner.ProcessResult(process.returncode, timed_out=outcome == 'timeout',
                                          interrupted=outcome == 'interrupt')
    process.wait.assert_called_once_with(timeout=7)
    if outcome == 'success':
        stop.assert_not_called()
    else:
        stop.assert_called_once_with(process)


@pytest.mark.parametrize('force_required', [False, True])
def test_windows_stop_terminates_then_kills_without_posix_apis(monkeypatch, force_required):
    monkeypatch.setattr(runner, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(runner, 'signal', SimpleNamespace(SIGTERM=15))
    process = Mock()
    process.wait.side_effect = ([runner.subprocess.TimeoutExpired('fixture', .5), -1]
                               if force_required else [0])

    runner.stop_process(process, grace=.5)

    process.terminate.assert_called_once_with()
    assert process.kill.call_count == int(force_required)
    assert process.wait.call_args_list == [call(timeout=.5)] * (1 + int(force_required))


@pytest.mark.parametrize('group_exists', [False, True])
def test_posix_stop_signals_session_after_leader_exit(monkeypatch, group_exists):
    killpg = Mock(side_effect=None if group_exists else ProcessLookupError)
    monkeypatch.setattr(runner, 'os', SimpleNamespace(name='posix', killpg=killpg))
    monkeypatch.setattr(runner, 'signal', SimpleNamespace(SIGTERM=15, SIGKILL=9))
    process = Mock(pid=321, returncode=0)

    runner.stop_process(process, grace=0)

    assert killpg.call_args_list == ([call(321, 15), call(321, 9)]
                                     if group_exists else [call(321, 15)])
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    process.wait.assert_called_once_with(timeout=0)


@pytest.fixture
def profile_dir(tmp_path):
    directory = tmp_path / 'installed'
    resources = directory / 'profile'
    environment = {'WFRL_ADDON_MODULE': runner.MODULE}
    for key, name in runner.PROFILE_DIRS.items():
        path = resources / name
        path.mkdir(parents=True)
        environment[key] = str(path)
    (resources / 'config/userpref.blend').touch()
    addon = resources / 'extensions/wfrl_blender'
    (addon / runner.EXAMPLE).parent.mkdir(parents=True)
    (addon / runner.EXAMPLE).write_bytes(b'geometry fixture')
    (addon / '__init__.py').touch()
    (addon / 'blender_manifest.toml').touch()
    runner.write_json(directory / 'environment.json', environment)
    return directory


def test_environment_uses_profile_and_independent_scene_copies(profile_dir, tmp_path, monkeypatch):
    profile = runner.load_profile(profile_dir)
    for key in ('BLENDER_USER_CONFIG', 'BLENDER_USER_RESOURCES', 'BLENDER_USER_EXTENSIONS', 'WFRL_ADDON_ROOT',
                'WFRL_ORIGINAL_RECON', 'WFRL_EXPECTED_GEOMETRY_SHA256', 'WFRL_SPLIT_SAVE',
                'WFRL_SIDEBAR_EXPECT_OLD', 'PYTHONPATH'):
        monkeypatch.setenv(key, 'inherited override')
    default, workflow = runner.select_tests(['mappo_full_view_default_window', 'reconstruction_workflow_window'])
    copies = []
    for check in (default, workflow):
        case = tmp_path / Path(check.script).stem
        case.mkdir()
        command, env, report = runner.prepare_case(check, Path(sys.executable), profile, case, None)
        for key, value in profile.environment.items():
            assert env[key] == value
        assert env['BLENDER_USER_RESOURCES'] == str(profile_dir / 'profile')
        assert env['BLENDER_USER_EXTENSIONS'] == str(profile_dir / 'profile/extensions')
        assert env['WFRL_EXPECTED_ADDON_ROOT'] == str(profile.addon_root)
        assert env['WFRL_TEST_OUTPUT'] == str(report.parent)
        assert env['WFRL_QUIT_AFTER_TEST'] == '1'
        assert env['PYTHONDONTWRITEBYTECODE'] == '1'
        assert 'inherited override' not in env.values()
        assert '--background' not in command and '--factory-startup' not in command
        assert '--disable-autoexec' in command
        assert command[command.index('--python-expr') + 1] == 'import sys; sys.dont_write_bytecode=True'
        scene = Path(env['WFRL_SPLIT_INPUT'])
        assert (str(scene) in command) is (check == default)
        assert scene.parent == case
        assert scene.read_bytes() == (profile.addon_root / runner.EXAMPLE).read_bytes()
        copies.append(scene)
    assert not copies[0].samefile(copies[1])
    assert not copies[0].samefile(profile.addon_root / runner.EXAMPLE)


@pytest.mark.parametrize('source', ['profile', 'config', 'config_link', 'module', 'resources', 'extensions'])
def test_daily_installation_is_rejected_before_output_or_launch(source, profile_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    daily = Path.home() / 'Library/Application Support/Blender/5.2/config'
    daily.mkdir(parents=True)
    environment = profile_dir / 'environment.json'
    overrides = runner.read_object(environment)
    selected = profile_dir
    if source == 'profile':
        selected = daily
    elif source == 'config':
        overrides['BLENDER_USER_CONFIG'] = str(daily)
    elif source == 'config_link':
        link = profile_dir / 'config-link'
        symlink_or_skip(link, daily, target_is_directory=True)
        overrides['BLENDER_USER_CONFIG'] = str(link)
    elif source in ('resources', 'extensions'):
        overrides['BLENDER_USER_' + source.upper()] = str(daily)
    else:
        overrides['WFRL_ADDON_MODULE'] = 'bl_ext.user_default.wfrl_blender'
    runner.write_json(environment, overrides)
    monkeypatch.setattr(runner, 'run_process', lambda *args: pytest.fail('Launched Blender for a daily profile'))
    output = tmp_path / 'results'
    with pytest.raises(SystemExit) as error:
        runner.main(['--installed-profile', str(selected), '--output', str(output)])
    assert error.value.code == 2
    assert 'Refusing daily Blender' in capsys.readouterr().err
    assert not output.exists()


def test_profile_with_a_removed_resource_directory_is_rejected(profile_dir):
    (profile_dir / 'profile/datafiles').rmdir()
    with pytest.raises(ValueError, match='directory is missing: BLENDER_USER_DATAFILES'):
        runner.load_profile(profile_dir)


def test_validator_is_called_directly_without_a_launcher(tmp_path, monkeypatch):
    blender = tmp_path / "Blender with 'quotes'"
    calls = []
    monkeypatch.setattr(runner, 'setup_command', lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(runner, 'load_profile', lambda directory: directory)
    assert runner.prepare_installation(blender, tmp_path) == tmp_path / 'package'
    (args, kwargs), = calls
    assert args[0] == [sys.executable, '-B', str(runner.VALIDATOR), '--blender', str(blender),
                       '--output', str(tmp_path / 'package')]
    assert args[3] == 'FRONTEND_PACKAGE_VALIDATION_PASS'
    assert kwargs['timeout_s'] == 1800
    assert list((tmp_path / 'install-tmp').iterdir()) == []
    assert not hasattr(runner, 'validator_launcher')


def test_profile_preserves_explicit_resource_and_extension_paths(profile_dir):
    environment = runner.read_object(profile_dir / 'environment.json')
    resources = profile_dir / 'resources'
    resources.mkdir()
    extensions = resources / 'custom-extensions'
    (profile_dir / 'profile/extensions').rename(extensions)
    environment.update(BLENDER_USER_RESOURCES=str(resources), BLENDER_USER_EXTENSIONS=str(extensions))
    runner.write_json(profile_dir / 'environment.json', environment)
    profile = runner.load_profile(profile_dir)
    assert profile.environment == environment
    assert profile.addon_root == extensions / runner.ADDON_ID


def test_zip_install_saves_and_passes_the_same_isolated_environment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runner, 'setup_command', lambda command, env, *args, **kwargs: calls.append(dict(env)))
    monkeypatch.setattr(runner, 'load_profile', lambda directory: directory)
    runner.prepare_installation(Path('/fake/Blender'), tmp_path, tmp_path / 'fixture.zip')
    environment = runner.read_object(tmp_path / 'package/environment.json')
    assert environment['BLENDER_USER_RESOURCES'] == str(tmp_path / 'package/profile')
    assert environment['BLENDER_USER_EXTENSIONS'] == str(tmp_path / 'package/profile/extensions')
    assert len(calls) == 3
    for env in calls:
        assert all(env[key] == value for key, value in environment.items())


@pytest.mark.parametrize(('exit_code', 'timed_out', 'report', 'marker', 'status'), [
    (0, False, {'status': 'PASS'}, 'complete', 'PASS'),
    (1, False, {'status': 'PASS'}, 'complete', 'FAIL'),
    (0, True, {'status': 'PASS'}, 'complete', 'FAIL'),
    (0, False, None, 'complete', 'FAIL'),
    (0, False, {'status': 'FAIL'}, 'complete', 'FAIL'),
    (0, False, {'status': 'RUNNING'}, 'complete', 'FAIL'),
    (0, False, {'status': 'PASS', 'final': False}, 'complete', 'FAIL'),
    (0, False, {'status': 'PASS'}, 'embedded', 'FAIL'),
    (0, False, {'status': 'SKIP', 'reason': 'Unavailable'}, 'embedded', 'SKIP'),
])
def test_verdict_requires_normal_exit_final_report_and_marker(exit_code, timed_out, report, marker, status):
    check = runner.CHECKS[0]
    log = f'{check.marker} /tmp/report.json\n' if marker == 'complete' else f'Waiting for {check.marker}\n'
    result, reasons = runner.evaluate(check, runner.ProcessResult(exit_code, timed_out), report, log)
    assert result == status
    assert bool(reasons) is (status != 'PASS')


@pytest.mark.parametrize('texture_status', ['PASS', 'FAIL', None])
def test_reopen_dependency_order_and_verdict(texture_status, profile_dir, tmp_path, monkeypatch):
    started = []

    def fake_process(command, env, log, timeout):
        name = Path(command[-1]).name
        started.append(name)
        check, = runner.select_tests([name])
        report = Path(env['WFRL_TEST_OUTPUT']) / check.report
        if name == TEXTURE:
            assert '--background' not in command
            data = {'status': texture_status}
            if texture_status == 'PASS':
                saved = Path(env['WFRL_SPLIT_SAVE'])
                saved.parent.mkdir()
                saved.write_bytes(b'saved scene')
                data['saved'] = str(saved)
            runner.write_json(report, data, mkdir=True)
        else:
            assert '--background' in command and '--factory-startup' not in command
            baseline = runner.read_object(Path(env['WFRL_EXPECTED_WINDOW_REPORT']))
            assert baseline['status'] == 'PASS'
            assert baseline['saved'] == env['WFRL_SPLIT_INPUT'] == command[command.index('--python') - 1]
            assert Path(baseline['saved']).read_bytes() == b'saved scene'
            runner.write_json(report, {'status': 'PASS'}, mkdir=True)
        log.write_text('\n'.join([f'line {i}' for i in range(43)] + [check.marker]) + '\n', encoding='utf-8')
        return runner.ProcessResult(0)

    monkeypatch.setattr(runner, 'run_process', fake_process)
    output = tmp_path / 'result'
    names = [REOPEN, TEXTURE] if texture_status else [REOPEN]
    code = runner.main(['--blender', sys.executable, '--installed-profile', str(profile_dir),
                        '--output', str(output), '--tests', *names])
    records = runner.read_object(output / 'result.json')['tests']
    assert records[-1]['mode'] == 'background'
    if texture_status == 'PASS':
        assert started == [TEXTURE, REOPEN]
        assert [item['status'] for item in records] == ['PASS', 'PASS']
        assert code == 0
    else:
        assert started == ([TEXTURE] if texture_status else [])
        assert records[-1]['status'] == 'NOT_RUN'
        assert TEXTURE in records[-1]['reasons'][0]
        assert code == (1 if texture_status else 2)
        if texture_status:
            assert records[0]['status'] == 'FAIL'
            assert len(records[0]['log_tail']) == 40
            assert records[0]['log_tail'][0] == 'line 4'
    for row in records:
        launch = runner.read_object(output / Path(row['script']).stem / 'launch.json')
        assert launch['mode'] == row['mode']


def test_list_prints_modes_and_dependency_without_running(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(runner, 'run_process', lambda *args: pytest.fail('--list launched a process'))
    output = tmp_path / 'result'
    assert runner.main(['--list', '--output', str(output), '--tests', REOPEN, TEXTURE]) == 0
    assert capsys.readouterr().out.splitlines() == [
        'Installation: workspace', f'{TEXTURE} [visible]', f'{REOPEN} [background] (requires PASS from {TEXTURE})',
    ]
    assert not output.exists()


def test_configured_modes_match_installed_module_documentation():
    verification = ROOT / '.agents/skills/windfarm-blender-dev/references/verification.md'
    row = next(line for line in verification.read_text(encoding='utf-8').splitlines() if '| 仅安装模块' in line)
    documented = {}
    for group in row.split('|')[1].split('；'):
        mode, = re.findall(r'`(visible|background)`', group)
        documented.update((script, mode) for script in re.findall(r'`(\w+\.py)`', group))
    assert documented == {check.script: check.mode for check in runner.CHECKS}
    assert {script for script, mode in documented.items() if mode == 'background'} == {REOPEN}
    assert len(documented) == 6
