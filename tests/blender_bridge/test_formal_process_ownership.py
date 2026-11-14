"""Formal CLI cleanup includes independently grouped and reparented descendants."""
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.formal_process import OwnedProcesses
from wfrl.blender_bridge import formal_process


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.01)


@pytest.mark.parametrize('windows', [False, True])
@pytest.mark.parametrize('unreadable', ['create_time', 'user', 'environ', 'foreign_user'])
def test_unrelated_unreadable_process_does_not_block_cleanup(monkeypatch, windows, unreadable):
    monkeypatch.setattr(formal_process, 'os', SimpleNamespace() if windows else SimpleNamespace(getuid=lambda: 501))
    owned = OwnedProcesses()
    owned.process = SimpleNamespace(poll=lambda: 0)
    process = Mock(pid=12345)
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    user_key = 'username' if windows else 'uids'
    user = 'user' if windows else SimpleNamespace(real=501)
    if unreadable == 'foreign_user':
        user = 'SYSTEM' if windows else SimpleNamespace(real=0)
    process.info = dict(pid=process.pid, create_time=None if unreadable == 'create_time' else time.time(),
                       **{user_key: None if unreadable == 'user' else user})
    process.environ.side_effect = psutil.AccessDenied(process.pid)
    monkeypatch.setattr(psutil, 'process_iter', lambda attrs: iter([process]))
    monkeypatch.setattr(psutil, 'Process', lambda: SimpleNamespace(username=lambda: 'user'))
    assert owned.status() == 'EMPTY_CONFIRMED'
    assert not owned.alive()
    assert owned.identities() == []
    if unreadable == 'foreign_user':
        process.environ.assert_not_called()


def test_recovery_keeps_unreadable_recorded_identity_until_it_can_be_checked(monkeypatch):
    identity = dict(pid=12345, create_time=time.time())
    owned = OwnedProcesses.recover([identity], token='run', env_key='WFRL_PROCESS_RUN_TOKEN')
    state = ['denied']
    def process(pid=None):
        if pid is None:
            return SimpleNamespace(username=lambda: 'user')
        if state[0] == 'denied':
            raise psutil.AccessDenied(pid)
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, 'Process', process)
    monkeypatch.setattr(psutil, 'process_iter', lambda attrs: iter([]))
    assert owned.status() == 'UNKNOWN'
    assert owned.alive() and not owned._cleaned
    assert owned.identities() == [identity]
    state[0] = 'gone'
    assert owned.status() == 'EMPTY_CONFIRMED'
    assert not owned.alive()


@pytest.mark.parametrize('missing_identity', [False, True])
def test_windows_recovery_discovers_same_user_token_orphan(monkeypatch, missing_identity):
    monkeypatch.setattr(formal_process, 'os', SimpleNamespace())
    root = dict(pid=12345, create_time=time.time() - 2)
    owned = OwnedProcesses.recover([root], token='run', env_key='WFRL_PROCESS_RUN_TOKEN')
    child = Mock(pid=12346)
    child.create_time.return_value = time.time()
    child.info = dict(pid=child.pid, create_time=None if missing_identity else child.create_time(),
                      username=None if missing_identity else 'user')
    child.is_running.return_value = True
    child.status.return_value = psutil.STATUS_RUNNING
    child.environ.return_value = {owned.ENV_KEY: owned.token}
    def process(pid=None):
        if pid is None:
            return SimpleNamespace(username=lambda: 'user')
        raise psutil.NoSuchProcess(pid)
    monkeypatch.setattr(psutil, 'Process', process)
    def processes(attrs):
        assert 'username' in attrs and 'uids' not in attrs
        return iter([child])
    monkeypatch.setattr(psutil, 'process_iter', processes)
    assert owned.status() == 'LIVE'
    assert dict(pid=child.pid, create_time=child.create_time()) in owned.identities()


