"""Fault injection for startup ownership, controller cancellation and cleanup proof."""
import signal
import time
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest

from wfrl.blender_bridge import backend_session, formal_process
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.formal_process import FormalProcesses, OwnedProcesses


class Process:
    pid = 12345

    def __init__(self, *, exit_on_term=True, exit_on_kill=True):
        self.returncode = None
        self.exit_on_term, self.exit_on_kill = exit_on_term, exit_on_kill
        self.signals = []

    def poll(self):
        return self.returncode

    def send_signal(self, signum):
        self.signals.append(signum)
        if signum == signal.SIGTERM and self.exit_on_term:
            self.returncode = -signal.SIGTERM
        if signum == signal.SIGKILL and self.exit_on_kill:
            self.returncode = -signal.SIGKILL

    def terminate(self):
        self.send_signal(signal.SIGTERM)

    def kill(self):
        self.send_signal(signal.SIGKILL)


def quick_owner_clock(monkeypatch):
    ticks = iter(index * .6 for index in range(1000))
    monkeypatch.setattr(formal_process, 'time', SimpleNamespace(
        time=time.time, monotonic=lambda: next(ticks), sleep=lambda _: None))


def dead_registration(monkeypatch, process, error):
    monkeypatch.setattr(formal_process.subprocess, 'Popen', Mock(return_value=process))
    monkeypatch.setattr(formal_process.psutil, 'Process', Mock(side_effect=error))
    monkeypatch.setattr(formal_process.psutil, 'process_iter', lambda attrs: iter(()))
    quick_owner_clock(monkeypatch)


@pytest.mark.parametrize('exit_on_term', [True, False])
@pytest.mark.parametrize('failure', ['registration', 'unexpected_registration', 'post_start'])
def test_owner_rolls_back_start_without_session_handle(monkeypatch, failure, exit_on_term):
    owner = FormalProcesses()
    process = Process(exit_on_term=exit_on_term)
    original = (psutil.AccessDenied(process.pid) if failure == 'registration'
                else RuntimeError(f'{failure} failed'))
    dead_registration(monkeypatch, process, original)
    post_start = None
    if failure == 'post_start':
        root = Mock(pid=process.pid)
        root.is_running.side_effect = lambda: process.poll() is None
        root.status.return_value = psutil.STATUS_RUNNING
        root.children.return_value = []
        root.create_time.return_value = time.time()
        monkeypatch.setattr(formal_process.psutil, 'Process', lambda pid: root)
        post_start = Mock(side_effect=original)
    with pytest.raises(type(original)) as caught:
        owner.start(['python'], cwd='.', env={}, on_started=post_start)
    assert caught.value is original
    assert owner.process is process and process.poll() is not None
    assert signal.SIGTERM in process.signals
    assert (signal.SIGKILL in process.signals) is not exit_on_term
    assert owner.status() == 'EMPTY_CONFIRMED'
    assert owner.rollback_error is None


def test_failed_rollback_retains_owner_and_primary_exception(monkeypatch):
    owner = FormalProcesses()
    process = Process(exit_on_term=False, exit_on_kill=False)
    original = psutil.AccessDenied(process.pid)
    dead_registration(monkeypatch, process, original)
    with pytest.raises(psutil.AccessDenied) as caught:
        owner.start(['python'], cwd='.', env={})
    assert caught.value is original
    assert owner.process is process and owner.alive()
    assert 'Startup rollback failed' in owner.rollback_error
    assert owner.rollback_error in caught.value.__notes__
    assert signal.SIGTERM in process.signals and signal.SIGKILL in process.signals
    session = BackendSession()
    session._formal_processes = owner
    session.lifecycle('FAILED', 'registration failed')
    assert not session.synchronization_state()['cleanup_confirmed']
    with pytest.raises(ValueError, match='active'):
        session.command(dict(command='run.reset'))


