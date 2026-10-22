"""Model selection preserves the NREL baseline and recorded-motion topology."""
import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
import shutil
import tempfile
import unittest

from wfrl_blender.turbine_geometry import DEFAULT_MODEL, blade_mesh, geometry_data


ROOT = Path(__file__).resolve().parents[2]


def _script(name):
    path = ROOT / 'scripts' / 'blender' / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BladeModelTests(unittest.TestCase):
    def test_default_uses_packaged_nrel_geometry(self):
        expected = json.loads((ROOT / 'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json').read_text())
        self.assertEqual(DEFAULT_MODEL, 'nrel5mw')
        self.assertEqual(geometry_data(), expected)
        self.assertEqual(blade_mesh(), blade_mesh(model='nrel5mw'))
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / 'nrel.json'
            _script('export_geometry').export(
                ROOT / 'projects/nrel5mw-reference/template', output, source=expected['source'])
            self.assertEqual(json.loads(output.read_text()), expected)

    def test_alternate_shape_preserves_recorded_motion_binding(self):
        baseline, alternate = geometry_data(), geometry_data('rootkink')
        for key in ('scalars', 'airfoils', 'shell', 'tower_stations'):
            self.assertEqual(alternate[key], baseline[key])
        self.assertEqual([row[:5] for row in alternate['blade_stations']],
                         [row[:5] for row in baseline['blade_stations']])
        original_vertices, original_faces = blade_mesh(subdiv=4, ring_points=48)
        vertices, faces = blade_mesh(subdiv=4, ring_points=48, model='rootkink')
        self.assertEqual(faces, original_faces)
        self.assertEqual([point[2] for point in vertices], [point[2] for point in original_vertices])
        self.assertNotEqual(vertices, original_vertices)
        self.assertTrue(all(math.isfinite(value) for point in vertices for value in point))
        for face in faces:
            for a, b in zip(face, face[1:] + face[:1]):
                self.assertGreater(math.dist(vertices[a], vertices[b]), 1e-8)

    def test_root_and_shoulder_dimensions(self):
        data = geometry_data('rootkink')
        stations = data['blade_stations']
        shoulder = max(stations, key=lambda row: row[5])
        self.assertEqual(stations[0][5:], [2.3, 1.0])
        self.assertEqual(stations[1][6], 3)
        self.assertEqual(shoulder[5], 4.85)
        self.assertAlmostEqual(shoulder[0] + data['scalars']['HubRad'], 8.3333)
        self.assertEqual(shoulder[6], 4)

    def test_regeneration_matches_packaged_alternate_asset(self):
        expected = geometry_data('rootkink')
        template = ROOT / 'projects/commercial-rootkink-reference/template'
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            generated = Path(directory) / 'template'
            shutil.copytree(template, generated)
            blade_path = Path('5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat')
            _script('make_rootkink_blade').build(target=generated / blade_path)
            self.assertEqual((generated / blade_path).read_bytes(), (template / blade_path).read_bytes())
            output = Path(directory) / 'rootkink.json'
            _script('export_geometry').export(generated, output, source=expected['source'])
            self.assertEqual(json.loads(output.read_text()), expected)


if __name__ == '__main__':
    unittest.main()
