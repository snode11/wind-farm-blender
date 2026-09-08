"""Own one existing Trainer or formal CLI process; never duplicate training."""
from collections import deque
from pathlib import Path
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid
import tempfile
import queue

from .training_progress import TrainingProgressReader

from .workflow import configured_scene, validate_run_options

from .snapshot_adapter import SnapshotAdapter


FORMAL_VALUE_OPTIONS = {
    'duty_penalty', 'episode_steps', 'iters', 'load_coef', 'n_steps', 'policy',
    'resume_from', 'reward', 'reward_alpha', 'reward_ref', 'save_interval',
    'seed', 'tag', 'warmup_steps', 'wind_direction', 'wind_sched', 'wind_speed',
}
FORMAL_FLAG_OPTIONS = {'no_purge', 'obs_duty', 'recurrent'}


class BackendSession:
    def __init__(self, scene=None, trainer_factory=None, scene_loader=None,
                 max_events=2048, kill_grace_seconds=5.0, mpi_launcher=None):
        self.session_id = uuid.uuid4().hex
        self.run_id = None
        self._progress_reader = None
        self._progress_dir = None
        self._progress_events = queue.Queue(maxsize=max_events)
        self.scene = scene
        self.mode = 'demo'
        self.status = 'READY'
        self.trainer_factory = trainer_factory
        self.scene_loader = scene_loader
        self.max_events = max_events
        self.kill_grace_seconds = kill_grace_seconds
        self.mpi_launcher = mpi_launcher
        self._condition = threading.Condition(threading.RLock())
        self._events = deque()
        self._latest = None
        self._trainer = self._thread = self._process = None
        self._paused = False
        self._permits = 0
        self._stopping = False
        self._stop_worker = None
        self._channel_states = {}
        self._formal_scene_dir = None

    @property
    def capabilities(self):
        return [] if self.mode == 'formal_training' else ['pause', 'single_step']

    def emit(self, kind, payload):
        with self._condition:
            if kind == 'snapshot': self._latest = (kind, payload)
            else:
                if len(self._events) >= self.max_events:
                    self._stopping = True
                    self._condition.notify_all()
                    self._events.clear()
                    self._events.append(('error', dict(code='EVENT_OVERFLOW', message='Reliable event queue overflow; event history incomplete', fatal=True)))
                    raise RuntimeError('Reliable event queue overflow')
                self._events.append((kind, payload))

    def channel_table(self):
        runtime = getattr(self._trainer, "runtime", None)
        if runtime is None or not hasattr(runtime, "channel_table"): return []
        fidelity_map = {
            "DIRECT": "DIRECT", "直读": "DIRECT",
            "DERIVED": "EXPORTED", "导出": "EXPORTED",
            "SYNTH": "SYNTH", "合成": "SYNTH",
        }
        return [[topic, sensor, fidelity_map.get(str(fidelity), str(fidelity)), enabled, provenance]
                for topic, sensor, fidelity, enabled, provenance in runtime.channel_table()]

    def lifecycle(self, status, reason=None):
        with self._condition:
            self.status = status
            self.emit('lifecycle', dict(run_status=status, reason=reason, mode=self.mode, run_id=self.run_id,
                                        capabilities=self.capabilities,
                                        scene=self.scene.to_dict() if hasattr(self.scene, "to_dict") else None,
                                        channel_states=dict(self._channel_states), channel_table=self.channel_table()))

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

    def poll(self):
        with self._condition:
            self._collect_progress()
            if self.status in ('RUNNING', 'PAUSED', 'STARTING'):
                finished = self._thread is not None and not self._thread.is_alive()
                if self._process is not None:
                    finished = self._process.poll() is not None
                if finished:
                    self._collect_progress(final=True)
                    error = getattr(self._trainer, 'error', None)
                    if self._process is not None and self._process.returncode: error = f'Formal process exited {self._process.returncode}'
                    if self._process is not None and self.alive():
                        error = error or 'Formal launcher exited while child processes remained'
                        self.lifecycle('FAILED', error)
                        self.stop(0)
                    else:
                        if not error: self.lifecycle('DRAINING')
                        self.lifecycle('FAILED' if error else 'STOPPED', error)
            if self.status in {'STOPPED', 'FAILED'} and not self.alive() and self._formal_scene_dir is not None:
                self._formal_scene_dir.cleanup()
                self._formal_scene_dir = None
            result = list(self._events); self._events.clear()
            if self._latest is not None: result.append(self._latest); self._latest = None
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
            if self._stop_worker is not None and self._stop_worker.is_alive():
                raise ValueError('Backend cleanup is still finishing')
            if mode not in ('demo', 'interactive_training', 'formal_training', 'replay'): raise ValueError('Unknown mode')
            if getattr(self, 'fake', False) and mode != 'demo': raise ValueError('Fake backend supports SYNTH demo only')
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
            self.run_id = uuid.uuid4().hex
            self.lifecycle('STARTING')
            try:
                if mode == 'formal_training':
                    if not path: raise ValueError('Formal training requires options.scene path')
                    if getattr(self.scene, 'backend', None) != 'fastfarm':
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
                        try: self._progress_events.put_nowait(('error', payload))
                        except queue.Full:
                            # Reserve visibility by replacing one queued statistic.
                            try: self._progress_events.get_nowait()
                            except queue.Empty: pass
                            self._progress_events.put_nowait(('error', payload))
                    self._progress_reader = TrainingProgressReader(progress_path, self.run_id,
                        lambda record: self._progress_events.put_nowait(('training_stats', record)), observability_error)
                    self._process = subprocess.Popen(argv, cwd=root, start_new_session=True)
                    self._progress_reader.start()
                else:
                    factory = self.trainer_factory
                    if factory is None:
                        if getattr(self.scene, 'backend', None) == 'floris':
                            if mode != 'demo': raise ValueError('FLORIS supports backend demo only; existing Trainer requires FAST.Farm')
                            from .floris_session import FlorisDemoSession
                            factory = FlorisDemoSession
                        else:
                            from wfrl.studio.trainer import Trainer
                            factory = Trainer
                    if set(options) & {'on_snapshot', 'demo', 'replay'}: raise ValueError('Reserved Trainer options')
                    adapter = SnapshotAdapter(self.scene, self.session_id, mode)
                    def snapshot_callback(snapshot):
                        try:
                            with self._condition:
                                if self._stopping: return
                                runtime = getattr(self._trainer, 'runtime', None)
                                if runtime is not None:
                                    for topic, enabled in self._channel_states.items():
                                        runtime.set_enabled(topic, enabled)
                                payload = adapter.encode(snapshot)
                                payload['run_id'] = self.run_id
                                payload['channel_table'] = self.channel_table()
                                self.emit('snapshot', payload)
                                if self._paused and self.status == 'RUNNING': self.lifecycle('PAUSED')
                                for event in adapter.safety_events(snapshot): self.emit('safety_event', event)
                                while self._paused and not self._stopping and self._permits == 0:
                                    self._condition.wait()
                                if self._permits: self._permits -= 1
                                if runtime is not None:
                                    for topic, enabled in self._channel_states.items():
                                        runtime.set_enabled(topic, enabled)
                        except Exception as exc:
                            self.lifecycle('FAILED', str(exc))
                            self.stop(0)
                    self._trainer = factory(self.scene, demo=mode == 'demo', replay=mode == 'replay',
                                            on_snapshot=snapshot_callback, **options)
                    self._trainer.start()
                    self._thread = self._trainer._thread
                self.lifecycle('RUNNING')
            except Exception as exc:
                self._collect_progress(final=True)
                self.lifecycle('FAILED', str(exc))
                if self.alive(): self.stop(0)
                if self._formal_scene_dir is not None and not self.alive():
                    self._formal_scene_dir.cleanup()
                    self._formal_scene_dir = None
                raise

    def command(self, command):
        name, args = command['command'], command.get('arguments', {})
        with self._condition:
            if name == 'run.start': return self.start(args.get('mode', self.mode), args.get('options', {}))
            if name == 'run.stop': return self.stop(float(args.get('timeout_seconds', 120)))
            if name == 'session.sync': return self.lifecycle(self.status)
            if name == 'channel.set':
                topic, enabled = args.get('topic'), args.get('enabled')
                runtime = getattr(self._trainer, 'runtime', None)
                if type(enabled) is not bool or runtime is None or topic not in runtime.topics():
                    raise ValueError('Channel subscription requires an available SceneRuntime topic and boolean enabled')
                self._channel_states[topic] = enabled
                return self.lifecycle(self.status)
            if name in ('scene.load', 'run.reset'):
                if (self.status not in ('READY', 'STOPPED', 'FAILED') or self.alive()
                        or (self._stop_worker is not None and self._stop_worker.is_alive())):
                    raise ValueError('Backend still active')
                if name == 'scene.load':
                    candidate = self._load(args.get('scene', args.get('scene_path')))
                    self.scene = configured_scene(candidate, args.get('scene_overrides', {}))
                self._channel_states.clear()
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
        if self._thread is not None and self._thread.is_alive(): return True
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
        try:
            os.killpg(self._process.pid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(self, timeout_seconds=120):
        if timeout_seconds < 0: raise ValueError('Stop timeout must be nonnegative')
        with self._condition:
            if self._stop_worker is not None and self._stop_worker.is_alive(): return
            if not self.alive():
                self._collect_progress(final=True)
                if self.status == 'FAILED': return
                if self.status in ('STARTING', 'RUNNING', 'PAUSED'):
                    self.lifecycle('DRAINING')
                if self.status == 'DRAINING': self.lifecycle('STOPPED')
                return
            self._stopping = True
            self._condition.notify_all()
            if self.status != 'FAILED': self.lifecycle('DRAINING')
            def drain():
                try:
                    if self._trainer is not None: self._trainer.stop(timeout=timeout_seconds)
                    if self._process is not None:
                        # Signal only our isolated process group, never a global MPI process.
                        self._signal_process_group(signal.SIGINT)
                        graceful = self._wait_until_stopped(timeout_seconds)
                        if not graceful:
                            self._signal_process_group(signal.SIGTERM)
                            stopped = self._wait_until_stopped(self.kill_grace_seconds)
                            if not stopped:
                                self._signal_process_group(signal.SIGKILL)
                                stopped = self._wait_until_stopped(self.kill_grace_seconds)
                            if stopped:
                                with self._condition: self._collect_progress(final=True)
                                self.lifecycle('FAILED', 'Stop timeout: formal backend was force terminated')
                                return
                    if self.alive():
                        self.lifecycle('FAILED', 'Stop timeout: backend cleanup unconfirmed; restart blocked')
                    else:
                        with self._condition: self._collect_progress(final=True)
                        if self.status != 'FAILED': self.lifecycle('STOPPED')
                except Exception as exc: self.lifecycle('FAILED', 'Cleanup failed: ' + str(exc))
            self._stop_worker = threading.Thread(target=drain, name='wfrl-bridge-drain', daemon=True)
            self._stop_worker.start()
