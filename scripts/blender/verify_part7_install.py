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
    repository = Path(os.environ["BLENDER_USER_EXTENSIONS"]).resolve()
    assert installed_path == repository / "wfrl_blender" / "__init__.py", installed_path

    from importlib import import_module
    required_panels = (
        ("panels.status", "WFRL_PT_connection"),
        ("panels.run", "WFRL_PT_WorkflowRun"),
        ("panels.scene", "WFRL_PT_WorkflowScene"),
        ("panels.safety", "WFRL_PT_WorkflowSafety"),
        ("panels.telemetry", "WFRL_PT_live_telemetry"),
        ("panels.home", "WFRL_PT_Home"),
        ("panels.manual_control", "WFRL_PT_ManualControl"),
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
        asset_root / "landscape" / "rocky_terrain_02_rough_2k.jpg",
    )
    assert all(path.is_file() and path.stat().st_size for path in required_assets)

    assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
    farm = import_module(addon.__package__ + '.farm_flex')
    replay = import_module(addon.__package__ + '.clearance_replay')
    scene = bpy.context.scene
    assert farm.is_active(scene)
    stamp = replay.sample(scene)['time_s']
    assert bpy.ops.wfrl.farm_transport(action='STEP') == {'FINISHED'}
    assert replay.sample(scene)['time_s'] > stamp
    assert bpy.ops.wfrl.farm_transport(action='STOP') == {'FINISHED'}
    runtime = import_module(addon.__package__ + '.runtime')
    assert runtime.get_state().connection == 'OFFLINE RESULTS'
    assert runtime.configuration_editable()
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            assert not scene.camera.data.show_passepartout
    assert not ({'torch', 'mpi4py', 'floris'} & sys.modules.keys())

    charts = import_module(addon.__package__ + ".charts")
    export_path = output.with_name("package-history-export.json")
    charts.export_job.start(export_path, charts.raw_history_for(scene).snapshot())
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
        "offline_results": {"ready": True, "backend_required": False},
        "history_export": str(export_path),
        "status": "PASS",
    }
    import_module(addon.__package__ + ".atomic_json").write_json(output, result)
    print("WFRL_PART7_INSTALLED_SMOKE=PASS")


def _run(command: list[str], env: dict[str, str], log: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.append("$ " + " ".join(command) + "\n" + completed.stdout)
    if completed.returncode:
        raise subprocess.CalledProcessError(completed.returncode, command, completed.stdout)


def verify(blender: Path = BLENDER, *, evidence_dir: Path = EVIDENCE) -> None:
    from build_extension import build

    if blender.parent != Path("."):
        blender = ROOT / blender
    evidence_dir = evidence_dir.resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)
    install_evidence = evidence_dir / "package-install.json"
    log_path = evidence_dir / "package-install.log"
    log: list[str] = []
    with tempfile.TemporaryDirectory(prefix=".wfrl-part7-", dir=evidence_dir) as directory:
        scratch = Path(directory)
        result = build(output_dir=scratch / "build")
        install_evidence.unlink(missing_ok=True)
        profile = scratch / "profile"
        repository = profile / "extensions"
        directories = {
            "BLENDER_USER_CONFIG": profile / "config",
            "BLENDER_USER_SCRIPTS": profile / "scripts",
            "BLENDER_USER_DATAFILES": profile / "datafiles",
            "BLENDER_USER_EXTENSIONS": repository,
            "TMPDIR": profile / "tmp",
            "XDG_CACHE_HOME": profile / "cache",
        }
        for path in directories.values():
            path.mkdir(parents=True)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("BLENDER_USER_") and key != "PYTHONPATH"}
        env.update({key: str(path) for key, path in directories.items()})
        env.update(BLENDER_USER_RESOURCES=str(profile), TMP=env["TMPDIR"], TEMP=env["TMPDIR"],
                   PYTHONDONTWRITEBYTECODE="1")
        command = [str(blender), "--background", "--disable-autoexec", "--python-exit-code", "1",
                   "--python-expr", "import sys; sys.dont_write_bytecode = True"]
        staged_install = scratch / install_evidence.name
        try:
            _run([*command, "--command", "extension", "repo-add", "wfrl_part7",
                  "--name", "WFRL Part 7 Fresh Install", "--directory", str(repository),
                  "--clear-all"], env, log, cwd=scratch)
            _run([*command, "--command", "extension", "install-file", "-r", "wfrl_part7",
                  "--enable", str(result.archive)], env, log, cwd=scratch)
            _run([*command, "--python", str(Path(__file__).resolve()), "--",
                  "--inside", "--output", str(staged_install),
                  "--source-root", str(ROOT)], env, log, cwd=scratch)
            if not staged_install.is_file():
                raise RuntimeError(f"Blender did not produce package-install.json; inspect {log_path}")
        finally:
            staged_log = scratch / log_path.name
            staged_log.write_text("\n".join(log), encoding="utf-8")
            staged_log.replace(log_path)

        history = evidence_dir / "package-history-export.json"
        inventory = evidence_dir / "package-inventory.json"
        checksum = evidence_dir / "package-checksum.sha256"
        installed_files = sorted(path.relative_to(repository).as_posix()
                                 for path in repository.rglob("*") if path.is_file())
        evidence = json.loads(staged_install.read_text(encoding="utf-8"))
        evidence.update(
            archive=str(result.archive), archive_sha256=result.sha256,
            extension_version=json.loads(result.inventory.read_text(encoding="utf-8"))["version"],
            checksum_file=str(checksum), inventory_file=str(inventory), history_export=str(history),
            installed_file_count=len(installed_files),
            installed_inventory_sha256=hashlib.sha256("\n".join(installed_files).encode()).hexdigest(),
            fresh_profile=True,
        )
        staged_install.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        staged_install.with_name(history.name).replace(history)
        result.inventory.replace(inventory)
        result.checksum.replace(checksum)
        staged_install.replace(install_evidence)
    print(install_evidence)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--blender", type=Path, default=BLENDER)
    parser.add_argument("--evidence-dir", type=Path, default=EVIDENCE,
                        help="directory for smoke evidence and temporary build and profile files")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None)
    if args.inside:
        _inside_blender(args.output, args.source_root)
    else:
        verify(args.blender, evidence_dir=args.evidence_dir)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
