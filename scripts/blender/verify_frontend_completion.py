"""Supervise a real visible Blender + FAST.Farm acceptance, with owned cleanup."""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def executable(env_var, default):
    configured = os.environ.get(env_var) or str(default)
    found = shutil.which(configured)
    if found is None:
        raise FileNotFoundError(f'Executable not found or not executable: {configured}; set {env_var} or add the command to PATH')
    return str(Path(found).absolute())


def windows_process_owner(bridge, output):
    import psutil

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from wfrl.blender_bridge.formal_process import OwnedProcesses

    identity = {'pid': bridge.pid, 'create_time': psutil.Process(bridge.pid).create_time()}
    # The run-specific output path is inherited by both launchers and their workers.
    return OwnedProcesses.recover([identity], token=str(output), env_key='WFRL_ACCEPT_OUTPUT')


def stop_windows_process(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def clean_windows_descendants(owner):
    status = owner.status()
    if status == 'LIVE':
        owner.signal(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while (status := owner.status()) == 'LIVE' and time.monotonic() < deadline:
            time.sleep(.1)
    return status


def main():
    windows = os.name == 'nt'
    process_options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                       if windows else {'start_new_session': True})
    backend_python = executable('WFRL_PYTHON', sys.executable)
    fastfarm = executable('WFCRL_FASTFARM_EXECUTABLE', os.environ.get('WFRL_FASTFARM', 'FAST.Farm'))
    mpiexec = executable('WFCRL_MPIEXEC', 'mpiexec')
    blender_default = shutil.which('blender') or '/Applications/Blender.app/Contents/MacOS/Blender'
    blender = executable('WFRL_BLENDER', blender_default)
    output_root = Path(os.environ.get('WFRL_ACCEPT_OUTPUT', ROOT / 'evidence' / 'frontend_completion')).expanduser().absolute()
    output_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix='run-', dir=output_root))
    print(f'Acceptance output: {output}', flush=True)
    env = os.environ.copy()
    env.update(WFCRL_FASTFARM_EXECUTABLE=fastfarm, WFCRL_MPIEXEC=mpiexec, PYTHONUNBUFFERED='1',
               WFRL_ACCEPT_OUTPUT=str(output))
    env.setdefault('OMP_NUM_THREADS', '2')
    children = []
    bridge = owner = None
    started = time.monotonic()
    result = {'output_dir': str(output)}
    try:
        with (output/'backend.log').open('w', encoding='utf-8') as log, (output/'blender.log').open('w', encoding='utf-8') as frontlog:
            bridge = subprocess.Popen([backend_python, '-m', 'wfrl.blender_bridge', '--port', '8881'],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, **process_options)
            children.append(bridge)
            if windows:
                owner = windows_process_owner(bridge, output)
            for _ in range(200):
                if bridge.poll() is not None:
                    raise RuntimeError('Bridge exited')
                try:
                    with socket.create_connection(('127.0.0.1', 8881), .1):
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('Bridge startup deadline')
            front = subprocess.Popen([blender, '--factory-startup',
                '--python-exit-code', '1', '--python', str(ROOT/'scripts/blender/accept_frontend.py')],
                cwd=ROOT, env=env, stdout=frontlog, stderr=subprocess.STDOUT, **process_options)
            children.append(front)
            while front.poll() is None and time.monotonic() - started < 600:
                time.sleep(.5)
            result['frontend_exit'] = front.poll()
            if not windows:
                bridge.send_signal(signal.SIGTERM)
                try:
                    bridge.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    result['bridge_cleanup_timeout'] = True
                result['bridge_exit'] = bridge.poll()
    finally:
        if owner is not None:
            owner.status()
        for process in reversed(children):
            if process.poll() is not None:
                continue
            if windows:
                try:
                    stop_windows_process(process)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    result.setdefault('launcher_cleanup_errors', {})[str(process.pid)] = str(exc)
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        if windows:
            result['owned_process_cleanup'] = clean_windows_descendants(owner) if owner is not None else 'UNKNOWN'
            result['bridge_exit'] = bridge.poll() if bridge is not None else None
        result['elapsed_seconds'] = time.monotonic() - started
        result['owned_launchers_exited'] = all(process.poll() is not None for process in children)
        (output/'supervision.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(result, flush=True)

    report_path = output/'report.json'
    passed = report_path.exists() and json.loads(report_path.read_text(encoding='utf-8')).get('passed')
    if windows:
        bridge_ok = (result['bridge_exit'] is not None and result['owned_launchers_exited']
                     and result['owned_process_cleanup'] == 'EMPTY_CONFIRMED')
    else:
        bridge_ok = result.get('bridge_exit') == 0
    return 0 if passed and result.get('frontend_exit') == 0 and bridge_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
