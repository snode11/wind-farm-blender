"""Separate launcher process ownership from the Bridge's current protocol session."""
import json
import os
from pathlib import Path
import tempfile

RUN_TOKEN = 'WFRL_LAUNCHER_RUN_TOKEN'
BINDING_FILE = 'WFRL_LAUNCHER_BINDING_FILE'
OWNER_SCHEMA = 'wfrl.launcher-owner.v1'
BINDING_SCHEMA = 'wfrl.bridge-binding.v1'


def atomic_write(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def process_identity(pid=None):
    import psutil
    process = psutil.Process(os.getpid() if pid is None else pid)
    return dict(pid=process.pid, create_time=process.create_time())


def binding_path(owner_file):
    return Path(str(owner_file) + '.bridge.json')


def read_binding(owner_file, owner, *, owns=None):
    """Accept only a live Bridge identity belonging to the fixed launcher instance."""
    import psutil
    try:
        binding = json.loads(binding_path(owner_file).read_text(encoding='utf-8'))
        identity = binding['bridge']
        if (binding['schema'] != BINDING_SCHEMA or binding['run_token'] != owner['run_token']
                or binding['host'] != owner['host'] or binding['port'] != owner['port']
                or not isinstance(binding['session_id'], str) or not binding['session_id']
                or type(binding['session_generation']) is not int or binding['session_generation'] < 0
                or type(identity['pid']) is not int or identity['pid'] <= 0):
            return None
        process = psutil.Process(identity['pid'])
        if (process.create_time() != identity['create_time'] or not process.is_running()
                or process.status() == psutil.STATUS_ZOMBIE
                or process.environ().get(RUN_TOKEN) != owner['run_token']):
            return None
        if owns is not None and not owns(identity):
            return None
        return binding
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return None


class SessionBinding:
    """The owned Bridge atomically publishes its session after each legal rotation."""
    def __init__(self, path, token, host, port):
        self.path, self.token, self.host, self.port = Path(path), token, host, port
        self.identity = process_identity()
        self.session_id = None
        self.generation = -1

    @classmethod
    def from_environment(cls, host, port):
        token, path = os.environ.get(RUN_TOKEN), os.environ.get(BINDING_FILE)
        if token is None and path is None:
            return None
        if not token or not path:
            raise RuntimeError('Incomplete launcher ownership environment')
        return cls(path, token, host, port)

    def publish(self, host, port, session_id):
        if host != self.host or (self.port and port != self.port):
            raise RuntimeError('Bridge endpoint does not match launcher ownership')
        if self.path.exists():
            previous = json.loads(self.path.read_text(encoding='utf-8'))
            if (previous.get('schema') != BINDING_SCHEMA or previous.get('run_token') != self.token
                    or previous.get('bridge') != self.identity
                    or previous.get('host') != host or previous.get('port') != port):
                raise RuntimeError('Bridge binding belongs to a different process instance')
        if session_id != self.session_id:
            self.generation += 1
            self.session_id = session_id
        atomic_write(self.path, dict(schema=BINDING_SCHEMA, run_token=self.token,
            host=host, port=port, bridge=self.identity, session_id=session_id,
            session_generation=self.generation))
