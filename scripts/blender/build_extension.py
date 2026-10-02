"""Build a byte-reproducible WFRL Blender extension and hash inventory."""

from __future__ import annotations

import hashlib
import argparse
import importlib.util
import json
from pathlib import Path
import tomllib
from typing import NamedTuple
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_SOURCE = ROOT / "blender_frontend" / "wfrl_blender"
PROTOCOL_SOURCE = ROOT / "wfrl" / "blender_bridge" / "messages.py"
LIDAR_REPLAY_SOURCE = ROOT / "wfrl" / "lidar" / "replay.py"
LIDAR_EVIDENCE_SOURCE = ROOT / "wfrl" / "lidar" / "evidence.py"
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
    # The offline player must also work outside a source checkout. Bundle only
    # the dependency-free reader, never the simulator or training dependencies.
    payloads.extend([
        ("_vendor/__init__.py", b""),
        ("_vendor/lidar/__init__.py", b""),
        ("_vendor/lidar/replay.py", LIDAR_REPLAY_SOURCE.read_bytes()),
        ("_vendor/lidar/evidence.py", LIDAR_EVIDENCE_SOURCE.read_bytes()),
        ("_vendor/lidar/dual_beam_replay.py", (LIDAR_REPLAY_SOURCE.parent / 'dual_beam_replay.py').read_bytes()),
        ("_vendor/lidar/molas_cl.py", (LIDAR_REPLAY_SOURCE.parent / 'molas_cl.py').read_bytes()),
    ])
    return sorted(payloads)


def _dual_reader():
    # Load the stdlib-only canonical reader without simulator dependencies.
    spec = importlib.util.spec_from_file_location('dual_bundle_reader', LIDAR_REPLAY_SOURCE.parent / 'dual_beam_replay.py')
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader


def _validate_farm_payloads(payloads):
    bundled = dict(payloads)
    manifest = json.loads(bundled['assets/mappo/manifest.json'])
    for name, expected in manifest['files'].items():
        if (Path(name).name != name
                or hashlib.sha256(bundled.get('assets/mappo/' + name, b'')).hexdigest() != expected):
            raise ValueError('Farm package integrity mismatch: ' + name)


def _bundle_dual(payloads, package, asset_dir, expected_method):
    package = Path(package)
    _, overlay = _dual_reader().resolve_package(package)
    if overlay is None:
        raise ValueError('Expected dual-beam package: ' + asset_dir)
    method = overlay['config'].get('reconstruction_method', 'hub-axis.v1')
    if method != expected_method:
        raise ValueError('Bundled dual-beam method differs from entry point: ' + asset_dir)
    bundled = dict(payloads)
    for name, expected in overlay['manifest']['source_hashes'].items():
        if hashlib.sha256(bundled.get('assets/mappo/' + name, b'')).hexdigest() != expected:
            raise ValueError('Dual-beam data differs from bundled geometry: ' + asset_dir + '/' + name)
    prefix = 'assets/' + asset_dir + '/'
    payloads = [(name, data) for name, data in payloads if not name.startswith(prefix)]
    manifest = dict(overlay['manifest'], source_package='../mappo', portable=True)
    payloads += [(prefix + name, (package / name).read_bytes()) for name in sorted(manifest['files'])]
    payloads.append((prefix + 'manifest.json', (json.dumps(manifest, ensure_ascii=False, sort_keys=True) + '\n').encode()))
    return payloads


def build(output_dir: Path = DIST, *, farm_package: Path | None = None,
          dual_package: Path | None = None, dual_tls_package: Path | None = None) -> BuildResult:
    manifest = tomllib.loads((PACKAGE_SOURCE / "blender_manifest.toml").read_text(encoding="utf-8"))
    package_id, version = manifest["id"], manifest["version"]
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{package_id}-{version}.zip"
    payloads = _payloads()
    if farm_package is not None:
        farm_package = Path(farm_package)
        data_manifest = json.loads((farm_package/'manifest.json').read_text())
        for name,digest in data_manifest['files'].items():
            if hashlib.sha256((farm_package/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('Farm package integrity mismatch: '+name)
        payloads = [(name,data) for name,data in payloads if not name.startswith('assets/mappo/')]
        payloads += [('assets/mappo/'+name,(farm_package/name).read_bytes())
                     for name in sorted(set(data_manifest['files']) | {'manifest.json'})]
        payloads.sort()
    _validate_farm_payloads(payloads)
    for package, asset_dir, method in ((dual_package, 'dual_beam', 'hub-axis.v1'),
                                      (dual_tls_package, 'dual_beam_tls', 'hub-tls.v1')):
        # A custom geometry-only delivery cannot inherit sidecars for another
        # source. The normal release always bundles both verified methods.
        if package is None and farm_package is not None:
            payloads = [(name, data) for name, data in payloads if not name.startswith('assets/' + asset_dir + '/')]
            continue
        package = package or PACKAGE_SOURCE / 'assets' / asset_dir
        payloads = _bundle_dual(payloads, package, asset_dir, method)
    payloads.sort()
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DIST)
    parser.add_argument('--dual-package', type=Path)
    parser.add_argument('--dual-tls-package', type=Path)
    args = parser.parse_args()
    result = build(args.output, dual_package=args.dual_package, dual_tls_package=args.dual_tls_package)
    print(result.archive)
    print(result.checksum)
    print(result.inventory)
    print(f"sha256={result.sha256}")


if __name__ == "__main__":
    main()
