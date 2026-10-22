"""Host contracts for the four synthetic patch morphologies; no Blender needed."""
from copy import deepcopy
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parents[1]
sys.path.insert(0, str(PROJECT))
from gw184.defect_schema import PRESETS, new_defect, new_document, validate_document, save_document, load_document
from gw184.defect_shapes import PATCH_KINDS, lightning_crater
from gw184.model import read_config
from gw184.reference_surface import ReferenceSurface


def polygon(shape):
    return [(a['s_m'], a['u']) for a in shape['left_anchors'] + list(reversed(shape['right_anchors']))]


def strictly_inside(point, vertices):
    """Independent ray crossing plus explicit boundary exclusion in parameter space."""
    x, y = point
    inside = False
    for a, b in zip(vertices, vertices[1:] + vertices[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx*(y-a[1]) - dy*(x-a[0])) < 1e-12 and (
                min(a[0], b[0])-1e-12 <= x <= max(a[0], b[0])+1e-12 and
                min(a[1], b[1])-1e-12 <= y <= max(a[1], b[1])+1e-12):
            return False
        if (a[1] > y) != (b[1] > y):
            if x < a[0] + (y-a[1])*dx/dy:
                inside = not inside
    return inside


class FourTypeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.surface = ReferenceSurface(read_config()[0])

    def document(self, *kinds):
        doc = new_document(self.surface)
        doc['defects'] = [new_defect(self.surface, kind, id='TEST-'+kind) for kind in kinds]
        return doc

    def test_all_six_defaults_are_disjoint_and_roundtrip_without_identity_loss(self):
        doc = self.document(*PRESETS)
        self.assertEqual(len(doc['defects']), 6)
        self.assertEqual(validate_document(doc, self.surface), doc)
        self.assertEqual(validate_document(json.loads(json.dumps(doc)), self.surface), doc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'four-types.json'
            save_document(path, doc, self.surface)
            loaded = load_document(path, self.surface)
        self.assertEqual(loaded, doc)
        self.assertEqual([d['id'] for d in loaded['defects']], [d['id'] for d in doc['defects']])
        self.assertTrue(all(d['provenance']['dimension_basis'] == 'synthetic_example' for d in loaded['defects']))
        self.assertTrue(all(d['effects']['physics_coupled'] is False for d in loaded['defects']))

    def test_four_shapes_bind_both_sides_and_orientations_reproducibly(self):
        for kind in PATCH_KINDS:
            for side in ('suction', 'pressure'):
                for angle in (0., 90., 180., 270.):
                    with self.subTest(kind=kind, side=side, angle=angle):
                        d = new_defect(self.surface, kind, anchor=dict(surface_side=side, theta_deg=angle))
                        sampled = self.surface.sample_shape(d)
                        self.assertEqual(sampled, self.surface.sample_shape(deepcopy(d)))
                        self.assertAlmostEqual(sampled['measured_length_m'], d['shape']['length_m'], delta=.001)
                        self.assertAlmostEqual(sampled['measured_max_width_m'], d['shape']['max_width_m'], delta=.001)
                        self.assertEqual(sampled['reference_geometry_sha256'], self.surface.reference_geometry_sha256)
                        for anchors, points in ((sampled['anchors'], sampled['centerline']),
                                (sampled['left_anchors'], sampled['left']), (sampled['right_anchors'], sampled['right'])):
                            for anchor, point in zip(anchors, points):
                                self.assertEqual(anchor['surface_side'], side)
                                self.assertLess(math.dist(self.surface.point(anchor), point), 1e-12)

    def test_seed_changes_irregular_boundary_but_preserves_dimensions(self):
        for kind in ('erosion', 'coating_loss', 'lightning'):
            with self.subTest(kind=kind):
                first = new_defect(self.surface, kind, shape=dict(seed=17))
                second = new_defect(self.surface, kind, shape=dict(seed=18))
                a, b = self.surface.sample_shape(first), self.surface.sample_shape(second)
                self.assertNotEqual(a['width_profile'], b['width_profile'])
                self.assertEqual(a['centerline'], b['centerline'])
                self.assertAlmostEqual(a['measured_max_width_m'], b['measured_max_width_m'], delta=.001)

    def test_invalid_patch_parameters_and_type_combinations_are_rejected(self):
        invalid = [('edge_roughness', -.001), ('edge_roughness', .501), ('edge_roughness', True),
                   ('edge_roughness', float('nan')), ('length_m', 0), ('max_width_m', 0),
                   ('max_width_m', 1.01), ('depth_m', -.001), ('seed', -1), ('seed', True),
                   ('generator_version', 'unknown')]
        for kind in PATCH_KINDS:
            for key, value in invalid:
                with self.subTest(kind=kind, key=key, value=value):
                    doc = self.document(kind)
                    doc['defects'][0]['shape'][key] = value
                    with self.assertRaises(ValueError): validate_document(doc, self.surface)
            for field, value in [('representation', 'hybrid' if kind != 'lightning' else 'geometry'),
                    ('appearance', dict(preset_id='rough-interior', preset_version='v1')),
                    ('effects', dict(visual_geometry=False, physics_coupled=True))]:
                with self.subTest(kind=kind, field=field):
                    doc = self.document(kind); doc['defects'][0][field] = value
                    with self.assertRaises(ValueError): validate_document(doc, self.surface)
            doc = self.document(kind)
            doc['defects'][0]['shape']['depth_m'] = .01 if kind == 'coating_loss' else 0
            with self.assertRaises(ValueError): validate_document(doc, self.surface)
            doc = self.document(kind); del doc['defects'][0]['shape']['edge_roughness']
            with self.assertRaisesRegex(ValueError, 'missing'): validate_document(doc, self.surface)
        for value in (.149, .601, True, float('inf')):
            doc = self.document('lightning'); doc['defects'][0]['shape']['crater_fraction'] = value
            with self.assertRaises(ValueError): validate_document(doc, self.surface)
        doc = self.document('lightning'); del doc['defects'][0]['shape']['crater_fraction']
        with self.assertRaisesRegex(ValueError, 'missing'): validate_document(doc, self.surface)

    def test_patch_parameter_endpoints_are_valid(self):
        for kind in PATCH_KINDS:
            for roughness in (0., .5):
                d = new_defect(self.surface, kind, shape=dict(edge_roughness=roughness))
                self.assertEqual(d['shape']['edge_roughness'], roughness)
        for fraction in (.15, .6):
            d = new_defect(self.surface, 'lightning', shape=dict(crater_fraction=fraction))
            self.assertEqual(d['shape']['crater_fraction'], fraction)

    def test_lightning_nested_crater_is_strictly_inside_and_keeps_event_identity(self):
        for side in ('suction', 'pressure'):
            for angle in (0., 90., 180., 270.):
                for fraction in (.15, .4, .6):
                    with self.subTest(side=side, angle=angle, fraction=fraction):
                        event = new_defect(self.surface, 'lightning', id='STRIKE', revision=3,
                            anchor=dict(surface_side=side, theta_deg=angle),
                            shape=dict(crater_fraction=fraction, edge_roughness=.5))
                        before = deepcopy(event)
                        crater = lightning_crater(event)
                        self.assertEqual(event, before)
                        for key in ('id', 'revision', 'blade_id', 'turbine_id', 'enabled', 'anchor'):
                            self.assertEqual(crater[key], event[key])
                        self.assertEqual(crater['shape']['depth_m'], event['shape']['depth_m'])
                        self.assertAlmostEqual(crater['shape']['length_m'], event['shape']['length_m']*fraction)
                        self.assertAlmostEqual(crater['shape']['max_width_m'], event['shape']['max_width_m']*fraction)
                        outer = polygon(self.surface.sample_shape(event))
                        inner = polygon(self.surface.sample_shape(crater))
                        self.assertTrue(all(strictly_inside(p, outer) for p in inner))
                        self.assertTrue(all(strictly_inside(((a[0]+b[0])/2, (a[1]+b[1])/2), outer)
                                            for a, b in zip(inner, inner[1:]+inner[:1])))

    def test_separate_events_must_not_overlap_even_with_lightning(self):
        for kind in PATCH_KINDS:
            with self.subTest(kind=kind):
                first = new_defect(self.surface, 'lightning', id='STRIKE')
                second = new_defect(self.surface, kind, id='SEPARATE', anchor=first['anchor'])
                doc = new_document(self.surface); doc['defects'] = [first, second]
                with self.assertRaisesRegex(ValueError, 'OVERLAPPING_DEFECTS'): validate_document(doc, self.surface)
                second['enabled'] = False
                validate_document(doc, self.surface)
                second['enabled'] = True; second['blade_id'] = 2
                validate_document(doc, self.surface)
                second['blade_id'] = 1; second['anchor']['surface_side'] = 'pressure'
                validate_document(doc, self.surface)

    def test_unknown_kind_is_rejected_at_creation_and_loading(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported defect kind'):
            new_defect(self.surface, 'unknown')
        doc = self.document('pit'); doc['defects'][0]['morphology'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'Unsupported morphology'): validate_document(doc, self.surface)

    def test_existing_crack_contract_and_shapes_match_saved_prechange_source(self):
        before_directory = os.environ.get('GW184_DEFECT_PRECHANGE_DIR')
        if not before_directory:
            self.skipTest('Set GW184_DEFECT_PRECHANGE_DIR to explicitly compare a saved prechange gw184 source directory')
        before_root = Path(before_directory).expanduser().resolve()
        reference_path = before_root/'reference_surface.py'
        schema_path = before_root/'defect_schema.py'
        self.assertTrue(reference_path.is_file() and schema_path.is_file(),
                        'Explicit GW184_DEFECT_PRECHANGE_DIR must contain reference_surface.py and defect_schema.py')
        def module(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            loaded = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(loaded)
            return loaded
        old_surface_module = module('gw184.reference_surface_before', reference_path)
        old_schema_module = module('gw184.defect_schema_before', schema_path)
        old_surface = old_surface_module.ReferenceSurface(read_config()[0])
        for kind in ('fine_crack', 'open_crack'):
            for side in ('suction', 'pressure'):
                for angle in (0., 90., 180., 270.):
                    with self.subTest(kind=kind, side=side, angle=angle):
                        options = dict(id='LEGACY', anchor=dict(surface_side=side, theta_deg=angle))
                        current = new_defect(self.surface, kind, **options)
                        old = old_schema_module.new_defect(old_surface, kind, **options)
                        self.assertEqual(current, old)
                        self.assertEqual(self.surface.sample_shape(current), old_surface.sample_shape(old))


if __name__ == '__main__':
    unittest.main()
