"""Host coverage of Windows paths, process cleanup, and subprocess decoding."""
from argparse import Namespace
from contextlib import nullcontext
import hashlib
from itertools import chain, repeat
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from zipfile import ZipFile

import psutil
import pytest

from scripts.blender import validate_frontend_package as validator
from scripts.blender import verify_frontend_completion as completion
from scripts.blender import wfrl_launcher as launcher
from wfrl.blender_bridge import formal_process


class FakePopen:
    def __init__(self, pid, *, returncode=None, stop_code=1, timeouts=0):
        self.pid = pid
        self.returncode = returncode
        self.stop_code = stop_code
        self.timeouts = timeouts
        self.calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.calls.append(('wait', timeout))
        if self.timeouts:
            self.timeouts -= 1
            raise subprocess.TimeoutExpired('fixture', timeout)
        self.returncode = self.stop_code
        return self.returncode

    def send_signal(self, signum):
        self.calls.append(('send_signal', signum))

    def terminate(self):
        self.calls.append(('terminate',))

    def kill(self):
        self.calls.append(('kill',))


@pytest.fixture
def run_acceptance(tmp_path, monkeypatch):
    def run(platform, bridge, cleanup_states=(), *, front=None, report=True):
        windows_group = 0x00000200
        if front is None:
            front = FakePopen(2, returncode=0)
        children = iter((bridge, front))
        now = 0

        def sleep(seconds):
            nonlocal now
            now += seconds

        def spawn(command, *, cwd, env, stdout, stderr, **options):
            expected = {'creationflags': windows_group} if platform == 'nt' else {'start_new_session': True}
            assert options == expected
            process = next(children)
            if process is front and report:
                (Path(env['WFRL_ACCEPT_OUTPUT']) / 'report.json').write_text('{"passed": true}', encoding='utf-8')
            return process

        def killpg(pid, signum):
            assert pid == bridge.pid
            bridge.calls.append(('killpg', signum))

        platform_os = SimpleNamespace(name=platform, environ=os.environ)
        platform_signal = SimpleNamespace(SIGTERM=signal.SIGTERM)
        if platform == 'posix':
            platform_os.killpg = killpg
            platform_signal.SIGKILL = 9
        monkeypatch.setattr(completion, 'os', platform_os)
        monkeypatch.setattr(completion, 'signal', platform_signal)
        monkeypatch.setattr(completion, 'subprocess', SimpleNamespace(
            Popen=spawn, STDOUT=subprocess.STDOUT, CREATE_NEW_PROCESS_GROUP=windows_group,
            TimeoutExpired=subprocess.TimeoutExpired))
        monkeypatch.setattr(completion, 'socket', SimpleNamespace(create_connection=lambda *args: nullcontext()))
        monkeypatch.setattr(completion, 'time', SimpleNamespace(monotonic=lambda: now, sleep=sleep))
        monkeypatch.setattr(completion, 'executable', lambda *args: sys.executable)
        monkeypatch.setenv('WFRL_ACCEPT_OUTPUT', str(tmp_path))
        owner = Mock()
        owner.status.side_effect = iter(cleanup_states)
        recover = Mock(return_value=owner)
        monkeypatch.setattr(formal_process, 'OwnedProcesses', SimpleNamespace(recover=recover))
        monkeypatch.setattr(psutil, 'Process', lambda pid: SimpleNamespace(create_time=lambda: 42.0))

        code = completion.main()
        output, = tmp_path.glob('run-*')
        if platform == 'nt':
            recover.assert_called_once_with([{'pid': bridge.pid, 'create_time': 42.0}],
                                            token=str(output), env_key='WFRL_ACCEPT_OUTPUT')
        else:
            recover.assert_not_called()
        supervision = json.loads((output / 'supervision.json').read_text(encoding='utf-8'))
        return code, supervision, owner
    return run


@pytest.mark.parametrize('timeouts', [0, 1], ids=['terminate', 'kill-fallback'])
def test_windows_acceptance_uses_cleanup_instead_of_bridge_exit_code(run_acceptance, timeouts):
    bridge = FakePopen(1, timeouts=timeouts)
    code, supervision, owner = run_acceptance('nt', bridge, ['LIVE', 'EMPTY_CONFIRMED'])
    expected = [('terminate',), ('wait', 10)]
    if timeouts:
        expected += [('kill',), ('wait', 10)]
    assert bridge.calls == expected
    assert code == 0
    assert supervision['bridge_exit'] == 1
    assert supervision['owned_launchers_exited']
    assert supervision['owned_process_cleanup'] == 'EMPTY_CONFIRMED'
    owner.signal.assert_not_called()


