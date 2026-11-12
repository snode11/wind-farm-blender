"""Cancellable POSIX MPI worker, with one acknowledged snapshot in flight.

The simulator's fixed-budget MPI protocol has no early-stop message. Inherited
run identity and creation-time checks retain ownership when descendants change
sessions or outlive their launcher, without claiming another run's processes.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from .formal_process import OwnedProcesses
from wfrl.mpi_runtime import launcher_environment


class IsolatedTrainer:
    def __init__(self, scene, on_snapshot, session_id='', mpi_launcher=None, **options):
        self.scene, self.callback, self.options = scene, on_snapshot, options
        self.session_id, self.mpi_launcher = session_id, mpi_launcher
        self.error = None
        self._thread = self._process = self._directory = None
        self._write_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._root_process = None
        self._processes = None
        self._listener = self._connection = None
        self._cancelled = False
        self._channels = []
        self._channel_states = {}
        self._cases = set()
        self.runtime = self

    def channel_table(self):
        return self._channels

    def topics(self):
        return [row[0] for row in self._channels]

    def set_enabled(self, topic, enabled):
        if self._channel_states.get(topic) != enabled:
            self._channel_states[topic] = enabled
            self._send('channel', topic=topic, enabled=enabled)

    def _worker_command(self):
        launcher = shutil.which(self.mpi_launcher or os.environ.get('WFCRL_MPIEXEC') or 'mpiexec')
        if not launcher:
            raise RuntimeError('Interactive FAST.Farm requires an executable mpiexec')
        return [launcher, '-n', '1', sys.executable, '-m',
                'wfrl.blender_bridge.trainer_worker', str(Path(self._directory.name) / 'config.json')]

    def _remember_children(self):
        if self._processes is not None:
            self._processes.alive()

    def _group_alive(self):
        if self._process is None:
            return False
        return self._processes is not None and self._processes.alive()

    @property
    def running(self):
        return bool((self._thread and self._thread.is_alive()) or self._group_alive())

    def start(self):
        if self.running:
            return
        from wfrl.scene.schema import dump_scene
        self.error = None
        self._cancelled = False
        self._channels = []
        self._channel_states.clear()
        self._thread = self._process = self._processes = None
        self._listener = self._connection = None
        self._directory = tempfile.TemporaryDirectory(prefix='wfrl-interactive-')
        try:
            scene_path = str(Path(self._directory.name) / 'scene.yaml')
            dump_scene(self.scene, scene_path)
            socket_path = str(Path(self._directory.name) / 'ipc')
            self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._listener.bind(socket_path)
            self._listener.listen(1)
            self._listener.settimeout(.2)
            config = dict(scene=scene_path, session_id=self.session_id, options=self.options, socket=socket_path)
            (Path(self._directory.name) / 'config.json').write_text(json.dumps(config), encoding='utf-8')
            root = Path(__file__).resolve().parents[2]
            self._processes = OwnedProcesses(env_key='WFRL_INTERACTIVE_RUN_TOKEN')
            self._process = self._processes.start(self._worker_command(), cwd=root,
                env=launcher_environment(), stdin=subprocess.DEVNULL)
            self._root_process = self._processes.root
            self._thread = threading.Thread(target=self._read, name='wfrl-mpi-worker', daemon=True)
            self._thread.start()
        except Exception as exc:
            if self._processes is not None:
                self._process = self._processes.process
            try:
                if self._process is not None:
                    self.stop(timeout=0)
                else:
                    self._cleanup()
            except Exception as cleanup:
                exc.add_note('Interactive worker cleanup failed: ' + str(cleanup))
            finally:
                if self._listener is not None:
                    try:
                        self._listener.close()
                    except OSError as cleanup:
                        exc.add_note('Interactive listener cleanup failed: ' + str(cleanup))
            raise

    def _record_error(self, message):
        if not self.error:
            self.error = message
        elif message not in self.error:
            self.error += '; ' + message

    def _send(self, command, **values):
        with self._write_lock:
            if self._connection is None:
                return
            try:
                self._connection.sendall((json.dumps(dict(command=command, **values)) + '\n').encode())
            except OSError:
                if not self._cancelled:
                    self._record_error('Interactive worker command connection closed')

    def _read(self):
        try:
            while self._process.poll() is None:
                try:
                    self._connection, _ = self._listener.accept()
                    break
                except socket.timeout:
                    continue
            if self._connection is None:
                if not self._cancelled:
                    self.error = f'Interactive worker exited before connecting: {self._process.returncode}'
                return
            if self._cancelled:
                self._send('stop')
            with self._connection.makefile('r', encoding='utf-8') as stream:
                for line in stream:
                    self._remember_children()
                    record = json.loads(line)
                    if record['kind'] == 'snapshot':
                        self._channels = record.get('channels', [])
                        if record.get('case_dir'):
                            self._cases.add(record['case_dir'])
                        if not self._cancelled:
                            self.callback(SimpleNamespace(payload=record['payload'], wire_events=record['events'],
                                                          training=record.get('training')))
                            self._send('continue')
                    elif record['kind'] == 'error':
                        self._record_error(record['message'])
            code = self._process.wait()
            if code and not self._cancelled:
                self.error = self.error or f'Interactive worker exited {code}'
        except Exception as exc:
            if not self._cancelled:
                self._record_error(f'Interactive worker transport: {exc}')
        finally:
            for resource in (self._connection, self._listener):
                if resource is not None:
                    try:
                        resource.close()
                    except OSError as exc:
                        self._record_error(f'Interactive worker socket cleanup failed: {exc}')
            try:
                if not self._group_alive():
                    self._cleanup()
            except Exception as exc:
                self._record_error(f'Interactive worker cleanup failed: {exc}')

    def _cleanup(self):
        with self._cleanup_lock:
            self._cleanup_files()

    def _cleanup_files(self):
        # Only this worker's reported immediate FAST.Farm case directories.
        root = Path(__file__).resolve().parents[2] / '__simul__' / 'fastfarm'
        for name in tuple(self._cases):
            path = Path(name).resolve()
            if path.parent == root.resolve() and path.is_dir():
                shutil.rmtree(path)
            self._cases.discard(name)
        if self._directory is not None:
            self._directory.cleanup()

    def stop(self, timeout=120):
        self._remember_children()
        self._cancelled = True
        self._send('stop')
        # Allow short natural cleanup; never drain a whole long training budget.
        deadline = time.monotonic() + min(2.0, max(0., timeout))
        while self.running and time.monotonic() < deadline:
            time.sleep(.02)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not self._group_alive():
                break
            # MPI ranks may create their own process groups. Track process
            # identity (PID plus creation time), not a global name or bare PID.
            self._processes.signal(sig)
            deadline = time.monotonic() + 3.0
            while self._group_alive() and time.monotonic() < deadline:
                time.sleep(.02)
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(3)
        if self.running:
            raise RuntimeError('Interactive worker cleanup unconfirmed; restart blocked')
        self._cleanup()
