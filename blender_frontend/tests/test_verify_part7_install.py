"""Host tests for the installed-package smoke test's output and profile."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts.blender import verify_part7_install as smoke


def stub_install(monkeypatch, evidence_dir, *, produce_output=True):
    calls = []
    sha256 = "a" * 64
    profile_directories = {
        "BLENDER_USER_CONFIG": "config",
        "BLENDER_USER_SCRIPTS": "scripts",
        "BLENDER_USER_DATAFILES": "datafiles",
        "BLENDER_USER_EXTENSIONS": "extensions",
        "TMPDIR": "tmp",
        "XDG_CACHE_HOME": "cache",
    }
    for key in (*profile_directories, "BLENDER_USER_RESOURCES", "BLENDER_USER_OTHER", "PYTHONPATH"):
        monkeypatch.setenv(key, str(evidence_dir.parent / "daily"))

    def fake_build(output_dir):
        assert output_dir.parent.parent == evidence_dir
        assert output_dir.name == "build"
        output_dir.mkdir()
        archive = output_dir / "wfrl_blender-1.2.3.zip"
        checksum = archive.with_suffix(".zip.sha256")
        inventory = archive.with_suffix(".inventory.json")
        archive.write_bytes(b"ZIP fixture")
        checksum.write_text(f"{sha256}  {archive.name}\n", encoding="utf-8")
        inventory.write_text('{"version": "1.2.3"}\n', encoding="utf-8")
        calls.append("build")
        return SimpleNamespace(archive=archive, checksum=checksum, inventory=inventory, sha256=sha256)

    def fake_run(command, *, cwd, env, **kwargs):
        assert cwd.parent == evidence_dir
        assert "--background" in command
        assert "PYTHONPATH" not in env
        assert "BLENDER_USER_OTHER" not in env
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        assert not (evidence_dir / "package-install.json").exists()
        profile = Path(env["BLENDER_USER_RESOURCES"])
        assert profile == cwd / "profile"
        for key, name in profile_directories.items():
            assert Path(env[key]) == profile / name
            assert Path(env[key]).is_dir()
        assert env["TMP"] == env["TEMP"] == env["TMPDIR"]
        if "--inside" in command:
            calls.append("smoke")
            output = Path(command[command.index("--output") + 1])
            assert output == cwd / "package-install.json"
            if produce_output:
                output.write_text('{"status": "PASS"}\n', encoding="utf-8")
                output.with_name("package-history-export.json").write_text(
                    '{"schema_version": "wfrl.history.v1"}\n', encoding="utf-8")
        else:
            operation = command[command.index("extension") + 1]
            calls.append(operation)
            if operation == "repo-add":
                assert Path(command[command.index("--directory") + 1]) == profile / "extensions"
            else:
                assert Path(command[-1]) == cwd / "build" / "wfrl_blender-1.2.3.zip"
                assert Path(command[-1]).is_file()
        return SimpleNamespace(returncode=0, stdout="Blender fixture\n")

    monkeypatch.setitem(sys.modules, "build_extension", SimpleNamespace(build=fake_build))
    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    return calls


@pytest.mark.parametrize("custom", [True, False], ids=["custom", "default"])
def test_install_cli_confines_writes_and_deletion(tmp_path, monkeypatch, custom):
    default_dir = tmp_path / "checkout" / "evidence" / "part7"
    evidence_dir = tmp_path / "custom-evidence" if custom else default_dir
    outside_dir = default_dir if custom else tmp_path / "other-evidence"
    for directory in (evidence_dir, outside_dir):
        directory.mkdir(parents=True)
        (directory / "package-install.json").write_text("previous report", encoding="utf-8")
    outside_report = outside_dir / "package-install.json"
    outside_mtime = outside_report.stat().st_mtime_ns
    monkeypatch.setattr(smoke, "ROOT", tmp_path / "checkout")
    monkeypatch.setattr(smoke, "EVIDENCE", default_dir)
    monkeypatch.chdir(tmp_path)
    args = ["--evidence-dir", "custom-evidence"] if custom else []
    monkeypatch.setattr(sys, "argv", ["verify_part7_install.py", *args])
    calls = stub_install(monkeypatch, evidence_dir)

    smoke.main()

    assert calls == ["build", "repo-add", "install-file", "smoke"]
    assert list(outside_dir.iterdir()) == [outside_report]
    assert outside_report.read_text(encoding="utf-8") == "previous report"
    assert outside_report.stat().st_mtime_ns == outside_mtime
    assert {path.name for path in evidence_dir.iterdir()} == {
        "package-install.json", "package-install.log", "package-history-export.json",
        "package-inventory.json", "package-checksum.sha256",
    }
    report = json.loads((evidence_dir / "package-install.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["extension_version"] == "1.2.3"
    assert report["archive"] == "wfrl_blender-1.2.3.zip"
    assert report["archive_sha256"] == "a" * 64
    assert report["fresh_profile"] is True
    for key, filename in (
        ("checksum_file", "package-checksum.sha256"),
        ("inventory_file", "package-inventory.json"),
        ("history_export", "package-history-export.json"),
    ):
        assert report[key] == filename
        assert (evidence_dir / report[key]).is_file()


def test_install_cli_rejects_missing_smoke_output(tmp_path, monkeypatch):
    evidence_dir = tmp_path / "missing"
    evidence_dir.mkdir()
    (evidence_dir / "package-install.json").write_text("stale report", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["verify_part7_install.py", "--evidence-dir", str(evidence_dir)])
    calls = stub_install(monkeypatch, evidence_dir, produce_output=False)

    with pytest.raises(RuntimeError, match="Blender did not produce package-install.json"):
        smoke.main()

    assert calls == ["build", "repo-add", "install-file", "smoke"]
    log = evidence_dir / "package-install.log"
    assert list(evidence_dir.iterdir()) == [log]
    assert "Blender fixture" in log.read_text(encoding="utf-8")
