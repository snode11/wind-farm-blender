"""Persistent, authenticated supervision for workspace manual-video jobs.

The controller never signals a recorded PID. A live supervisor holds the output
lease and an unreaped child identity until its process group has been stopped.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unicodedata
import uuid

TERMINAL = {'COMPLETE', 'FAILED', 'CANCELLED'}
LEASE_SCHEMA = 'wfrl.manual-video-lease.v1'


def write_json(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', prefix='.status-',
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def require_supervision():
    if os.name != 'posix' or not all(hasattr(os, key) for key in ('waitid', 'WNOWAIT', 'P_PID')):
        raise ValueError('手动视频监管需要支持 flock 和 waitid(WNOWAIT) 的 macOS / Linux Python')


def destination_path(value):
    path = Path(value).expanduser().absolute()
    return path.parent.resolve(strict=True) / path.name


def output_lock(value):
    path = destination_path(value)
    parent = path.parent.stat()
    identity = f'{parent.st_dev}:{parent.st_ino}:' + unicodedata.normalize('NFC', path.name).casefold()
    key = hashlib.sha256(identity.encode()).hexdigest()[:32]
    return path.parent / ('.wfrl-output-' + key + '.lock')


class Lease:
    """An advisory lease whose inode stays in place across owners and restarts."""
    def __init__(self, path, owner):
        require_supervision()
        import fcntl
        self.path, self.fd = Path(path), None
        created = False
        flags = os.O_RDWR | os.O_NOFOLLOW
        try:
            fd = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            fd = os.open(self.path, flags)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError(f'不是普通作业锁文件：{self.path}')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError(f'已有作业占用：{self.path}') from exc
            existing = os.read(fd, 65536)
            if not created:
                record = json.loads(existing) if existing else None
                if not isinstance(record, dict) or record.get('schema') != LEASE_SCHEMA:
                    raise ValueError(f'拒绝使用未知锁文件：{self.path}')
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(dict(schema=LEASE_SCHEMA, owner=owner)).encode())
            os.fsync(fd)
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def lease_busy(path, *, owner=None):
    import fcntl
    try:
        fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        if owner is not None:
            try:
                record = json.loads(os.pread(fd, 65536, 0))
                if not isinstance(record, dict):
                    return True
                return record.get('owner') == owner if record.get('schema') == LEASE_SCHEMA else True
            except (OSError, ValueError):
                return True
        return True
    finally:
        os.close(fd)


def load_job(path):
    path = Path(path).resolve(strict=True)
    job = read_json(path)
    if (not isinstance(job, dict) or job.get('schema') != 'wfrl.manual-video-job.v2'
            or not isinstance(job.get('job_id'), str) or len(job['job_id']) != 32):
        raise ValueError('需要包含持久化身份的 wfrl.manual-video-job.v2 作业')
    if not isinstance(job.get('control_token'), str) or len(job['control_token']) != 64:
        raise ValueError('作业缺少监管凭据')
    bytes.fromhex(job['job_id'])
    bytes.fromhex(job['control_token'])
    return path, job


def _signature(job, value):
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return hmac.new(bytes.fromhex(job['control_token']), payload, hashlib.sha256).hexdigest()


def _rpc(path, job, action):
    endpoint = read_json(path.parent / 'control.json')
    if (not isinstance(endpoint, dict) or endpoint['job_id'] != job['job_id']
            or type(endpoint.get('port')) is not int or not 0 < endpoint['port'] < 65536):
        raise ValueError('监管端点作业身份不匹配')
    request = dict(job_id=job['job_id'], attempt_id=endpoint['attempt_id'],
                   nonce=secrets.token_hex(16), action=action)
    request['signature'] = _signature(job, request)
    with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=.5) as connection:
        connection.settimeout(.5)
        connection.sendall(json.dumps(request).encode() + b'\n')
        with connection.makefile('rb') as handle:
            result = json.loads(handle.readline(1024 * 1024))
    if not isinstance(result, dict):
        raise ValueError('监管响应格式错误')
    signature = result.pop('signature', '')
    if (not isinstance(signature, str) or result.get('job_id') != job['job_id']
            or result.get('attempt_id') != endpoint['attempt_id']
            or result.get('request_nonce') != request['nonce']
            or not hmac.compare_digest(signature, _signature(job, result))):
        raise ValueError('无法验证监管端点身份；不会向保存的 PID 发送信号')
    result.pop('request_nonce')
    return result


def query(job_path):
    path, job = load_job(job_path)
    try:
        return _rpc(path, job, 'status')
    except (OSError, ValueError, KeyError):
        try:
            record = read_json(path.parent / 'status.json')
        except (OSError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        if record.get('job_id') == job['job_id'] and record.get('final') and record.get('state') in TERMINAL:
            return dict(record, active=False)
        busy = lease_busy(path.parent / 'supervision.lock', owner=job['job_id']) or lease_busy(
            output_lock(job['output_mp4']), owner=job['job_id'])
        return dict(record, state='LOST', job_id=job['job_id'], active=busy, final=False,
                    error='无法联系已验证的监管进程；运行状态未知，不能安全按 PID 取消')


def wait(job_path, *, timeout=None):
    started = time.monotonic()
    while True:
        record = query(job_path)
        if record.get('final') or record['state'] == 'LOST':
            return record
        if timeout is not None and time.monotonic() - started >= timeout:
            raise TimeoutError('视频作业仍在运行')
        time.sleep(.1)


def cancel(job_path):
    path, job = load_job(job_path)
    record = query(path)
    if record.get('final'):
        return record
    try:
        _rpc(path, job, 'cancel')
    except (OSError, ValueError, KeyError):
        record = query(path)
        if not record.get('final'):
            raise ValueError(record.get('error', '无法联系监管进程；未取消作业'))
    return wait(path, timeout=15)


def _sibling(name):
    """Load pure workspace helpers without importing Blender's package entry point."""
    namespace = '_wfrl_manual_video_tools'
    if namespace not in sys.modules:
        package = types.ModuleType(namespace)
        package.__path__ = [str(Path(__file__).resolve().parent)]
        sys.modules[namespace] = package
    return importlib.import_module(namespace + '.' + name)


