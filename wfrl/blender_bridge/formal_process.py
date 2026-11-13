"""Local process ownership across process-group changes and reparenting."""
import math
import os
import signal as signals
import subprocess
import sys
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
        self.rollback_error = None
        self.stop_request_error = None

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

    def start(self, argv, *, cwd, env, on_started=None, **options):
        if sys.platform == 'win32':
            # Windows has no sessions. A new process group lets CTRL_BREAK_EVENT
            # reach this tree without the caller's console group.
            options['creationflags'] = options.get('creationflags', 0) | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self.process = subprocess.Popen(argv, cwd=cwd, env=dict(env, **{self.ENV_KEY: self.token}),
                                            start_new_session=sys.platform != 'win32', **options)
            try:
                self.root = psutil.Process(self.process.pid)
                self._owned.add(self.root)
            except psutil.NoSuchProcess:
                pass
            if on_started is not None:
                on_started()
        except BaseException as exc:
            try:
                self._rollback_start()
            except Exception as cleanup:
                self.rollback_error = 'Startup rollback failed: ' + (str(cleanup) or type(cleanup).__name__)
                exc.add_note(self.rollback_error)
            raise
        return self.process

    def _rollback_start(self):
        """Retain the Popen handle even when process registration never succeeded."""
        if self.process is None:
            return
        errors = []
        def pending():
            try:
                return self.alive()
            except Exception as exc:
                self._uncertain = True
                if str(exc) not in errors:
                    errors.append(str(exc))
                return True
        for signum in (signals.SIGTERM, getattr(signals, 'SIGKILL', signals.SIGTERM)):
            try:
                self.signal(signum)
            except Exception as exc:
                errors.append(str(exc))
            # Registration or descendant inspection must not prevent root cleanup.
            try:
                if self.process.poll() is None:
                    if signum == signals.SIGTERM:
                        self.process.terminate()
                    else:
                        self.process.kill()
            except Exception as exc:
                errors.append(str(exc))
            deadline = time.monotonic() + 1.0
            while pending() and time.monotonic() < deadline:
                time.sleep(.02)
            if not pending():
                return
        detail = ': ' + '; '.join(errors) if errors else ''
        raise RuntimeError('owned processes remain or their cleanup is unconfirmed' + detail)

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
        if self.root is None and self.process is not None and self.process.poll() is None:
            try:
                self.root = psutil.Process(self.process.pid)
                self._owned.add(self.root)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied:
                self._uncertain = True
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

    def _request_interrupt(self):
        if self.process is None or self.process.poll() is not None:
            return False
        signum = signals.CTRL_BREAK_EVENT if sys.platform == 'win32' else signals.SIGINT
        self.process.send_signal(signum)
        return True

    def signal(self, signum):
        """Request graceful stop from the controller; force signals cover the tree."""
        with self._lock:
            if signum == signals.SIGINT:
                try:
                    return self._request_interrupt()
                except Exception as exc:
                    request = 'CTRL_BREAK' if sys.platform == 'win32' else 'SIGINT'
                    self.stop_request_error = f'{request} stop request failed: {exc}; 应用层收尾未确认'
                    raise RuntimeError(self.stop_request_error) from exc
            try:
                self._remember(force=True)
            except (psutil.Error, OSError):
                self._uncertain = True
            if sys.platform == 'win32':
                signum = signals.SIGTERM
            for process in tuple(self._owned):
                try:
                    process.send_signal(signum)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if self.process is not None and self.process.poll() is None:
                try:
                    self.process.send_signal(signum)
                except ProcessLookupError:
                    pass


class FormalProcesses(OwnedProcesses):
    def __init__(self):
        super().__init__(env_key='WFRL_FORMAL_RUN_TOKEN')
        self._training_command = None

    def start(self, argv, **options):
        # Match the Python worker's arguments, excluding the MPI launcher's prefix.
        self._training_command = next((tuple(argv[index:]) for index, arg in enumerate(argv)
                                       if os.path.basename(arg) == 'train_fastfarm.py'), None)
        return super().start(argv, **options)

    def _request_interrupt(self):
        if sys.platform == 'win32' or self._training_command is None:
            return super()._request_interrupt()
        self._remember(force=True)
        workers = []
        for process in tuple(self._owned):
            try:
                if tuple(process.cmdline()[1:]) == self._training_command and self._exists(process):
                    workers.append(process)
            except psutil.NoSuchProcess:
                continue
        if len(workers) != 1:
            raise RuntimeError('Cannot identify exactly one formal training main process')
        workers[0].send_signal(signals.SIGINT)
        return True
