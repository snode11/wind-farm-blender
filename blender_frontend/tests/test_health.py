import os
import sys
import time
from pathlib import Path

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


def executable(tmp_path, body):
    path = tmp_path / 'probe tool'
    path.write_text(f'#!{sys.executable}\n' + body)
    path.chmod(0o755)
    return str(path)


def test_missing_optional_environment_does_not_hide_blender():
    checker = HealthChecker()
    checker.start(HealthConfig(), blender_version=(5, 2, 0))
    results = collect(checker)
    assert results['blender'].status == 'READY'
    assert all(results[key].status == 'MISSING' for key in ('python', 'mpi', 'fastfarm'))


def test_python_version_and_mpi_actual_execution(tmp_path):
    mpi = executable(tmp_path, 'import sys\nassert sys.argv[1:] == ["--version"]\nprint("MPI fixture 1.2")\n')
    checker = HealthChecker()
    checker.start(HealthConfig(backend_python=sys.executable, mpi_path=mpi), blender_version=(5, 2, 0))
    results = collect(checker)
    assert results['python'].status == results['mpi'].status == 'READY'
    assert 'MPI fixture 1.2' in results['mpi'].detail
    assert 'Python 3.' in results['python'].detail


def test_timeout_reaps_real_process(tmp_path):
    pid_file = tmp_path / 'pid'
    tool = executable(tmp_path, f'import os,time\nopen({str(pid_file)!r},"w").write(str(os.getpid()))\ntime.sleep(60)\n')
    checker = HealthChecker(timeout=1.0)
    checker.start(HealthConfig(mpi_path=tool))
    results = collect(checker)
    assert results['mpi'].status == 'TIMEOUT'
    pid = int(pid_file.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('probe process leaked')


def test_cancel_returns_immediately_and_reaps_process(tmp_path):
    pid_file = tmp_path / 'pid'
    tool = executable(tmp_path, f'import os,time\nopen({str(pid_file)!r},"w").write(str(os.getpid()))\ntime.sleep(60)\n')
    checker = HealthChecker(timeout=10)
    checker.start(HealthConfig(mpi_path=tool))
    deadline = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert pid_file.exists()
    start = time.monotonic()
    checker.cancel()
    assert time.monotonic() - start < .1
    collect(checker)
    import pytest
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)


def test_fastfarm_only_checks_executable_and_never_runs(tmp_path):
    marker = tmp_path / 'simulation-started'
    tool = executable(tmp_path, f'open({str(marker)!r},"w").write("bad")\n')
    checker = HealthChecker()
    checker.start(HealthConfig(fastfarm_path=tool))
    result = collect(checker)['fastfarm']
    assert result.status == 'CHECKED'
    assert 'version' in result.detail
    assert not marker.exists()


def test_relative_path_is_rejected():
    checker = HealthChecker()
    checker.start(HealthConfig(backend_python='python'))
    assert collect(checker)['python'].status == 'INVALID'


def test_versions_below_manifest_and_backend_requirements_are_unsupported(tmp_path):
    python = executable(tmp_path, 'print("Python 3.10.14")\n')
    checker = HealthChecker()
    checker.start(HealthConfig(backend_python=python), blender_version=(5, 1, 0))
    results = collect(checker)
    assert results['blender'].status == 'UNSUPPORTED'
    assert results['python'].status == 'UNSUPPORTED'
