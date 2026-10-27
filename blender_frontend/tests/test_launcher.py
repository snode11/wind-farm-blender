from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import socket
import stat
import struct
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import venv

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


def test_python_executable_keeps_virtual_environment_entry(tmp_path):
    environment = tmp_path / 'venv' / 'bin'
    environment.mkdir(parents=True)
    python = environment / 'python'
    python.symlink_to(sys.executable)
    assert launcher.resolve_executable(str(python), 'Python') == str(python.absolute())


def test_selected_python_executes_inside_its_virtual_environment(tmp_path):
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    selected = launcher.resolve_executable(str(environment / "bin" / "python"), "Python")
    check = subprocess.run([selected, "-c", "import sys; print(sys.prefix)"],
                           capture_output=True, text=True, check=True)
    assert Path(check.stdout.strip()) == environment


@pytest.mark.parametrize("declaration", ["fastfarm", "'fastfarm'", '"fastfarm"'])
def test_fastfarm_yaml_forms_require_the_same_dependencies(tmp_path, capsys, declaration):
    scene = tmp_path / "scene.yaml"
    scene.write_text(f"backend: {declaration}\n")
    code = launcher.main(["verify", *base_args(tmp_path), "--scene", str(scene), "--json"])
    output = json.loads(capsys.readouterr().out)
    assert code == 1
    assert output["backend"] == "MISSING"
    assert "MPI path is not configured" in output["error"]


