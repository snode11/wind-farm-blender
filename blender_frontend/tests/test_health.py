import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psutil

from wfrl_blender import health
from wfrl_blender.health import HealthChecker, HealthConfig

WAIT_TIMEOUT = 60


def collect(checker):
    checker._thread.join(WAIT_TIMEOUT)
    results = checker.poll()
    assert not checker.running, f'Health worker did not exit; results={results!r}'
    return {result.component: result for result in results}


def wait_probe(checker, pid_file):
    deadline = time.monotonic() + WAIT_TIMEOUT
    while not pid_file.exists():
        assert checker.running, f'Probe exited before ready: {checker.poll()!r}'
        assert time.monotonic() < deadline, f'Probe did not publish its PID: {pid_file}'
        threading.Event().wait(.01)
    return int(pid_file.read_text(encoding='ascii'))


def sleeping_probe(python_tool, pid_file):
    return python_tool(pid_file.with_name('probe tool'),
        'import os, threading\nfrom pathlib import Path\n'
        f'path = Path({str(pid_file)!r})\n'
        'temporary = path.with_suffix(".tmp")\n'
        'temporary.write_text(str(os.getpid()), encoding="ascii")\n'
        'temporary.replace(path)\n'
        'threading.Event().wait()\n')


def test_missing_optional_environment_does_not_hide_blender():
    checker = HealthChecker(timeout=30)
    checker.start(HealthConfig(), blender_version=(5, 2, 0))
    results = collect(checker)
    assert results['blender'].status == 'READY'
    assert all(results[key].status == 'MISSING' for key in ('python', 'mpi', 'fastfarm'))


def test_python_version_and_mpi_actual_execution(tmp_path, python_tool):
    mpi = python_tool(tmp_path / 'probe tool',
        'import sys\nassert sys.argv[1:] == ["--version"]\nprint("MPI fixture 1.2")\n')
    checker = HealthChecker(timeout=30)
    checker.start(HealthConfig(backend_python=sys.executable, mpi_path=str(mpi)), blender_version=(5, 2, 0))
    results = collect(checker)
    assert results['python'].status == results['mpi'].status == 'READY'
    assert 'MPI fixture 1.2' in results['mpi'].detail
    assert 'Python 3.' in results['python'].detail


def test_timeout_reaps_real_process(tmp_path, python_tool, monkeypatch):
    pid_file = tmp_path / 'pid'
    tool = sleeping_probe(python_tool, pid_file)
    clock = [0.]
    clock_started = threading.Event()

    def monotonic():
        current = clock[0]
        clock_started.set()
        return current

    monkeypatch.setattr(health, 'time', SimpleNamespace(monotonic=monotonic))
    checker = HealthChecker(timeout=2.0)
    checker.start(HealthConfig(mpi_path=str(tool)))
    try:
        assert clock_started.wait(WAIT_TIMEOUT), 'Probe did not initialize its timeout clock'
        pid = wait_probe(checker, pid_file)
    except BaseException:
        checker.cancel()
        raise
    finally:
        clock[0] = 3.
        results = collect(checker)
    assert results['mpi'].status == 'TIMEOUT'
    assert not psutil.pid_exists(pid), 'probe process leaked'


def test_cancel_returns_immediately_and_reaps_process(tmp_path, python_tool, monkeypatch):
    pid_file = tmp_path / 'pid'
    tool = sleeping_probe(python_tool, pid_file)
    monkeypatch.setattr(health, 'time', SimpleNamespace(monotonic=lambda: 0.))
    checker = HealthChecker(timeout=10)
    checker.start(HealthConfig(mpi_path=str(tool)))
    try:
        pid = wait_probe(checker, pid_file)
        with monkeypatch.context() as patch:
            patch.setattr(checker._thread, 'join', Mock(side_effect=AssertionError('cancel joined the worker')))
            checker.cancel()
            assert checker._cancel.is_set()
    finally:
        checker.cancel()
        results = collect(checker)
    assert results['mpi'].status == 'CANCELLED'
    assert not psutil.pid_exists(pid), 'probe process leaked'


def test_fastfarm_only_checks_executable_and_never_runs(tmp_path, python_tool):
    marker = tmp_path / 'simulation-started'
    tool = python_tool(tmp_path / 'probe tool', f'open({str(marker)!r},"w").write("bad")\n')
    checker = HealthChecker(timeout=30)
    checker.start(HealthConfig(fastfarm_path=str(tool)))
    result = collect(checker)['fastfarm']
    assert result.status == 'CHECKED'
    assert 'version' in result.detail
    assert not marker.exists()


def test_relative_path_is_rejected():
    checker = HealthChecker(timeout=30)
    checker.start(HealthConfig(backend_python='python'))
    assert collect(checker)['python'].status == 'INVALID'


def test_versions_below_manifest_and_backend_requirements_are_unsupported(tmp_path, python_tool):
    python = python_tool(tmp_path / 'probe tool', 'print("Python 3.10.14")\n')
    checker = HealthChecker(timeout=30)
    checker.start(HealthConfig(backend_python=str(python)), blender_version=(5, 1, 0))
    results = collect(checker)
    assert results['blender'].status == 'UNSUPPORTED'
    assert results['python'].status == 'UNSUPPORTED'
