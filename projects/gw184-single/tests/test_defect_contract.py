"""Host tests for frozen geometry binding and whole-state edit transactions."""
from copy import deepcopy
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gw184.model import read_config
from gw184.reference_surface import ReferenceSurface, _dot, _cross, _norm, _sub, _add, _mul
from gw184.defect_schema import new_document, new_defect, validate_document, save_document, load_document
from gw184.defect_store import DefectStore


class SurfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.surface = ReferenceSurface(read_config()[0])

    def test_reference_identity_is_deterministic_and_binds_geometry(self):
        s = self.surface
        self.assertEqual(s.reference_geometry_sha256, ReferenceSurface(read_config()[0]).reference_geometry_sha256)
        g = read_config()[0]; g['prebend_m'] = 2.9
        self.assertNotEqual(s.reference_geometry_sha256, ReferenceSurface(g).reference_geometry_sha256)
        self.assertEqual(len(s.reference_geometry_sha256), 64)
        self.assertEqual(s.metadata()['surface_registration'], {'suction': 'FFA upper branch', 'pressure': 'FFA lower branch'})
        metadata = s.metadata(); metadata['valid_domain']['u'] = [0, 1]
        self.assertEqual(s.domain['u'], (.05, .95))

    def test_roundtrip_arbitrary_and_exact_station_points_on_both_sides(self):
        rng = random.Random(119)
        positions = [(18., .05), (89.4, .95), (45., .4)]
        positions += [(rng.uniform(19, 88), rng.uniform(.08, .92)) for _ in range(20)]
        for side in ('suction', 'pressure'):
            for s, u in positions:
                anchor = dict(s_m=s, u=u, surface_side=side, theta_deg=0.)
                p = self.surface.point(anchor); located = self.surface.locate(p)
                self.assertEqual(located['surface_side'], side)
                self.assertAlmostEqual(located['s_m'], s, places=10)
                self.assertAlmostEqual(located['u'], u, places=9)
                self.assertLess(_norm(_sub(p, self.surface.point(located))), 1e-9)

    def test_oriented_frame_is_right_handed_and_camera_independent(self):
        for side, sign in [('suction', 1), ('pressure', -1)]:
            f = self.surface.frame(dict(s_m=45., u=.4, surface_side=side))
            self.assertGreater(sign*f['normal'][0], .8)
            self.assertGreater(f['e_s'][2], .98)
            self.assertAlmostEqual(_norm(f['normal']), 1.)
            self.assertAlmostEqual(_dot(f['normal'], f['e_s']), 0.)
            self.assertAlmostEqual(_dot(_cross(f['e_s'], f['e_t']), f['normal']), 1.)

    def test_click_rejects_groove_wall_and_unsupported_regions(self):
        anchor = dict(s_m=45., u=.4, surface_side='suction')
        f = self.surface.frame(anchor)
        below = _add(f['point'], _mul(f['normal'], -.01))
        with self.assertRaisesRegex(ValueError, 'OFF_REFERENCE'): self.surface.locate(below)
        with self.assertRaisesRegex(ValueError, 'tolerance'): self.surface.locate(below, tolerance_m=.02)
        for bad in (dict(anchor, s_m=17.9), dict(anchor, s_m=89.5), dict(anchor, u=.049),
                    dict(anchor, surface_side='front'), dict(anchor, theta_deg=360), dict(anchor, region_hint='leading')):
            with self.assertRaises(ValueError): self.surface.point(bad)

    def test_explicit_projection_roundtrip(self):
        anchor = dict(s_m=45.13, u=.437, surface_side='pressure')
        p = self.surface.point(anchor); projected = self.surface.project(p, 'pressure')
        self.assertLess(_norm(_sub(p, self.surface.point(projected))), 1e-9)
        f = self.surface.frame(anchor)
        mapped = self.surface.project(_add(p, _mul(f['normal'], .01)), 'pressure')
        self.assertLess(_norm(_sub(p, self.surface.point(mapped))), 1e-5)

    def test_full_shape_binding_dimensions_and_reproducibility(self):
        for kind in ('fine_crack', 'open_crack'):
            for side in ('suction', 'pressure'):
                for theta in (0., 90., 180., 270.):
                    d = new_defect(self.surface, kind, anchor=dict(surface_side=side, theta_deg=theta))
                    shape = self.surface.sample_shape(d)
                    self.assertEqual(shape, self.surface.sample_shape(deepcopy(d)))
                    self.assertAlmostEqual(shape['measured_length_m'], d['shape']['length_m'], delta=.001)
                    self.assertAlmostEqual(shape['measured_max_width_m'], d['shape']['max_width_m'], delta=.001)
                    for anchors, points in ((shape['anchors'], shape['centerline']),
                            (shape['left_anchors'], shape['left']), (shape['right_anchors'], shape['right'])):
                        for a, p in zip(anchors, points):
                            self.assertLess(_norm(_sub(self.surface.point(a), p)), 1e-12)
                            self.assertEqual(a['surface_side'], side)
        other = deepcopy(d); other['shape']['seed'] += 1
        self.assertNotEqual(self.surface.sample_shape(d)['centerline'], self.surface.sample_shape(other)['centerline'])


