"""Cross-platform process supervisor for the WFRL Bridge and Blender extension."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import struct

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = Path(__file__).with_name("wfrl_blender_bootstrap.py")


def ownership_modules():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from wfrl.blender_bridge import launcher_state
        from wfrl.blender_bridge.formal_process import OwnedProcesses
    except ImportError as exc:
        raise RuntimeError(f"Launcher Python {sys.executable} requires the project's psutil dependency") from exc
    return launcher_state, OwnedProcesses


def log(message: str) -> None:
    print(f"[WFRL] {message}", flush=True)


def resolve_executable(value: str, label: str) -> str:
    expanded = os.path.expanduser(value)
    candidate = expanded if Path(expanded).is_absolute() else shutil.which(expanded)
    if not candidate or not Path(candidate).is_file():
        raise RuntimeError(f"{label} executable was not found: {value}")
    return str(Path(candidate).absolute())


def ensure_free(host: str, port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.25)
        if probe.connect_ex((host, port)) == 0:
            raise RuntimeError(
                f"127.0.0.1:{port} is already in use; refusing to attach to an unowned process"
            )


def wait_for_bridge(process: subprocess.Popen, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"Bridge exited during startup with code {code}")
        with socket.socket() as probe:
            probe.settimeout(0.15)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    raise RuntimeError(f"Bridge did not listen on 127.0.0.1:{port} within {timeout:g}s")


def _request_inactive_session(port: int, session_id: str | None, timeout: float) -> str | None:
    """Confirm current inactivity, excluding lifecycle history covered by hello_ack."""
    def inactive(payload):
        if {'cleanup_confirmed', 'worker_alive'} & payload.keys():
            if (type(payload.get('cleanup_confirmed')) is not bool or
                    type(payload.get('worker_alive')) is not bool):
                raise ValueError('Invalid cleanup confirmation')
            if payload['cleanup_confirmed'] and (payload['worker_alive'] or
                    payload['run_status'] not in {'READY', 'STOPPED', 'FAILED'}):
                raise ValueError('Contradictory cleanup confirmation')
            return payload['cleanup_confirmed'] and not payload['worker_alive']
        return payload['run_status'] in {'READY', 'STOPPED'}

    def frame(kind: str, sequence: int, payload: dict) -> bytes:
        envelope_session = "" if kind == "hello" else session_id
        body = json.dumps({"protocol_version": 1, "type": kind, "session_id": envelope_session,
                           "sequence": sequence, "payload": payload}, separators=(",", ":")).encode()
        return struct.pack(">I", len(body)) + body
    decoder = bytearray()
    resumed = False
    synchronized_revision = current_revision = None
    if timeout <= 0:
        return None
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=min(2.0, timeout)) as client:
            def remaining_timeout():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Session inactivity confirmation deadline')
                return min(0.5, remaining)

            client.settimeout(remaining_timeout())
            client.sendall(frame("hello", 0, {"supported_versions": [1], "capabilities": [],
                                               "resume_session_id": session_id}))
            while time.monotonic() < deadline:
                try:
                    client.settimeout(remaining_timeout())
                    chunk = client.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                decoder.extend(chunk)
                while len(decoder) >= 4:
                    size = struct.unpack(">I", decoder[:4])[0]
                    if len(decoder) < 4 + size:
                        break
                    message = json.loads(bytes(decoder[4:4 + size]))
                    del decoder[:4 + size]
                    if not isinstance(message, dict) or not isinstance(message.get('payload'), dict):
                        return None
                    confirmed_id = message["session_id"]
                    if message["protocol_version"] != 1 or not isinstance(confirmed_id, str) or not confirmed_id:
                        return None
                    if session_id is not None and confirmed_id != session_id:
                        return None
                    payload = message["payload"]
                    revision = payload.get('state_revision')
                    if message['type'] in {'hello_ack', 'lifecycle'} and 'state_revision' in payload:
                        if type(revision) is not int or revision < 0:
                            return None
                    if message["type"] == "hello_ack":
                        if (resumed or payload.get("resumed") is not (session_id is not None)
                                or payload["selected_version"] != 1):
                            return None
                        resumed = True
                        synchronized_revision = current_revision = revision
                        if inactive(payload):
                            return confirmed_id
                        if session_id is None:
                            return None
                        watermark = payload.get('last_command_sequence', 0)
                        if type(watermark) is not int or not -1 <= watermark < 2**53 - 1:
                            return None
                        client.settimeout(remaining_timeout())
                        client.sendall(frame("command", max(0, watermark) + 1,
                                             {"command": "run.stop", "arguments": {}}))
                    elif resumed and message["type"] == "lifecycle":
                        if synchronized_revision is None:
                            if payload['run_status'] in {'STOPPED', 'FAILED'}:
                                # Unversioned history cannot confirm a stop; the
                                # next resume must report current inactivity.
                                return None
                            continue
                        if revision is None:
                            return None
                        if revision <= synchronized_revision:
                            continue
                        if revision <= current_revision:
                            return None
                        current_revision = revision
                        if inactive(payload):
                            return confirmed_id
                    elif message["type"] == "error" and payload.get("fatal"):
                        return None
            return None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def request_protocol_stop(port: int, session_id: str, timeout: float) -> bool:
    """Resume the owned session; success requires an explicit inactive backend state."""
    return _request_inactive_session(port, session_id, timeout) is not None


def confirm_idle_bridge(port: int, timeout: float) -> str | None:
    """Claim only an explicitly inactive session; active sessions receive no command."""
    return _request_inactive_session(port, None, timeout)


def stop_owned(process: subprocess.Popen | None, label: str, timeout: float = 180.0) -> bool:
    if process is None or process.poll() is not None:
        return True
    log(f"Requesting graceful stop of owned {label} process (PID {process.pid})")
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        print(f"[WFRL] ERROR: {label} cleanup is unconfirmed after {timeout:g}s; "
              f"owned PID {process.pid} remains supervised and was not force-killed", file=sys.stderr)
        return False


def common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--blender", default=os.environ.get("WFRL_BLENDER", "blender"))
    parser.add_argument("--python", default=os.environ.get("WFRL_PYTHON", sys.executable))
    parser.add_argument("--scene", default=str(ROOT / "scenes" / "turb3_demo.yaml"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--project-dir", default=str(ROOT))
    parser.add_argument("--mpi", default=os.environ.get("WFRL_MPI", ""))
    parser.add_argument("--fastfarm", default=os.environ.get("WFRL_FASTFARM", ""))
    parser.add_argument("--fake", action="store_true", help="use the SYNTH fixture backend")
    parser.add_argument("--startup-timeout", type=float, default=15.0)


def scene_backend(scene: str | os.PathLike[str], python: str, project: Path) -> str:
    """Read scene YAML using the backend environment's parser."""
    reader = """
import json
from pathlib import Path
import sys
import yaml

document = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not isinstance(document, dict):
    raise ValueError("scene must be a YAML mapping")
backend = document.get("backend")
if not isinstance(backend, str):
    raise ValueError("scene backend must be a string: fastfarm or floris")
backend = backend.lower()
if backend not in {"fastfarm", "floris"}:
    raise ValueError(f"unknown scene backend: {backend!r}")
print(json.dumps(backend))
"""
    parsed = subprocess.run([python, "-c", reader, str(scene)], cwd=project,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if parsed.returncode:
        detail = (parsed.stderr or parsed.stdout).strip().splitlines()
        raise RuntimeError("backend Python cannot parse scene YAML" +
                           (f": {detail[-1]}" if detail else ""))
    return json.loads(parsed.stdout)


def preflight(args: argparse.Namespace, *, require_backend: bool) -> dict[str, object]:
    report: dict[str, object] = {"ok": False, "backend": "SYNTH fixture" if args.fake else "configured backend"}
    try:
        ownership_modules()
        blender = resolve_executable(args.blender, "Blender")
        python = resolve_executable(args.python, "Python")
        project = Path(args.project_dir).expanduser().resolve()
        scene = Path(args.scene).expanduser().resolve()
        if not project.is_dir():
            raise RuntimeError(f"project directory was not found: {project}")
        if not scene.is_file():
            raise RuntimeError(f"scene file was not found: {scene}")
        backend = "fake" if args.fake else scene_backend(scene, python, project)
        report["backend"] = "SYNTH fixture" if args.fake else backend
        if require_backend and backend == "fastfarm":
            missing = []
            for label, value in (("MPI", args.mpi), ("FAST.Farm", args.fastfarm)):
                if not value:
                    missing.append(f"{label} path is not configured")
            if missing:
                report["backend"] = "MISSING"
                raise RuntimeError("; ".join(missing))
            report["mpi"] = resolve_executable(args.mpi, "MPI")
            report["fastfarm"] = resolve_executable(args.fastfarm, "FAST.Farm")
        check = "import psutil; from wfrl.blender_bridge.launcher_state import SessionBinding; " + {
            "fake": "import wfrl.blender_bridge; from wfrl.blender_bridge.fake_backend import FakeTrainer",
            "floris": "from wfrl.blender_bridge.floris_session import FlorisDemoSession",
            "fastfarm": "from wfrl.studio.trainer import Trainer",
        }[backend]
        import_check = subprocess.run(
            [python, "-c", check], cwd=project,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if import_check.returncode:
            detail = (import_check.stderr or import_check.stdout).strip().splitlines()
            raise RuntimeError("backend Python cannot import WFRL" + (f": {detail[-1]}" if detail else ""))
        ensure_free("127.0.0.1", args.port)
        report.update(ok=True, blender=blender, python=python, project_dir=str(project), scene=str(scene), port=args.port)
    except Exception as exc:
        report["error"] = str(exc)
    return report


def launch(args: argparse.Namespace) -> int:
    report = preflight(args, require_backend=not args.fake)
    if not report["ok"]:
        raise RuntimeError(str(report["error"]))
    bridge: subprocess.Popen | None = None
    state_api, process_class = ownership_modules()
    owned = process_class(env_key=state_api.RUN_TOKEN)
    from wfrl.mpi_runtime import launcher_environment
    env = launcher_environment()
    for key in (state_api.RUN_TOKEN, state_api.BINDING_FILE, 'WFRL_PROCESS_RUN_TOKEN',
                'WFRL_FORMAL_RUN_TOKEN', 'WFRL_SESSION_FILE'):
        env.pop(key, None)
    descriptor, session_path = tempfile.mkstemp(prefix="wfrl-session-", suffix=".json")
    os.close(descriptor)
    session_file = Path(session_path)
    mpi_path = str(report.get("mpi", args.mpi))
    fastfarm_path = str(report.get("fastfarm", args.fastfarm))
    env.update(WFRL_PROJECT_DIR=str(report["project_dir"]), WFRL_BACKEND_PYTHON=str(report["python"]),
               WFRL_SCENE=str(report["scene"]), WFRL_PORT=str(args.port), WFRL_MPI=mpi_path,
               WFRL_FASTFARM=fastfarm_path, WFCRL_FASTFARM_EXECUTABLE=fastfarm_path,
               WFCRL_MPIEXEC=mpi_path,
               PYTHONDONTWRITEBYTECODE="1")
    bridge_env = dict(env, **{state_api.BINDING_FILE: str(state_api.binding_path(session_file))})
    owner = dict(schema=state_api.OWNER_SCHEMA, run_token=owned.token, host='127.0.0.1',
                 port=args.port, root=None, known_processes=[], process_status='UNKNOWN',
                 protocol_stop_confirmed=False)
    state_api.atomic_write(session_file, owner)
    bridge_args = [str(report["python"]), "-m", "wfrl.blender_bridge", "--host", "127.0.0.1",
                   "--port", str(args.port), "--scene", str(report["scene"])]
    if args.fake:
        bridge_args.append("--fake")
    result = 1
    stop_confirmed = stopped_by_launcher = False
    confirmed_session = None
    try:
        command = bridge_args
        if not args.fake and report["backend"] == "fastfarm":
            mpi = mpi_path or None
            if not mpi:
                raise RuntimeError("FAST.Farm scenes require --mpi PATH (Bridge must run under mpiexec -n 1)")
            command = [mpi, "-n", "1", *bridge_args]
        log("Starting Bridge: " + " ".join(command))
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        bridge = owned.start(command, cwd=report["project_dir"], env=bridge_env, creationflags=creationflags)
        if owned.root is not None:
            owner['root'] = dict(pid=owned.root.pid, create_time=owned.root.create_time())
        owner['known_processes'] = owned.identities()
        state_api.atomic_write(session_file, owner)
        wait_for_bridge(bridge, args.port, args.startup_timeout)
        deadline = time.monotonic() + args.startup_timeout
        while state_api.read_binding(session_file, owner, owns=owned.owns) is None:
            if bridge.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Owned Bridge did not publish a verifiable session binding')
            time.sleep(.05)
        blender_args = [str(report["blender"]), "--python-exit-code", "12", "--python", str(BOOTSTRAP)]
        log(f"Starting Blender with MAPPO replay; owned Bridge PID {bridge.pid} ready on port {args.port}")
        result = subprocess.run(blender_args, cwd=report["project_dir"], env=env).returncode
    finally:
        status = None
        try:
            stop_warning = None
            while bridge:
                status = owned.status()
                if status in {'EMPTY_CONFIRMED', 'UNKNOWN'}:
                    break
                owner.update(process_status=status, known_processes=owned.identities(),
                             protocol_stop_confirmed=stop_confirmed)
                state_api.atomic_write(session_file, owner)
                binding = state_api.read_binding(session_file, owner, owns=owned.owns)
                if binding is not None and binding['session_id'] != confirmed_session:
                    stop_confirmed = False
                if not stop_confirmed:
                    if binding is not None:
                        log("Requesting run.stop through the resumed extension session")
                        stop_confirmed = request_protocol_stop(args.port, binding['session_id'], 180)
                        if stop_confirmed:
                            confirmed_session = binding['session_id']
                    if not stop_confirmed:
                        warning = binding['session_id'] if binding is not None else 'unverified binding'
                        if warning != stop_warning:
                            print("[WFRL] ERROR: backend stop was not confirmed; retrying while "
                                  "this launcher remains in the foreground", file=sys.stderr, flush=True)
                        stop_warning = warning
                if stop_confirmed:
                    stop_warning = None
                    status = owned.status()
                    if status == 'UNKNOWN':
                        break
                    stopped_by_launcher = True
                    stop_owned(bridge, "Bridge")
                    owned.signal(signal.SIGTERM)
                status = owned.status()
                if status == 'UNKNOWN':
                    break
                if status != 'EMPTY_CONFIRMED':
                    time.sleep(1.0)
            if bridge and (not stop_confirmed or (bridge.poll() and not stopped_by_launcher)):
                result = 1
        finally:
            if status != 'UNKNOWN':
                status = owned.status()
            if status == 'EMPTY_CONFIRMED':
                session_file.unlink(missing_ok=True)
                state_api.binding_path(session_file).unlink(missing_ok=True)
            else:
                result = 1
                owner.update(process_status=status, known_processes=owned.identities(),
                             protocol_stop_confirmed=stop_confirmed)
                state_api.atomic_write(session_file, owner)
                recovery = [sys.executable, str(Path(__file__).absolute()), "stop-session",
                            "--session-file", str(session_file), "--port", str(args.port)]
                command = subprocess.list2cmdline(recovery) if os.name == "nt" else shlex.join(recovery)
                print(f"[WFRL] ERROR: owned backend process cleanup is {status}. "
                      f"Session credentials retained at {session_file}.\n"
                      f"[WFRL] Recover by checking the retained session: {command}",
                      file=sys.stderr, flush=True)
    return result


def stop_session(args: argparse.Namespace) -> int:
    """Confirm inactivity using retained credentials or an idle-only handshake."""
    session_file = Path(args.session_file).expanduser().absolute()
    session_id = session_file.read_text(encoding="utf-8").strip()
    if session_id.startswith('{'):
        state_api, process_class = ownership_modules()
        try:
            owner = json.loads(session_id)
            if (owner['schema'] != state_api.OWNER_SCHEMA or owner['host'] != '127.0.0.1'
                    or type(owner['port']) is not int or not 1024 <= owner['port'] <= 65535
                    or (args.port is not None and args.port != owner['port'])):
                raise ValueError('Owner endpoint mismatch')
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError('Invalid launcher ownership record or conflicting --port') from exc
        binding = state_api.read_binding(session_file, owner)
        confirmed = binding is not None and request_protocol_stop(owner['port'], binding['session_id'], args.timeout)
        if confirmed:
            log(f'Owned session is confirmed inactive; process exit remains unverified. Record retained at {session_file}')
            return 0
        try:
            identities = list(owner['known_processes'])
            if owner.get('root') is not None:
                identities.append(owner['root'])
            owned = process_class.recover(identities, token=owner['run_token'], env_key=state_api.RUN_TOKEN)
            status = owned.status()
            owner.update(process_status=status, known_processes=owned.identities())
            state_api.atomic_write(session_file, owner)
        except (ValueError, KeyError, TypeError):
            status = 'UNKNOWN'
        if status == 'EMPTY_CONFIRMED':
            session_file.unlink(missing_ok=True)
            state_api.binding_path(session_file).unlink(missing_ok=True)
            log('Owned process cleanup is EMPTY_CONFIRMED; session records removed')
            return 0
        print(f'[WFRL] ERROR: owned process cleanup is {status}; Bridge stop is unconfirmed. '
              f'Record retained at {session_file}',
              file=sys.stderr, flush=True)
        return 1
    port = args.port if args.port is not None else 8765
    if session_id:
        confirmed = request_protocol_stop(port, session_id, args.timeout)
    else:
        idle_session_id = confirm_idle_bridge(port, args.timeout)
        confirmed = idle_session_id is not None
        if idle_session_id:
            session_file.write_text(idle_session_id, encoding="utf-8")
    if confirmed:
        log(f"Session is confirmed inactive. Credentials retained at {session_file}; "
            "Bridge process exit is unverified.")
        return 0
    print(f"[WFRL] ERROR: session stop is unconfirmed on port {port}; "
          f"credentials retained at {session_file}. Retry the same command when the owned Bridge is reachable.",
          file=sys.stderr, flush=True)
    return 1


def verify(args: argparse.Namespace) -> int:
    report = preflight(args, require_backend=True)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    elif report["ok"]:
        log(f"PASS: Blender, Python, scene, free port, and {report['backend']} are ready")
    else:
        log(f"FAIL: {report.get('error', 'unknown error')}")
        if report.get("backend") == "MISSING":
            log("Backend status: MISSING (Blender/frontend availability was not reported as backend-ready)")
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch_parser = commands.add_parser("launch")
    common(launch_parser)
    launch_parser.set_defaults(handler=launch)
    verify_parser = commands.add_parser("verify")
    common(verify_parser)
    verify_parser.add_argument("--json", action="store_true")
    verify_parser.set_defaults(handler=verify)
    stop_parser = commands.add_parser("stop-session", help="request a stop using retained session credentials")
    stop_parser.add_argument("--session-file", required=True)
    stop_parser.add_argument("--port", type=int)
    stop_parser.add_argument("--timeout", type=float, default=180.0)
    stop_parser.set_defaults(handler=stop_session)
    args = parser.parse_args(argv)
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if args.command == "stop-session" and args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        return args.handler(args)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"[WFRL] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
