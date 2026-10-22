"""Supervise a real visible Blender + FAST.Farm acceptance, with owned cleanup."""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]


def executable(env_var, default):
    configured = os.environ.get(env_var) or str(default)
    found = shutil.which(configured)
    if found is None:
        raise FileNotFoundError(f'Executable not found or not executable: {configured}; set {env_var} or add the command to PATH')
    return str(Path(found).absolute())


backend_python = executable('WFRL_PYTHON', sys.executable)
fastfarm = executable('WFCRL_FASTFARM_EXECUTABLE', os.environ.get('WFRL_FASTFARM', 'FAST.Farm'))
mpiexec = executable('WFCRL_MPIEXEC', 'mpiexec')
blender_default = shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender'
blender = executable('WFRL_BLENDER', blender_default)
OUT = Path(os.environ.get('WFRL_ACCEPT_OUTPUT', ROOT / 'evidence' / 'frontend_completion'))
OUT.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env.update(WFCRL_FASTFARM_EXECUTABLE=fastfarm, WFCRL_MPIEXEC=mpiexec, PYTHONUNBUFFERED='1')
env.setdefault('OMP_NUM_THREADS', '2')
children = []
started = time.monotonic()
result = {}
try:
    with (OUT/'backend.log').open('w') as log, (OUT/'blender.log').open('w') as frontlog:
        bridge = subprocess.Popen([backend_python,'-m','wfrl.blender_bridge','--port','8881'],
            cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        children.append(bridge)
        for _ in range(200):
            if bridge.poll() is not None:
                raise RuntimeError('Bridge exited')
            try:
                with socket.create_connection(('127.0.0.1',8881),.1): break
            except OSError: time.sleep(.1)
        else: raise RuntimeError('Bridge startup deadline')
        front = subprocess.Popen([blender,'--factory-startup',
            '--python-exit-code','1','--python',str(ROOT/'scripts/blender/accept_frontend.py')],
            cwd=ROOT,env=env,stdout=frontlog,stderr=subprocess.STDOUT,start_new_session=True)
        children.append(front)
        while front.poll() is None and time.monotonic()-started < 600:
            time.sleep(.5)
        result['frontend_exit'] = front.poll()
        bridge.send_signal(signal.SIGTERM)
        try: bridge.wait(timeout=25)
        except subprocess.TimeoutExpired: result['bridge_cleanup_timeout'] = True
        result['bridge_exit'] = bridge.poll()
finally:
    for process in reversed(children):
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid,signal.SIGKILL);process.wait()
    result['elapsed_seconds'] = time.monotonic()-started
    result['owned_launchers_exited'] = all(p.poll() is not None for p in children)
    (OUT/'supervision.json').write_text(json.dumps(result,indent=2))
    print(result,flush=True)

report_path = OUT/'report.json'
passed = report_path.exists() and json.loads(report_path.read_text()).get('passed')
raise SystemExit(0 if passed and result.get('frontend_exit') == 0 and result.get('bridge_exit') == 0 else 1)
