import sys
import time

import psutil

from wfrl_blender.health import HealthChecker, HealthConfig


def collect(checker):
    deadline = time.monotonic() + 5
    results = []
    while checker.running and time.monotonic() < deadline:
        results.extend(checker.poll())
        time.sleep(.01)
    results.extend(checker.poll())
    assert not checker.running
    return {result.component: result for result in results}


def sleeping_probe(python_tool, pid_file):
    return python_tool(pid_file.with_name('probe tool'),
        'import os, time\nfrom pathlib import Path\n'
        f'Path({str(pid_file)!r}).write_text(str(os.getpid()), encoding="ascii")\n'
        'time.sleep(60)\n')


def test_missing_optional_environment_does_not_hide_blender():
    checker = HealthChecker()
    checker.start(HealthConfig(), blender_version=(5, 2, 0))
    results = collect(checker)
    assert results['blender'].status == 'READY'
    assert all(results[key].status == 'MISSING' for key in ('python', 'mpi', 'fastfarm'))


def test_python_version_and_mpi_actual_execution(tmp_path, python_tool):
    mpi = python_tool(tmp_path / 'probe tool',
        'import sys\nassert sys.argv[1:] == ["--version"]\nprint("MPI fixture 1.2")\n')
    checker = HealthChecker()
    checker.start(HealthConfig(backend_python=sys.executable, mpi_path=str(mpi)), blender_version=(5, 2, 0))
    results = collect(checker)
    assert results['python'].status == results['mpi'].status == 'READY'
    assert 'MPI fixture 1.2' in results['mpi'].detail
    assert 'Python 3.' in results['python'].detail


def test_timeout_reaps_real_process(tmp_path, python_tool):
    pid_file = tmp_path / 'pid'
    tool = sleeping_probe(python_tool, pid_file)
    checker = HealthChecker(timeout=2.0)
    checker.start(HealthConfig(mpi_path=str(tool)))
    results = collect(checker)
    assert results['mpi'].status == 'TIMEOUT'
    assert not psutil.pid_exists(int(pid_file.read_text(encoding='ascii'))), 'probe process leaked'


def test_cancel_returns_immediately_and_reaps_process(tmp_path, python_tool):
    pid_file = tmp_path / 'pid'
    tool = sleeping_probe(python_tool, pid_file)
    checker = HealthChecker(timeout=10)
    checker.start(HealthConfig(mpi_path=str(tool)))
    deadline = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert pid_file.exists()
    start = time.monotonic()
    checker.cancel()
    assert time.monotonic() - start < .1
    collect(checker)
    assert not psutil.pid_exists(int(pid_file.read_text(encoding='ascii'))), 'probe process leaked'


def test_fastfarm_only_checks_executable_and_never_runs(tmp_path, python_tool):
    marker = tmp_path / 'simulation-started'
    tool = python_tool(tmp_path / 'probe tool', f'open({str(marker)!r},"w").write("bad")\n')
    checker = HealthChecker()
    checker.start(HealthConfig(fastfarm_path=str(tool)))
    result = collect(checker)['fastfarm']
    assert result.status == 'CHECKED'
    assert 'version' in result.detail
    assert not marker.exists()


def test_relative_path_is_rejected():
    checker = HealthChecker()
    checker.start(HealthConfig(backend_python='python'))
    assert collect(checker)['python'].status == 'INVALID'


def test_versions_below_manifest_and_backend_requirements_are_unsupported(tmp_path, python_tool):
    python = python_tool(tmp_path / 'probe tool', 'print("Python 3.10.14")\n')
    checker = HealthChecker()
    checker.start(HealthConfig(backend_python=str(python)), blender_version=(5, 1, 0))
    results = collect(checker)
    assert results['blender'].status == 'UNSUPPORTED'
    assert results['python'].status == 'UNSUPPORTED'
