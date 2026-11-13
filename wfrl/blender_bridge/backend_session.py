"""Own one existing Trainer or formal CLI process; never duplicate training."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import math
import os
import signal
import shutil
import sys
import threading
import time
import uuid
import tempfile
import queue

from .training_progress import TrainingProgressReader

from .workflow import configured_scene, validate_run_options

from .snapshot_adapter import SnapshotAdapter
from .messages import encode_message


FORMAL_VALUE_OPTIONS = {
    'duty_penalty', 'episode_steps', 'iters', 'load_coef', 'n_steps', 'policy',
    'resume_from', 'reward', 'reward_alpha', 'reward_ref', 'save_interval',
    'seed', 'tag', 'warmup_steps', 'wind_direction', 'wind_sched', 'wind_speed',
}
FORMAL_FLAG_OPTIONS = {'no_purge', 'obs_duty', 'recurrent'}
_UNSPECIFIED_REASON = object()


def _aged_payload(payload, elapsed):
    payload = deepcopy(payload)
    def age(value):
        if isinstance(value, dict):
            if 'source_age_seconds' in value:
                value['source_age_seconds'] += elapsed
            for child in value.values():
                age(child)
        elif isinstance(value, list):
            for child in value:
                age(child)
    age(payload)
    return payload


class _EventBuffer:
    """Bound reliable history separately from coalescible training observations."""
    def __init__(self, capacity):
        self.capacity = capacity
        self.stats_capacity = max(2, capacity)
        self._items = deque()
        self._lock = threading.Lock()
        self._reliable_count = self._stats_count = 0

    def put_nowait(self, event):
        kind, payload = event
        with self._lock:
            if kind == 'training_stats':
                if self._stats_count >= self.stats_capacity:
                    # Keep the latest metrics even when newer progress has no stats.
                    for index, (old_kind, old_payload, _) in enumerate(self._items):
                        if old_kind == kind and old_payload['record_kind'] == payload['record_kind']:
                            del self._items[index]
                            self._stats_count -= 1
                            break
                    else:
                        for index, (old_kind, _, _) in enumerate(self._items):
                            if old_kind == kind:
                                del self._items[index]
                                self._stats_count -= 1
                                break
                self._stats_count += 1
            else:
                if self._reliable_count >= self.capacity:
                    raise queue.Full
                self._reliable_count += 1
            self._items.append((kind, deepcopy(payload), time.monotonic()))

    def get_nowait(self):
        with self._lock:
            if not self._items:
                raise queue.Empty
            kind, payload, queued_at = self._items.popleft()
            if kind == 'training_stats':
                self._stats_count -= 1
            else:
                self._reliable_count -= 1
            if kind in {'training_stats', 'safety_event'}:
                payload = _aged_payload(payload, max(0., time.monotonic() - queued_at))
            return kind, payload

    def __len__(self):
        with self._lock:
            return len(self._items)


class BackendSession:
    def __init__(self, scene=None, trainer_factory=None, scene_loader=None,
                 max_events=2048, kill_grace_seconds=5.0, mpi_launcher=None):
        if type(max_events) is not int or max_events < 1:
            raise ValueError('max_events must be a positive integer')
        self.session_id = uuid.uuid4().hex
        self.state_revision = 0
        self.run_id = None
        self._progress_reader = None
        self._progress_dir = None
        self._progress_events = _EventBuffer(max_events)
        self.scene = scene
        self.fake = False
        self.mode = 'demo'
        self.status = 'READY'
        self._lifecycle_reason = None
        self.trainer_factory = trainer_factory
        self.scene_loader = scene_loader
        self.max_events = max_events
        self.kill_grace_seconds = kill_grace_seconds
        self.mpi_launcher = mpi_launcher
        self._condition = threading.Condition(threading.RLock())
        self._events = _EventBuffer(max_events)
        self._overflowed = False
        self._overflow_error = self._overflow_lifecycle = None
        self._latest = None
        self._latest_at = None
        self._last_snapshot = None
        self._last_stats = {}
        self._trainer = self._thread = self._process = None
        self._formal_processes = None
        self._formal_cancel_requested = False
        self._paused = False
        self._permits = 0
        self._stopping = False
        self._stop_worker = None
        self._channel_states = {}
        self._formal_scene_dir = None
        self._finalizing = False
        self.cleanup_confirmed = True
        self.worker_alive = False

    @property
    def capabilities(self):
        return [] if self.mode == 'formal_training' else ['pause', 'single_step']

    def emit(self, kind, payload):
        with self._condition:
            if kind == 'snapshot':
                self._latest = (kind, deepcopy(payload))
                self._latest_at = time.monotonic()
                self._last_snapshot = self._latest, self._latest_at
                return
            if kind == 'training_stats':
                key = payload['record_kind']
                self._last_stats.pop(key, None)
                self._last_stats[key] = deepcopy(payload), time.monotonic()
            if self._overflowed and kind == 'lifecycle' and payload['run_status'] == 'FAILED':
                self._overflow_lifecycle = (kind, payload)
                return
            try:
                self._events.put_nowait((kind, payload))
            except queue.Full:
                message = 'Reliable event queue overflow; additional event rejected'
                self._overflowed = self._stopping = True
                self._condition.notify_all()
                # Reserved diagnostic/terminal slots preserve the accepted history.
                self._overflow_error = ('error', dict(code='EVENT_OVERFLOW', message=message, fatal=True))
                self.lifecycle('FAILED', message)
                self.stop(0)
                raise RuntimeError(message)

    def replay_latest(self):
        """Restore the current run's last observations for a resumed connection."""
        with self._condition:
            for payload, queued_at in self._last_stats.values():
                self._events.put_nowait(('training_stats',
                    _aged_payload(payload, max(0., time.monotonic() - queued_at))))
            if self._last_snapshot is not None:
                self._latest, self._latest_at = self._last_snapshot

    def clear_observations(self):
        with self._condition:
            self._latest = self._last_snapshot = None
            self._last_stats.clear()

    def channel_table(self):
        runtime = self._trainer.runtime if self._trainer is not None else None
        if runtime is None: return []
        fidelity_map = {
            "DIRECT": "DIRECT", "直读": "DIRECT",
            "DERIVED": "EXPORTED", "导出": "EXPORTED",
            "SYNTH": "SYNTH", "合成": "SYNTH",
        }
        return [[topic, sensor, fidelity_map.get(str(fidelity), str(fidelity)), enabled, provenance]
                for topic, sensor, fidelity, enabled, provenance in runtime.channel_table()]

    def lifecycle(self, status, reason=_UNSPECIFIED_REASON):
        with self._condition:
            if reason is _UNSPECIFIED_REASON:
                reason = self._lifecycle_reason if status == self.status else None
            self.status = status
            self._lifecycle_reason = reason
            self._refresh_cleanup_state()
            self.state_revision += 1
            self.emit('lifecycle', dict(self._state_payload(), reason=self._lifecycle_reason))

    def _failure_reason(self, reason):
        message = str(reason) or type(reason).__name__
        if self.status == 'FAILED' and self._lifecycle_reason:
            if message in self._lifecycle_reason:
                return self._lifecycle_reason
            return self._lifecycle_reason + '; ' + message
        return message

    def _fail(self, reason):
        with self._condition:
            self.lifecycle('FAILED', self._failure_reason(reason))

    def _backend_error(self):
        error = self._trainer.error if self._trainer is not None else None
        process = self._formal_process()
        if process is not None:
            code = process.poll()
            cancelled = (self._formal_cancel_requested and os.name == 'posix'
                         and code in (-signal.SIGINT, 128 + signal.SIGINT))
            if code is not None and code != 0 and not cancelled:
                error = error or f'Formal process exited {code}'
        if self._formal_processes is not None:
            rollback = getattr(self._formal_processes, 'rollback_error', None)
            if rollback:
                error = f'{error}; {rollback}' if error else rollback
        return error

    def _formal_process(self):
        if self._formal_processes is not None:
            return getattr(self._formal_processes, 'process', self._process)
        return self._process

    def _trainer_cleanup_error(self):
        trainer = self._trainer
        if trainer is None:
            return None
        runtime = getattr(trainer, 'runtime', None)
        retained = []
        # IsolatedTrainer exposes itself as a channel proxy, not a SceneRuntime.
        if runtime is not None and runtime is not trainer:
            retained.append('runtime')
        if any(getattr(trainer, name, None) is not None for name in ('driver', '_driver')):
            retained.append('driver')
        if retained:
            return 'Cleanup unconfirmed: trainer retains ' + '/'.join(retained) + '; restart blocked'
        return None

    def _refresh_cleanup_state(self):
        before = self.cleanup_confirmed, self.worker_alive, self.status, self._lifecycle_reason
        alive = self.alive()
        inactive = self.status in {'READY', 'STOPPED', 'FAILED'}
        draining = self._stop_worker is not None and self._stop_worker.is_alive()
        cleanup_ok = True
        if inactive and not alive and not draining and not self._finalizing:
            error = self._backend_error() if self.status != 'READY' else None
            resource_error = self._trainer_cleanup_error()
            for reason in (error, resource_error):
                if reason:
                    self._lifecycle_reason = self._failure_reason(reason)
                    self.status = 'FAILED'
            cleanup_ok = resource_error is None
            self._finalizing = True
            try:
                self._collect_progress(final=True)
                if self._formal_scene_dir is not None:
                    self._formal_scene_dir.cleanup()
                    self._formal_scene_dir = None
            except Exception as exc:
                cleanup_ok = False
                self._lifecycle_reason = self._failure_reason('Cleanup failed: ' + (str(exc) or type(exc).__name__))
                self.status = 'FAILED'
            finally:
                self._finalizing = False
        confirmed = (inactive and not alive and not draining and not self._finalizing and cleanup_ok
                     and self._progress_reader is None and self._progress_dir is None
                     and self._formal_scene_dir is None)
        self.cleanup_confirmed, self.worker_alive = confirmed, alive
        return before != (confirmed, alive, self.status, self._lifecycle_reason)

    def _state_payload(self):
        return dict(run_status=self.status, mode=self.mode, run_id=self.run_id,
                    state_revision=self.state_revision, capabilities=self.capabilities,
                    cleanup_confirmed=self.cleanup_confirmed, worker_alive=self.worker_alive,
                    scene=self.scene.to_dict() if self.scene is not None else None,
                    channel_states=dict(self._channel_states), channel_table=self.channel_table())

    def synchronization_state(self):
        """Capture current state and its lifecycle watermark under the same lock."""
        with self._condition:
            if self._refresh_cleanup_state():
                self.state_revision += 1
                self.emit('lifecycle', dict(self._state_payload(), reason=self._lifecycle_reason))
            return self._state_payload()

    def _collect_progress(self, final=False):
        if final and self._progress_reader is not None:
            self._progress_reader.finish()
            self._progress_reader = None
        while True:
            try:
                kind, payload = self._progress_events.get_nowait()
            except queue.Empty:
                break
            if kind == 'training_stats':
                age = max(0., time.time() - payload['timestamp']['value'])
                payload['source_age_seconds'] = max(age, payload['source_age_seconds'])
                for channel in payload['stats'].values():
                    channel['source_age_seconds'] = max(age, channel['source_age_seconds'])
            self.emit(kind, payload)
        if final and self._progress_dir is not None:
            self._progress_dir.cleanup()
            self._progress_dir = None

    def tick(self):
        """Advance backend completion and cleanup independently of event consumers."""
        with self._condition:
            try:
                self._advance()
            except RuntimeError:
                if not self._overflowed:
                    raise

    def _advance(self):
        with self._condition:
            self._collect_progress()
            if self.status in ('RUNNING', 'PAUSED', 'STARTING'):
                finished = self._thread is not None and not self._thread.is_alive()
                process = self._formal_process()
                if process is not None:
                    finished = process.poll() is not None
                if finished:
                    error = self._backend_error()
                    if self.alive():
                        error = error or 'Backend controller exited while child processes remained'
                        self._fail(error)
                        self.stop(0)
                    else:
                        if not error: self.lifecycle('DRAINING')
                        self.lifecycle('FAILED' if error else 'STOPPED', error)
            if self._refresh_cleanup_state():
                self.state_revision += 1
                self.emit('lifecycle', dict(self._state_payload(), reason=self._lifecycle_reason))

    def poll(self):
        with self._condition:
            self.tick()
            result = []
            while True:
                try: result.append(self._events.get_nowait())
                except queue.Empty: break
            for event in (self._overflow_error, self._overflow_lifecycle):
                if event is not None: result.append(event)
            self._overflow_error = self._overflow_lifecycle = None
            if self._latest is not None:
                kind, payload = self._latest
                result.append((kind, _aged_payload(payload, max(0., time.monotonic() - self._latest_at))))
                self._latest = None
            return result

    def _load(self, path):
        if self.scene_loader is None:
            from wfrl.scene.schema import load_scene
            return load_scene(path)
        return self.scene_loader(path)

    def _formal_command(self, scene_path, options):
        unsupported = set(options) - FORMAL_VALUE_OPTIONS - FORMAL_FLAG_OPTIONS
        if unsupported:
            raise ValueError('Unsupported formal options: ' + ', '.join(sorted(unsupported)))
        root = Path(__file__).resolve().parents[2]
        configured_launcher = self.mpi_launcher or os.environ.get('WFCRL_MPIEXEC') or 'mpiexec'
        launcher = shutil.which(str(configured_launcher))
        if not launcher:
            raise ValueError(f'Formal FAST.Farm training requires an executable mpiexec launcher: {configured_launcher}')
        argv = [str(Path(launcher).resolve()), '-n', '1', sys.executable,
                str(root / 'scripts/train/train_fastfarm.py'),
                '--scene', str(Path(scene_path).resolve())]
        for key in sorted(options):
            value = options[key]
            flag = '--' + key.replace('_', '-')
            if key in FORMAL_FLAG_OPTIONS:
                if type(value) is not bool:
                    raise ValueError(f'Formal option {key} must be boolean')
                if value:
                    argv.append(flag)
            elif value is not None:
                if type(value) not in (str, int, float):
                    raise ValueError(f'Formal option {key} must be a scalar value')
                argv.extend([flag, str(value)])
        return root, argv

    def start(self, mode, options):
        with self._condition:
            if self.status not in ('READY', 'STOPPED'): raise ValueError('Session is active or failure unresolved')
            if self.alive() or (self._stop_worker is not None and self._stop_worker.is_alive()):
                raise ValueError('Backend cleanup is still finishing')
            if not self.synchronization_state()['cleanup_confirmed']:
                raise ValueError('Backend cleanup is still finishing')
            if mode not in ('demo', 'interactive_training', 'formal_training', 'replay'): raise ValueError('Unknown mode')
            if self.fake and mode != 'demo': raise ValueError('Fake backend supports SYNTH demo only')
            options = dict(options)
            path = options.pop('scene', options.pop('scene_path', None))
            candidate = self._load(path) if path else self.scene
            if candidate is None: raise ValueError('Load a scene first')
            overrides = options.pop('scene_overrides', {})
            if overrides: candidate = configured_scene(candidate, overrides)
            validate_run_options(mode, options)
            self.scene = candidate
            self.mode = mode
            self._paused = self._stopping = False
            self._permits = 0
            self._trainer = self._thread = self._process = None
            self._formal_processes = None
            self._formal_cancel_requested = False
            self.clear_observations()
            self.run_id = uuid.uuid4().hex
            self.lifecycle('STARTING')
            try:
                if mode == 'formal_training':
                    if not path: raise ValueError('Formal training requires options.scene path')
                    if self.scene.backend != 'fastfarm':
                        raise ValueError('Formal training requires FAST.Farm')
                    if overrides:
                        from wfrl.scene.schema import dump_scene
                        self._formal_scene_dir = tempfile.TemporaryDirectory(prefix='wfrl-bridge-scene-')
                        path = dump_scene(self.scene, str(Path(self._formal_scene_dir.name) / 'scene.yaml'))
                    root, argv = self._formal_command(path, options)
                    self._progress_dir = tempfile.TemporaryDirectory(prefix='wfrl-training-progress-')
                    progress_path = Path(self._progress_dir.name) / 'progress.jsonl'
                    argv.extend(['--training-progress', str(progress_path), '--run-id', self.run_id])
                    def observability_error(message):
                        payload = dict(code='TRAINING_OBSERVABILITY_ERROR', message=message, fatal=False)
                        self._progress_events.put_nowait(('error', payload))
                    self._progress_reader = TrainingProgressReader(progress_path, self.run_id,
                        lambda record: self._progress_events.put_nowait(('training_stats', record)), observability_error)
                    from .isolated_trainer import launcher_environment
                    from .formal_process import FormalProcesses
                    self._formal_processes = FormalProcesses()
                    self._process = self._formal_processes.start(argv, cwd=root, env=launcher_environment(),
                                                                 on_started=self._progress_reader.start)
                else:
                    factory = self.trainer_factory
                    if 'tip_package' in options:
                        from .tip_replay import RecordedTipTrainer
                        factory = RecordedTipTrainer
                    if factory is None:
                        if self.scene.backend == 'floris':
                            if mode != 'demo': raise ValueError('FLORIS supports backend demo only; existing Trainer requires FAST.Farm')
                            from .floris_session import FlorisDemoSession
                            factory = FlorisDemoSession
                        else:
                            if os.name == "posix" and mode in {"interactive_training", "replay"}:
                                from functools import partial
                                from .isolated_trainer import IsolatedTrainer
                                factory = partial(IsolatedTrainer, session_id=self.session_id, mpi_launcher=self.mpi_launcher)
                            else:
                                from wfrl.studio.trainer import Trainer
                                factory = Trainer
                    if set(options) & {'on_snapshot', 'demo', 'replay'}: raise ValueError('Reserved Trainer options')
                    adapter = SnapshotAdapter(self.scene, self.session_id, mode)
                    last_control_step = -1
                    def snapshot_callback(snapshot):
                        nonlocal last_control_step
                        try:
                            with self._condition:
                                if self._stopping: return
                                runtime = self._trainer.runtime
                                if runtime is not None:
                                    for topic, enabled in self._channel_states.items():
                                        runtime.set_enabled(topic, enabled)
                                wire_snapshot = hasattr(snapshot, 'wire_events')
                                payload = snapshot.payload if wire_snapshot else adapter.encode(snapshot)
                                payload['run_id'] = self.run_id
                                payload['channel_table'] = self.channel_table()
                                self.emit('snapshot', payload)
                                if mode == 'interactive_training':
                                    from .training_progress import interactive_progress
                                    progress = (snapshot.training if wire_snapshot else
                                                interactive_progress(snapshot, self.run_id, len(self.scene.turbines)))
                                    if progress:
                                        progress['run_id'] = self.run_id
                                        self.emit('training_stats', progress)
                                # Preparation and optimizer updates can repeat step 0
                                # or the preceding control step. Pause/step only at a
                                # new control boundary, which the frontend can confirm.
                                control_boundary = (payload.get('phase') != 'warmup'
                                                    and payload['step'] > last_control_step)
                                if control_boundary:
                                    last_control_step = payload['step']
                                if control_boundary and self._paused and self.status == 'RUNNING':
                                    self.lifecycle('PAUSED')
                                events = snapshot.wire_events if wire_snapshot else adapter.safety_events(snapshot)
                                for event in events: self.emit('safety_event', event)
                                while control_boundary and self._paused and not self._stopping and self._permits == 0:
                                    self._condition.wait()
                                if control_boundary and self._permits: self._permits -= 1
                                if runtime is not None:
                                    for topic, enabled in self._channel_states.items():
                                        runtime.set_enabled(topic, enabled)
                        except Exception as exc:
                            self._fail(exc)
                            self.stop(0)
                    self._trainer = factory(self.scene, demo=mode == 'demo', replay=mode == 'replay',
                                            on_snapshot=snapshot_callback, **options)
                    self._trainer.start()
                    self._thread = self._trainer._thread
                if self.status == 'STARTING' and not self._stopping:
                    self.lifecycle('RUNNING')
            except Exception as exc:
                try:
                    self._fail(exc)
                except Exception as failure:
                    exc.add_note('Failure reporting failed: ' + str(failure))
                try:
                    if self.alive(): self.stop(0)
                except Exception as cleanup:
                    exc.add_note('Cleanup request failed: ' + str(cleanup))
                raise

    def command(self, command):
        name, args = command['command'], command.get('arguments', {})
        with self._condition:
            if name == 'run.stop': return self.stop(float(args.get('timeout_seconds', 120)))
            if name == 'session.sync': return self.lifecycle(self.status)
            if (self.status == 'DRAINING' or (self._stop_worker is not None and self._stop_worker.is_alive())
                    or (self._stopping and self.alive())):
                raise ValueError('Backend cleanup is still finishing')
            if name == 'run.start': return self.start(args.get('mode', self.mode), args.get('options', {}))
            if name == 'channel.set':
                topic, enabled = args.get('topic'), args.get('enabled')
                runtime = self._trainer.runtime if self._trainer is not None else None
                if type(enabled) is not bool or runtime is None or topic not in runtime.topics():
                    raise ValueError('Channel subscription requires an available SceneRuntime topic and boolean enabled')
                self._channel_states[topic] = enabled
                return self.lifecycle(self.status)
            if name in ('scene.load', 'run.reset'):
                if (self.status not in ('READY', 'STOPPED', 'FAILED') or self.alive()
                        or (self._stop_worker is not None and self._stop_worker.is_alive())):
                    raise ValueError('Backend still active')
                if not self.synchronization_state()['cleanup_confirmed']:
                    raise ValueError('Backend cleanup is still finishing')
                if name == 'scene.load':
                    candidate = self._load(args.get('scene', args.get('scene_path')))
                    candidate = configured_scene(candidate, args.get('scene_overrides', {}))
                    payload = dict(self._state_payload(), run_status='READY', run_id=None,
                                   state_revision=self.state_revision + 1, reason=None,
                                   channel_states={}, scene=candidate.to_dict())
                    encode_message(dict(protocol_version=1, type='lifecycle', session_id=self.session_id,
                                        sequence=0, payload=payload))
                    self.scene = candidate
                self._channel_states.clear()
                self.clear_observations()
                self._overflowed = False
                self.run_id = None
                self.lifecycle('READY'); return
            if self.mode == 'formal_training': raise ValueError('Formal CLI does not support pause or single step')
            if name == 'run.pause' and self.status == 'RUNNING':
                self._paused = True; return
            if name == 'run.resume' and self.status == 'PAUSED':
                self._paused = False; self._permits = 0; self._condition.notify_all(); self.lifecycle('RUNNING'); return
            if name == 'run.step' and self.status == 'PAUSED':
                if self._permits: raise ValueError('Single step already pending')
                self._permits = 1; self._condition.notify_all(); self.lifecycle('PAUSED'); return
            raise ValueError('Command not valid in current state')

    def alive(self):
        if self._trainer is not None and self._trainer.running: return True
        if self._thread is not None and self._thread.is_alive(): return True
        if self._formal_processes is not None:
            return self._formal_processes.alive()
        if self._process is not None:
            self._process.poll()
            try: os.killpg(self._process.pid, 0); return True
            except ProcessLookupError: pass
            except PermissionError:
                # macOS may report EPERM briefly for an exited group whose
                # remaining member is no longer signalable.
                return self._process.returncode is None
        return False

    def _wait_until_stopped(self, timeout_seconds):
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while self.alive() and time.monotonic() < deadline:
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        return not self.alive()

    def _signal_process_group(self, signum):
        if self._formal_processes is not None:
            return self._formal_processes.signal(signum)
        try:
            if signum == signal.SIGINT:
                if self._process.poll() is not None:
                    return False
                self._process.send_signal(signum)
                return True
            os.killpg(self._process.pid, signum)
        except ProcessLookupError:
            return False

    def stop(self, timeout_seconds=120):
        try:
            valid_timeout = (type(timeout_seconds) in (int, float) and math.isfinite(timeout_seconds)
                             and 0 <= timeout_seconds <= threading.TIMEOUT_MAX)
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            raise ValueError('Stop timeout must be finite, nonnegative and within threading.TIMEOUT_MAX')
        with self._condition:
            if self._stop_worker is not None and self._stop_worker.is_alive(): return
            if self.status in {'STARTING', 'RUNNING', 'PAUSED', 'DRAINING'}:
                error = self._backend_error()
                if error:
                    self._fail(error)
            if not self.alive():
                if self.status == 'FAILED':
                    self.synchronization_state()
                    return
                if self.status in ('STARTING', 'RUNNING', 'PAUSED'):
                    self.lifecycle('DRAINING')
                if self.status == 'DRAINING': self.lifecycle('STOPPED')
                else: self.synchronization_state()
                return
            self._stopping = True
            self._condition.notify_all()
            if self.status != 'FAILED': self.lifecycle('DRAINING')
            def drain():
                try:
                    if self._trainer is not None:
                        self._trainer.stop(timeout=timeout_seconds)
                    if self._formal_process() is not None:
                        request_failed = False
                        try:
                            self._formal_cancel_requested = bool(self._signal_process_group(signal.SIGINT))
                        except Exception as exc:
                            request_failed = True
                            reason = f'Graceful stop failed: {exc}'
                            if '应用层收尾未确认' not in reason:
                                reason += '; 应用层收尾未确认'
                            self._fail(reason)
                        graceful = not request_failed and self._wait_until_stopped(timeout_seconds)
                        if not graceful:
                            self._signal_process_group(signal.SIGTERM)
                            stopped = self._wait_until_stopped(self.kill_grace_seconds)
                            if not stopped:
                                self._signal_process_group(getattr(signal, 'SIGKILL', signal.SIGTERM))
                                stopped = self._wait_until_stopped(self.kill_grace_seconds)
                            reason = 'Graceful stop failed' if request_failed else 'Stop timeout'
                            outcome = ('formal backend was force terminated; 应用层收尾未确认'
                                       if stopped else 'formal process cleanup unconfirmed')
                            self._fail(reason + ': ' + outcome)
                    with self._condition:
                        # Poll again after waiting: errors can arrive during shutdown.
                        alive = self.alive()
                        error = self._backend_error()
                        if error:
                            self._fail(error)
                        if alive:
                            self._fail('Stop timeout: backend cleanup unconfirmed; restart blocked')
                        else:
                            resource_error = self._trainer_cleanup_error()
                            if resource_error:
                                self._fail(resource_error)
                            self.lifecycle('FAILED' if self.status == 'FAILED' else 'STOPPED')
                except Exception as exc: self._fail('Cleanup failed: ' + (str(exc) or type(exc).__name__))
            self._stop_worker = threading.Thread(target=drain, name='wfrl-bridge-drain', daemon=True)
            self._stop_worker.start()
