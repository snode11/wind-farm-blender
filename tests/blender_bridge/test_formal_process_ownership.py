"""Formal CLI cleanup includes independently grouped and reparented descendants."""
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from wfrl.blender_bridge.backend_session import BackendSession


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.01)


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
