"""Cancelable environment probes. Workers never import or access bpy.

FAST.Farm is deliberately not invoked: this integration has no established safe
version-only command for its custom MPI executable. CHECKED means executable
access only, never proven simulator availability. Bridge health belongs to the
protocol handshake, not an executable or TCP reachability test.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time


@dataclass(frozen=True)
class HealthConfig:
    project_dir: str = ''
    backend_python: str = ''
    mpi_path: str = ''
    fastfarm_path: str = ''


@dataclass(frozen=True)
class HealthResult:
    component: str
    status: str
    detail: str


class HealthChecker:
    def __init__(self, timeout=2.0):
        if timeout <= 0 or timeout > 30:
            raise ValueError('Probe timeout must be between 0 and 30 seconds')
        self.timeout = timeout
        self._thread = None
        self._cancel = threading.Event()
        self._results = queue.SimpleQueue()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self, config, blender_version=()):
        """Start once idle. Snapshot Blender data on the caller's main thread."""
        if self.running:
            return False
        self.poll()
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(config, tuple(blender_version)), daemon=True, name='WFRL-health')
        self._thread.start()
        return True

    def cancel(self):
        """Signal cleanup; do not join or wait on the UI/main thread."""
        self._cancel.set()

    def poll(self):
        results = []
        while True:
            try:
                results.append(self._results.get_nowait())
            except queue.Empty:
                return results

    def _run(self, config, blender_version):
        try:
            supported = blender_version >= (5, 2, 0)
            self._results.put(HealthResult('blender', 'READY' if supported else 'UNSUPPORTED',
                '.'.join(map(str, blender_version)) if blender_version else 'Blender version not supplied'))
            for name, path in (('python', config.backend_python), ('mpi', config.mpi_path), ('fastfarm', config.fastfarm_path)):
                if self._cancel.is_set():
                    break
                self._results.put(self._probe(name, path))
        except Exception as exc:
            self._results.put(HealthResult('environment', 'ERROR', str(exc)))

    def _probe(self, component, path):
        if not path:
            return HealthResult(component, 'MISSING', 'Not configured; optional for Local Demo')
        file = Path(path)
        if not file.is_absolute():
            return HealthResult(component, 'INVALID', 'Configure an absolute executable path')
        if not file.is_file() or not os.access(file, os.X_OK):
            return HealthResult(component, 'INVALID', 'Executable is missing or not executable')
        if component == 'fastfarm':
            return HealthResult(component, 'CHECKED', 'Executable access checked; version and runtime capability unverified (not launched)')
        # Both commands are explicit, shell-free version queries. No simulation,
        # project imports, MPI ranks or training jobs are started by these probes.
        argv = [str(file), '--version']
        process = None
        try:
            # File-backed output avoids pipe deadlocks and unbounded RAM use.
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=(os.name == 'posix'))
                deadline = time.monotonic() + self.timeout
                while process.poll() is None:
                    if self._cancel.wait(.025):
                        return HealthResult(component, 'CANCELLED', 'Environment check cancelled')
                    if time.monotonic() >= deadline:
                        return HealthResult(component, 'TIMEOUT', 'Version probe timed out')
                output.seek(0)
                raw = output.read(65537)
                detail = raw[:65536].decode('utf-8', errors='replace').strip()
                if len(raw) > 65536:
                    detail += '\n[Probe output exceeds 64 KiB; remaining output omitted.]'
                if process.returncode != 0 or not detail:
                    return HealthResult(component, 'ERROR', f'Version probe exited {process.returncode}: {detail}')
                if component == 'python':
                    version = re.match(r'Python (\d+)\.(\d+)(?:\.|$)', detail)
                    if not version or tuple(map(int, version.groups())) < (3, 11):
                        return HealthResult(component, 'UNSUPPORTED', detail + '; requires Python >=3.11')
                return HealthResult(component, 'READY', detail)
        except OSError as exc:
            return HealthResult(component, 'ERROR', str(exc))
        finally:
            if process is not None:
                # Kill the whole probe process group, including children retaining
                # inherited output descriptors. Reaping occurs only on this worker.
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    elif process.poll() is None:
                        process.kill()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    pass