@pytest.mark.parametrize('failure', ['registration', 'reader'])
def test_session_start_rolls_back_before_process_assignment(monkeypatch, failure):
    scene = SimpleNamespace(backend='fastfarm', to_dict=lambda: {})
    session = BackendSession(scene, scene_loader=lambda _: scene)
    process = Process()
    original = OSError('registration failed') if failure == 'registration' else RuntimeError('reader start failed')
    dead_registration(monkeypatch, process, original)
    reader = SimpleNamespace(start=Mock(), finish=Mock())
    if failure == 'reader':
        monkeypatch.setattr(formal_process.psutil, 'Process', Mock(side_effect=psutil.NoSuchProcess(process.pid)))
        reader.start.side_effect = original
    monkeypatch.setattr(backend_session, 'TrainingProgressReader', lambda *args: reader)
    monkeypatch.setattr(session, '_formal_command', lambda *_: ('.', ['python']))
    with pytest.raises(type(original)) as caught:
        session.start('formal_training', dict(scene='unused.yaml'))
    assert caught.value is original
    assert session._process is None
    assert session._formal_processes.process is process
    assert process.poll() is not None
    assert session.status == 'FAILED' and str(original) in session._lifecycle_reason
    assert session.synchronization_state()['cleanup_confirmed']
    assert session._progress_reader is session._progress_dir is None
    reader.finish.assert_called_once_with()


def ps_process(pid, command):
    process = Mock(pid=pid)
    process.cmdline.return_value = command
    process.is_running.return_value = True
    process.status.return_value = psutil.STATUS_RUNNING
    return process


def test_posix_interrupt_targets_training_main_not_launcher_or_solver(monkeypatch):
    monkeypatch.setattr(formal_process, 'sys', SimpleNamespace(platform='linux'))
    owner = FormalProcesses()
    owner.process = Process()
    command = ['/project/train_fastfarm.py', '--run-id', 'run-1']
    owner._training_command = tuple(command)
    launcher = ps_process(1, ['mpiexec', '-n', '1', 'python', *command])
    worker = ps_process(2, ['python', *command])
    solver = ps_process(3, ['FAST.Farm'])
    foreign = ps_process(4, ['python', *command[:-1], 'run-2'])
    owner._owned = {launcher, worker, solver, foreign}
    monkeypatch.setattr(owner, '_remember', lambda **kwargs: None)
    assert owner.signal(signal.SIGINT)
    worker.send_signal.assert_called_once_with(signal.SIGINT)
    for process in (launcher, solver, foreign):
        process.send_signal.assert_not_called()
    assert owner.process.signals == []
    owner.signal(signal.SIGTERM)
    for process in owner._owned:
        assert process.send_signal.call_args.args == (signal.SIGTERM,)


@pytest.mark.parametrize('matches', [0, 2])
def test_unidentified_training_main_never_interrupts_solver(monkeypatch, matches):
    monkeypatch.setattr(formal_process, 'sys', SimpleNamespace(platform='linux'))
    owner = FormalProcesses()
    owner.process = Process()
    owner._training_command = ('/project/train_fastfarm.py',)
    owner._owned = {ps_process(index, ['python', *owner._training_command]) for index in range(matches)}
    solver = ps_process(4, ['FAST.Farm'])
    owner._owned.add(solver)
    monkeypatch.setattr(owner, '_remember', lambda **kwargs: None)
    with pytest.raises(RuntimeError, match='exactly one'):
        owner.signal(signal.SIGINT)
    assert '应用层收尾未确认' in owner.stop_request_error
    assert owner.process.signals == []
    for process in owner._owned:
        process.send_signal.assert_not_called()


def join_stop(session):
    session._stop_worker.join(1)
    assert not session._stop_worker.is_alive()
    session.poll()


@pytest.mark.parametrize('code, expected', [(0, 'STOPPED'), (-2, 'STOPPED'), (130, 'STOPPED'),
                                            (17, 'FAILED'), (-11, 'FAILED')])
def test_formal_exit_during_graceful_wait_is_rechecked(monkeypatch, code, expected):
    process = Process()
    owner = SimpleNamespace(process=process, alive=lambda: process.poll() is None,
                            signal=Mock(return_value=True))
    session = BackendSession()
    session._formal_processes = owner
    session.lifecycle('RUNNING')
    def wait(timeout):
        process.returncode = code
        return True
    monkeypatch.setattr(session, '_wait_until_stopped', wait)
    session.stop(.1)
    join_stop(session)
    state = session.synchronization_state()
    assert state['run_status'] == expected
    assert state['cleanup_confirmed'] and not state['worker_alive']
    owner.signal.assert_called_once_with(signal.SIGINT)
    if expected == 'FAILED':
        assert f'Formal process exited {code}' in session._lifecycle_reason


