from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import venv
import psutil

import pytest
from blender_frontend.tests._platform import symlink_or_skip, venv_python, write_python_tool
from wfrl.blender_bridge import launcher_state


ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts" / "blender" / "wfrl_launcher.py"
SPEC = importlib.util.spec_from_file_location("wfrl_launcher", PATH)
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def base_args(tmp_path: Path, blender: Path | None = None) -> list[str]:
    blender = blender or write_python_tool(tmp_path / "blender", "raise SystemExit(0)")
    return ["--blender", str(blender), "--python", sys.executable,
            "--project-dir", str(ROOT), "--scene", str(ROOT / "scenes" / "turb3_demo.yaml")]


def test_verify_fake_reports_fixture_ready(tmp_path, capsys):
    code = launcher.main(["verify", *base_args(tmp_path), "--fake", "--json"])
    output = capsys.readouterr().out
    assert code == 0
    assert '"backend": "SYNTH fixture"' in output
    assert '"ok": true' in output


def test_python_executable_keeps_virtual_environment_entry(tmp_path):
    python = venv_python(tmp_path / 'venv')
    python.parent.mkdir(parents=True)
    symlink_or_skip(python, sys.executable)
    assert launcher.resolve_executable(str(python), 'Python') == str(python.absolute())


def test_selected_python_executes_inside_its_virtual_environment(tmp_path):
    environment = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False, symlinks=os.name == 'posix').create(environment)
    selected = launcher.resolve_executable(str(venv_python(environment)), "Python")
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


def test_launch_fake_cleans_up_owned_bridge(tmp_path, python_tool):
    marker = tmp_path / "blender-ran"
    blender = python_tool(tmp_path / "blender", f"from pathlib import Path\nPath({str(marker)!r}).write_text('ok')")
    code = launcher.main(["launch", *base_args(tmp_path, blender), "--fake",
                          "--port", "18765", "--startup-timeout", "5"])
    assert code == 0
    assert marker.read_text() == "ok"
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", 18765)) != 0


def protocol_frame(kind, status=None, *, session_id="owned-session", sequence=1, **changes):
    payload = {"run_status": status, "mode": "demo", "capabilities": []}
    if kind in {'hello_ack', 'lifecycle'}:
        inactive = status in {'READY', 'STOPPED', 'FAILED'}
        payload.update(cleanup_confirmed=inactive, worker_alive=not inactive)
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
        assert launcher_state.RUN_TOKEN not in env
        assert launcher_state.BINDING_FILE not in env
        assert 'WFRL_SESSION_FILE' not in env
        return SimpleNamespace(returncode=0)

    def start(body="import time; time.sleep(60)", *, session_id="owned-session"):
        session_contents[0] = session_id
        class Process:
            current = None

            def __getattr__(self, name):
                return getattr(self.current, name)
        bridge = Process()

        def spawn(argv, *, env, **kwargs):
            bridge.current = popen([sys.executable, '-c', body], env=env, **kwargs)
            children.append(bridge.current)
            binding_file = Path(env[launcher_state.BINDING_FILE])
            session_file = Path(str(binding_file).removesuffix('.bridge.json'))
            session_files.append(session_file)
            if session_contents[0]:
                launcher_state.atomic_write(binding_file, dict(schema=launcher_state.BINDING_SCHEMA,
                    run_token=env[launcher_state.RUN_TOKEN], host='127.0.0.1', port=int(env['WFRL_PORT']),
                    bridge=launcher_state.process_identity(bridge.pid), session_id=session_contents[0],
                    session_generation=0))
            return bridge.current

        monkeypatch.setattr(launcher, "preflight", lambda *args, **kwargs: report)
        monkeypatch.setattr(launcher, "wait_for_bridge", lambda *args: None)
        monkeypatch.setattr(launcher.subprocess, "run", blender_run)
        monkeypatch.setattr(launcher.subprocess, "Popen", Mock(side_effect=spawn))
        return bridge, session_files

    yield start
    for child in children:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
    for path in session_files:
        path.unlink(missing_ok=True)
        launcher_state.binding_path(path).unlink(missing_ok=True)


def saved_session(path):
    binding = launcher_state.binding_path(path)
    return json.loads(binding.read_text())['session_id'] if binding.exists() else ''