def start(job_path, *, python=None, lease=None, retry=False, command=None, preflight_check=None,
          project_root=None):
    """Start an independently queryable supervisor; inherited leases close startup gaps."""
    require_supervision()
    path, job = load_job(job_path)
    if query(path).get('state') == 'COMPLETE':
        raise ValueError('已成功的作业不会重新执行或改写状态')
    own_lease = lease is None
    lease = lease or Lease(output_lock(job['output_mp4']), job['job_id'])
    supervision = None
    try:
        supervision = Lease(path.parent / 'supervision.lock', job['job_id'])
        if query(path).get('state') == 'COMPLETE':
            raise ValueError('已成功的作业不会重新执行或改写状态')
        if retry:
            _sibling('manual_video_render').validate_master(path)
        elif preflight_check is not None:
            preflight_check(job['output_mp4'])
        else:
            from wfrl.camera_video.publication import preflight
            preflight(job['output_mp4'])
        attempt = uuid.uuid4().hex
        write_json(path.parent / 'status.json', dict(job_id=job['job_id'], attempt_id=attempt,
                   state='PREPARING', active=True, final=False, completed_frames=0,
                   total_frames=job.get('frame_count', 0)))
        worker = command or ([str(python or sys.executable), '-B', str(Path(__file__).resolve()),
                               'encode', '--job', str(path)] if retry else job['render_command'])
        argv = [str(python or sys.executable), '-B', str(Path(__file__).resolve()), 'supervise',
                '--job', str(path), '--attempt', attempt, '--output-fd', str(lease.fd),
                '--supervision-fd', str(supervision.fd), '--'] + list(worker)
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        if project_root is not None:
            environment['PYTHONPATH'] = os.pathsep.join(filter(None, (
                str(Path(project_root).resolve()), environment.get('PYTHONPATH'))))
        try:
            with (path.parent / 'supervisor.log').open('ab') as log:
                process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                           pass_fds=(lease.fd, supervision.fd), start_new_session=True)
        except OSError as exc:
            write_json(path.parent / 'status.json', dict(job_id=job['job_id'], attempt_id=attempt,
                       state='FAILED', final=True, active=False, error=f'无法启动监管进程：{exc}'))
            raise
        threading.Thread(target=process.wait, daemon=True).start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                record = _rpc(path, job, 'status')
                if record['attempt_id'] == attempt:
                    return path
            except (OSError, ValueError, KeyError):
                pass
            final = read_json(path.parent / 'status.json')
            if final.get('attempt_id') == attempt and final.get('final'):
                return path
            time.sleep(.05)
        raise RuntimeError(f'监管端点未就绪；请查询作业 {path}，不会重复启动')
    finally:
        if supervision is not None:
            supervision.close()
        if own_lease:
            lease.close()