class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.surface = ReferenceSurface(read_config()[0])

    def make_document(self):
        doc = new_document(self.surface)
        doc['defects'] = [new_defect(self.surface, 'fine_crack', id='fine'),
                          new_defect(self.surface, 'open_crack', id='open')]
        return doc

    def test_independent_copy_and_roundtrip(self):
        doc = self.make_document(); checked = validate_document(doc, self.surface)
        checked['defects'][0]['anchor']['s_m'] = 60
        self.assertEqual(doc['defects'][0]['anchor']['s_m'], 44.)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'defects.json'
            save_document(path, doc, self.surface)
            self.assertEqual(load_document(path, self.surface), doc)
            bad = deepcopy(doc); bad['reference_geometry_sha256'] = 'EXAMPLE_ONLY'
            with self.assertRaises(ValueError): save_document(path, bad, self.surface)
            self.assertEqual(load_document(path, self.surface), doc)

    def test_invalid_hash_versions_dimensions_and_resources(self):
        for key, value in [('reference_geometry_sha256', 'a'*64), ('parameterization_version', 'old'), ('reference_geometry_id', 'wrong')]:
            doc = self.make_document(); doc[key] = value
            with self.assertRaisesRegex(ValueError, 'STALE_GEOMETRY'): validate_document(doc, self.surface)
        for path, value in [(('shape', 'length_m'), float('nan')), (('shape', 'max_width_m'), 0),
                (('shape', 'seed'), True), (('shape', 'generator_version'), 'unknown'),
                (('appearance', 'preset_version'), 'v2'), (('effects', 'physics_coupled'), True),
                (('anchor', 's_m'), 12), (('provenance', 'reference_asset_id'), 'missing-file')]:
            doc = self.make_document(); doc['defects'][0][path[0]][path[1]] = value
            with self.assertRaises(ValueError): validate_document(doc, self.surface)
        for key, value in [('blade_id', True), ('revision', 0), ('morphology', 'lightning'), ('enabled', 1)]:
            doc = self.make_document(); doc['defects'][0][key] = value
            with self.assertRaises(ValueError): validate_document(doc, self.surface)

    def test_duplicate_id_and_overlap_only_enabled_same_blade_side(self):
        doc = self.make_document(); doc['defects'][1]['id'] = 'fine'
        with self.assertRaisesRegex(ValueError, 'Duplicate'): validate_document(doc, self.surface)
        doc = self.make_document(); doc['defects'][1]['anchor'] = deepcopy(doc['defects'][0]['anchor'])
        with self.assertRaisesRegex(ValueError, 'OVERLAPPING'): validate_document(doc, self.surface)
        doc['defects'][1]['enabled'] = False
        validate_document(doc, self.surface)
        doc['defects'][1]['enabled'] = True; doc['defects'][1]['blade_id'] = 2
        validate_document(doc, self.surface)
        doc['defects'][1]['blade_id'] = 1; doc['defects'][1]['anchor']['surface_side'] = 'pressure'
        validate_document(doc, self.surface)


class StoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.surface = ReferenceSurface(read_config()[0])

    def test_prepare_cancel_failed_build_undo_redo_and_stale_tasks(self):
        visible, disposed = {}, []
        def prepare(doc):
            if any(d['id'] == 'fail-build' for d in doc['defects']): raise RuntimeError('build failed')
            return {'document': deepcopy(doc), 'bvh': object(), 'mask': object()}
        def activate(resource, doc, version): visible.update(resource=resource, document=doc, version=version)
        store = DefectStore(self.surface, prepare=prepare, activate=activate, dispose=disposed.append)
        initial = store.resource
        doc = store.document; doc['defects'] = [new_defect(self.surface, 'open_crack')]
        token = store.prepare(doc)
        self.assertIs(visible['resource'], initial)
        store.cancel(token); self.assertIn(token.resource, disposed)
        self.assertEqual(store.version, 0)
        token = store.prepare(doc); stale = store.prepare(doc)
        self.assertEqual(store.commit(token), 1)
        self.assertIs(visible['resource'], token.resource)
        self.assertIn(initial, disposed)
        self.assertFalse(store.accepts_version(0)); self.assertTrue(store.accepts_version(1))
        with self.assertRaisesRegex(ValueError, 'STALE_STATE'): store.commit(stale)
        store.cancel(stale)
        bad = store.document; bad['defects'][0]['id'] = 'fail-build'
        with self.assertRaisesRegex(RuntimeError, 'build failed'): store.prepare(bad)
        self.assertEqual(store.document, doc); self.assertIs(store.resource, token.resource)
        self.assertTrue(store.undo()); self.assertEqual(store.document['defects'], [])
        self.assertEqual(store.version, 2); self.assertTrue(store.redo()); self.assertEqual(store.document, doc)
        self.assertEqual(store.version, 3); self.assertFalse(store.redo())

    def test_revision_guard_and_candidate_mutation_guard(self):
        store = DefectStore(self.surface)
        doc = store.document; doc['defects'] = [new_defect(self.surface, 'fine_crack')]
        store.commit(store.prepare(doc))
        candidate = store.document; candidate['defects'][0]['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'revision'): store.prepare(candidate)
        candidate['defects'][0]['revision'] += 1
        token = store.prepare(candidate); token.document['defects'][0]['anchor']['s_m'] += 1
        with self.assertRaisesRegex(ValueError, 'changed after'): store.commit(token)
        store.cancel(token)
        self.assertTrue(store.document['defects'][0]['enabled'])
        copied = store.document; copied['defects'].clear()
        self.assertEqual(len(store.document['defects']), 1)

    def test_partial_activation_failure_restores_committed_whole_state(self):
        live = {}; disposed = []
        def activate(resource, doc, version):
            live.update(resource=resource, doc=doc, version=version)
            if any(d['id'] == 'fail-activate' for d in doc['defects']): raise RuntimeError('swap failed')
        store = DefectStore(self.surface, prepare=lambda d: object(), activate=activate, dispose=disposed.append)
        before = store.resource
        doc = store.document; doc['defects'] = [new_defect(self.surface, 'open_crack', id='fail-activate')]
        token = store.prepare(doc)
        with self.assertRaisesRegex(RuntimeError, 'swap failed'): store.commit(token)
        self.assertIs(live['resource'], before); self.assertEqual(live['doc']['defects'], [])
        self.assertEqual(store.version, 0); self.assertFalse(store.can_undo)
        store.cancel(token); self.assertIn(token.resource, disposed)

    def test_explicit_snapshot_restore_preserves_ids_revisions_and_shape(self):
        saved = new_document(self.surface)
        saved['defects'] = [new_defect(self.surface, 'open_crack', id='saved', shape={'seed': 482})]
        restored = DefectStore(self.surface, document=deepcopy(saved))
        self.assertEqual(restored.document, saved)
        original_shape = self.surface.sample_shape(saved['defects'][0])
        self.assertEqual(self.surface.sample_shape(restored.document['defects'][0]), original_shape)
        edited = restored.document
        edited['defects'][0]['revision'] += 1; edited['defects'][0]['anchor']['s_m'] += 3
        restored.commit(restored.prepare(edited))
        self.assertEqual(restored.restore(saved), 2)
        self.assertEqual(restored.document, saved)
        self.assertEqual(self.surface.sample_shape(restored.document['defects'][0]), original_shape)
        self.assertTrue(restored.undo()); self.assertEqual(restored.document, edited)
        self.assertEqual(restored.version, 3)


if __name__ == '__main__': unittest.main()
