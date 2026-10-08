"""Same-source identity and committed clock checks without a Blender window."""
import copy
import json
from types import SimpleNamespace as NS
import unittest

import numpy as np

from wfrl_blender import split_mappo_texture as texture
from wfrl_blender.blade_recon_data import ReconSequence


class SameSourceTextureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provenance = json.loads((texture.package_path()/'manifest.json').read_text())
        cls.sequence = ReconSequence(texture.package_path()/'recon.json')

    def test_delivered_clock_preserves_601_actual_sample_ids(self):
        spec = texture._validate_clock(self.sequence)
        self.assertEqual(spec.frame_end, 3601)
        self.assertEqual(spec.sampling_hz, 10.)
        sequence = copy.copy(self.sequence)
        sequence.times = self.sequence.times.copy()
        sequence.times[9] += .001
        with self.assertRaisesRegex(ValueError, '601'):
            texture._validate_clock(sequence)
        sequence.times = self.sequence.times
        sequence.source_frames = np.roll(self.sequence.source_frames, 1)
        with self.assertRaisesRegex(ValueError, '601'):
            texture._validate_clock(sequence)

    def test_missing_source_identity_is_rejected_instead_of_defaulted(self):
        scene = {'wfrl_farm_manifest_sha256': texture.SOURCE_HASH}
        texture._validate_source(scene, self.provenance, check_files=False)
        bad = copy.deepcopy(self.provenance)
        bad.pop('mappo_manifest_sha256', None)
        bad['source_package_hashes'].pop('assets/mappo/manifest.json')
        with self.assertRaisesRegex(ValueError, '不匹配'):
            texture._validate_source(scene, bad, check_files=False)
        with self.assertRaisesRegex(ValueError, '不匹配'):
            texture._validate_source({'wfrl_farm_manifest_sha256': 'another clock-compatible source'},
                                     self.provenance, check_files=False)
        bad = copy.deepcopy(self.provenance)
        bad['clock']['simulation_start_s'] = 0.
        with self.assertRaisesRegex(ValueError, '时间映射'):
            texture._validate_source(scene, bad, check_files=False)

    def test_runtime_model_identity_is_required_for_saved_and_fresh_provenance(self):
        scene = {'wfrl_farm_manifest_sha256': texture.SOURCE_HASH}
        for kind in ('missing_model', 'missing_hash', 'empty_hash', 'wrong_hash'):
            bad = copy.deepcopy(self.provenance)
            if kind == 'missing_model':
                bad.pop('model')
            elif kind == 'missing_hash':
                bad['model'].pop('sha256')
            else:
                bad['model']['sha256'] = '' if kind == 'empty_hash' else '0'*64
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    texture._validate_source(scene, bad, check_files=False)

    def test_runtime_requires_complete_declared_absolute_clock(self):
        scene = {'wfrl_farm_manifest_sha256': texture.SOURCE_HASH}
        fields = ('samples', 'fps', 'source_frame_start', 'source_frame_end',
                  'time_start_s', 'time_end_s', 'simulation_start_s', 'simulation_end_s',
                  'source_hz', 'timeline_fps', 'stride', 'frame_start', 'frame_end')
        for field in fields:
            for kind in ('missing', 'wrong', 'string', 'boolean'):
                bad = copy.deepcopy(self.provenance)
                clock = bad['clock']
                if kind == 'missing':
                    clock.pop(field)
                elif kind == 'wrong':
                    clock[field] += 1
                elif kind == 'string':
                    clock[field] = str(clock[field])
                else:
                    clock[field] = bool(clock[field])
                with self.subTest(field=field, kind=kind):
                    with self.assertRaisesRegex(ValueError, '时间映射'):
                        texture._validate_source(scene, bad, check_files=False)

    def test_runtime_absolute_sample_clock_requires_saved_values(self):
        for field in ('sim_t', 'blender_frame'):
            for kind in ('missing', 'wrong', 'nonfinite', 'string', 'wrong_numeric_type'):
                sequence = copy.copy(self.sequence)
                sequence.frames = copy.deepcopy(self.sequence.frames)
                row = sequence.frames[13]
                if kind == 'missing':
                    row.pop(field)
                elif kind == 'wrong':
                    row[field] += 1
                elif kind == 'nonfinite':
                    row[field] = float('nan')
                elif kind == 'string':
                    row[field] = str(row[field])
                elif field == 'blender_frame':
                    row[field] = float(row[field])
                else:
                    row[field] = None
                with self.subTest(field=field, kind=kind):
                    with self.assertRaises(ValueError):
                        texture._validate_clock(sequence)

    def test_committed_information_reports_timeline_and_held_sample_separately(self):
        scene = NS(as_pointer=lambda: 900, frame_current=52, frame_subframe=0.)
        try:
            texture._SESSIONS[900] = dict(sequence=self.sequence, index=8)
            value = texture.information(scene)
            self.assertEqual(value['index'], 8)
            self.assertEqual(value['time_s'], .8)
            self.assertEqual(value['sample_time_s'], 117.8)
            self.assertAlmostEqual(value['timeline_time_s'], 117.85)
            self.assertEqual(value['age_ms'], 50.)
            self.assertTrue(value['synchronized'])
            scene.frame_current = 3602
            self.assertFalse(texture.information(scene)['in_range'])
            self.assertIsNone(texture.information(scene)['age_ms'])
        finally:
            texture._SESSIONS.pop(900, None)

    def test_separate_markers_keep_independent_synthetic_identity(self):
        from wfrl_blender import split_reconstruction as split, split_surface_texture as synthetic
        scene = {texture.MARKER: True, synthetic.MARKER: True, 'split_texture_current_source': 'MAPPO'}
        self.assertTrue(texture.active(scene))
        self.assertFalse(synthetic.active(scene))
        self.assertIs(split.texture_adapter(scene), texture)
        scene['split_texture_current_source'] = 'SYNTHETIC'
        self.assertFalse(texture.active(scene))
        self.assertTrue(synthetic.active(scene))
        self.assertIs(split.texture_adapter(scene), synthetic)


if __name__ == '__main__':
    unittest.main()
