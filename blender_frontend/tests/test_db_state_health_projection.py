"""State, health and projection failure boundaries on the host."""
from dataclasses import asdict
import math
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from wfrl_blender import camera_projection as projection, health
from wfrl_blender.state import FrontendState


@pytest.mark.parametrize('capabilities', [None, [[], 'pause'], [1], [''], 'pause'])
def test_synchronize_rejects_invalid_capabilities_without_partial_state(capabilities):
    state = FrontendState()
    state.synchronize('old', 'STOPPED', 'replay', ['pause'], state_revision=8,
                      cleanup_confirmed=True, worker_alive=False)
    state.disconnect('connection lost')
    before = asdict(state)
    with pytest.raises(ValueError):
        state.synchronize('new', 'RUNNING', 'formal_training', capabilities, state_revision=1,
                          cleanup_confirmed=False, worker_alive=True)
    assert asdict(state) == before


def test_synchronize_iterable_failure_keeps_all_state():
    def broken_capabilities():
        yield 'pause'
        raise RuntimeError('capability source failed')

    state = FrontendState()
    before = asdict(state)
    with pytest.raises(RuntimeError, match='capability source'):
        state.synchronize('new', 'RUNNING', 'replay', broken_capabilities(),
                          cleanup_confirmed=False, worker_alive=True)
    assert asdict(state) == before


@pytest.mark.parametrize('timeout', [math.nan, math.inf, -math.inf, True, '2', None, 10**400])
def test_health_timeout_rejects_nonfinite_or_non_numeric_values(timeout):
    with pytest.raises(ValueError):
        health.HealthChecker(timeout=timeout)


def test_health_worker_start_failure_is_reported_without_escaping(monkeypatch):
    thread = NS(start=Mock(side_effect=RuntimeError('cannot start worker')), is_alive=lambda: False)
    monkeypatch.setattr(health.threading, 'Thread', lambda **kwargs: thread)
    checker = health.HealthChecker()
    assert checker.start(health.HealthConfig(), (5, 2, 0)) is False
    assert not checker.running
    results = checker.poll()
    assert len(results) == 1
    assert results[0].status == 'ERROR'
    assert 'cannot start worker' in results[0].detail


@pytest.mark.skipif(health.os.name != 'posix', reason='POSIX process-group cleanup')
def test_health_cleanup_error_still_reaps_and_never_reports_ready(monkeypatch, tmp_path):
    executable = tmp_path / 'probe'
    executable.touch(mode=0o755)
    process = NS(pid=12345, returncode=0, poll=lambda: 0, wait=Mock())

    def popen(argv, **kwargs):
        kwargs['stdout'].write(b'MPI fixture 1.2\n')
        return process

    monkeypatch.setattr(health.subprocess, 'Popen', popen)
    monkeypatch.setattr(health.os, 'killpg', Mock(side_effect=PermissionError('cleanup denied')))
    result = health.HealthChecker()._probe('mpi', str(executable))
    process.wait.assert_called_once()
    assert result.status == 'ERROR'
    assert 'cleanup denied' in result.detail


@pytest.mark.parametrize('matrix', [
    (), ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((1, 0, 0, 0), (0, 1, 0, 0)),
    ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 0)),
    ((1, 2, 3, 0), (2, 4, 6, 0), (0, 0, 1, 0)),
    ((1, 0, 0, math.nan), (0, 1, 0, 0), (0, 0, 1, 0)),
    ((math.inf, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0)),
    ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 0)),
])
def test_cv_transform_rejects_invalid_or_degenerate_view_matrix(matrix):
    with pytest.raises(ValueError):
        projection.cv_from_world(matrix)


def test_cv_transform_keeps_axis_direction_and_translation():
    matrix = ((1., 0., 0., -2.), (0., 1., 0., -3.), (0., 0., 1., -4.), (0., 0., 0., 1.))
    expected = (matrix[0], (0., -1., 0., 3.), (0., 0., -1., 4.), matrix[3])
    assert projection.cv_from_world(matrix) == expected
    assert projection.cv_from_world(matrix[:3]) == expected[:3]


@pytest.mark.parametrize('args', [(-1e308, 1e308, 1.), (0., 1., 1e-320), (1e16, 1e16 + 4., 1.)])
def test_sample_times_rejects_overflow_and_unrepresentable_distinct_times(args):
    with pytest.raises(ValueError):
        projection.sample_times(*args)


@pytest.mark.parametrize('limit', [math.nan, math.inf, 0, -1, True, 2.5])
def test_sample_times_requires_a_positive_integer_limit(limit):
    with pytest.raises(ValueError):
        projection.sample_times(0., 1., .5, limit=limit)


def test_projection_reports_unrepresentable_integer_as_value_error():
    with pytest.raises(ValueError):
        projection.rotation(10**400, 0, 0)


@pytest.mark.parametrize('maximum', [math.nan, math.inf, -math.inf, True])
def test_projection_rejects_invalid_gpu_limit(maximum):
    with pytest.raises(ValueError):
        projection.resolution(90., 60., max_size=maximum)


@pytest.mark.parametrize('calculate', [
    lambda: projection.resolution(90., 60., 10**400),
    lambda: projection.intrinsics(90., 60., 10**400, 480),
    lambda: projection.pixel_scales(90., 60., 10**400, 480),
])
def test_projection_rejects_unrepresentable_pixel_dimensions(calculate):
    with pytest.raises(ValueError):
        calculate()
