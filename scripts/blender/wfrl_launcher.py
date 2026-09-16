"""Cross-platform process supervisor for the WFRL Bridge and Blender extension."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import re
import struct

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = Path(__file__).with_name("wfrl_blender_bootstrap.py")


def log(message: str) -> None:
    print(f"[WFRL] {message}", flush=True)


def resolve_executable(value: str, label: str) -> str:
    candidate = shutil.which(value) if not Path(value).is_absolute() else value
    if not candidate or not Path(candidate).is_file():
        raise RuntimeError(f"{label} executable was not found: {value}")
    return str(Path(candidate).resolve())


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


def request_protocol_stop(port: int, session_id: str, timeout: float) -> bool:
    """Resume the Blender-owned protocol session and wait for backend STOPPED."""
    def frame(kind: str, sequence: int, payload: dict) -> bytes:
        envelope_session = "" if kind == "hello" else session_id
        body = json.dumps({"protocol_version": 1, "type": kind, "session_id": envelope_session,
                           "sequence": sequence, "payload": payload}, separators=(",", ":")).encode()
        return struct.pack(">I", len(body)) + body
    decoder = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.settimeout(0.5)
            client.sendall(frame("hello", 0, {"supported_versions": [1], "capabilities": [],
                                               "resume_session_id": session_id}))
            sent_stop = False
            while time.monotonic() < deadline:
                try:
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
                    payload = message["payload"]
                    if message["type"] == "hello_ack":
                        if payload["run_status"] in {"READY", "STOPPED", "FAILED"}:
                            return True
                        client.sendall(frame("command", 2**53 - 1,
                                             {"command": "run.stop", "arguments": {}}))
                        sent_stop = True
                    elif message["type"] == "lifecycle" and payload["run_status"] in {"STOPPED", "FAILED"}:
                        return True
                    elif message["type"] == "error" and payload.get("fatal"):
                        return False
            return not sent_stop
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return False


def stop_owned(process: subprocess.Popen | None, label: str, timeout: float = 180.0) -> bool:
    if process is None or process.poll() is not None:
        return True
    log(f"Requesting graceful stop of owned {label} process (PID {process.pid})")
    if os.name == "nt":
        process.send_signal(subprocess.CTRL_BREAK_EVENT)
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


def scene_backend(scene: str | os.PathLike[str]) -> str:
    text = Path(scene).read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*backend\s*:\s*([^#\s]+)", text)
    if not match:
        raise RuntimeError(f"scene does not declare a backend: {scene}")
    return match.group(1).strip().lower()


def preflight(args: argparse.Namespace, *, require_backend: bool) -> dict[str, object]:
    report: dict[str, object] = {"ok": False, "backend": "SYNTH fixture" if args.fake else "configured backend"}
    try:
        blender = resolve_executable(args.blender, "Blender")
        python = resolve_executable(args.python, "Python")
        project = Path(args.project_dir).expanduser().resolve()
        scene = Path(args.scene).expanduser().resolve()
        if not project.is_dir():
            raise RuntimeError(f"project directory was not found: {project}")
        if not scene.is_file():
            raise RuntimeError(f"scene file was not found: {scene}")
        backend = "fake" if args.fake else scene_backend(scene)
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
        check = {
            "fake": "import wfrl.blender_bridge; from wfrl.blender_bridge.fake_backend import FakeTrainer",
            "floris": "from wfrl.blender_bridge.floris_session import FlorisDemoSession",
            "fastfarm": "from wfrl.studio.trainer import Trainer",
        }.get(backend, "import wfrl.blender_bridge")
        import_check = subprocess.run(
            [python, "-c", check], cwd=project,
            capture_output=True, text=True, timeout=15,
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
    env = os.environ.copy()
    session_file = Path(tempfile.mkstemp(prefix="wfrl-session-", suffix=".txt")[1])
    mpi_path = str(report.get("mpi", args.mpi))
    fastfarm_path = str(report.get("fastfarm", args.fastfarm))
    env.update(WFRL_PROJECT_DIR=str(report["project_dir"]), WFRL_BACKEND_PYTHON=str(report["python"]),
               WFRL_SCENE=str(report["scene"]), WFRL_PORT=str(args.port), WFRL_MPI=mpi_path,
               WFRL_FASTFARM=fastfarm_path, WFCRL_FASTFARM_EXECUTABLE=fastfarm_path,
               WFCRL_MPIEXEC=mpi_path, WFRL_SESSION_FILE=str(session_file),
               PYTHONDONTWRITEBYTECODE="1")
    bridge_args = [str(report["python"]), "-m", "wfrl.blender_bridge", "--host", "127.0.0.1",
                   "--port", str(args.port), "--scene", str(report["scene"])]
    if args.fake:
        bridge_args.append("--fake")
    result = 1
    try:
        command = bridge_args
        if not args.fake and report["backend"] == "fastfarm":
            mpi = mpi_path or None
            if not mpi:
                raise RuntimeError("FAST.Farm scenes require --mpi PATH (Bridge must run under mpiexec -n 1)")
            command = [mpi, "-n", "1", *bridge_args]
        log("Starting Bridge: " + " ".join(command))
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        bridge = subprocess.Popen(command, cwd=report["project_dir"], env=env, creationflags=creationflags)
        wait_for_bridge(bridge, args.port, args.startup_timeout)
        blender_args = [str(report["blender"]), "--python-exit-code", "12", "--python", str(BOOTSTRAP)]
        log(f"Starting Blender with MAPPO replay; owned Bridge PID {bridge.pid} ready on port {args.port}")
        result = subprocess.run(blender_args, cwd=report["project_dir"], env=env).returncode
    finally:
        session_id = session_file.read_text().strip() if session_file.exists() else ""
        stop_confirmed = True
        if session_id and bridge and bridge.poll() is None:
            log("Requesting run.stop through the resumed extension session")
            if not request_protocol_stop(args.port, session_id, 180):
                print("[WFRL] ERROR: backend stop was not confirmed; Bridge will remain supervised",
                      file=sys.stderr)
                result = 1
                stop_confirmed = False
        if stop_confirmed and not stop_owned(bridge, "Bridge"):
            result = 1
        try:
            session_file.unlink()
        except FileNotFoundError:
            pass
    return result


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
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    try:
        return args.handler(args)
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"[WFRL] ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