def test_observed_orphan_remains_owned_when_environment_is_unreadable(tmp_path, monkeypatch):
    marker = tmp_path/'orphan.pid'
    release = tmp_path/'parent.exit'
    child_code = (f'from pathlib import Path; import os,time; '
                  f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)')
    parent_code = (f'import subprocess,sys,time; from pathlib import Path\n'
                   f'subprocess.Popen([sys.executable,"-c",{child_code!r}], start_new_session=True)\n'
                   f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n')
    owned = OwnedProcesses(env_key='WFRL_LAUNCHER_RUN_TOKEN')
    parent = owned.start([sys.executable,'-c',parent_code], cwd=tmp_path, env=os.environ)
    child = None
    try:
        wait_for(lambda: marker.exists() and marker.read_text())
        child = psutil.Process(int(marker.read_text()))
        assert owned.status() == 'LIVE'
        assert child.pid in [row['pid'] for row in owned.identities()]
        release.touch()
        parent.wait(5)
        original, denied = psutil.Process.environ, [True]
        def environ(process):
            if denied[0] and process.pid == child.pid:
                raise psutil.AccessDenied(process.pid)
            return original(process)
        monkeypatch.setattr(psutil.Process,'environ',environ)
        assert owned.status() == 'LIVE' and not owned._cleaned
        denied[0] = False
        assert owned.status() == 'LIVE'
        identity = dict(pid=child.pid, create_time=child.create_time())
        assert owned.owns(identity)
        assert not owned.owns(dict(identity, create_time=identity['create_time']+1))
        child.terminate()
        wait_for(lambda: owned.status() == 'EMPTY_CONFIRMED')
    finally:
        if child is not None and owned._exists(child):
            child.kill()
        if parent.poll() is None:
            parent.kill()
            parent.wait(5)


@pytest.mark.skipif(os.name != 'posix', reason='Formal training uses POSIX supervision')
@pytest.mark.parametrize('observe_parent', [False, True])
def test_detached_child_blocks_cleanup_after_formal_launcher_exits(tmp_path, monkeypatch, observe_parent):
    marker, release = tmp_path / 'child.pid', tmp_path / 'release'
    child_code = (f'from pathlib import Path; import os,time; '
                  f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)')
    parent_code = (f'import subprocess,sys,time; from pathlib import Path\n'
                   f'subprocess.Popen([sys.executable,"-c",{child_code!r}], start_new_session=True)\n'
                   f'while not Path({str(release)!r}).exists(): time.sleep(.01)\n')
    scene = SimpleNamespace(backend='fastfarm', to_dict=lambda: {})
    session = BackendSession(scene, scene_loader=lambda _: scene, kill_grace_seconds=.1)
    monkeypatch.setattr(session, '_formal_command',
                        lambda *_: (str(Path.cwd()), [sys.executable, '-c', parent_code]))
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(60)'])
    child = None
    try:
        session.start('formal_training', dict(scene=str(tmp_path / 'scene.yaml')))
        wait_for(lambda: marker.exists() and marker.read_text())
        child = psutil.Process(int(marker.read_text()))
        if observe_parent:
            assert session.synchronization_state()['worker_alive']
        release.touch()
        session._process.wait(3)
        state = session.synchronization_state()
        assert state['worker_alive'] is True
        assert state['cleanup_confirmed'] is False
        session.poll()
        assert session.status == 'FAILED'
        wait_for(lambda: not session.alive())
        wait_for(lambda: session.synchronization_state()['cleanup_confirmed'])
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
        assert unrelated.poll() is None
    finally:
        release.touch()
        session.stop(0)
        if session._stop_worker:
            session._stop_worker.join(2)
        if child is not None and child.is_running():
            child.kill()
        if session._process and session._process.poll() is None:
            session._process.kill()
            session._process.wait(2)
        unrelated.terminate()
        unrelated.wait(2)


def _windows_modules(monkeypatch, popen=None):
    monkeypatch.setattr(formal_process, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(formal_process, 'signals', SimpleNamespace(SIGINT=2, SIGTERM=15, CTRL_BREAK_EVENT=1))
    monkeypatch.setattr(formal_process, 'subprocess', SimpleNamespace(Popen=popen, CREATE_NEW_PROCESS_GROUP=0x200))


def test_windows_start_creates_process_group_for_ctrl_break(monkeypatch):
    calls = []
    def popen(argv, **options):
        calls.append(options)
        return SimpleNamespace(pid=-1)
    _windows_modules(monkeypatch, popen)
    monkeypatch.setattr(psutil, 'Process', Mock(side_effect=psutil.NoSuchProcess(-1)))
    OwnedProcesses().start(['mpiexec'], cwd='.', env={}, creationflags=0x8)
    assert calls[0]['creationflags'] == 0x208


def test_windows_graceful_stop_sends_ctrl_break_to_root_only(monkeypatch):
    _windows_modules(monkeypatch)
    owned = OwnedProcesses()
    owned._remember = lambda force=False: None
    owned.process = Mock(poll=Mock(return_value=None))
    child = Mock()
    owned._owned = {child}
    owned.signal(2)
    owned.process.send_signal.assert_called_once_with(1)
    child.send_signal.assert_not_called()


def test_windows_forced_stop_terminates_every_owned_process(monkeypatch):
    _windows_modules(monkeypatch)
    owned = OwnedProcesses()
    owned._remember = lambda force=False: None
    gone, alive = Mock(), Mock()
    gone.send_signal.side_effect = psutil.NoSuchProcess(1)
    owned._owned = {gone, alive}
    owned.signal(9)
    alive.send_signal.assert_called_once_with(15)
    gone.send_signal.assert_called_once_with(15)