@pytest.mark.parametrize('code', [-2, 130, 17])
def test_exit_before_cancel_request_is_not_expected_cancellation(code):
    process = Process()
    process.returncode = code
    session = BackendSession()
    session._formal_processes = SimpleNamespace(process=process, alive=lambda: False)
    session.status = 'RUNNING'
    session.stop(0)
    assert session.status == 'FAILED'
    assert f'Formal process exited {code}' in session._lifecycle_reason


@pytest.mark.parametrize('via_stop', [False, True])
@pytest.mark.parametrize('resource', ['runtime', 'driver', '_driver'])
@pytest.mark.parametrize('worker_error', [None, 'worker cleanup failed'])
def test_dead_trainer_retaining_resources_blocks_cleanup_and_reset(resource, via_stop, worker_error):
    trainer = SimpleNamespace(runtime=None, running=False, error=worker_error)
    retained = SimpleNamespace(driver=object(), channel_table=lambda: [])
    setattr(trainer, resource, retained)
    session = BackendSession()
    session._trainer = trainer
    session._thread = SimpleNamespace(is_alive=lambda: False)
    session.status = 'RUNNING'
    if via_stop:
        session.stop(0)
    else:
        session.tick()
    state = session.synchronization_state()
    assert state['run_status'] == 'FAILED'
    assert not state['worker_alive'] and not state['cleanup_confirmed']
    if worker_error:
        assert worker_error in session._lifecycle_reason
    assert 'trainer retains' in session._lifecycle_reason
    assert getattr(trainer, resource) is retained
    with pytest.raises(ValueError, match='cleanup'):
        session.command(dict(command='run.reset'))
    setattr(trainer, resource, None)
    assert session.synchronization_state()['cleanup_confirmed']
    assert session.status == 'FAILED'
    session.command(dict(command='run.reset'))
    assert session.status == 'READY'


def test_trainer_error_arriving_after_stop_return_is_rechecked():
    session = BackendSession()
    class Trainer:
        runtime = error = None
        stopped = False

        @property
        def running(self):
            if self.stopped:
                self.error = 'final worker failure'
            return not self.stopped

        def stop(self, timeout):
            self.stopped = True
            assert self.error is None

    trainer = Trainer()
    session._trainer = trainer
    session.status = 'RUNNING'
    session.stop(0)
    join_stop(session)
    assert session.status == 'FAILED'
    assert session._lifecycle_reason == 'final worker failure'
    assert session.cleanup_confirmed


@pytest.mark.parametrize('terminates', [False, True])
def test_windows_ctrl_break_failure_forces_cleanup_and_keeps_diagnosis(monkeypatch, terminates):
    monkeypatch.setattr(formal_process, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(formal_process, 'signals', SimpleNamespace(SIGINT=2, SIGTERM=15, CTRL_BREAK_EVENT=1))
    process = Process(exit_on_term=terminates)
    def send(signum):
        if signum == 1:
            process.signals.append(signum)
            raise OSError('no shared console')
        Process.send_signal(process, signum)
    process.send_signal = send
    owner = FormalProcesses()
    owner.process = process
    monkeypatch.setattr(owner, '_remember', lambda **kwargs: None)
    session = BackendSession(kill_grace_seconds=0)
    session._formal_processes = owner
    session.status = 'RUNNING'
    waits = []
    def wait(timeout):
        waits.append(timeout)
        return not owner.alive()
    monkeypatch.setattr(session, '_wait_until_stopped', wait)
    session.stop(120)
    join_stop(session)
    assert waits and 120 not in waits
    assert process.signals[0] == 1 and signal.SIGTERM in process.signals
    assert session.status == 'FAILED'
    for text in ('CTRL_BREAK', 'no shared console', '应用层收尾未确认'):
        assert text in session._lifecycle_reason
        assert text in owner.stop_request_error
    assert session.cleanup_confirmed is terminates
    assert session.worker_alive is not terminates