def test_windows_acceptance_cleans_owned_descendants(run_acceptance):
    code, supervision, owner = run_acceptance('nt', FakePopen(1), ['LIVE', 'LIVE', 'EMPTY_CONFIRMED'])
    owner.signal.assert_called_once_with(signal.SIGTERM)
    assert supervision['owned_process_cleanup'] == 'EMPTY_CONFIRMED'
    assert code == 0


@pytest.mark.parametrize('condition', ['launcher-live', 'descendant-live', 'ownership-unknown'])
def test_windows_acceptance_rejects_unconfirmed_cleanup(run_acceptance, condition):
    timeouts = 2 if condition == 'launcher-live' else 0
    status = {'launcher-live': 'EMPTY_CONFIRMED', 'descendant-live': 'LIVE',
              'ownership-unknown': 'UNKNOWN'}[condition]
    bridge = FakePopen(1, timeouts=timeouts)
    code, supervision, _ = run_acceptance('nt', bridge, chain(['LIVE'], repeat(status)))
    assert supervision['owned_launchers_exited'] is (not timeouts)
    assert supervision['owned_process_cleanup'] == status
    assert code == 1


@pytest.mark.parametrize(('frontend_exit', 'report'), [(1, True), (0, False)])
def test_windows_acceptance_still_requires_frontend_success(run_acceptance, frontend_exit, report):
    front = FakePopen(2, returncode=frontend_exit)
    code, _, _ = run_acceptance('nt', FakePopen(1), ['LIVE', 'EMPTY_CONFIRMED'], front=front, report=report)
    assert code == 1


def test_windows_acceptance_stops_timed_out_frontend_and_bridge(run_acceptance):
    front = FakePopen(2, stop_code=0, timeouts=1)
    bridge = FakePopen(1)
    code, supervision, _ = run_acceptance('nt', bridge, ['LIVE', 'EMPTY_CONFIRMED'], front=front)
    assert front.calls == [('terminate',), ('wait', 10), ('kill',), ('wait', 10)]
    assert bridge.calls == [('terminate',), ('wait', 10)]
    assert supervision['owned_launchers_exited']
    assert supervision['frontend_exit'] is None
    assert code == 1


@pytest.mark.parametrize('bridge_exit', [0, 1])
def test_posix_acceptance_still_requires_zero_bridge_exit(run_acceptance, bridge_exit):
    bridge = FakePopen(1, stop_code=bridge_exit)
    code, supervision, _ = run_acceptance('posix', bridge)
    assert bridge.calls == [('send_signal', signal.SIGTERM), ('wait', 25)]
    assert code == bridge_exit
    assert supervision['bridge_exit'] == bridge_exit


def test_posix_bridge_timeout_keeps_group_cleanup_and_failed_verdict(run_acceptance):
    bridge = FakePopen(1, stop_code=0, timeouts=2)
    code, supervision, _ = run_acceptance('posix', bridge)
    assert bridge.calls == [('send_signal', signal.SIGTERM), ('wait', 25), ('killpg', signal.SIGTERM),
                            ('wait', 10), ('killpg', 9), ('wait', None)]
    assert supervision['bridge_cleanup_timeout']
    assert supervision['owned_launchers_exited']
    assert supervision['bridge_exit'] is None
    assert code == 1


@pytest.mark.parametrize('name', [
    'C:/entry.py', 'c:entry.py', 'assets/file.', 'assets/file ', 'assets./file', 'assets /file',
    'assets/./file', 'a<b', 'a>b', 'a:b', 'a"b', 'a|b', 'a?b', 'a*b',
    '', '/absolute', 'assets/../escape', 'assets\\entry',
])
def test_inventory_rejects_unsafe_paths_on_any_host(name):
    with pytest.raises(ValueError, match='Unsafe inventory path'):
        validator.safe_relative(name)


@pytest.mark.parametrize('stem', [
    'CON', 'prn', 'Aux', 'NuL',
    *(f'{prefix}{number}' for prefix in ('CoM', 'lPt') for number in range(1, 10)),
])
def test_inventory_rejects_device_names_with_or_without_extension(stem):
    for name in (stem, f'assets/{stem}.py', f'{stem}.data/entry.py'):
        with pytest.raises(ValueError, match='Unsafe inventory path'):
            validator.safe_relative(name)


@pytest.mark.parametrize('name', ['assets/场景.json', 'LPT0/com10.json', 'auxiliary/nul_backup.py'])
def test_inventory_keeps_valid_relative_names(name):
    assert validator.safe_relative(name).as_posix() == name


