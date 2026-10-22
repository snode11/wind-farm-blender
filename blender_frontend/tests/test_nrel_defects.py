"""Host contracts for NREL reference binding and complete edit transactions.

The isolated package alias keeps these tests independent of Blender registration.
Run with unittest or pytest; neither bpy nor NumPy is needed here.
"""
from copy import deepcopy
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest


FRONTEND = Path(__file__).resolve().parents[1] / 'wfrl_blender'
ALIAS = '_wfrl_nrel_defect_contract_tests'
package = types.ModuleType(ALIAS)
package.__path__ = [str(FRONTEND)]
sys.modules.setdefault(ALIAS, package)
defects_package = types.ModuleType(ALIAS + '.nrel_defects')
defects_package.__path__ = [str(FRONTEND / 'nrel_defects')]
sys.modules.setdefault(ALIAS + '.nrel_defects', defects_package)
geometry = importlib.import_module(ALIAS + '.turbine_geometry')
reference = importlib.import_module(ALIAS + '.nrel_defects.reference_surface')
schema = importlib.import_module(ALIAS + '.nrel_defects.defect_schema')
store_module = importlib.import_module(ALIAS + '.nrel_defects.defect_store')


class NrelDefectContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vertices, cls.faces = geometry.blade_mesh(4, 48)
        cls.surface = reference.ReferenceSurface(cls.vertices, cls.faces, ring_points=48)

    def defect(self, kind='fine_crack', **overrides):
        return schema.new_defect(self.surface, kind, **overrides)

    def document(self, *defects):
        result = schema.new_document(self.surface)
        result['defects'] = list(defects)
        return result

    def test_reference_identity_covers_actual_vertices_and_is_defensive(self):
        other = reference.ReferenceSurface(self.vertices, self.faces, ring_points=48)
        self.assertEqual(self.surface.reference_geometry_sha256, other.reference_geometry_sha256)
        changed = [tuple(point) for point in self.vertices]
        point = list(changed[48 * 30 + 7]); point[0] += .002
        changed[48 * 30 + 7] = tuple(point)
        altered = reference.ReferenceSurface(changed, self.faces, ring_points=48)
        self.assertNotEqual(self.surface.reference_geometry_sha256, altered.reference_geometry_sha256)
        metadata = self.surface.metadata()
        metadata['valid_domain']['u'] = [0., 1.]
        self.assertEqual(tuple(self.surface.domain['u']), (.05, .95))

    def test_both_surface_sides_roundtrip_clicks_and_reject_off_surface_points(self):
        low, high = self.surface.domain['s_m']
        for side in ('suction', 'pressure'):
            for fraction, chord in ((.25, .21), (.5, .4), (.75, .77)):
                anchor = dict(s_m=low + (high-low)*fraction, u=chord,
                              surface_side=side, theta_deg=0.)
                frame = self.surface.frame(anchor)
                found = self.surface.locate(frame['point'], surface_side=side)
                self.assertAlmostEqual(found['s_m'], anchor['s_m'], places=8)
                self.assertAlmostEqual(found['u'], chord, places=8)
                wall = tuple(p-.01*n for p, n in zip(frame['point'], frame['normal']))
                with self.assertRaises(ValueError):
                    self.surface.locate(wall, surface_side=side)
        with self.assertRaises(ValueError):
            self.surface.point(dict(anchor, s_m=high+.01))
        with self.assertRaises(ValueError):
            self.surface.point(dict(anchor, region_hint='leading'))

    def test_six_types_across_three_turbines_and_two_sides_save_exactly(self):
        rows = [self.defect(kind, turbine_id='T'+str(index % 3 + 1),
                    blade_id=index//3+1,
                    anchor=dict(surface_side='suction' if index % 2 == 0 else 'pressure'))
                for index, kind in enumerate(schema.PRESETS)]
        self.assertEqual(len(rows), 6)
        doc = schema.validate_document(self.document(*rows), self.surface)
        self.assertEqual({d['turbine_id'] for d in doc['defects']}, {'T1', 'T2', 'T3'})
        self.assertTrue(all(d['effects']['physics_coupled'] is False for d in rows))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'defects.json'
            schema.save_document(path, doc, self.surface)
            self.assertEqual(schema.load_document(path, self.surface), doc)
            self.assertEqual(json.loads(path.read_text()), doc)

    def test_reference_hash_and_gw184_parameterization_mismatch_are_rejected(self):
        doc = self.document(self.defect())
        for key, value in (('reference_geometry_id', 'gw184-reference'),
                           ('parameterization_version', 'gw184-healthy-triangles-v1'),
                           ('reference_geometry_sha256', '0'*64)):
            bad = deepcopy(doc); bad[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'STALE_GEOMETRY'):
                schema.validate_document(bad, self.surface)

    def test_unknown_turbines_and_physics_effects_are_rejected(self):
        for turbine in ('T0', 'T4', 'GW184'):
            with self.subTest(turbine=turbine), self.assertRaises(ValueError):
                self.defect(turbine_id=turbine)
        bad = self.document(self.defect())
        bad['defects'][0]['effects']['physics_coupled'] = True
        with self.assertRaises(ValueError):
            schema.validate_document(bad, self.surface)

    def test_overlap_is_scoped_by_turbine_blade_side_and_enabled_state(self):
        first = self.defect('pit')
        overlapping = deepcopy(first); overlapping['id'] = 'DifferentEvent'
        with self.assertRaisesRegex(ValueError, 'OVERLAPPING_DEFECTS'):
            schema.validate_document(self.document(first, overlapping), self.surface)
        for changes in (dict(turbine_id='T2'), dict(blade_id=2), dict(enabled=False),
                        dict(anchor=dict(first['anchor'], surface_side='pressure'))):
            second = deepcopy(overlapping); second.update(changes)
            schema.validate_document(self.document(first, second), self.surface)

    def test_invalid_load_does_not_replace_an_existing_valid_file(self):
        doc = self.document(self.defect())
        bad = deepcopy(doc); bad['defects'][0]['shape']['length_m'] = math.nan
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'defects.json'
            schema.save_document(path, doc, self.surface)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                schema.save_document(path, bad, self.surface)
            self.assertEqual(path.read_bytes(), before)

    def test_cancel_undo_redo_and_stale_analysis_keep_whole_documents(self):
        initial = self.document(self.defect())
        store = store_module.DefectStore(self.surface, initial)
        changed = deepcopy(initial); changed['defects'][0]['enabled'] = False
        changed['defects'][0]['revision'] += 1
        token = store.prepare(changed); store.cancel(token)
        self.assertEqual(store.document, initial)
        version = store.version
        store.commit(store.prepare(changed))
        self.assertFalse(store.accepts_version(version))
        self.assertTrue(store.undo()); self.assertEqual(store.document, initial)
        self.assertTrue(store.redo()); self.assertEqual(store.document, changed)
        self.assertGreater(store.version, version)

    def test_failed_prepare_and_failed_activation_retain_committed_state(self):
        initial = self.document(self.defect())
        state = {'document': None, 'fail_prepare': False, 'fail_activate': False}
        def prepare(document):
            if state['fail_prepare']:
                raise RuntimeError('injected prepare failure')
            return deepcopy(document)
        def activate(resource, document, version):
            if state['fail_activate'] and not document['defects'][0]['enabled']:
                raise RuntimeError('injected activate failure')
            state['document'] = deepcopy(document)
        store = store_module.DefectStore(self.surface, initial, prepare=prepare, activate=activate)
        changed = deepcopy(initial); changed['defects'][0]['enabled'] = False
        changed['defects'][0]['revision'] += 1
        state['fail_prepare'] = True
        with self.assertRaises(RuntimeError): store.prepare(changed)
        self.assertEqual(store.document, initial)
        state['fail_prepare'] = False
        token = store.prepare(changed); state['fail_activate'] = True
        with self.assertRaises(RuntimeError): store.commit(token)
        self.assertEqual(store.document, initial)
        self.assertEqual(state['document'], initial)
        store.cancel(token)


if __name__ == '__main__':
    unittest.main()
