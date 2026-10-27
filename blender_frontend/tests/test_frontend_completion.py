"""Acceptance evidence is isolated per invocation and uses the selected Python."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import venv

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'scripts/blender/verify_frontend_completion.py'


def executable(path, body):
    path.write_text(f'#!{sys.executable}\n{body}\n')
    path.chmod(0o755)
    return path


@pytest.fixture
def acceptance_processes(tmp_path):
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', 8881))
        except OSError:
            pytest.skip('acceptance port 8881 is in use')
    backend = executable(tmp_path / 'fixture-backend', '''
import signal
import socket
import sys

def stop(*args):
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
port = int(sys.argv[sys.argv.index('--port') + 1])
with socket.socket() as listener:
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(('127.0.0.1', port))
    listener.listen()
    while True:
        client, address = listener.accept()
        client.close()
''')
    frontend = executable(tmp_path / 'fixture-blender', '''
import json
import os
from pathlib import Path
import sys

output = Path(os.environ['WFRL_ACCEPT_OUTPUT'])
if not output.is_absolute():
    raise SystemExit('acceptance output must be absolute')
output.mkdir(parents=True, exist_ok=True)
(output / 'invocation.json').write_text(json.dumps({'output': str(output), 'argv': sys.argv}))
if 'FIXTURE_FRONTEND_REPORT' in os.environ:
    (output / 'report.json').write_text(os.environ['FIXTURE_FRONTEND_REPORT'])
''')
    inert = executable(tmp_path / 'inert-tool', 'raise SystemExit(99)')

    def run(output, *, report=None, python=sys.executable, backend_python=backend, extra_env=None):
        env = os.environ.copy()
        env.update(WFCRL_FASTFARM_EXECUTABLE=str(inert), WFCRL_MPIEXEC=str(inert),
                   WFRL_BLENDER=str(frontend), WFRL_ACCEPT_OUTPUT=str(output),
                   PYTHONDONTWRITEBYTECODE='1')
        if backend_python is None:
            env.pop('WFRL_PYTHON', None)
        else:
            env['WFRL_PYTHON'] = str(backend_python)
        env.pop('FIXTURE_FRONTEND_REPORT', None)
        if report is not None:
            env['FIXTURE_FRONTEND_REPORT'] = json.dumps(report)
        env.update(extra_env or {})
        return subprocess.run([str(python), str(SOURCE)], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=30)

    return run


def run_outputs(output):
    return sorted(path for path in output.iterdir() if path.is_dir())


def test_missing_current_report_cannot_reuse_previous_pass(tmp_path, acceptance_processes):
    output = tmp_path / 'acceptance'
    output.mkdir()
    previous_report = json.dumps({'passed': True, 'evidence': 'preserve'})
    (output / 'report.json').write_text(previous_report)
    result = acceptance_processes(output)
    assert result.returncode == 1, result.stdout + result.stderr
    assert (output / 'report.json').read_text() == previous_report
    current, = run_outputs(output)
    assert not (current / 'report.json').exists()
    supervision = json.loads((current / 'supervision.json').read_text())
    assert supervision['frontend_exit'] == supervision['bridge_exit'] == 0
    assert supervision['owned_launchers_exited']


def test_each_acceptance_uses_new_output_and_preserves_existing_evidence(tmp_path, acceptance_processes):
    output = tmp_path / 'acceptance'
    output.mkdir()
    old_evidence = {'report.json': '{"passed": true}', 'backend.log': 'backend evidence',
                    'blender.log': 'frontend evidence', 'supervision.json': 'supervision evidence'}
    for name, contents in old_evidence.items():
        (output / name).write_text(contents)
    for _ in range(2):
        result = acceptance_processes(output, report={'passed': True})
        assert result.returncode == 0, result.stdout + result.stderr
    for name, contents in old_evidence.items():
        assert (output / name).read_text() == contents
    runs = run_outputs(output)
    assert len(runs) == 2
    for current in runs:
        assert json.loads((current / 'report.json').read_text())['passed'] is True
        invocation = json.loads((current / 'invocation.json').read_text())
        assert Path(invocation['output']) == current
        assert invocation['argv'][-1] == str(ROOT / 'scripts/blender/accept_frontend.py')
        assert json.loads((current / 'supervision.json').read_text())['owned_launchers_exited']


def test_relative_acceptance_output_reaches_the_current_run(tmp_path, acceptance_processes):
    result = acceptance_processes(Path('acceptance'), report={'passed': True})
    assert result.returncode == 0, result.stdout + result.stderr
    current, = run_outputs(tmp_path / 'acceptance')
    invocation = json.loads((current / 'invocation.json').read_text())
    assert Path(invocation['output']) == current.absolute()


@pytest.mark.parametrize('explicit_python', [False, True])
def test_supervisor_executes_backend_in_selected_virtual_environment(tmp_path, acceptance_processes, explicit_python):
    environment = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=True, symlinks=True).create(environment)
    python = environment / 'bin/python'
    selected = python
    if explicit_python:
        selected = environment / 'bin/backend-python'
        selected.symlink_to(python)
    identities = tmp_path / 'identities'
    identities.mkdir()
    (identities / 'sitecustomize.py').write_text('''
import json
import os
from pathlib import Path
import sys

record = dict(prefix=sys.prefix, executable=sys.executable, argv=sys.orig_argv)
Path(os.environ['FIXTURE_IDENTITY_DIR'], str(os.getpid()) + '.json').write_text(json.dumps(record))
''')
    result = acceptance_processes(tmp_path / 'acceptance', report={'passed': True}, python=python,
        backend_python=selected if explicit_python else None,
        extra_env={'PYTHONPATH': os.pathsep.join([str(identities), str(ROOT)]),
                   'FIXTURE_IDENTITY_DIR': str(identities)})
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(path.read_text()) for path in identities.glob('*.json')]
    backend, = [record for record in records if record['argv'][1:3] == ['-m', 'wfrl.blender_bridge']]
    assert Path(backend['prefix']) == environment
    assert backend['executable'] == str(selected.absolute())
