"""Build a byte-reproducible WFRL Blender extension and hash inventory."""

from __future__ import annotations

import hashlib
import argparse
import importlib.util
import json
import math
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
SAME_SOURCE_MAPPO_MANIFEST_SHA256 = 'd3002397dadf1e5351b9c9647add83f44b921a126dc800a97e1fc92897f71bde'


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
        if relative.as_posix() in {"protocol.py", "build-info.json"}:
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


def _validate_blade_texture_payloads(payloads):
    """The sidebar surface entry must deliver one matching saved output."""
    bundled = dict(payloads)
    prefix = 'assets/blade_recon_synth_tex/'
    manifest = json.loads(bundled[prefix + 'manifest.json'])
    if (manifest.get('schema') != 'wfrl.blade-recon-provenance.v1'
            or manifest.get('source_classification') != 'SYNTHETIC'):
        raise ValueError('Blade surface package must declare synthetic provenance')
    required = {'recon.json', 'texture/texture.json', 'reference/model.py', 'reference/atlas.py',
                *{f'texture/blade{b}_tex.png' for b in range(1, 4)}}
    if not required.issubset(manifest.get('files', {})):
        raise ValueError('Blade surface package is incomplete')
    for name, record in manifest['files'].items():
        if (Path(name).is_absolute() or '..' in Path(name).parts
                or hashlib.sha256(bundled.get(prefix + name, b'')).hexdigest() != record.get('sha256')
                or len(bundled.get(prefix + name, b'')) != record.get('size_bytes')):
            raise ValueError('Blade surface package integrity mismatch: ' + name)
    display_model = hashlib.sha256(bundled['_vendor/blade_recon/model.py']).hexdigest()
    reference_model = hashlib.sha256(bundled[prefix + 'reference/model.py']).hexdigest()
    if (manifest.get('model', {}).get('sha256') != display_model
            or manifest.get('reference_model', {}).get('sha256') != reference_model):
        raise ValueError('Blade surface model identity mismatch')
    recon = json.loads(bundled[prefix + 'recon.json'])
    clock = manifest['clock']
    frames = recon['frames']
    if (clock['fps'] != recon['fps'] or clock['samples'] != len(frames)
            or clock['source_frame_start'] != frames[0]['frame']
            or clock['source_frame_end'] != frames[-1]['frame']
            or clock['time_start_s'] != frames[0]['t']
            or clock['time_end_s'] != frames[-1]['t']):
        raise ValueError('Blade surface clock does not match saved samples')
    texture = json.loads(bundled[prefix + 'texture/texture.json'])
    turbine = recon['turbine']
    if (texture['n_ring'] != turbine['n_ring'] or texture['n_sections'] != turbine['n_sections']
            or texture['atlas']['r0'] != turbine['hub_radius_m']
            or texture['atlas']['r1'] != turbine['tip_radius_m']):
        raise ValueError('Blade surface atlas does not match reconstruction')


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


