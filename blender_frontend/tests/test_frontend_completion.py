"""The acceptance supervisor preserves the selected Python environment launcher."""
from contextlib import nullcontext
import json
from pathlib import Path
import runpy
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[2] / 'scripts/blender/verify_frontend_completion.py'


@pytest.mark.parametrize('explicit_python', [False, True])
def test_supervisor_keeps_python_symlink_for_virtual_environment(tmp_path, monkeypatch, explicit_python):
    environment = tmp_path / 'venv/bin'
    environment.mkdir(parents=True)
    python = environment / 'python'
    python.symlink_to(Path(sys.executable).resolve())
    monkeypatch.setattr(sys, 'executable', str(python))
    selected = python
    if explicit_python:
        selected = environment / 'backend-python'
        selected.symlink_to(python)
        monkeypatch.setenv('WFRL_PYTHON', str(selected))
    else:
        monkeypatch.delenv('WFRL_PYTHON', raising=False)

    tool = tmp_path / 'inert-tool'
    tool.write_text('#!/bin/sh\nexit 99\n')
    tool.chmod(0o755)
    for name in ('WFCRL_FASTFARM_EXECUTABLE', 'WFCRL_MPIEXEC', 'WFRL_BLENDER'):
        monkeypatch.setenv(name, str(tool))
    output = tmp_path / 'acceptance'
    output.mkdir()
    (output / 'report.json').write_text(json.dumps({'passed': True}))
    monkeypatch.setenv('WFRL_ACCEPT_OUTPUT', str(output))
    launches = []

    class InertProcess:
        def __init__(self, backend):
            self.returncode = None if backend else 0

        def poll(self):
            return self.returncode

        def send_signal(self, signal):
            self.returncode = 0

        def wait(self, timeout=None):
            self.returncode = 0
            return self.returncode

    def capture_launch(argv, **kwargs):
        launches.append((argv, kwargs))
        return InertProcess(backend=len(launches) == 1)

    monkeypatch.setattr('subprocess.Popen', capture_launch)
    monkeypatch.setattr('socket.create_connection', lambda *args: nullcontext())
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(SOURCE))
    assert stopped.value.code == 0
    assert len(launches) == 2
    assert launches[0][0][0] == str(selected.absolute())
    assert launches[0][0][1:3] == ['-m', 'wfrl.blender_bridge']
    assert selected.is_symlink()
    assert str(selected.absolute()) != str(selected.resolve())
    supervision = json.loads((output / 'supervision.json').read_text())
    assert supervision['owned_launchers_exited']
