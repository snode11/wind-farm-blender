from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import stat
import struct
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts" / "blender" / "wfrl_launcher.py"
SPEC = importlib.util.spec_from_file_location("wfrl_launcher", PATH)
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def executable(path: Path, body: str) -> Path:
    path.write_text(f"#!{sys.executable}\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def base_args(tmp_path: Path, blender: Path | None = None) -> list[str]:
    blender = blender or executable(tmp_path / "blender", "raise SystemExit(0)")
    return ["--blender", str(blender), "--python", sys.executable,
            "--project-dir", str(ROOT), "--scene", str(ROOT / "scenes" / "turb3_demo.yaml")]


def test_verify_fake_reports_fixture_ready(tmp_path, capsys):
    code = launcher.main(["verify", *base_args(tmp_path), "--fake", "--json"])
    output = capsys.readouterr().out
    assert code == 0
    assert '"backend": "SYNTH fixture"' in output
    assert '"ok": true' in output


def test_verify_fastfarm_truthfully_reports_missing_backend(tmp_path, capsys):
    code = launcher.main(["verify", *base_args(tmp_path), "--json"])
    output = capsys.readouterr().out
    assert code == 1
    assert '"backend": "MISSING"' in output
    assert "MPI path is not configured" in output


def test_occupied_port_is_rejected_without_attaching(tmp_path, capsys):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    try:
        port = listener.getsockname()[1]
        code = launcher.main(["verify", *base_args(tmp_path), "--fake", "--port", str(port), "--json"])
    finally:
        listener.close()
    assert code == 1
    assert "refusing to attach to an unowned process" in capsys.readouterr().out


def test_launch_fake_cleans_up_owned_bridge(tmp_path):
    marker = tmp_path / "blender-ran"
    blender = executable(tmp_path / "blender", f"from pathlib import Path\nPath({str(marker)!r}).write_text('ok')")
    code = launcher.main(["launch", *base_args(tmp_path, blender), "--fake",
                          "--port", "18765", "--startup-timeout", "5"])
    assert code == 0
    assert marker.read_text() == "ok"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 18765)) != 0


def protocol_frame(kind, status=None, *, session_id="owned-session", sequence=1, **changes):
    payload = {"run_status": status, "mode": "demo", "capabilities": []}
    if kind == "hello_ack":
        payload.update(selected_version=1, resumed=True)
    elif kind == "lifecycle":
        payload["reason"] = None
    payload.update(changes)
    message = {"protocol_version": 1, "type": kind, "session_id": session_id,
               "sequence": sequence, "payload": payload}
    body = json.dumps(message).encode()
    return struct.pack(">I", len(body)) + body


@pytest.fixture
def protocol_socket(monkeypatch):
    def install(events):
        pending = iter(events)
        now = [0.0]
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)

        def receive(_size):
            event = next(pending)
            now[0] += 1.0 if isinstance(event, socket.timeout) else 0.01
            if isinstance(event, Exception):
                raise event
            return event

        client.recv.side_effect = receive
        monkeypatch.setattr(launcher.socket, "create_connection", Mock(return_value=client))
        monkeypatch.setattr(launcher.time, "monotonic", lambda: now[0])
        return client
    return install


@pytest.mark.parametrize("after_hello", [False, True])
@pytest.mark.parametrize("end", [b"", socket.timeout()])
def test_protocol_stop_requires_confirmation_before_disconnect_or_timeout(protocol_socket, after_hello, end):
    events = [protocol_frame("hello_ack", "RUNNING")] if after_hello else []
    client = protocol_socket([*events, end])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False
    assert client.sendall.call_count == (2 if after_hello else 1)


@pytest.mark.parametrize("status", ["READY", "STOPPED", "FAILED"])
def test_protocol_stop_accepts_confirmed_inactive_session(protocol_socket, status):
    client = protocol_socket([protocol_frame("hello_ack", status)])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is True
    assert client.sendall.call_count == 1


@pytest.mark.parametrize("active_status", ["STARTING", "RUNNING", "PAUSED", "DRAINING"])
@pytest.mark.parametrize("terminal_status", ["STOPPED", "FAILED"])
def test_protocol_stop_sends_command_and_waits_for_terminal_status(protocol_socket, active_status, terminal_status):
    frames = (protocol_frame("hello_ack", active_status)
              + protocol_frame("lifecycle", terminal_status, sequence=2))
    client = protocol_socket([frames[:3], frames[3:]])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is True
    sent = [json.loads(call.args[0][4:]) for call in client.sendall.call_args_list]
    assert sent[0]["payload"]["resume_session_id"] == "owned-session"
    assert sent[1]["session_id"] == "owned-session"
    assert sent[1]["payload"] == {"command": "run.stop", "arguments": {}}


def test_launch_keeps_bridge_when_protocol_stop_is_unconfirmed(tmp_path, monkeypatch, protocol_socket, capsys):
    protocol_socket([b""])
    bridge = Mock(pid=4321)
    bridge.poll.return_value = None
    report = {"ok": True, "backend": "SYNTH fixture", "project_dir": str(ROOT),
              "python": sys.executable, "scene": "fixture.yaml", "blender": "fixture-blender"}
    monkeypatch.setattr(launcher, "preflight", lambda *args, **kwargs: report)
    monkeypatch.setattr(launcher, "wait_for_bridge", lambda *args: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", Mock(return_value=bridge))

    def blender_run(*args, env, **kwargs):
        Path(env["WFRL_SESSION_FILE"]).write_text("owned-session")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", blender_run)
    stop_owned = Mock(return_value=True)
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 1
    stop_owned.assert_not_called()
    assert "backend stop was not confirmed" in capsys.readouterr().err
