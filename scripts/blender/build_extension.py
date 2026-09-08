"""Build a byte-reproducible WFRL Blender extension and hash inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib
from typing import NamedTuple
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SOURCE = ROOT / "blender_frontend" / "wfrl_blender"
PROTOCOL_SOURCE = ROOT / "wfrl" / "blender_bridge" / "messages.py"
DIST = ROOT / "dist"
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
EXCLUDED_NAMES = {".DS_Store"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}


class BuildResult(NamedTuple):
    archive: Path
    checksum: Path
    inventory: Path
    sha256: str


def _payloads() -> list[tuple[str, bytes]]:
    payloads: list[tuple[str, bytes]] = []
    for path in PACKAGE_SOURCE.rglob("*"):
        relative = path.relative_to(PACKAGE_SOURCE)
        if (not path.is_file() or path.name in EXCLUDED_NAMES
                or any(part in EXCLUDED_PARTS for part in relative.parts)
                or path.suffix == ".pyc"):
            continue
        if relative.as_posix() == "protocol.py":
            continue
        payloads.append((relative.as_posix(), path.read_bytes()))
    payloads.append(("protocol.py", PROTOCOL_SOURCE.read_bytes()))
    return sorted(payloads)


def build(output_dir: Path = DIST) -> BuildResult:
    manifest = tomllib.loads((PACKAGE_SOURCE / "blender_manifest.toml").read_text(encoding="utf-8"))
    package_id, version = manifest["id"], manifest["version"]
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{package_id}-{version}.zip"
    payloads = _payloads()
    with ZipFile(archive, "w", ZIP_DEFLATED, compresslevel=9) as zipped:
        for name, data in payloads:
            info = ZipInfo(name, ZIP_TIMESTAMP)
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            zipped.writestr(info, data, compresslevel=9)

    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(".zip.sha256")
    checksum.write_text(f"{archive_sha256}  {archive.name}\n", encoding="ascii")
    inventory = archive.with_suffix(".inventory.json")
    inventory.write_text(json.dumps({
        "schema_version": "wfrl.extension-package.v1",
        "package_id": package_id,
        "version": version,
        "archive": archive.name,
        "archive_sha256": archive_sha256,
        "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                  for name, data in payloads],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return BuildResult(archive, checksum, inventory, archive_sha256)


def main() -> None:
    result = build()
    print(result.archive)
    print(result.checksum)
    print(result.inventory)
    print(f"sha256={result.sha256}")


if __name__ == "__main__":
    main()