def test_launch_keeps_supervising_detached_child_after_parent_exits(tmp_path, monkeypatch, owned_launch):
    child_pid, release_parent, release_child = [tmp_path/name for name in ('child.pid','parent.exit','child.exit')]
    child_code = (f'from pathlib import Path; import os,time\n'
        f'Path({str(child_pid)!r}).write_text(str(os.getpid()))\n'
        f'deadline=time.monotonic()+30\n'
        f'while not Path({str(release_child)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n')
    parent_code = ('from wfrl.blender_bridge.formal_process import OwnedProcesses\n'
        'from pathlib import Path\nimport os,sys,time\n'
        f'OwnedProcesses().start([sys.executable,"-c",{child_code!r}],cwd=os.getcwd(),env=os.environ)\n'
        f'while not Path({str(release_parent)!r}).exists(): time.sleep(.01)\n')
    unrelated = subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
    bridge, files = owned_launch(parent_code)
    real_sleep, observed = time.sleep, []
    child = None
    def stop(*args):
        deadline=time.monotonic()+5
        while not child_pid.exists():
            assert time.monotonic()<deadline
            real_sleep(.01)
        release_parent.touch()
        while bridge.poll() is None:
            assert time.monotonic()<deadline
            real_sleep(.01)
        return False
    def wait(_delay):
        if not observed:
            process=psutil.Process(int(child_pid.read_text()))
            assert process.is_running() and bridge.poll()==0
            state=json.loads(files[0].read_text())
            assert state['process_status']=='LIVE' and not state['protocol_stop_confirmed']
            observed.append(process.pid)
            release_child.touch()
        real_sleep(.02)
    monkeypatch.setattr(launcher,'request_protocol_stop',stop)
    monkeypatch.setattr(launcher.time,'sleep',wait)
    monkeypatch.setattr(launcher,'stop_owned',Mock(side_effect=AssertionError('Stop not confirmed')))
    try:
        assert launcher.main(['launch',*base_args(tmp_path),'--fake']) == 1
        assert observed and not files[0].exists()
        assert unrelated.poll() is None
    finally:
        release_parent.touch()
        release_child.touch()
        if child_pid.exists():
            try:
                child=psutil.Process(int(child_pid.read_text()))
                if child.is_running():
                    child.kill()
            except psutil.NoSuchProcess:
                pass
        monkeypatch.setattr(launcher.time,'sleep',real_sleep)
        unrelated.terminate()
        unrelated.wait(5)


@pytest.mark.parametrize('confirmed', [False, True])
@pytest.mark.parametrize('transient', [False, True])
def test_unknown_process_inventory_retains_records_after_root_exit(tmp_path, monkeypatch, owned_launch, capsys,
                                                                 confirmed, transient):
    bridge, files=owned_launch()
    state_api, cls=launcher.ownership_modules()
    class UnknownInventory(cls):
        unknown_seen = False
        def status(self):
            if self.process is not None and self.process.poll() is not None:
                if transient and self.unknown_seen:
                    return 'EMPTY_CONFIRMED'
                self.unknown_seen = True
                return 'UNKNOWN'
            return super().status()
    monkeypatch.setattr(launcher,'ownership_modules',lambda:(state_api,UnknownInventory))
    monkeypatch.setattr(state_api, 'read_binding',
                        lambda path, owner, **kwargs: json.loads(state_api.binding_path(path).read_text()))
    def stop(*args):
        bridge.terminate()
        deadline=time.monotonic()+5
        while bridge.poll() is None:
            assert time.monotonic()<deadline
            real_sleep(.01)
        return confirmed
    real_sleep=time.sleep
    monkeypatch.setattr(launcher,'request_protocol_stop',stop)
    monkeypatch.setattr(launcher.time,'sleep',Mock(side_effect=AssertionError('UNKNOWN must not loop')))
    assert launcher.main(['launch',*base_args(tmp_path),'--fake']) == 1
    assert bridge.poll() is not None and files[0].exists()
    assert json.loads(files[0].read_text())['process_status']=='UNKNOWN'
    assert launcher_state.binding_path(files[0]).exists()
    assert json.loads(files[0].read_text())['known_processes']
    error = capsys.readouterr().err
    assert 'UNKNOWN' in error and 'stop-session' in error and str(files[0]) in error


