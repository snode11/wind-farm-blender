"""Verify the selected extension ZIP with both offline packages in an isolated Python.

No installed add-on, simulator, or source import is used. This checks the actual
archive's reader; Blender UI verification is a separate evidence level.
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
import zipfile


def verify(archive: Path, normal: Path, close: Path) -> dict:
    archive, normal, close = (p.resolve() for p in (archive, normal, close))
    with tempfile.TemporaryDirectory(prefix="wfrl-delivery-") as directory:
        package = Path(directory) / "wfrl_blender"
        with zipfile.ZipFile(archive) as zipped:
            required = {"clearance_replay.py", "clearance_visual.py", "panels/clearance.py",
                        "_vendor/lidar/replay.py", "_vendor/lidar/evidence.py", "blender_manifest.toml"}
            if not required.issubset(zipped.namelist()):
                raise ValueError("Archive is missing the lidar player")
            for name in zipped.namelist():
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe archive path")
            zipped.extractall(package)
        code = r'''
import json,sys,tomllib
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from wfrl_blender._vendor.lidar.replay import ReplayPackage,ReplayReader
result={"version":tomllib.loads((Path(sys.argv[1])/"wfrl_blender/blender_manifest.toml").read_text())["version"],"packages":{}}
for label,path in zip(("normal","close"),sys.argv[2:]):
    package=ReplayPackage.load(path)
    assert package.manifest["segment"]["id"]==label
    reader=ReplayReader(package)
    assert reader.at(reader.end_s)["statistics"]==package.statistics
    end=reader.at(reader.end_s)
    reader.at(reader.start_s)
    assert reader.at(reader.end_s)==end
    result["packages"][label]={"path":path,"statistics":package.statistics,"evidence_contract":package.manifest["validation"].get("evidence_contract","legacy")}
assert not ({"wfrl","bpy","torch","floris","mpi4py"}&sys.modules.keys())
print(json.dumps(result))
'''
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, directory, str(normal), str(close)],
            cwd=directory, env=env, check=True, text=True, capture_output=True)
    result = json.loads(completed.stdout)
    result.update(archive=str(archive), sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                  verification="isolated archive reader; no Blender GUI or hardware claim")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("normal", type=Path)
    parser.add_argument("close", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = verify(args.archive, args.normal, args.close)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