@pytest.mark.parametrize("contents", [
    "backend: other\n", "backend: [fastfarm]\n", "backend: {name: fastfarm}\n",
    "backend: null\n", "- backend: fastfarm\n", "backend: [\n", "{}\n",
])
def test_preflight_rejects_unknown_or_invalid_yaml_backend(tmp_path, capsys, contents):
    scene = tmp_path / "scene.yaml"
    scene.write_text(contents)
    code = launcher.main(["verify", *base_args(tmp_path), "--scene", str(scene), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "scene" in report["error"].lower()


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
    frames = (protocol_frame("hello_ack", active_status, state_revision=10)
              + protocol_frame("lifecycle", terminal_status, sequence=2, state_revision=11))
    client = protocol_socket([frames[:3], frames[3:]])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is True
    sent = [json.loads(call.args[0][4:]) for call in client.sendall.call_args_list]
    assert sent[0]["payload"]["resume_session_id"] == "owned-session"
    assert sent[1]["session_id"] == "owned-session"
    assert sent[1]["payload"] == {"command": "run.stop", "arguments": {}}


@pytest.mark.parametrize("terminal_status", ["STOPPED", "FAILED"])
@pytest.mark.parametrize("revision", [4, 10])
def test_protocol_stop_ignores_watermark_covered_terminal_history(protocol_socket, terminal_status, revision):
    client = protocol_socket([protocol_frame("hello_ack", "RUNNING", state_revision=10),
                              protocol_frame("lifecycle", terminal_status, sequence=2, state_revision=revision), b""])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False
    assert client.sendall.call_count == 2


@pytest.mark.parametrize("terminal_status", ["STOPPED", "FAILED"])
def test_protocol_stop_accepts_live_terminal_after_covered_history(protocol_socket, terminal_status):
    client = protocol_socket([protocol_frame("hello_ack", "RUNNING", state_revision=10),
                              protocol_frame("lifecycle", "DRAINING", sequence=2, state_revision=11),
                              protocol_frame("lifecycle", "STOPPED", sequence=3, state_revision=4),
                              protocol_frame("lifecycle", terminal_status, sequence=4, state_revision=12)])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is True
    assert client.sendall.call_count == 2
    assert client.recv.call_count == 4


@pytest.mark.parametrize("kind", ["hello_ack", "lifecycle"])
@pytest.mark.parametrize("revision", [-1, True, None, "11", 11.5])
def test_protocol_stop_rejects_invalid_state_revisions(protocol_socket, kind, revision):
    events = [protocol_frame("hello_ack", "RUNNING", state_revision=10)] if kind == "lifecycle" else []
    events.append(protocol_frame(kind, "STOPPED", sequence=len(events) + 1, state_revision=revision))
    protocol_socket(events)
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False


@pytest.mark.parametrize("revision", [None, 11])
def test_protocol_stop_rejects_missing_or_reversed_live_revision(protocol_socket, revision):
    terminal = {} if revision is None else {"state_revision": revision}
    protocol_socket([protocol_frame("hello_ack", "RUNNING", state_revision=10),
                     protocol_frame("lifecycle", "DRAINING", sequence=2, state_revision=12),
                     protocol_frame("lifecycle", "STOPPED", sequence=3, **terminal)])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False


@pytest.mark.parametrize("terminal_status", ["STOPPED", "FAILED"])
def test_protocol_stop_legacy_terminal_requires_an_authoritative_retry(protocol_socket, terminal_status):
    client = protocol_socket([protocol_frame("hello_ack", "RUNNING"),
                              protocol_frame("lifecycle", terminal_status, sequence=2), b""])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False
    assert client.sendall.call_count == 2
    retry = protocol_socket([protocol_frame("hello_ack", terminal_status, sequence=3)])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is True
    assert retry.sendall.call_count == 1


def test_protocol_stop_legacy_wait_respects_the_call_timeout(monkeypatch):
    client, peer = socket.socketpair()
    try:
        peer.sendall(protocol_frame("hello_ack", "RUNNING"))
        monkeypatch.setattr(launcher.socket, "create_connection", lambda *args, **kwargs: client)
        started = time.monotonic()
        assert launcher.request_protocol_stop(18765, "owned-session", .05) is False
        assert time.monotonic() - started < .3
    finally:
        client.close()
        peer.close()


@pytest.mark.parametrize("changes", [{"session_id": "other-session"}, {"resumed": False}])
def test_protocol_stop_rejects_an_unrelated_session(protocol_socket, changes):
    client = protocol_socket([protocol_frame("hello_ack", "READY", **changes)])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False
    assert client.sendall.call_count == 1


def test_protocol_stop_requires_matching_lifecycle_identity(protocol_socket):
    client = protocol_socket([protocol_frame("hello_ack", "RUNNING"),
                              protocol_frame("lifecycle", "STOPPED", session_id="other-session")])
    assert launcher.request_protocol_stop(18765, "owned-session", 1.0) is False
    assert client.sendall.call_count == 2


@pytest.fixture
def owned_launch(tmp_path, monkeypatch):
    children = []
    session_files = []
    session_contents = ["owned-session"]
    report = {"ok": True, "backend": "SYNTH fixture", "project_dir": str(ROOT),
              "python": sys.executable, "scene": "fixture.yaml", "blender": "fixture-blender"}
    popen = subprocess.Popen

    def blender_run(*args, env, **kwargs):
        session_file = Path(env["WFRL_SESSION_FILE"])
        session_file.write_text(session_contents[0])
        session_files.append(session_file)
        return SimpleNamespace(returncode=0)

    def start(body="import time; time.sleep(60)", *, session_id="owned-session"):
        bridge = popen([sys.executable, "-c", body])
        session_contents[0] = session_id
        children.append(bridge)
        monkeypatch.setattr(launcher, "preflight", lambda *args, **kwargs: report)
        monkeypatch.setattr(launcher, "wait_for_bridge", lambda *args: None)
        monkeypatch.setattr(launcher.subprocess, "run", blender_run)
        monkeypatch.setattr(launcher.subprocess, "Popen", Mock(return_value=bridge))
        return bridge, session_files

    yield start
    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
    for path in session_files:
        path.unlink(missing_ok=True)


@pytest.mark.parametrize("declaration", ["fastfarm", "'fastfarm'", '"fastfarm"'])
def test_fastfarm_yaml_always_launches_bridge_under_mpi(tmp_path, monkeypatch, owned_launch, declaration):
    project = tmp_path / "project"
    trainer = project / "wfrl" / "studio" / "trainer.py"
    trainer.parent.mkdir(parents=True)
    trainer.write_text("class Trainer: pass\n")
    for package in (project / "wfrl", trainer.parent):
        (package / "__init__.py").touch()
    scene = project / "scene.yaml"
    scene.write_text(f"backend: {declaration}\n")
    mpi = executable(tmp_path / "mpi", "raise SystemExit(99)")
    fastfarm = executable(tmp_path / "fastfarm", "raise SystemExit(99)")
    blender = executable(tmp_path / "blender", "raise SystemExit(99)")
    args = SimpleNamespace(blender=str(blender), python=sys.executable, project_dir=str(project),
        scene=str(scene), fake=False, mpi=str(mpi), fastfarm=str(fastfarm), port=18766, startup_timeout=5)
    report = launcher.preflight(args, require_backend=True)
    assert report["ok"], report
    assert report["backend"] == "fastfarm"
    bridge, _ = owned_launch()
    monkeypatch.setattr(launcher, "preflight", lambda *args, **kwargs: report)
    monkeypatch.setattr(launcher, "request_protocol_stop", lambda *args: True)
    assert launcher.launch(args) == 0
    command = launcher.subprocess.Popen.call_args.args[0]
    assert command[:4] == [str(mpi.absolute()), "-n", "1", sys.executable]
    assert bridge.poll() is not None


def test_launch_retries_unconfirmed_stop_before_terminating_owned_bridge(tmp_path, monkeypatch, owned_launch, capsys):
    bridge, session_files = owned_launch()
    attempts = []
    stop_owned = launcher.stop_owned

    def protocol_stop(port, session_id, timeout):
        assert bridge.poll() is None
        assert session_files[0].read_text() == session_id == "owned-session"
        attempts.append(port)
        return len(attempts) == 2

    def terminate_after_confirmation(process, label, timeout=180):
        assert len(attempts) == 2
        return stop_owned(process, label, timeout=5)

    monkeypatch.setattr(launcher, "request_protocol_stop", protocol_stop)
    monkeypatch.setattr(launcher, "stop_owned", terminate_after_confirmation)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 0
    assert attempts == [8765, 8765]
    assert bridge.poll() is not None
    assert not session_files[0].exists()
    assert "backend stop was not confirmed" in capsys.readouterr().err


def socket_client(events):
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.recv.side_effect = events
    return client


def test_launch_legacy_stop_never_signals_before_authoritative_retry(tmp_path, monkeypatch, owned_launch):
    bridge, session_files = owned_launch()
    initial = socket_client([protocol_frame("hello_ack", "RUNNING"),
                             protocol_frame("lifecycle", "STOPPED", sequence=2), b""])
    retry = socket_client([protocol_frame("hello_ack", "STOPPED", sequence=3)])
    connect = Mock(side_effect=[initial, retry])
    monkeypatch.setattr(launcher.socket, "create_connection", connect)
    stop_owned = launcher.stop_owned

    def stop_after_authoritative_retry(process, label):
        assert connect.call_count == 2
        assert session_files[0].read_text() == "owned-session"
        return stop_owned(process, label, timeout=5)

    monkeypatch.setattr(launcher, "stop_owned", stop_after_authoritative_retry)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 0
    assert initial.sendall.call_count == 2
    assert retry.sendall.call_count == 1
    assert bridge.poll() is not None
    assert not session_files[0].exists()


@pytest.mark.parametrize("status", ["READY", "STOPPED", "FAILED"])
def test_launch_confirms_idle_bridge_when_bootstrap_has_no_session(
        tmp_path, monkeypatch, owned_launch, status):
    bridge, session_files = owned_launch(session_id="")
    client = socket_client([protocol_frame("hello_ack", status, session_id="idle-session", resumed=False)])
    monkeypatch.setattr(launcher.socket, "create_connection", Mock(return_value=client))
    stop_owned = launcher.stop_owned

    def stop_confirmed_idle(process, label):
        assert client.sendall.call_count == 1
        hello = json.loads(client.sendall.call_args.args[0][4:])
        assert hello["payload"]["resume_session_id"] is None
        assert session_files[0].read_text() == "idle-session"
        return stop_owned(process, label, timeout=5)

    monkeypatch.setattr(launcher, "stop_owned", stop_confirmed_idle)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 0
    assert bridge.poll() is not None
    assert not session_files[0].exists()


@pytest.mark.parametrize("response", [
    protocol_frame("hello_ack", "RUNNING", session_id="unowned-session", resumed=False),
    protocol_frame("error", "RUNNING", session_id="unowned-session", fatal=True),
    b"",
])
def test_launch_never_adopts_or_signals_unknown_active_session(
        tmp_path, monkeypatch, owned_launch, capsys, response):
    bridge, session_files = owned_launch(session_id="")
    client = socket_client([response])
    connect = Mock(side_effect=[client, KeyboardInterrupt()])
    monkeypatch.setattr(launcher.socket, "create_connection", connect)
    stop_owned = Mock(side_effect=AssertionError("cannot signal an unconfirmed backend"))
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    with pytest.raises(KeyboardInterrupt):
        launcher.main(["launch", *base_args(tmp_path), "--fake"])
    assert connect.call_count == 2
    stop_owned.assert_not_called()
    assert bridge.poll() is None
    assert session_files[0].read_text() == ""
    assert client.sendall.call_count == 1
    assert "stop-session" in capsys.readouterr().err


def test_launch_observes_natural_bridge_exit_during_stop_retry(tmp_path, monkeypatch, owned_launch):
    marker = tmp_path / "exit"
    bridge, session_files = owned_launch(
        "from pathlib import Path\nimport time\n"
        f"while not Path({str(marker)!r}).exists(): time.sleep(.01)\n")

    def protocol_stop(*args):
        marker.write_text("exit")
        bridge.wait(timeout=5)
        return False

    monkeypatch.setattr(launcher, "request_protocol_stop", protocol_stop)
    stop_owned = Mock(return_value=True)
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 0
    stop_owned.assert_not_called()
    assert bridge.poll() == 0
    assert not session_files[0].exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), RuntimeError("protocol failure")])