@pytest.mark.parametrize("declaration", ["fastfarm", "'fastfarm'", '"fastfarm"'])
def test_fastfarm_yaml_always_launches_bridge_under_mpi(tmp_path, monkeypatch, owned_launch, declaration):
    project = tmp_path / "project"
    trainer = project / "wfrl" / "studio" / "trainer.py"
    trainer.parent.mkdir(parents=True)
    trainer.write_text("class Trainer: pass\n")
    for package in (project / "wfrl", trainer.parent):
        (package / "__init__.py").touch()
    bridge_package = project / 'wfrl/blender_bridge'
    bridge_package.mkdir()
    (bridge_package / '__init__.py').touch()
    (bridge_package / 'launcher_state.py').write_text('class SessionBinding: pass\n')
    scene = project / "scene.yaml"
    scene.write_text(f"backend: {declaration}\n")
    mpi = write_python_tool(tmp_path / "mpi", "raise SystemExit(99)")
    fastfarm = write_python_tool(tmp_path / "fastfarm", "raise SystemExit(99)")
    blender = write_python_tool(tmp_path / "blender", "raise SystemExit(99)")
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
        assert saved_session(session_files[0]) == session_id == "owned-session"
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
        assert saved_session(session_files[0]) == "owned-session"
        return stop_owned(process, label, timeout=5)

    monkeypatch.setattr(launcher, "stop_owned", stop_after_authoritative_retry)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 0
    assert initial.sendall.call_count == 2
    assert retry.sendall.call_count == 1
    assert bridge.poll() is not None
    assert not session_files[0].exists()


@pytest.mark.parametrize("status", ["READY", "STOPPED", "FAILED"])
def test_launch_confirms_bridge_published_session_without_frontend_identity(
        tmp_path, monkeypatch, owned_launch, status):
    bridge, session_files = owned_launch(session_id="idle-session")
    client = socket_client([protocol_frame("hello_ack", status, session_id="idle-session")])
    monkeypatch.setattr(launcher.socket, "create_connection", Mock(return_value=client))
    stop_owned = launcher.stop_owned

    def stop_confirmed_idle(process, label):
        assert client.sendall.call_count == 1
        hello = json.loads(client.sendall.call_args.args[0][4:])
        assert hello["payload"]["resume_session_id"] == 'idle-session'
        assert saved_session(session_files[0]) == "idle-session"
        return stop_owned(process, label, timeout=5)

    monkeypatch.setattr(launcher, "stop_owned", stop_confirmed_idle)
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 0
    assert bridge.poll() is not None
    assert not session_files[0].exists()


def test_launch_never_adopts_or_signals_unknown_active_session(
        tmp_path, monkeypatch, owned_launch, capsys):
    bridge, session_files = owned_launch(session_id="")
    monkeypatch.setattr(launcher, 'wait_for_bridge', Mock(side_effect=RuntimeError('Controlled startup failure')))
    connect = Mock(side_effect=AssertionError('No authoritative owned Bridge binding'))
    monkeypatch.setattr(launcher.socket, "create_connection", connect)
    sleep = launcher.time.sleep
    def interrupt_wait(_delay):
        monkeypatch.setattr(launcher.time, 'sleep', sleep)
        raise KeyboardInterrupt()
    monkeypatch.setattr(launcher.time, 'sleep', interrupt_wait)
    stop_owned = Mock(side_effect=AssertionError("cannot signal an unconfirmed backend"))
    monkeypatch.setattr(launcher, "stop_owned", stop_owned)
    with pytest.raises(KeyboardInterrupt):
        launcher.main(["launch", *base_args(tmp_path), "--fake"])
    connect.assert_not_called()
    stop_owned.assert_not_called()
    assert bridge.poll() is None
    assert saved_session(session_files[0]) == ""
    assert "stop-session" in capsys.readouterr().err


def test_unverifiable_live_binding_reports_once_across_retries(tmp_path, monkeypatch, owned_launch, capsys):
    bridge, files = owned_launch()
    def binding(path, owner, **kwargs):
        path = launcher_state.binding_path(path)
        return json.loads(path.read_text()) if path.exists() else None
    monkeypatch.setattr(launcher_state, 'read_binding', binding)
    def blender(*args, **kwargs):
        launcher_state.binding_path(files[0]).unlink()
        return SimpleNamespace(returncode=0)
    attempts = []
    def wait(_delay):
        attempts.append(1)
        assert len(attempts) <= 3
        if len(attempts) == 3:
            bridge.terminate()
            bridge.wait()
    monkeypatch.setattr(launcher.subprocess, 'run', blender)
    monkeypatch.setattr(launcher.time, 'sleep', wait)
    monkeypatch.setattr(launcher, 'request_protocol_stop', Mock(side_effect=AssertionError('No verified binding')))
    assert launcher.main(['launch', *base_args(tmp_path), '--fake']) == 1
    assert len(attempts) == 3
    assert capsys.readouterr().err.count('backend stop was not confirmed') == 1


