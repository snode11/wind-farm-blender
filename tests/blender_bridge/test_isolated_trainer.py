import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.01)


def test_cancel_owns_worker_group_and_allows_restart(tmp_path):
    spec = importlib.util.find_spec('wfrl.blender_bridge.isolated_trainer')
    assert spec is not None, 'Interactive MPI training needs an owned cancellable worker'
    from wfrl.blender_bridge.isolated_trainer import IsolatedTrainer
    script = tmp_path / 'worker.py'
    script.write_text('''import json, subprocess, sys, socket
config = json.load(open(sys.argv[1]))
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(config['socket'])
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], start_new_session=True)
def emit(payload):
    # Native simulator output must never be parsed as application messages.
    print('Time: 123 of 456 ... noisy simulator output', flush=True)
    connection.sendall((json.dumps({'kind':'snapshot','payload':payload,'events':[], 'channels':[]})+'\\n').encode())
emit({'step':1, 'child_pid':child.pid})
for line in connection.makefile('r'):
    command = json.loads(line)
    if command['command'] == 'continue':
        emit({'step':2})
    # Deliberately never drain. Cancellation must clean all owned descendants.
''')
    class FixtureTrainer(IsolatedTrainer):
        def _worker_command(self):
            return [sys.executable, '-u', str(script), str(Path(self._directory.name) / 'config.json')]
    received = []
    from wfrl.scene.schema import load_scene
    trainer = FixtureTrainer(load_scene('scenes/turb3_ctrl3.yaml'), on_snapshot=lambda s: received.append(s.payload))
    unrelated = __import__('subprocess').Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        trainer.start()
        wait_for(lambda: len(received) >= 2)
        started = time.monotonic()
        trainer.stop(timeout=.1)
        assert time.monotonic() - started < 8
        assert not trainer.running
        assert trainer.error is None
        import psutil
        assert not psutil.pid_exists(received[0]["child_pid"]), "MPI children may create their own process groups"
        assert unrelated.poll() is None
        trainer.start()
        wait_for(lambda: len(received) >= 4)
        trainer.stop(timeout=.1)
        assert not trainer.running
    finally:
        try:
            trainer.stop(timeout=0)
        finally:
            import psutil
            for sample in received:
                if sample.get('child_pid'):
                    try: psutil.Process(sample['child_pid']).kill()
                    except psutil.NoSuchProcess: pass
            unrelated.terminate(); unrelated.wait(timeout=5)
