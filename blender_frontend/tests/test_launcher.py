from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import stat
import sys


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
