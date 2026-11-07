"""Failure outcomes and cleanup ownership survive competing lifecycle operations."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from wfrl.blender_bridge.backend_session import BackendSession
from test_part4_backend import FakeTrainer, scene


def join_drain(session):
    if session._stop_worker is not None:
        session._stop_worker.join(1)
        assert not session._stop_worker.is_alive()


@pytest.mark.parametrize('backend', ['trainer', 'formal'])
def test_stop_before_tick_preserves_failed_worker_exit(backend):
    session = BackendSession(scene())
    session.status = 'RUNNING'
    if backend == 'trainer':
        session._trainer = SimpleNamespace(runtime=None, running=False, error='worker failed')
        session._thread = SimpleNamespace(is_alive=lambda: False)
        reason = 'worker failed'
    else:
        session._process = SimpleNamespace(poll=lambda: 17, returncode=17)
        session._formal_processes = SimpleNamespace(alive=lambda: False)
        reason = 'Formal process exited 17'
    session.stop(0)
    events = session.poll()
    assert session.status == 'FAILED'
    assert session._lifecycle_reason == reason
    assert not any(p['run_status'] == 'STOPPED' for kind, p in events if kind == 'lifecycle')
    assert session.cleanup_confirmed and not session.worker_alive


def test_worker_failure_reported_during_stop_is_not_success():
    session = BackendSession(scene())
    trainer = SimpleNamespace(runtime=None, running=True, error=None)

    def stop(timeout):
        trainer.error = 'worker failed while stopping'
        trainer.running = False

    trainer.stop = stop
    session._trainer = trainer
    session.lifecycle('RUNNING')
    session.stop(0)
    join_drain(session)
    session.poll()
    assert session.status == 'FAILED'
    assert session._lifecycle_reason == trainer.error


@pytest.mark.parametrize('cleanup', ['exception', 'timeout', 'formal_force'])
def test_cleanup_failure_preserves_primary_diagnosis(monkeypatch, cleanup):
    session = BackendSession(scene())
    trainer = SimpleNamespace(runtime=None, running=True, error=None)
    if cleanup == 'exception':
        trainer.stop = Mock(side_effect=OSError('cleanup exploded'))
        secondary = 'Cleanup failed: cleanup exploded'
    elif cleanup == 'timeout':
        trainer.stop = Mock()
        secondary = 'Stop timeout'
    else:
        session._process = SimpleNamespace(returncode=None, poll=lambda: None)
        session._formal_processes = SimpleNamespace(alive=lambda: trainer.running)
        monkeypatch.setattr(session, '_signal_process_group', Mock())

        def wait(timeout):
            if timeout == session.kill_grace_seconds:
                trainer.running = False
                return True
            return False

        monkeypatch.setattr(session, '_wait_until_stopped', wait)
        secondary = 'force terminated'
    if cleanup != 'formal_force':
        session._trainer = trainer
    session.lifecycle('FAILED', 'primary worker failure')
    try:
        session.stop(0)
        join_drain(session)
        session.poll()
        assert session.status == 'FAILED'
        assert session._lifecycle_reason.startswith('primary worker failure')
        assert secondary in session._lifecycle_reason
    finally:
        trainer.running = False


def test_draining_rejects_channel_changes_but_allows_stop_and_sync():
    session = BackendSession(scene())
    entered, release = threading.Event(), threading.Event()
    runtime = SimpleNamespace(topics=lambda: ['power'], channel_table=lambda: [])
    trainer = SimpleNamespace(runtime=runtime, running=True, error=None)

    def stop(timeout):
        trainer.running = False
        entered.set()
        assert release.wait(1)

    trainer.stop = Mock(side_effect=stop)
    session._trainer = trainer
    session.lifecycle('FAILED', 'worker failure')
    session.stop(0)
    try:
        assert entered.wait(1)
        session.command(dict(command='run.stop', arguments=dict(timeout_seconds=0)))
        session.command(dict(command='session.sync', arguments={}))
        with pytest.raises(ValueError, match='cleanup'):
            session.command(dict(command='channel.set', arguments=dict(topic='power', enabled=False)))
        assert session._channel_states == {}
        assert trainer.stop.call_count == 1
    finally:
        release.set()
        join_drain(session)


def test_synchronous_snapshot_failure_cannot_transition_back_to_running():
    class FailingSnapshotTrainer(FakeTrainer):
        def start(self):
            self.callback(SimpleNamespace())

    session = BackendSession(scene(), trainer_factory=FailingSnapshotTrainer)
    session.start('demo', {})
    events = session.poll()
    assert session.status == 'FAILED'
    assert 'ctx' in session._lifecycle_reason
    assert [p['run_status'] for kind, p in events if kind == 'lifecycle'] == ['STARTING', 'FAILED']
    session.stop(0)
    assert session.status == 'FAILED'


@pytest.mark.parametrize('resource', ['progress_reader', 'scene_directory'])
def test_start_failure_keeps_primary_exception_and_blocks_reset_until_cleanup(resource):
    original = ValueError('primary start failure')
    cleanup = Mock(side_effect=OSError('owned resource cleanup failed'))
    session = BackendSession(scene())

    def factory(*args, **kwargs):
        if resource == 'progress_reader':
            session._progress_reader = SimpleNamespace(finish=cleanup)
        else:
            session._formal_scene_dir = SimpleNamespace(cleanup=cleanup)
        raise original

    session.trainer_factory = factory
    try:
        with pytest.raises(ValueError, match='primary start failure') as caught:
            session.start('demo', {})
        assert caught.value is original
        assert session.status == 'FAILED'
        assert session._lifecycle_reason.startswith('primary start failure')
        assert 'owned resource cleanup failed' in session._lifecycle_reason
        session.stop(0)
        assert not session.synchronization_state()['cleanup_confirmed']
        with pytest.raises(ValueError, match='cleanup'):
            session.command(dict(command='run.reset', arguments={}))
    finally:
        cleanup.side_effect = None
        session.synchronization_state()
    assert session.cleanup_confirmed
    session.command(dict(command='run.reset', arguments={}))
    assert session.status == 'READY'


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), 'nan', 'inf', 1e300])
def test_invalid_stop_timeout_is_rejected_before_state_mutation(timeout):
    session = BackendSession(scene())
    trainer = SimpleNamespace(runtime=None, running=True, error=None)
    trainer.stop = Mock(side_effect=lambda timeout: setattr(trainer, 'running', False))
    session._trainer = trainer
    session.status = 'RUNNING'
    try:
        with pytest.raises(ValueError, match='timeout'):
            session.command(dict(command='run.stop', arguments=dict(timeout_seconds=timeout)))
        trainer.stop.assert_not_called()
        assert session.status == 'RUNNING' and session._stop_worker is None
    finally:
        trainer.running = False
        join_drain(session)
