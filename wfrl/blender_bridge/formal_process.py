"""Local Formal CLI ownership across process-group changes and reparenting."""
import os
import subprocess
import threading
import time
import uuid

import psutil


class FormalProcesses:
    """Signal process identities; inherited run identity also locates orphaned children."""
    ENV_KEY = 'WFRL_FORMAL_RUN_TOKEN'

    def __init__(self):
        self.token = uuid.uuid4().hex
        self.process = None
        self._owned = set()
        self._lock = threading.RLock()
        self._started = time.time()
        self._last_scan = float('-inf')
        self._cleaned = False

    def start(self, argv, *, cwd, env):
        self.process = subprocess.Popen(argv, cwd=cwd, env=dict(env, **{self.ENV_KEY: self.token}),
                                        start_new_session=True)
        try:
            self._owned.add(psutil.Process(self.process.pid))
        except psutil.NoSuchProcess:
            pass
        return self.process

    @staticmethod
    def _exists(process):
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            return True

    def _scan_orphans(self):
        self._last_scan = time.monotonic()
        for process in psutil.process_iter(['pid', 'create_time', 'uids']):
            try:
                created, uids = process.info['create_time'], process.info['uids']
                if created is None or uids is None or created < self._started - 1 or uids.real != os.getuid():
                    continue
                if process.environ().get(self.ENV_KEY) == self.token:
                    self._owned.add(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _remember(self, *, force=False):
        if self.process is None or self._cleaned:
            return
        for process in tuple(self._owned):
            if not self._exists(process):
                self._owned.discard(process)
                continue
            try:
                self._owned.update(process.children(recursive=True))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        exited = self.process.poll() is not None
        # An exited launcher can leave children that changed both parent and PGID.
        # Only this run's inherited identity can claim an otherwise unrelated PID.
        scanned = False
        if exited and (force or not self._owned or time.monotonic() - self._last_scan >= .1):
            self._scan_orphans()
            scanned = True
        self._owned = {p for p in self._owned if self._exists(p)}
        if exited and not self._owned:
            if not scanned:
                self._scan_orphans()
                self._owned = {p for p in self._owned if self._exists(p)}
            self._cleaned = not self._owned

    def alive(self):
        with self._lock:
            self._remember()
            return bool(self._owned) or (self.process is not None and self.process.poll() is None)

    def signal(self, signum):
        with self._lock:
            self._remember(force=True)
            for process in tuple(self._owned):
                try:
                    process.send_signal(signum)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