def _exited(process):
    # WNOWAIT pins the original child PID/PGID until group cleanup is complete.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _stop_group(process, *, grace=1.0):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Darwin can report EPERM when a group contains only zombies.
            listing = subprocess.run(['/bin/ps', '-A', '-o', 'pgid=,stat='],
                                     check=True, capture_output=True, text=True).stdout
            live = any(int(parts[0]) == process.pid and not parts[1].startswith('Z')
                       for line in listing.splitlines() if len(parts := line.split()) == 2)
            if not _exited(process) or live:
                raise
        if sig == signal.SIGTERM:
            time.sleep(grace)
    return process.wait(timeout=5)


def supervise(path, attempt, output_fd, supervision_fd, command):
    require_supervision()
    path, job = load_job(path)
    for fd, lock in ((output_fd, output_lock(job['output_mp4'])),
                     (supervision_fd, path.parent / 'supervision.lock')):
        actual, expected = os.fstat(fd), os.stat(lock, follow_symlinks=False)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise ValueError('继承的监管锁身份不匹配')
    record = dict(job_id=job['job_id'], attempt_id=attempt, state='PREPARING',
                  active=True, final=False, completed_frames=0, total_frames=job.get('frame_count', 0))
    cancelled, process = False, None
    accepting_cancel = True
    lifecycle_lock = threading.Lock()
    stop_server = threading.Event()
    serving = None

    def request_cancel(*unused):
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, request_cancel)
    signal.signal(signal.SIGINT, request_cancel)
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    try:
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(8)
            server.settimeout(.1)
            write_json(path.parent / 'control.json', dict(job_id=job['job_id'], attempt_id=attempt,
                       port=server.getsockname()[1], supervisor_pid=os.getpid()))

            def serve():
                nonlocal cancelled
                while not stop_server.is_set():
                    try:
                        connection, address = server.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    with connection:
                        connection.settimeout(.5)
                        try:
                            with connection.makefile('rb') as handle:
                                request = json.loads(handle.readline(4096))
                            if not isinstance(request, dict) or request.get('action') not in {'status', 'cancel'}:
                                continue
                            signature = request.pop('signature', '')
                            if (request.get('job_id') != job['job_id'] or request.get('attempt_id') != attempt
                                    or not hmac.compare_digest(signature, _signature(job, request))):
                                continue
                            if request.get('action') == 'cancel':
                                with lifecycle_lock:
                                    if accepting_cancel and (process is None or not _exited(process)):
                                        cancelled = True
                            response = dict(record, request_nonce=request['nonce'])
                            response['signature'] = _signature(job, response)
                            connection.sendall(json.dumps(response).encode() + b'\n')
                        except (OSError, ValueError, KeyError, TypeError):
                            continue

            serving = threading.Thread(target=serve, daemon=True)
            serving.start()
            environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
                               WFRL_MANUAL_ATTEMPT=attempt, WFRL_MANUAL_OUTPUT_FD=str(output_fd))
            environment['BLENDER_USER_CONFIG'] = str(path.parent / 'blender_config')
            with (path.parent / 'process.log').open('ab') as log:
                process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment,
                                           pass_fds=(output_fd,), start_new_session=True)
            write_json(path.parent / 'process.json', dict(job_id=job['job_id'], attempt_id=attempt,
                       pid=process.pid, supervisor_pid=os.getpid(), command=command))
            while True:
                try:
                    progress = read_json(path.parent / 'worker-status.json')
                    if progress.get('attempt_id') == attempt and progress.get('job_id') == job['job_id']:
                        record.update(progress)
                except (OSError, ValueError):
                    pass
                record.update(active=True, final=False)
                with lifecycle_lock:
                    if _exited(process) or cancelled:
                        accepting_cancel = False
                        break
                write_json(path.parent / 'status.json', record)
                time.sleep(.05)
            record['state'] = 'CANCELLING' if cancelled else 'FINALIZING'
            code = _stop_group(process)
            process = None
            try:
                progress = read_json(path.parent / 'worker-status.json')
                if progress.get('attempt_id') == attempt and progress.get('job_id') == job['job_id']:
                    record.update(progress)
            except (OSError, ValueError):
                pass
            renderer = _sibling('manual_video_render')
            renderer.cleanup_publication(path)
            from wfrl.camera_video.media import sha256
            output = Path(job['output_mp4'])
            committed = (record.get('state') == 'COMPLETE' and not output.is_symlink() and output.is_file()
                         and sha256(output) == record.get('video_sha256'))
            delivery = renderer.delivery_result(path)
            if delivery:
                record.update(delivery)
            if delivery or (committed and (code == 0 or cancelled)):
                record['state'] = 'COMPLETE'
                record.pop('error', None)
            elif cancelled:
                record['state'] = 'CANCELLED'
            else:
                record.update(state='FAILED', error=record.get('error') or
                              f'导出进程退出（{code}），未确认完整交付；详情见 process.log')
            record.update(exit_code=code, active=False, final=True)
            write_json(path.parent / 'status.json', record)
    except BaseException as exc:
        if process is not None:
            _stop_group(process)
        record.update(state='FAILED', error=f'{type(exc).__name__}: {exc}', active=False, final=True)
        write_json(path.parent / 'status.json', record)
        raise
    finally:
        stop_server.set()
        if serving is not None:
            serving.join(timeout=1)
        os.close(output_fd)
        os.close(supervision_fd)


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    argv = list(sys.argv[1:] if argv is None else argv)
    internal = bool(argv and argv[0] in {'supervise', 'encode'})
    parser = argparse.ArgumentParser(prog='manual_video_job.py' if internal else 'manage_manual_video.py',
                                     description='Query, wait, cancel or retry a saved manual-video job.')
    parser.add_argument('action', choices=('supervise', 'encode') if internal else ('status', 'wait', 'cancel', 'retry'))
    parser.add_argument('--job', type=Path, required=True)
    if internal:
        parser.add_argument('--attempt')
        parser.add_argument('--output-fd', type=int)
        parser.add_argument('--supervision-fd', type=int)
    else:
        parser.add_argument('--detach', action='store_true', help='For retry: return after launch')
    separator = argv.index('--') if '--' in argv else len(argv)
    args = parser.parse_args(argv[:separator])
    if not internal and args.detach and args.action != 'retry':
        parser.error('--detach 仅用于 retry')
    if args.action == 'supervise':
        supervise(args.job, args.attempt, args.output_fd, args.supervision_fd, argv[separator + 1:])
        return 0
    if args.action == 'encode':
        _sibling('manual_video_render').resume_job(args.job)
        return 0
    if args.action == 'retry':
        start(args.job, retry=True)
        print(json.dumps(dict(job=str(args.job.resolve())), ensure_ascii=False), flush=True)
        if args.detach:
            return 0
    record = cancel(args.job) if args.action == 'cancel' else (
        query(args.job) if args.action == 'status' else wait(args.job))
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return {'COMPLETE': 0, 'FAILED': 1, 'CANCELLED': 130, 'LOST': 3}.get(record['state'], 0)


if __name__ == '__main__':
    raise SystemExit(main())