def _validate_mappo_texture_payloads(payloads):
    """Keep the same-source entry bound to its saved geometry and atlas."""
    bundled = dict(payloads)
    prefix = 'assets/blade_recon_mappo_tex/'
    manifest = json.loads(bundled[prefix + 'manifest.json'])
    if (manifest.get('schema') != 'wfrl.blade-recon-provenance.v1'
            or manifest.get('source_classification') != 'SAME_SOURCE_SIMULATION'):
        raise ValueError('MAPPO texture package must declare same-source simulation provenance')
    required = {'recon.json', 'texture/texture.json', 'model_compatibility.json', 'texture_quality.json',
                *{f'texture/blade{b}_tex.png' for b in range(1, 4)}}
    if not required.issubset(manifest.get('files', {})):
        raise ValueError('MAPPO texture package is incomplete')
    for name, record in manifest['files'].items():
        content = bundled.get(prefix + name, b'')
        if (Path(name).is_absolute() or '..' in Path(name).parts
                or hashlib.sha256(content).hexdigest() != record.get('sha256')
                or len(content) != record.get('size_bytes')):
            raise ValueError('MAPPO texture package integrity mismatch: ' + name)
    display_model = hashlib.sha256(bundled['_vendor/blade_recon/model.py']).hexdigest()
    if manifest.get('model', {}).get('sha256') != display_model:
        raise ValueError('MAPPO texture display model identity mismatch')
    equivalence = manifest['model_compatibility']
    checks = equivalence['models']
    if (len(checks) != 2 or {row['model'] for row in checks} != {'texture', 'vendor'}
            or not equivalence['uv_float32_bit_identical']
            or any(row['samples_checked'] != 601
                   or row['maximum_vertex_difference_m'] != 0
                   or row['maximum_axis_difference_m'] != 0
                   or not all(row[key] for key in ('r_bit_identical', 'foils_bit_identical',
                       'chord_bit_identical', 'twist_bit_identical', 'faces_bit_identical',
                       'vertices_bit_identical', 'axes_bit_identical')) for row in checks)):
        raise ValueError('MAPPO texture model equivalence is incomplete')
    if manifest.get('geometry_precision_status') != 'NOT_ACCEPTED':
        raise ValueError('MAPPO texture must preserve the original geometry acceptance status')
    source_assets = {name: digest for name, digest in manifest['source_package_hashes'].items()
                     if name.startswith('assets/mappo/')}
    if not {'assets/mappo/manifest.json', 'assets/mappo/geometry.npz',
            'assets/mappo/blade-reference.json'}.issubset(source_assets):
        raise ValueError('MAPPO texture lacks captured source identities')
    declared_manifest = manifest.get('mappo_manifest_sha256', source_assets.get('assets/mappo/manifest.json'))
    if (declared_manifest != SAME_SOURCE_MAPPO_MANIFEST_SHA256
            or declared_manifest != source_assets['assets/mappo/manifest.json']):
        raise ValueError('MAPPO texture belongs to another source package: manifest identity')
    for name, digest in source_assets.items():
        if hashlib.sha256(bundled.get(name, b'')).hexdigest() != digest:
            raise ValueError('MAPPO texture belongs to another source package: ' + name)
    def real(value):
        return type(value) in (int, float) and math.isfinite(value)

    def timestamp(value, expected):
        return real(value) and abs(value - expected) <= 1e-9

    def integer(value, expected):
        return type(value) is int and value == expected

    recon = json.loads(bundled[prefix + 'recon.json'])
    frames = recon.get('frames')
    if (not real(recon.get('fps')) or recon['fps'] != 10
            or not isinstance(frames, list) or len(frames) != 601
            or any(not isinstance(row, dict) or not isinstance(row.get('state'), list)
                   or len(row['state']) != 13 or not all(real(value) for value in row['state'])
                   or not integer(row.get('frame'), index)
                   or not timestamp(row.get('t'), index / 10)
                   or not timestamp(row.get('sim_t'), 117 + index / 10)
                   or not integer(row.get('blender_frame'), 1 + index * 6)
                   for index, row in enumerate(frames))):
        raise ValueError('MAPPO texture must retain all 601 original 10 Hz states')
    farm_manifest = json.loads(bundled['assets/mappo/manifest.json'])
    if (farm_manifest['segment']['start_s'] != 117
            or farm_manifest['segment']['end_s'] != 177):
        raise ValueError('MAPPO texture experiment requires the matching 117-177 s farm segment')
    clock = manifest.get('clock')
    expected_clock = dict(samples=601, fps=10., source_frame_start=0, source_frame_end=600,
        time_start_s=0., time_end_s=60., simulation_start_s=117., simulation_end_s=177., source_hz=40.,
        timeline_fps=60., stride=6, frame_start=1, frame_end=3601)
    integer_clock = {'samples', 'source_frame_start', 'source_frame_end', 'stride', 'frame_start', 'frame_end'}
    if (not isinstance(clock, dict) or any(
            not (integer(clock.get(name), expected) if name in integer_clock
                 else real(clock.get(name)) and clock[name] == expected)
            for name, expected in expected_clock.items())):
        raise ValueError('MAPPO texture clock does not match saved samples')
    texture = json.loads(bundled[prefix + 'texture/texture.json'])
    turbine = recon['turbine']
    if (turbine['n_ring'] != 32 or turbine['n_sections'] != 40
            or texture['n_ring'] != turbine['n_ring']
            or texture['n_sections'] != turbine['n_sections']
            or texture['atlas']['r0'] != turbine['hub_radius_m']
            or texture['atlas']['r1'] != turbine['tip_radius_m']):
        raise ValueError('MAPPO texture atlas does not match saved model')


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
    _validate_blade_texture_payloads(payloads)
    _validate_mappo_texture_payloads(payloads)
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
    identity = [{"path": name, "sha256": hashlib.sha256(data).hexdigest()}
                for name, data in payloads]
    build_info = dict(schema='wfrl.extension-build.v1', version=version,
        payload_sha256=hashlib.sha256(json.dumps(identity, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest(), payload_files=len(payloads))
    payloads.append(('build-info.json', (json.dumps(build_info, sort_keys=True, indent=2)+'\n').encode()))
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