@pytest.mark.parametrize('child_state, expected', [
    ('gone', 'EMPTY_CONFIRMED'), ('reused', 'EMPTY_CONFIRMED'), ('live', 'LIVE'), ('denied', 'UNKNOWN')])
def test_stop_session_rechecks_recorded_identities_after_bridge_exit(tmp_path, monkeypatch, capsys,
                                                                  child_state, expected):
    root = dict(pid=12345, create_time=time.time() - 2)
    child = dict(pid=12346, create_time=time.time() - 1)
    path = tmp_path/'owner.json'
    owner = dict(schema=launcher_state.OWNER_SCHEMA, run_token='run', host='127.0.0.1', port=18765,
                 root=root, known_processes=[root, child], process_status='UNKNOWN')
    launcher_state.atomic_write(path, owner)
    binding = launcher_state.binding_path(path)
    launcher_state.atomic_write(binding, dict(schema=launcher_state.BINDING_SCHEMA, run_token='run',
        host=owner['host'], port=owner['port'], bridge=root, session_id='session', session_generation=0))
    candidate = Mock(pid=child['pid'])
    candidate.create_time.return_value = child['create_time'] + (1 if child_state == 'reused' else 0)
    candidate.is_running.return_value = True
    candidate.status.return_value = psutil.STATUS_RUNNING
    candidate.children.return_value = []
    def process(pid=None):
        if pid is None:
            return SimpleNamespace(username=lambda: 'user')
        if pid == root['pid'] or child_state == 'gone':
            raise psutil.NoSuchProcess(pid)
        if child_state == 'denied':
            raise psutil.AccessDenied(pid)
        return candidate
    monkeypatch.setattr(psutil, 'Process', process)
    monkeypatch.setattr(psutil, 'process_iter', lambda attrs: iter([]))
    request = Mock(side_effect=AssertionError('Exited Bridge must not receive a protocol request'))
    monkeypatch.setattr(launcher, 'request_protocol_stop', request)
    empty = expected == 'EMPTY_CONFIRMED'
    assert launcher.main(['stop-session', '--session-file', str(path)]) == (0 if empty else 1)
    assert path.exists() is (not empty) and binding.exists() is (not empty)
    output = capsys.readouterr()
    assert expected in output.out + output.err
    if not empty:
        saved = json.loads(path.read_text())
        assert saved['process_status'] == expected and child in saved['known_processes']
    candidate.send_signal.assert_not_called()
    request.assert_not_called()


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
    assert launcher.main(["launch", *base_args(tmp_path), "--fake"]) == 1
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
    assert saved_session(session_files[0]) == "owned-session"
    error = capsys.readouterr().err
    assert "stop-session" in error
    assert str(session_files[0]) in error
    assert "--port 8765" in error


def test_failed_live_backend_never_authorizes_bridge_exit_or_credential_removal(
        tmp_path, monkeypatch, owned_launch):
    from wfrl.blender_bridge.backend_session import BackendSession
    session = BackendSession()
    session._trainer = SimpleNamespace(running=True, runtime=None, stop=lambda timeout: None)
    session.lifecycle('FAILED', 'Controlled worker failure')
    bridge, session_files = owned_launch(session_id=session.session_id)
    client = socket_client([protocol_frame('hello_ack', session_id=session.session_id,
                                           **session.synchronization_state()), b''])
    connect = Mock(side_effect=[client, KeyboardInterrupt()])
    monkeypatch.setattr(launcher.socket, 'create_connection', connect)
    stop_owned = Mock(side_effect=AssertionError('Cannot stop Bridge while worker cleanup is unconfirmed'))
    monkeypatch.setattr(launcher, 'stop_owned', stop_owned)
    with pytest.raises(KeyboardInterrupt):
        launcher.main(['launch', *base_args(tmp_path), '--fake'])
    assert bridge.poll() is None
    assert saved_session(session_files[0]) == session.session_id
    assert client.sendall.call_count == 2
    stop_owned.assert_not_called()


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
