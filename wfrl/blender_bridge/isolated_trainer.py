"""Cancellable POSIX MPI worker, with one acknowledged snapshot in flight.

The simulator's fixed-budget MPI protocol has no early-stop message. Keeping the
Trainer and its spawned simulator in their own process group lets a user cancel
that computation without taking down the Bridge or touching another run.
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
import psutil
from types import SimpleNamespace



def launcher_environment():
    # A Bridge may itself have been launched by mpiexec. A fresh launcher must
    # not inherit the enclosing MPI job identity. Transport tuning is different:
    # it must reach both the worker and the FAST.Farm process it spawns.
    transport_keys = ('OMPI_MCA_btl', 'OMPI_MCA_pml')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('OMPI_', 'PMI_', 'PMIX_', 'OPAL_', 'PRTE_'))
           or any(key == prefix or key.startswith(prefix + '_')
                  for prefix in transport_keys)}
    if sys.platform == 'darwin':
        # These Bridge jobs and their simulator ranks run on this Mac. Open MPI
        # can select an unusable external/virtual interface for a spawned rank,
        # leaving matching Send/Recv calls stuck after the initial handshake.
        # Use loopback TCP by default; retain explicit caller transport choices.
        env.setdefault('OMPI_MCA_btl', 'self,tcp')
        if not any(key in env for key in ('OMPI_MCA_btl_tcp_if_include',
                                          'OMPI_MCA_btl_tcp_if_exclude')):
            env['OMPI_MCA_btl_tcp_if_include'] = 'lo0'
    return env


class IsolatedTrainer:
    def __init__(self, scene, on_snapshot, session_id='', mpi_launcher=None, **options):
        self.scene, self.callback, self.options = scene, on_snapshot, options
        self.session_id, self.mpi_launcher = session_id, mpi_launcher
        self.error = None
        self._thread = self._process = self._directory = None
        self._write_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._owned = set()
        self._root_process = None
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
        if self._root_process is not None:
            try:
                self._owned.update(self._root_process.children(recursive=True))
            except psutil.NoSuchProcess:
                pass

    def _group_alive(self):
        if self._process is None:
            return False
        self._remember_children()
        self._process.poll()
        return any(self._exists(p) for p in tuple(self._owned))

    @staticmethod
    def _exists(process):
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

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
        self._owned.clear()
        self._channel_states.clear()
        self._directory = tempfile.TemporaryDirectory(prefix='wfrl-interactive-')
        scene_path = str(Path(self._directory.name) / 'scene.yaml')
        dump_scene(self.scene, scene_path)
        socket_path = str(Path(self._directory.name) / 'ipc')
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(socket_path)
        self._listener.listen(1)
        self._listener.settimeout(.2)
        self._connection = None
        config = dict(scene=scene_path, session_id=self.session_id, options=self.options, socket=socket_path)
        (Path(self._directory.name) / 'config.json').write_text(json.dumps(config))
        root = Path(__file__).resolve().parents[2]
        try:
            self._process = subprocess.Popen(self._worker_command(), cwd=root,
                env=launcher_environment(), start_new_session=True, stdin=subprocess.DEVNULL)
        except Exception:
            self._directory.cleanup()
            raise
        self._root_process = psutil.Process(self._process.pid)
        self._owned.add(self._root_process)
        self._thread = threading.Thread(target=self._read, name='wfrl-mpi-worker', daemon=True)
        self._thread.start()

    def _send(self, command, **values):
        with self._write_lock:
            if self._connection is None:
                return
            try:
                self._connection.sendall((json.dumps(dict(command=command, **values)) + '\n').encode())
            except OSError:
                if not self._cancelled:
                    self.error = 'Interactive worker command connection closed'

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
            for line in self._connection.makefile('r'):
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
                    self.error = record['message']
            code = self._process.wait()
            if code and not self._cancelled:
                self.error = self.error or f'Interactive worker exited {code}'
        except Exception as exc:
            if not self._cancelled:
                self.error = f'Interactive worker transport: {exc}'
        finally:
            if self._connection:
                self._connection.close()
            if self._listener:
                self._listener.close()
            if not self._group_alive():
                self._cleanup()

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
            for process in tuple(self._owned):
                try:
                    process.send_signal(sig)
                except psutil.NoSuchProcess:
                    pass
            deadline = time.monotonic() + 3.0
            while self._group_alive() and time.monotonic() < deadline:
                time.sleep(.02)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(3)
        if self.running:
            raise RuntimeError('Interactive worker cleanup unconfirmed; restart blocked')
        self._cleanup()
