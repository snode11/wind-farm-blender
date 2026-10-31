"""Clean, reproducible Blender extension package contract."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
import zipfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "scripts" / "blender" / "build_extension.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("wfrl_build_extension", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module

class PackageTests(unittest.TestCase):
    def test_optional_texture_payload_still_rejects_partial_or_wrong_source(self):
        builder = load_builder()
        payloads = builder._payloads()
        prefix = 'assets/blade_recon_mappo_tex/'
        absent = [(name, data) for name, data in payloads if not name.startswith(prefix)]
        builder._validate_mappo_texture_payloads(absent, required=False)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            builder._validate_mappo_texture_payloads(absent)
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            builder._validate_mappo_texture_payloads(absent + [(prefix + 'recon.json', b'{}')], required=False)
        wrong_source = [(name, data + b'changed' if name == 'assets/mappo/geometry.npz' else data)
                        for name, data in payloads]
        with self.assertRaisesRegex(ValueError, 'another source package'):
            builder._validate_mappo_texture_payloads(wrong_source, required=False)

    def test_custom_geometry_bundle_omits_unavailable_source_results(self):
        from scripts.blender.build_farm_flex_delivery import bundle
        from scripts.blender.validate_frontend_package import verify_archive, verify_installed
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'custom-farm'
            shutil.copytree(builder.PACKAGE_SOURCE / 'assets/mappo', package)
            manifest = json.loads((package / 'manifest.json').read_text())
            manifest['source_label'] = 'Independent accepted-controller review package'
            (package / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
            destination = root / 'delivery'
            bundle(package, destination)
            archive = next((destination / 'runtime-archive').glob('*.zip'))
            inventory = verify_archive(archive, archive.with_suffix('.inventory.json'))
            verify_installed(destination / 'blender_frontend/wfrl_blender', inventory)
            with zipfile.ZipFile(archive) as zipped:
                names = zipped.namelist()
                self.assertEqual(zipped.read('assets/mappo/manifest.json'),
                                 (package / 'manifest.json').read_bytes())
                self.assertFalse(any(name.startswith(('assets/blade_recon_mappo_tex/',
                    'assets/blade_response/', 'assets/dual_beam/', 'assets/dual_beam_tls/')) for name in names))
                self.assertIn('assets/blade_recon_synth_tex/manifest.json', names)
                self.assertEqual(zipped.read('manual_control.py'),
                                 (builder.PACKAGE_SOURCE / 'manual_control.py').read_bytes())

    def test_explicit_canonical_farm_keeps_all_matching_source_results(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            default = builder.build(output / 'default')
            explicit = builder.build(output / 'explicit',
                farm_package=builder.PACKAGE_SOURCE / 'assets/mappo')
            self.assertEqual(default.archive.read_bytes(), explicit.archive.read_bytes())

    def test_build_is_reproducible_and_manifest_matches_archive(self):
        builder = load_builder()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = builder.build(output / "one")
            second = builder.build(output / "two")

            self.assertEqual(first.archive.read_bytes(), second.archive.read_bytes())
            self.assertEqual(first.sha256, hashlib.sha256(first.archive.read_bytes()).hexdigest())
            inventory = json.loads(first.inventory.read_text(encoding="utf-8"))
            self.assertEqual(inventory["archive_sha256"], first.sha256)
            self.assertEqual(inventory["package_id"], "wfrl_blender")
            self.assertEqual(inventory["version"], tomllib.loads((builder.PACKAGE_SOURCE / "blender_manifest.toml").read_text())["version"])
            with zipfile.ZipFile(first.archive) as zipped:
                names = zipped.namelist()
                self.assertEqual(names, sorted(names))
                self.assertIn("blender_manifest.toml", names)
                self.assertIn("protocol.py", names)
                identity = json.loads(zipped.read('build-info.json'))
                content = [{"path": name, "sha256": hashlib.sha256(zipped.read(name)).hexdigest()}
                           for name in names if name != 'build-info.json']
                self.assertEqual(identity['payload_sha256'], hashlib.sha256(
                    json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
                self.assertEqual(identity['payload_files'], len(content))
                self.assertEqual(identity['version'], inventory['version'])
                for name in ('manifest.json', 'data.json', 'geometry.npz', 'source-surfaces.json', 'tower-motion.npz', 'deflection-t1.json', 'source-run.json'):
                    self.assertIn('assets/mappo/' + name, names)
                manifest = json.loads(zipped.read('assets/mappo/manifest.json'))
                self.assertEqual(manifest['schema'], 'wfrl.farm-flex-review.v3')
                self.assertEqual(manifest['tower_model'], 'elastodyn-flexible')
                self.assertEqual(manifest['segment']['end_s'] - manifest['segment']['start_s'], 60)
                for name, digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(zipped.read('assets/mappo/' + name)).hexdigest(), digest)
                for directory, method in (('dual_beam', 'hub-axis.v1'), ('dual_beam_tls', 'hub-tls.v1')):
                    prefix = 'assets/' + directory + '/'
                    sidecar = json.loads(zipped.read(prefix + 'manifest.json'))
                    self.assertEqual(sidecar['reconstruction_method'], method)
                    self.assertEqual(sidecar['performance_status'], 'PENDING_ACCEPTANCE')
                    self.assertEqual(sidecar['source_package'], '../mappo')
                    self.assertTrue(sidecar['portable'])
                    for name, digest in sidecar['files'].items():
                        self.assertEqual(hashlib.sha256(zipped.read(prefix + name)).hexdigest(), digest)
                    for name, digest in sidecar['source_hashes'].items():
                        self.assertEqual(hashlib.sha256(zipped.read('assets/mappo/' + name)).hexdigest(), digest)
                self.assertNotIn('sample_demo', zipped.read('state.py').decode())
                self.assertIn("_vendor/lidar/replay.py", names)
                self.assertIn("_vendor/lidar/evidence.py", names)
                self.assertIn("assets/nrel5mw_geometry.json", names)
                self.assertIn("assets/landscape/rocky_terrain_02_diff_2k.jpg", names)
                self.assertNotIn("__pycache__", "\n".join(names))
                self.assertFalse(any(name.endswith((".pyc", ".DS_Store")) for name in names))
                self.assertEqual(inventory["files"], [
                    {"path": name, "sha256": hashlib.sha256(zipped.read(name)).hexdigest(),
                     "size": len(zipped.read(name))} for name in names
                ])
                self.assertEqual({info.date_time for info in zipped.infolist()}, {builder.ZIP_TIMESTAMP})
            self.assertEqual(first.checksum.read_text(encoding="ascii"),
                             f"{first.sha256}  {first.archive.name}\n")

    def test_isolated_zip_import_uses_bundled_stdlib_codec(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = load_builder().build(Path(directory) / 'dist').archive
            package = Path(directory) / 'wfrl_blender'
            with zipfile.ZipFile(archive) as zipped:
                zipped.extractall(package)
            env = dict(os.environ); env.pop('PYTHONPATH', None)
            code = '''import sys
from wfrl_blender.protocol import encode_message, FrameDecoder
from wfrl_blender.transport import TransportClient
from wfrl_blender._vendor.lidar.replay import ReplayPackage, ReplayReader
m = dict(protocol_version=1,type='hello',session_id='',sequence=0,payload=dict(supported_versions=[1],capabilities=[],resume_session_id=None))
assert FrameDecoder().feed(encode_message(m)) == [m]
assert not ({'wfrl','bpy','torch','floris','mpi4py'} & sys.modules.keys())
'''
            subprocess.run([sys.executable, '-c', code], cwd=directory, env=env, check=True, capture_output=True)

if __name__ == '__main__': unittest.main()
