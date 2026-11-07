"""Local process ownership across process-group changes and reparenting."""
import math
import os
import subprocess
import threading
import time
import uuid

import psutil


class OwnedProcesses:
    """Signal process identities; inherited run identity also locates orphaned children."""
    ENV_KEY = 'WFRL_PROCESS_RUN_TOKEN'

    def __init__(self, *, env_key='WFRL_PROCESS_RUN_TOKEN'):
        self.ENV_KEY = env_key
        self.token = uuid.uuid4().hex
        self.process = None
        self.root = None
        self._owned = set()
        self._known = set()
        self._pending = set()
        self._lock = threading.RLock()
        self._started = time.time()
        self._last_scan = float('-inf')
        self._cleaned = False
        self._uncertain = False

    @classmethod
    def recover(cls, identities, *, token, env_key):
        """Recheck recorded PID/create-time pairs and the inherited run token."""
        if not isinstance(token, str) or not token or not isinstance(identities, list) or not identities:
            raise ValueError('Missing process ownership identities')
        owned = cls(env_key=env_key)
        for identity in identities:
            pid, created = identity['pid'], identity['create_time']
            if (type(pid) is not int or pid <= 0 or type(created) not in (int, float)
                    or not math.isfinite(created) or created <= 0):
                raise ValueError('Invalid process ownership identity')
            owned._known.add((pid, created))
        owned.token = token
        owned._pending = owned._known.copy()
        owned._started = min(created for _, created in owned._known)
        return owned

    def start(self, argv, *, cwd, env, **options):
        self.process = subprocess.Popen(argv, cwd=cwd, env=dict(env, **{self.ENV_KEY: self.token}),
                                        start_new_session=True, **options)
        try:
            self.root = psutil.Process(self.process.pid)
            self._owned.add(self.root)
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
        user_attribute = 'uids' if hasattr(os, 'getuid') else 'username'
        user = os.getuid() if user_attribute == 'uids' else psutil.Process().username()
        attributes = ['pid', 'create_time', user_attribute]
        for process in psutil.process_iter(attributes):
            try:
                if not self._exists(process):
                    continue
                created = process.info['create_time']
                if created is not None and created < self._started - 1:
                    continue
                identity = process.info[user_attribute]
                if identity is not None and (identity.real if user_attribute == 'uids' else identity) != user:
                    continue
                if process.environ().get(self.ENV_KEY) == self.token:
                    self._owned.add(process)
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                # An unreadable environment alone is not evidence of run ownership.
                if process in self._owned:
                    self._uncertain = True

    def _remember(self, *, force=False):
        if (self.process is None and not self._known) or self._cleaned:
            return
        self._uncertain = False
        for identity in tuple(self._pending):
            pid, created = identity
            try:
                process = psutil.Process(pid)
                if process.create_time() == created and self._exists(process):
                    self._owned.add(process)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self._uncertain = True
                continue
            self._pending.discard(identity)
        for process in tuple(self._owned):
            if not self._exists(process):
                self._owned.discard(process)
                continue
            try:
                self._owned.update(process.children(recursive=True))
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self._uncertain = True
        exited = self.process is None or self.process.poll() is not None
        # An exited launcher can leave children that changed both parent and PGID.
        # Only this run's inherited identity can claim an otherwise unrelated PID.
        scanned = False
        if exited and (force or not self._owned or time.monotonic() - self._last_scan >= .1):
            self._scan_orphans()
            scanned = True
        self.identities()
        self._owned = {p for p in self._owned if self._exists(p)}
        if exited and not self._owned:
            if not scanned:
                self._scan_orphans()
                self._owned = {p for p in self._owned if self._exists(p)}
            self._cleaned = not self._owned and not self._pending and not self._uncertain

    def status(self):
        with self._lock:
            try:
                self._remember()
            except (psutil.Error, OSError):
                self._uncertain = True
            if self._owned or (self.process is not None and self.process.poll() is None):
                return 'LIVE'
            return 'UNKNOWN' if self._uncertain or self._pending else 'EMPTY_CONFIRMED'

    def alive(self):
        return self.status() != 'EMPTY_CONFIRMED'

    def identities(self):
        with self._lock:
            for process in tuple(self._owned):
                try:
                    self._known.add((process.pid, process.create_time()))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            return [dict(pid=pid, create_time=created) for pid, created in sorted(self._known)]

    def owns(self, identity):
        with self._lock:
            self._remember(force=True)
            return any(process.pid == identity['pid'] and process.create_time() == identity['create_time']
                       and self._exists(process) for process in self._owned)

    def signal(self, signum):
        with self._lock:
            self._remember(force=True)
            for process in tuple(self._owned):
                try:
                    process.send_signal(signum)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass


class FormalProcesses(OwnedProcesses):
    def __init__(self):
        super().__init__(env_key='WFRL_FORMAL_RUN_TOKEN')
