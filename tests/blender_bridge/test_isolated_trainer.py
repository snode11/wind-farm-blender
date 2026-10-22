import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest


def test_launcher_environment_removes_job_identity_but_preserves_transport(monkeypatch):
    from wfrl.blender_bridge import isolated_trainer

    inherited = {
        'OMPI_COMM_WORLD_RANK': '0',
        'OMPI_COMM_WORLD_SIZE': '1',
        'OMPI_MCA_num_procs': '1',
        'OMPI_MCA_initial_wdir': '/old/job',
        'OMPI_MCA_btl_tcp_sndbuf': '65536',
        'OMPI_MCA_btlextra': 'not-a-btl-option',
        'OMPI_MCA_pmlextra': 'not-a-pml-option',
        'PMI_RANK': '0',
        'PMIX_NAMESPACE': 'old-job',
        'PMIX_SERVER_URI41': 'old-server',
        'OPAL_USER_PARAMS_GIVEN': '1',
        'PRTE_LAUNCHED': '1',
        'PRTE_SHARED_FS': 'FALSE',
        'OMPI_MCA_btl': 'self,tcp',
        'OMPI_MCA_btl_tcp_if_include': 'lo0',
        'OMPI_MCA_pml': 'ob1',
        'OMPI_MCA_pml_ob1_verbose': '1',
        'OMP_NUM_THREADS': '2',
        'WFCRL_MPIEXEC': '/custom/mpiexec',
        'PATH': '/custom/bin',
    }
    original = inherited.copy()
    monkeypatch.setattr(isolated_trainer.os, 'environ', inherited)
    monkeypatch.setattr(isolated_trainer.sys, 'platform', 'linux')

    actual = isolated_trainer.launcher_environment()

    assert actual == {key: original[key] for key in (
        'OMPI_MCA_btl', 'OMPI_MCA_btl_tcp_if_include',
        'OMPI_MCA_btl_tcp_sndbuf', 'OMPI_MCA_pml',
        'OMPI_MCA_pml_ob1_verbose', 'OMP_NUM_THREADS', 'WFCRL_MPIEXEC', 'PATH',
    )}
    assert inherited == original
    assert actual is not inherited


@pytest.mark.parametrize('platform', ['darwin', 'linux', 'win32'])
def test_launcher_environment_uses_loopback_defaults_only_on_macos(monkeypatch, platform):
    from wfrl.blender_bridge import isolated_trainer

    inherited = {'OMP_NUM_THREADS': '2'}
    monkeypatch.setattr(isolated_trainer.os, 'environ', inherited)
    monkeypatch.setattr(isolated_trainer.sys, 'platform', platform)

    actual = isolated_trainer.launcher_environment()

    expected = {'OMP_NUM_THREADS': '2'}
    if platform == 'darwin':
        expected.update(OMPI_MCA_btl='self,tcp', OMPI_MCA_btl_tcp_if_include='lo0')
    assert actual == expected
    assert inherited == {'OMP_NUM_THREADS': '2'}


@pytest.mark.parametrize('transport', [
    {'OMPI_MCA_btl': 'self,sm'},
    {'OMPI_MCA_btl_tcp_if_include': 'en0'},
    {'OMPI_MCA_btl_tcp_if_exclude': 'lo0'},
    {'OMPI_MCA_btl_tcp_if_include': 'en0', 'OMPI_MCA_btl_tcp_if_exclude': 'utun0'},
])
def test_launcher_environment_respects_macos_transport_overrides(monkeypatch, transport):
    from wfrl.blender_bridge import isolated_trainer

    original = transport.copy()
    monkeypatch.setattr(isolated_trainer.os, 'environ', transport)
    monkeypatch.setattr(isolated_trainer.sys, 'platform', 'darwin')

    actual = isolated_trainer.launcher_environment()

    expected = original.copy()
    expected.setdefault('OMPI_MCA_btl', 'self,tcp')
    if not any(key in original for key in (
        'OMPI_MCA_btl_tcp_if_include', 'OMPI_MCA_btl_tcp_if_exclude',
    )):
        expected['OMPI_MCA_btl_tcp_if_include'] = 'lo0'
    assert actual == expected
    assert transport == original


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_cancel_owns_worker_group_and_allows_restart(tmp_path):
    spec = importlib.util.find_spec('wfrl.blender_bridge.isolated_trainer')
    assert spec is not None, 'Interactive MPI training needs an owned cancellable worker'
    from wfrl.blender_bridge.isolated_trainer import IsolatedTrainer
    script = tmp_path / 'worker.py'
    script.write_text('''import json, subprocess, sys, socket
config = json.load(open(sys.argv[1]))
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(config['socket'])
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
def emit(payload):
    # Native simulator output must never be parsed as application messages.
    print('Time: 123 of 456 ... noisy simulator output', flush=True)
    connection.sendall((json.dumps({'kind':'snapshot','payload':payload,'events':[], 'channels':[]})+'\\n').encode())
emit({'step':1, 'child_pid':child.pid})
for line in connection.makefile('r'):
    command = json.loads(line)
    if command['command'] == 'continue':
        emit({'step':2})
    # Deliberately never drain. Cancellation must clean all owned descendants.
''')
    class FixtureTrainer(IsolatedTrainer):
        def _worker_command(self):
            return [sys.executable, '-u', str(script), str(Path(self._directory.name) / 'config.json')]
    received = []
    from wfrl.scene.schema import load_scene
    trainer = FixtureTrainer(load_scene('scenes/turb3_ctrl3.yaml'), on_snapshot=lambda s: received.append(s.payload))
    unrelated = __import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        trainer.start()
        wait_for(lambda: len(received) >= 2)
        started = time.monotonic()
        trainer.stop(timeout=.1)
        assert time.monotonic() - started < 8
        assert not trainer.running
        assert trainer.error is None
        import psutil
        assert not psutil.pid_exists(received[0]["child_pid"]), "MPI children may create their own process groups"
        assert unrelated.poll() is None
        trainer.start()
        wait_for(lambda: len(received) >= 4)
        trainer.stop(timeout=.1)
        assert not trainer.running
    finally:
        try:
            trainer.stop(timeout=0)
        finally:
            import psutil
            for sample in received:
                if sample.get('child_pid'):
                    try: psutil.Process(sample['child_pid']).kill()
                    except psutil.NoSuchProcess: pass
            unrelated.terminate(); unrelated.wait(timeout=5)