@pytest.mark.parametrize(('first', 'second'), [
    ('Manifest.json', 'manifest.json'), ('assets/Straße.json', 'assets/STRASSE.json'),
])
def test_archive_rejects_casefolded_duplicate_names(tmp_path, first, second):
    archive = tmp_path / 'fixture.zip'
    payload = b'fixture'
    with ZipFile(archive, 'w') as zipped:
        for name in (first, second):
            zipped.writestr(name, payload)
    data = {'archive_sha256': validator.sha256(archive), 'files': [
        {'path': name, 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        for name in (first, second)]}
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate inventory path'):
        validator.verify_archive(archive, inventory)


def test_launcher_expands_home_and_preserves_command_lookup(tmp_path, monkeypatch):
    executable = tmp_path / 'backend.exe'
    executable.touch()
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    which = Mock(return_value=str(executable))
    monkeypatch.setattr(launcher.shutil, 'which', which)
    assert launcher.resolve_executable('~/backend.exe', 'Python') == str(executable)
    which.assert_not_called()
    assert launcher.resolve_executable('backend', 'Python') == str(executable)
    which.assert_called_once_with('backend')


BAD_DIAGNOSTIC = '路径：场景'.encode('utf-8') + b'\xff\n'
DECODED_DIAGNOSTIC = '路径：场景\ufffd'


def byte_output(stdout=b'', stderr=b'', exit_code=0):
    return [sys.executable, '-c',
            f'import sys; sys.stdout.buffer.write({stdout!r}); sys.stderr.buffer.write({stderr!r}); '
            f'sys.exit({exit_code})']


def run_utf8(command, **kwargs):
    assert kwargs.get('encoding') == 'utf-8'
    assert kwargs.get('errors') == 'replace'
    return subprocess.run(command, **kwargs)


def test_down_ffprobe_and_ffmpeg_decode_utf8_with_replacement(frontend_module, stub_module, tmp_path, monkeypatch):
    stub_module('bpy_extras.io_utils', ExportHelper=type('ExportHelper', (), {}))
    down = frontend_module('wfrl_blender.manual_down')
    metadata = {'streams': [{'codec_name': 'h264', 'width': 64, 'height': 36, 'avg_frame_rate': '25/1'}],
                'frames': [{'best_effort_timestamp_time': '0.0'}]}
    outputs = {
        'ffprobe': byte_output(json.dumps(metadata).encode('utf-8'), stderr=BAD_DIAGNOSTIC),
        'ffmpeg': byte_output(stderr=BAD_DIAGNOSTIC),
    }
    calls = []

    def run(command, **kwargs):
        completed = run_utf8(outputs[command[0]], **kwargs)
        assert completed.stderr.strip() == DECODED_DIAGNOSTIC
        calls.append(command[0])
        return completed

    monkeypatch.setattr(down, 'subprocess', SimpleNamespace(run=run))
    result = down._verify_video(tmp_path / '视频.mp4', 1, 25, (64, 36), 'ffprobe', 'ffmpeg')
    assert result == {'frame_count': 1, 'fps': 25, 'resolution': [64, 36], 'full_decode': True}
    assert calls == ['ffprobe', 'ffmpeg']


def test_launcher_python_probes_decode_utf8_with_replacement(tmp_path, monkeypatch):
    scene = tmp_path / '场景.yaml'
    scene.write_text('backend: floris\n', encoding='utf-8')
    args = Namespace(fake=False, blender=sys.executable, python=sys.executable,
                     project_dir=str(tmp_path), scene=str(scene), port=8765)
    probes = []

    def run(command, **kwargs):
        if len(command) == 4:
            assert command[-1] == str(scene.resolve())
            child = byte_output(b'"floris"\n', stderr=BAD_DIAGNOSTIC)
            probes.append('scene')
        else:
            assert 'FlorisDemoSession' in command[-1]
            child = byte_output(stderr=BAD_DIAGNOSTIC, exit_code=1)
            probes.append('imports')
        completed = run_utf8(child, **kwargs)
        assert completed.stderr.strip() == DECODED_DIAGNOSTIC
        return completed

    monkeypatch.setattr(launcher, 'subprocess', SimpleNamespace(run=run))
    report = launcher.preflight(args, require_backend=False)
    assert probes == ['scene', 'imports']
    assert report['error'] == f'backend Python cannot import WFRL: {DECODED_DIAGNOSTIC}'


def test_package_validator_decodes_utf8_with_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(validator, 'subprocess', SimpleNamespace(
        run=run_utf8, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired))
    result = validator.run_command(byte_output(stdout=b'PASS\n' + BAD_DIAGNOSTIC),
                                   env=os.environ.copy(), output=tmp_path, name='probe', required_markers=['PASS'])
    assert result['status'] == 'PASS'
    assert Path(result['log']).read_text(encoding='utf-8').splitlines() == ['PASS', DECODED_DIAGNOSTIC]
