"""Install the built ZIP into a fresh Blender profile and smoke the installed copy.

This script deliberately does not add ``blender_frontend`` to ``sys.path``.  Its
inner mode runs inside Blender after the extension was installed and enabled by
Blender's extension CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
EVIDENCE = ROOT / "evidence" / "part7"


def _inside_blender(output: Path, source_root: Path) -> None:
    import bpy

    modules = [module for name, module in sys.modules.items()
               if name.endswith(".wfrl_blender") or name == "wfrl_blender"]
    assert len(modules) == 1, [getattr(module, "__file__", None) for module in modules]
    addon = modules[0]
    installed_path = Path(addon.__file__).resolve()
    assert not installed_path.is_relative_to(source_root.resolve()), installed_path
    assert "extensions" in installed_path.parts, installed_path

    from importlib import import_module
    required_panels = (
        ("panels.status", "WFRL_PT_connection"),
        ("panels.run", "WFRL_PT_WorkflowRun"),
        ("panels.scene", "WFRL_PT_WorkflowScene"),
        ("panels.safety", "WFRL_PT_WorkflowSafety"),
        ("panels.telemetry", "WFRL_PT_live_telemetry"),
        ("panels.demo", "WFRL_PT_Manual"),
    )
    missing_types = [class_name for module_name, class_name in required_panels
                     if not getattr(import_module(addon.__package__ + "." + module_name), class_name).is_registered]
    assert not missing_types, missing_types
    assert hasattr(bpy.types.Scene, "wfrl_workflow")
    assert hasattr(bpy.types.Scene, "wfrl_chart_channel")
    assert hasattr(bpy.ops.wfrl, "capture_screenshot")
    assert hasattr(bpy.ops.wfrl, "capture_recording")

    asset_root = installed_path.parent / "assets"
    required_assets = (
        asset_root / "nrel5mw_geometry.json",
        asset_root / "landscape" / "SOURCES.md",
        asset_root / "landscape" / "kloofendal_48d_partly_cloudy_puresky_2k.hdr",
        asset_root / "landscape" / "rocky_terrain_02_diff_2k.jpg",
        asset_root / "landscape" / "rocky_terrain_02_nor_gl_2k.jpg",
        asset_root / "landscape" / "rocky_terrain_02_rough_2k.jpg",
    )
    assert all(path.is_file() and path.stat().st_size for path in required_assets)

    assert bpy.ops.wfrl.load_demo() == {"FINISHED"}
    assert bpy.ops.wfrl.demo_start() == {"FINISHED"}
    assert bpy.ops.wfrl.demo_pause() == {"FINISHED"}
    scene = bpy.context.scene
    scene.wfrl_manual_enabled = True
    scene.wfrl_manual_yaw = 12.0
    assert scene["wfrl_run_status"] == "PAUSED"
    assert not bpy.ops.wfrl.load_demo.poll()
    runtime = import_module(addon.__package__ + ".runtime")
    assert not runtime.configuration_editable()
    try:
        runtime.select_mode("interactive_training")
    except ValueError:
        pass
    else:
        raise AssertionError("active demo did not lock mode changes")

    charts = import_module(addon.__package__ + ".charts")
    export_path = output.with_name("package-history-export.json")
    charts.export_job.start(export_path, charts.raw_history.snapshot())
    deadline = time.monotonic() + 10
    while charts.export_job.poll() == "WRITING" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert charts.export_job.status == "COMPLETE", charts.export_job.error
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert exported["schema_version"] == "wfrl.history.v1"

    result = {
        "schema_version": "wfrl.part7-installed-smoke.v1",
        "blender_version": bpy.app.version_string,
        "module_name": addon.__name__,
        "installed_module": str(installed_path),
        "source_checkout": str(source_root.resolve()),
        "source_checkout_on_sys_path": any(
            Path(entry or ".").resolve().is_relative_to(source_root.resolve()) for entry in sys.path
        ),
        "registered_types": [class_name for _, class_name in required_panels],
        "capabilities": ["demo", "manual_pose", "training_ui", "history_export",
                         "screenshot_operator", "recording_operator"],
        "assets": [{"path": str(path.relative_to(installed_path.parent)), "size": path.stat().st_size}
                   for path in required_assets],
        "locks": {"load_demo_during_pause": True, "mode_change_during_pause": True},
        "history_export": str(export_path),
        "status": "PASS",
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("WFRL_PART7_INSTALLED_SMOKE=PASS")


def _run(command: list[str], env: dict[str, str], log: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.append("$ " + " ".join(command) + "\n" + completed.stdout)
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout)


def verify(blender: Path = BLENDER) -> None:
    from build_extension import build

    result = build()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    install_evidence = EVIDENCE / "package-install.json"
    install_evidence.unlink(missing_ok=True)
    log: list[str] = []
    with tempfile.TemporaryDirectory(prefix="wfrl-part7-profile-") as profile_string:
        profile = Path(profile_string)
        config = profile / "config"
        scripts = profile / "scripts"
        datafiles = profile / "datafiles"
        repository = profile / "extensions"
        for directory in (config, scripts, datafiles, repository):
            directory.mkdir()
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.update(BLENDER_USER_CONFIG=str(config), BLENDER_USER_SCRIPTS=str(scripts),
                   BLENDER_USER_DATAFILES=str(datafiles))
        _run([str(blender), "--command", "extension", "repo-add", "wfrl_part7",
              "--name", "WFRL Part 7 Fresh Install", "--directory", str(repository),
              "--clear-all"], env, log)
        _run([str(blender), "--command", "extension", "install-file", "-r", "wfrl_part7",
              "--enable", str(result.archive)], env, log)
        _run([str(blender), "--background", "--python-exit-code", "1",
              "--python", str(Path(__file__).resolve()), "--",
              "--inside", "--output", str(install_evidence),
              "--source-root", str(ROOT)], env, log)
        if not install_evidence.is_file():
            (EVIDENCE / "package-install.log").write_text("\n".join(log), encoding="utf-8")
            raise RuntimeError("Blender did not produce package-install.json; inspect package-install.log")
        installed_files = sorted(path.relative_to(repository).as_posix()
                                 for path in repository.rglob("*") if path.is_file())
        evidence = json.loads(install_evidence.read_text(encoding="utf-8"))
        evidence.update(
            archive=str(result.archive.relative_to(ROOT)), archive_sha256=result.sha256,
            checksum_file=str(result.checksum.relative_to(ROOT)),
            inventory_file=str(result.inventory.relative_to(ROOT)),
            installed_file_count=len(installed_files),
            installed_inventory_sha256=hashlib.sha256("\n".join(installed_files).encode()).hexdigest(),
            fresh_profile=True,
        )
        install_evidence.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (EVIDENCE / "package-install.log").write_text("\n".join(log), encoding="utf-8")
    (EVIDENCE / "package-inventory.json").write_bytes(result.inventory.read_bytes())
    (EVIDENCE / "package-checksum.sha256").write_bytes(result.checksum.read_bytes())
    print(EVIDENCE / "package-install.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--blender", type=Path, default=BLENDER)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
    if args.inside:
        _inside_blender(args.output, args.source_root)
    else:
        verify(args.blender)


if __name__ == "__main__":
    main()
