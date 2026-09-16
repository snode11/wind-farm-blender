"""Clean, reproducible Blender extension package contract."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "scripts" / "blender" / "build_extension.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("wfrl_build_extension", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module

class PackageTests(unittest.TestCase):
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
            self.assertEqual(inventory["version"], "0.3.0")
            with zipfile.ZipFile(first.archive) as zipped:
                names = zipped.namelist()
                self.assertEqual(names, sorted(names))
                self.assertIn("blender_manifest.toml", names)
                self.assertIn("protocol.py", names)
                for name in ('manifest.json', 'data.json', 'geometry.npz', 'source-surfaces.json'):
                    self.assertIn('assets/mappo/' + name, names)
                manifest = json.loads(zipped.read('assets/mappo/manifest.json'))
                self.assertEqual(manifest['segment']['end_s'] - manifest['segment']['start_s'], 60)
                for name, digest in manifest['files'].items():
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