def test_launch_retains_recovery_credentials_when_supervision_is_interrupted(
        tmp_path, monkeypatch, owned_launch, capsys, failure):
    bridge, session_files = owned_launch()
    protocol_stop = Mock(side_effect=[False, failure])
    monkeypatch.setattr(launcher, "request_protocol_stop", protocol_stop)
    stop_owned = Mock(return_value=True)
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    command = ["launch", *base_args(tmp_path), "--fake"]
    if isinstance(failure, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            launcher.main(command)
    else:
        assert launcher.main(command) == 1
    assert protocol_stop.call_count == 2
    stop_owned.assert_not_called()
    assert bridge.poll() is None
    assert session_files[0].read_text() == "owned-session"
    error = capsys.readouterr().err
    assert "stop-session" in error
    assert str(session_files[0]) in error
    assert "--port 8765" in error


@pytest.mark.parametrize("confirmed", [False, True])
def test_stop_session_uses_protocol_identity_and_preserves_credentials(tmp_path, monkeypatch, confirmed):
    session_file = tmp_path / "session.txt"
    session_file.write_text("owned-session")
    request = Mock(return_value=confirmed)
    monkeypatch.setattr(launcher, "request_protocol_stop", request)
    stop_owned = Mock()
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    assert launcher.main(["stop-session", "--session-file", str(session_file), "--port", "18765"]) == (
        0 if confirmed else 1)
    request.assert_called_once_with(18765, "owned-session", 180.0)
    stop_owned.assert_not_called()
    assert session_file.read_text() == "owned-session"


@pytest.mark.parametrize("status", ["READY", "RUNNING"])
def test_stop_session_without_credentials_only_claims_a_confirmed_idle_session(tmp_path, monkeypatch, status):
    session_file = tmp_path / "session.txt"
    session_file.write_text("")
    client = socket_client([protocol_frame("hello_ack", status, session_id="idle-session", resumed=False)])
    monkeypatch.setattr(launcher.socket, "create_connection", Mock(return_value=client))
    stop_owned = Mock()
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    assert launcher.main(["stop-session", "--session-file", str(session_file)]) == (0 if status == "READY" else 1)
    assert client.sendall.call_count == 1
    assert session_file.read_text() == ("idle-session" if status == "READY" else "")
    stop_owned.assert_not_called()
