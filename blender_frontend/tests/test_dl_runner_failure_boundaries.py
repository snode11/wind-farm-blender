"""Runner failure contracts using owned-process and Blender API substitutes."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from blender_frontend.tests._platform import symlink_or_skip


ROOT = Path(__file__).resolve().parents[2]


def load_runner(name):
    spec = importlib.util.spec_from_file_location('dl_' + name, ROOT / 'scripts/blender' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source = load_runner('run_regressions')
installed = load_runner('run_installed_windows')


@pytest.mark.parametrize('name', ['run_regressions', 'run_installed_windows', 'profile_frontend_startup'])
def test_entrypoint_disables_bytecode_before_workspace_imports(name):
    from blender_frontend.tests.test_blender_subprocess_bytecode import _assert_bytecode_disabled

    path = ROOT / 'scripts/blender' / (name + '.py')
    _assert_bytecode_disabled(ast.parse(path.read_text(encoding='utf-8')), str(path))


class Process:
    def __init__(self, outcome=0):
        self.outcome = outcome
        self.returncode = None
        self.pid = 12345

    def wait(self, timeout=None):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        self.returncode = self.outcome
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.outcome = self.returncode = -9


class Owner:
    ENV_KEY = 'WFRL_DL_FAKE_TOKEN'
    token = 'synthetic-owned-process'

    def __init__(self, process, *, live=True):
        self.process = process
        self.live = live
        self.started = []
        self.signals = []

    def start(self, command, **options):
        self.started.append((command, options))
        return self.process

    def alive(self):
        return self.live

    def status(self):
        return 'UNKNOWN' if self.live else 'EMPTY_CONFIRMED'

    def identities(self):
        return [{'pid': self.process.pid, 'create_time': 123456.0}]

    def signal(self, signum):
        self.signals.append(signum)
        self.live = False
        if self.process.poll() is None:
            self.process.kill()


@pytest.mark.parametrize('writer', [source.write_json, installed.write_json], ids=['source', 'installed'])
def test_json_write_failure_preserves_previous_report_and_removes_temporary(tmp_path, monkeypatch, writer):
    destination = tmp_path / 'result.json'
    original = '{"status": "RUNNING", "final": false}\n'
    destination.write_text(original, encoding='utf-8')
    monkeypatch.setattr(os, 'fsync', Mock(side_effect=OSError('disk sync failed')))
    with pytest.raises(OSError, match='disk sync failed'):
        writer(destination, {'status': 'PASS', 'final': True})
    assert destination.read_text(encoding='utf-8') == original
    assert list(tmp_path.iterdir()) == [destination]


@pytest.mark.parametrize('write_failure', [False, True])
def test_bootstrap_publishes_only_complete_json(tmp_path, monkeypatch, write_failure):
    script = tmp_path / 'synthetic.py'
    script.write_text('pass\n', encoding='utf-8')
    destination = tmp_path / 'execution.json'
    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setattr(sys, 'argv', ['fixture', '--', str(script), 'background', str(destination), str(ROOT)])
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(
        app=SimpleNamespace(background=True, version_string='fixture'), data=SimpleNamespace(is_dirty=False),
        context=SimpleNamespace(preferences=SimpleNamespace(filepaths=SimpleNamespace(use_save_on_exit=True)))))
    monkeypatch.setitem(sys.modules, 'wfrl_blender', SimpleNamespace(__file__=str(source.ADDON_ROOT / '__init__.py')))
    monkeypatch.setenv('TMPDIR', str(tmp_path))
    if write_failure:
        sync = os.fsync
        calls = []

        def failing_sync(fd):
            calls.append(fd)
            if len(calls) > 1:
                raise OSError('fsync refused')
            sync(fd)

        monkeypatch.setattr(os, 'fsync', failing_sync)
        with pytest.raises(OSError, match='fsync refused'):
            exec(source.BOOTSTRAP, {})
    else:
        exec(source.BOOTSTRAP, {})
    record = source.read_json(destination)
    assert record['status'] == ('RUNNING' if write_failure else 'PASS')
    assert record['final'] is not write_failure
    assert not list(tmp_path.glob('.json-*.tmp'))


def test_stop_continues_to_force_signal_after_term_failure(monkeypatch):
    process = Process()
    owner = Owner(process)

    def send(signum):
        owner.signals.append(signum)
        if signum == 15:
            raise PermissionError('TERM request denied')
        owner.live = False
        process.kill()

    owner.signal = send
    monkeypatch.setattr(source, 'signal', SimpleNamespace(SIGTERM=15, SIGKILL=9))
    source.stop_process(process, owner, grace=0)
    assert owner.signals == [15, 9]
    assert process.returncode == -9 and not owner.alive()


def test_unconfirmed_cleanup_distinguishes_unknown_ownership(monkeypatch):
    process = Process(-11)
    process.wait()
    owner = Owner(process)
    owner.signal = Mock()
    with pytest.raises(RuntimeError, match='UNKNOWN'):
        source.stop_process(process, owner, grace=0)


def test_tree_inspection_failure_still_uses_retained_launcher_handle():
    process = Process()
    process.terminate = Mock()
    owner = Owner(process)
    owner.signal = Mock(side_effect=PermissionError('tree inspection denied'))
    with pytest.raises(RuntimeError, match='cleanup unconfirmed'):
        source.stop_process(process, owner, grace=0)
    process.terminate.assert_called_once_with()
    assert process.returncode == -9


def invocation(tmp_path, monkeypatch, process, owner):
    case = tmp_path / 'case'
    case.mkdir()
    monkeypatch.setattr(source, 'OwnedProcesses', lambda **kwargs: owner)
    monkeypatch.setattr(source, 'source_hashes', lambda script: {'source': 'stable'})
    args = SimpleNamespace(blender=Path(sys.executable), timeout=0.01)
    spec = source.Regression('synthetic.py', 'SYNTHETIC_PASS', None, package='synthetic')
    return source.run_invocation(spec, 'run', args, case, tmp_path / 'scratch', tmp_path / 'bootstrap.py')


@pytest.mark.parametrize('failure', [RuntimeError('inspection unavailable'), subprocess.TimeoutExpired('reap', 0)])
@pytest.mark.parametrize('interrupted', [False, True])
def test_source_retries_cleanup_with_same_handles_and_preserves_timeout(tmp_path, monkeypatch, failure, interrupted):
    process = Process(KeyboardInterrupt() if interrupted else subprocess.TimeoutExpired('synthetic', .01))
    owner = Owner(process)
    attempts = []

    def stop(child, tree, **kwargs):
        attempts.append((child, tree))
        if len(attempts) == 1:
            raise failure
        tree.live = False
        child.kill()

    monkeypatch.setattr(source, 'stop_process', stop)
    result = invocation(tmp_path, monkeypatch, process, owner)
    assert attempts == [(process, owner), (process, owner)]
    assert result['status'] == 'FAIL' and result['timed_out'] is not interrupted
    assert result['interrupted'] is interrupted
    assert result['returncode'] == -9
    assert owner.started[0][1]['stdout'].closed


def test_source_unconfirmed_cleanup_is_recorded_and_retryable(tmp_path, monkeypatch):
    process = Process(-11)
    owner = Owner(process)
    stop = Mock(side_effect=RuntimeError('cleanup unconfirmed: UNKNOWN'))
    monkeypatch.setattr(source, 'stop_process', stop)
    result = invocation(tmp_path, monkeypatch, process, owner)
    assert result['status'] == 'FAIL' and result['returncode'] == -11
    recovery = tmp_path / 'case/run/cleanup.json'
    assert recovery.is_file()
    record = json.loads(recovery.read_text(encoding='utf-8'))
    assert record['identities'] == owner.identities()
    assert record['token'] == owner.token and record['env_key'] == owner.ENV_KEY
    assert any(str(recovery) in reason and 'UNKNOWN' in reason for reason in result['reasons'])
    assert owner.started[0][1]['stdout'].closed

    def finish(child, tree, **kwargs):
        assert child is process and tree is owner
        tree.live = False

    monkeypatch.setattr(source, 'stop_process', finish)
    source.retry_cleanup(recovery)
    assert not recovery.exists()


def test_cleanup_retry_recovers_recorded_identities_after_runner_exit(tmp_path, monkeypatch):
    recovery = tmp_path / 'cleanup.json'
    owner = Owner(Process(-11))
    source.write_json(recovery, dict(status='UNCONFIRMED', identities=owner.identities(),
                                    token=owner.token, env_key=owner.ENV_KEY))
    recover = Mock(return_value=owner)
    monkeypatch.setattr(source, 'OwnedProcesses', SimpleNamespace(recover=recover))
    stop = Mock()
    monkeypatch.setattr(source, 'stop_process', stop)
    source.retry_cleanup(recovery)
    recover.assert_called_once_with(owner.identities(), token=owner.token, env_key=owner.ENV_KEY)
    stop.assert_called_once_with(None, owner)
    assert not recovery.exists() and not source.cleanup_pending(tmp_path)


def test_failed_recovery_record_write_keeps_handles_for_retry(tmp_path, monkeypatch):
    process = Process(-11)
    owner = Owner(process)
    writer = source.write_json

    def write(path, value):
        if path.name == 'cleanup.json':
            raise OSError('record disk unavailable')
        writer(path, value)

    monkeypatch.setattr(source, 'write_json', write)
    monkeypatch.setattr(source, 'stop_process', Mock(side_effect=RuntimeError('cleanup unconfirmed')))
    result = invocation(tmp_path, monkeypatch, process, owner)
    assert result['status'] == 'FAIL' and source.cleanup_pending(tmp_path)
    assert any('recovery record failed' in reason for reason in result['reasons'])
    assert not (tmp_path / 'case/run/cleanup.json').exists()
    monkeypatch.setattr(source, 'stop_process', lambda child, tree: setattr(tree, 'live', False))
    source.retry_cleanup(tmp_path / 'case/run/cleanup.json')
    assert not source.cleanup_pending(tmp_path)


def test_source_stops_batch_after_unconfirmed_cleanup(tmp_path, monkeypatch):
    process = Process(-11)
    owner = Owner(process)
    monkeypatch.setattr(source, 'OwnedProcesses', lambda **kwargs: owner)
    monkeypatch.setattr(source, 'source_hashes', lambda script: {})
    monkeypatch.setattr(source, 'package_inputs', lambda: {})
    monkeypatch.setattr(source, 'stop_process', Mock(side_effect=RuntimeError('cleanup unconfirmed')))
    output = tmp_path / 'output'
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    sentinel = scratch / 'existing.txt'
    sentinel.write_text('keep', encoding='utf-8')
    code = source.main(['--blender', sys.executable, '--tests', 'home_entry_smoke', 'manual_pose_smoke',
                        '--output', str(output), '--scratch-root', str(scratch)])
    summary = source.read_json(output / 'result.json')
    assert code == 1 and summary['status'] == 'FAIL' and summary['final'] is True
    assert [row['status'] for row in summary['tests']] == ['FAIL', 'NOT_RUN']
    assert len(owner.started) == 1
    assert sentinel.read_text(encoding='utf-8') == 'keep'
    monkeypatch.setattr(source, 'stop_process', lambda child, tree, **kwargs: setattr(tree, 'live', False))
    source.retry_cleanup(output / 'home_entry_smoke/run/cleanup.json')


@pytest.mark.parametrize('kind', ['source', 'installed'])
def test_registration_error_after_spawn_is_reported_and_reaps_handle(tmp_path, monkeypatch, kind):
    import psutil

    process = Process()
    owner = Owner(process)
    owner.start = Mock(side_effect=psutil.AccessDenied(process.pid))
    if kind == 'source':
        result = invocation(tmp_path, monkeypatch, process, owner)
        assert result['status'] == 'FAIL'
        reasons = result['reasons']
    else:
        monkeypatch.setattr(installed, 'OwnedProcesses', lambda **kwargs: owner)
        result = installed.run_process(['synthetic'], {}, tmp_path / 'blender.log', .01)
        status, reasons = installed.evaluate(installed.CHECKS[0], result, {'status': 'PASS'}, installed.CHECKS[0].marker)
        assert status == 'FAIL'
    assert any(str(process.pid) in reason for reason in reasons)
    assert process.returncode == -9 and not owner.alive()


def test_recovery_without_identities_reports_its_restart_limit(tmp_path, monkeypatch):
    process = Process(-11)
    owner = Owner(process)
    owner.identities = lambda: []
    monkeypatch.setattr(source, 'stop_process', Mock(side_effect=RuntimeError('cleanup unconfirmed: UNKNOWN')))
    result = invocation(tmp_path, monkeypatch, process, owner)
    assert any('no recorded identities' in reason for reason in result['reasons'])
    monkeypatch.setattr(source, 'stop_process', lambda child, tree, **kwargs: setattr(tree, 'live', False))
    source.retry_cleanup(tmp_path / 'case/run/cleanup.json')


@pytest.mark.parametrize('timeout', ['nan', 'inf', '-inf'])
def test_source_rejects_nonfinite_timeout_before_creating_output(tmp_path, timeout):
    output = tmp_path / 'output'
    with pytest.raises(SystemExit) as exc:
        source.main(['--blender', sys.executable, '--tests', 'home_entry_smoke', '--mode', 'visible',
                     '--output', str(output), '--scratch-root', str(tmp_path / 'scratch'), '--timeout=' + timeout])
    assert exc.value.code == 2
    assert not output.exists()


def test_source_rejects_existing_dangling_output_symlink(tmp_path):
    output, target = tmp_path / 'output', tmp_path / 'target'
    symlink_or_skip(output, target, target_is_directory=True)
    with pytest.raises(SystemExit) as exc:
        source.main(['--blender', sys.executable, '--tests', 'home_entry_smoke', '--mode', 'visible',
                     '--output', str(output), '--scratch-root', str(tmp_path / 'scratch')])
    assert exc.value.code == 2
    assert output.is_symlink() and not target.exists()


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity', '1e309', '-1e309'])
@pytest.mark.parametrize('kind', ['source', 'installed'])
def test_nonfinite_json_is_rejected_as_invalid_evidence(tmp_path, kind, constant):
    report = tmp_path / 'result.json'
    report.write_text('{"status": "PASS", "metric": ' + constant + '}', encoding='utf-8')
    if kind == 'source':
        assert source.read_json(report)['status'] == 'MISSING_OR_INVALID_JSON'
    else:
        with pytest.raises(ValueError):
            installed.read_object(report)


@pytest.mark.parametrize('value', ['false', 1, []])
def test_source_requires_boolean_normal_return_evidence(value):
    spec = source.Regression('synthetic.py', 'SYNTHETIC_PASS', None)
    execution = dict(status='PASS', returned_normally=value, background=True, final=True)
    assert source.evaluate(spec, 'run', 0, False, execution, None, spec.marker)[0] == 'FAIL'


@pytest.mark.parametrize('returncode', [0, -11, -15])
def test_installed_cleans_descendants_after_launcher_exit(tmp_path, monkeypatch, returncode):
    process = Process(returncode)
    owner = Owner(process)
    monkeypatch.setattr(installed.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(installed, 'OwnedProcesses', lambda **kwargs: owner, raising=False)
    log = tmp_path / 'blender.log'
    result = installed.run_process(['synthetic'], {}, log, .01)
    assert not owner.alive()
    assert result.returncode == returncode
    check = installed.CHECKS[0]
    assert installed.evaluate(check, result, {'status': 'PASS'}, check.marker)[0] == 'FAIL'
    assert owner.started[0][1]['stdout'].closed


def test_installed_batch_stops_with_failed_exit_and_recovery_record(tmp_path, monkeypatch):
    shared = sys.modules[installed.cleanup_process.__module__]
    owner = Owner(Process(-11))
    profile = installed.Profile(tmp_path / 'profile', {'WFRL_ADDON_MODULE': installed.MODULE}, tmp_path / 'addon')
    monkeypatch.setattr(installed, 'load_profile', lambda path: profile)
    monkeypatch.setattr(installed, 'OwnedProcesses', lambda **kwargs: owner)
    monkeypatch.setattr(installed, 'prepare_case', lambda check, blender, profile, case, prerequisite:
                        (['synthetic'], {}, case / 'result.json'))
    monkeypatch.setattr(shared, 'stop_process', Mock(side_effect=RuntimeError('cleanup unconfirmed: UNKNOWN')))
    output = tmp_path / 'results'
    code = installed.main(['--blender', sys.executable, '--installed-profile', str(profile.directory),
                           '--output', str(output), '--tests', *(check.script for check in installed.CHECKS[:2])])
    report = installed.read_object(output / 'result.json')
    assert code == 1 and report['status'] == 'FAIL' and report['final'] is True
    assert [row['status'] for row in report['tests']] == ['FAIL', 'NOT_RUN']
    assert report['tests'][0]['returncode'] == -11
    assert len(owner.started) == 1
    monkeypatch.setattr(shared, 'stop_process', lambda child, tree: setattr(tree, 'live', False))
    shared.retry_cleanup(output / Path(installed.CHECKS[0].script).stem / 'blender.cleanup.json')


def test_installed_preserves_unicode_spaces_and_quotes_in_argv_and_environment(tmp_path, monkeypatch):
    root = tmp_path / "中文 路径 'quoted'"
    addon = root / 'installed addon'
    example = addon / installed.EXAMPLE
    example.parent.mkdir(parents=True)
    example.write_bytes(b'fixture scene')
    case = root / 'case output'
    case.mkdir()
    profile = installed.Profile(root, {'WFRL_ADDON_MODULE': installed.MODULE}, addon)
    blender = root / "Blender 安装 'bin'/blender.exe"
    command, env, report = installed.prepare_case(installed.CHECKS[0], blender, profile, case, None)
    owner = Owner(Process(0), live=False)
    monkeypatch.setattr(installed, 'OwnedProcesses', lambda **kwargs: owner)
    assert installed.run_process(command, env, case / 'blender.log', 1).returncode == 0
    argv, options = owner.started[0]
    assert argv[0] == str(blender)
    assert argv[argv.index('--python') - 1] == env['WFRL_SPLIT_INPUT']
    assert Path(env['WFRL_SPLIT_INPUT']).read_bytes() == b'fixture scene'
    assert env['WFRL_TEST_OUTPUT'] == str(report.parent)
    assert options['env'] == env and options['cwd'] == case
    assert not options.get('shell', False)


@pytest.mark.parametrize('report_status', ['PASS', 'SKIP', 'NOT_RUN'])
def test_installed_python_error_overrides_report_and_marker(report_status):
    check = installed.CHECKS[0]
    log = check.marker + '\nError: Python: Traceback (most recent call last):\n  File "面板.py", line 2, in draw\nRuntimeError: callback failed\n'
    status, reasons = installed.evaluate(check, installed.ProcessResult(0), {'status': report_status}, log)
    assert status == 'FAIL' and any('callback failed' in reason for reason in reasons)


def test_installed_setup_rejects_traceback_after_success_marker(tmp_path, monkeypatch):
    log = tmp_path / 'setup.log'

    def run(*args):
        log.write_text('SETUP_PASS\nTraceback (most recent call last):\n  File "setup.py", line 5\nRuntimeError: failed\n', encoding='utf-8')
        return installed.ProcessResult(0)

    monkeypatch.setattr(installed, 'run_process', run)
    with pytest.raises(RuntimeError, match='failed'):
        installed.setup_command(['synthetic'], {}, log, 'SETUP_PASS')


@pytest.mark.parametrize('interrupted', [False, True])
def test_installed_setup_preserves_cleanup_failure_with_timeout_or_interrupt(tmp_path, monkeypatch, interrupted):
    result = installed.ProcessResult(-9, timed_out=not interrupted, interrupted=interrupted,
                                     error='cleanup unconfirmed: UNKNOWN; fixture.cleanup.json')
    monkeypatch.setattr(installed, 'run_process', lambda *args: result)
    with pytest.raises(KeyboardInterrupt if interrupted else RuntimeError, match='UNKNOWN'):
        installed.setup_command(['synthetic'], {}, tmp_path / 'setup.log')


@pytest.fixture
def profile_script(tmp_path, monkeypatch):
    root = tmp_path / '路径 with spaces'
    output = root / 'evidence/frontend_completion'
    output.mkdir(parents=True)
    scene_path = output / 'profile-scene.json'
    scene_path.write_text('{}', encoding='utf-8')
    addon = ModuleType('wfrl_blender')
    addon.__path__ = []
    addon.register, addon.unregister = Mock(), Mock()
    atomic = runpy.run_path(str(ROOT / 'blender_frontend/wfrl_blender/atomic_json.py'))
    addon.atomic_json = SimpleNamespace(write_json=atomic['write_json'])
    live = ModuleType('wfrl_blender.live_scene')
    live.build_live_scene = Mock()
    model = ModuleType('wfrl_blender.scene_model')
    model.SceneDTO = SimpleNamespace(from_mapping=lambda value: value)
    for name, module in {'wfrl_blender': addon, 'wfrl_blender.live_scene': live,
                         'wfrl_blender.scene_model': model,
                         'bpy': SimpleNamespace(app=SimpleNamespace(background=True), data=SimpleNamespace(objects=[]))}.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys, 'path', sys.path.copy())

    def execute():
        path = ROOT / 'scripts/blender/profile_frontend_startup.py'
        exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'),
             {'__file__': str(root / 'scripts/blender/profile_frontend_startup.py'), '__name__': '__main__'})

    return SimpleNamespace(output=output, scene_path=scene_path, addon=addon, live=live, execute=execute)


@pytest.mark.parametrize('stage', ['input', 'register', 'build', 'write'])
def test_profile_failure_releases_registration_without_completion(profile_script, monkeypatch, capsys, stage):
    fixture = profile_script
    if stage == 'input':
        fixture.scene_path.write_text('invalid JSON', encoding='utf-8')
    elif stage == 'register':
        fixture.addon.register.side_effect = RuntimeError('registration failed')
    elif stage == 'build':
        fixture.live.build_live_scene.side_effect = RuntimeError('build failed')
    else:
        monkeypatch.setattr(os, 'fsync', Mock(side_effect=OSError('disk sync failed')))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        fixture.execute()
    assert fixture.addon.unregister.call_count == fixture.addon.register.call_count
    assert 'STARTUP_PROFILE' not in capsys.readouterr().out
    assert not (fixture.output / 'startup-profile.json').exists()


def test_profile_unregister_failure_cannot_publish_completion(profile_script, capsys):
    fixture = profile_script
    fixture.addon.unregister.side_effect = RuntimeError('resources still owned')
    with pytest.raises(RuntimeError, match='resources still owned'):
        fixture.execute()
    assert 'STARTUP_PROFILE' not in capsys.readouterr().out
    assert not (fixture.output / 'startup-profile.json').exists()


def test_profile_success_preserves_report_schema_and_completion(profile_script, capsys):
    fixture = profile_script
    fixture.execute()
    report = json.loads((fixture.output / 'startup-profile.json').read_text(encoding='utf-8'))
    assert set(report) == {'background', 'total_seconds', 'objects', 'scope', 'functions'}
    assert report['background'] is True and report['total_seconds'] >= 0 and report['objects'] == 0
    fixture.addon.register.assert_called_once_with()
    fixture.addon.unregister.assert_called_once_with()
    assert capsys.readouterr().out.startswith('STARTUP_PROFILE ')
    assert not list(fixture.output.glob('.json-*.tmp'))


def test_profile_atomic_write_preserves_previous_report(profile_script, monkeypatch, capsys):
    fixture = profile_script
    destination = fixture.output / 'startup-profile.json'
    original = '{"previous": "complete"}\n'
    destination.write_text(original, encoding='utf-8')
    monkeypatch.setattr(os, 'fsync', Mock(side_effect=OSError('disk sync failed')))
    with pytest.raises(OSError, match='disk sync failed'):
        fixture.execute()
    assert destination.read_text(encoding='utf-8') == original
    assert not list(fixture.output.glob('.json-*.tmp'))
    assert 'STARTUP_PROFILE' not in capsys.readouterr().out


@pytest.mark.parametrize('name', ['run_regressions', 'run_installed_windows', 'profile_frontend_startup'])
def test_touched_scripts_keep_explicit_text_encodings(name):
    from blender_frontend.tests.test_text_encoding_guard import _missing_encodings

    path = ROOT / 'scripts/blender' / (name + '.py')
    assert not _missing_encodings(path.read_text(encoding='utf-8'))
