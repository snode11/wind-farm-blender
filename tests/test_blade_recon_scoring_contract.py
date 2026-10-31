"""Both scorer CLIs require independent, matching model and sample identities."""
import copy
import importlib.util
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIG = dict(hub_height_m=90.0, overhang_m=5.0, tilt_deg=5.0,
              cone_deg=2.5, hub_radius_m=1.5, tip_radius_m=63.0,
              prebend_tip_m=0.0, n_sections=8, n_ring=8, spin=1)


class ScoringContractTests(unittest.TestCase):
    def payloads(self):
        frame = {'frame': 0, 'state': [0.0] * 13,
                 'observed_sections': [[True] * 8 for _ in range(3)]}
        return ({'turbine': CONFIG.copy(), 'frames': [frame]},
                {'turbine': CONFIG.copy(), 'frames': [copy.deepcopy(frame)]})

    def run_scorers(self, recon, truth, *, truth_turbine=None,
                    variants=('blade_recon', 'blade_recon0.2')):
        results = []
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            files = dict(recon=recon, truth=truth)
            if truth_turbine is not None:
                files['truth_turbine'] = truth_turbine
            for name, payload in files.items():
                (directory / (name + '.json')).write_text(json.dumps(payload))
            for variant in variants:
                command = [sys.executable, str(ROOT / variant / 'evaluate.py'),
                           '--recon', str(directory / 'recon.json'),
                           '--truth', str(directory / 'truth.json'), '--skip', '0']
                if truth_turbine is not None:
                    command += ['--truth-turbine', str(directory / 'truth_turbine.json')]
                results.append(subprocess.run(command, text=True, capture_output=True))
        return results

    def assert_rejected(self, recon, truth, message):
        for result in self.run_scorers(recon, truth):
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(message, result.stderr)

    def test_identical_same_model_baseline(self):
        for result in self.run_scorers(*self.payloads()):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('评分帧数 1', result.stdout)
            self.assertIn('均值 0.000  P95 0.000  最大 0.000', result.stdout)
            self.assertIn('时间未验证 1 帧', result.stdout)

    def test_explicit_offset_clock_is_independent_of_nominal_fps(self):
        recon, truth = self.payloads()
        recon['fps'], truth['fps'] = 10., 25.
        for payload in (recon, truth):
            payload['frames'][0]['t'] = 117.125
            for index, stamp in ((37, 118.375), (48, 118.625)):
                frame = copy.deepcopy(payload['frames'][0])
                frame.update(frame=index, t=stamp)
                payload['frames'].append(frame)
        for result in self.run_scorers(recon, truth):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('显式时间已验证 3 帧', result.stdout)

    def test_conflicting_or_nonfinite_explicit_time_is_rejected(self):
        for value in (117., float('nan'), float('inf'), None, True, '0'):
            recon, truth = self.payloads()
            recon['frames'][0]['t'] = value
            truth['frames'][0]['t'] = 0.
            self.assert_rejected(recon, truth, 'time identity mismatch' if value == 117. else 't must be finite')

    def test_one_sided_legacy_time_is_explicitly_unverified(self):
        recon, truth = self.payloads()
        recon['frames'][0]['t'] = 117.
        for result in self.run_scorers(recon, truth):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('时间未验证 1 帧', result.stdout)

    def test_same_model_cyclic_blade_alignment_preserves_zero_error(self):
        recon, truth = self.payloads()
        recon['frames'][0]['state'] = [120., 1., 2., 3., 4., 5., 6., .1, .2, .3, .4, .5, .6]
        truth['frames'][0]['state'] = [0., 3., 1., 2., 6., 4., 5., .3, .1, .2, .6, .4, .5]
        for result in self.run_scorers(recon, truth):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('截面网格误差(m)  观测区     n=  24  均值 0.000', result.stdout)

    def test_different_radius_cannot_receive_false_zero(self):
        recon, truth = self.payloads()
        truth['turbine']['tip_radius_m'] = 70.0
        self.assert_rejected(recon, truth, 'model identity mismatch')

    def test_legacy_truth_without_model_is_explicitly_rejected(self):
        recon, truth = self.payloads()
        del truth['turbine']
        self.assert_rejected(recon, truth, 'truth turbine identity')

    def test_legacy_truth_accepts_only_explicit_independent_model(self):
        recon, truth = self.payloads()
        del truth['turbine']
        for result in self.run_scorers(recon, truth, truth_turbine=CONFIG):
            self.assertEqual(result.returncode, 0, result.stderr)
        for result in self.run_scorers(recon, truth, truth_turbine={**CONFIG, 'tip_radius_m': 70.0}):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('model identity mismatch', result.stderr)

    def test_embedded_truth_identity_cannot_be_overridden(self):
        recon, truth = self.payloads()
        truth['turbine']['tip_radius_m'] = 70.0
        for result in self.run_scorers(recon, truth, truth_turbine=CONFIG):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('truth turbine identity conflicts', result.stderr)

    def test_incomplete_or_invalid_identity_is_rejected(self):
        for config in ({}, {**CONFIG, 'tip_radius_m': float('nan')},
                       {**CONFIG, 'n_sections': 8.5}, {**CONFIG, 'spin': 0},
                       {**CONFIG, 'tip_radius_m': 1.0},
                       {**CONFIG, 'unknown_model': 'another-template'}):
            with self.subTest(config=config):
                recon, truth = self.payloads()
                truth['turbine'] = config
                self.assert_rejected(recon, truth, 'truth turbine identity')

    def test_fixed_template_payload_and_hash_are_part_of_model_identity(self):
        recon, truth = self.payloads()
        config = {**CONFIG, 'n_ring': 12}
        ring = [[math.cos(i * math.tau / 12), math.sin(i * math.tau / 12)] for i in range(12)]
        template = {
            'schema': 'blade-healthy-loft.v1',
            'coordinate_contract': 'leading_upwind_radial_metres',
            'ring_contract': 'TE_pressure_LE_suction_cosine_chord',
            'dimension_contract': {key: config[key] for key in ('hub_radius_m', 'tip_radius_m', 'n_sections', 'n_ring')},
            'radii_m': [1.5 + i * (63.0 - 1.5) / 7 for i in range(8)],
            'offsets_m': [copy.deepcopy(ring) for _ in range(8)],
            'section_chord_m': [2.] * 8,
            'provenance': {'kind': 'fixed_design_asset', 'geometry_asset_sha256': 'a' * 64,
                           'builder_sha256': 'b' * 64},
            'tip_policy': 'source_illustrative_taper_with_1um_terminal_ring'}

        def identity(payload):
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            return {**config, 'healthy_template': payload, 'healthy_template_sha256': digest}

        recon['turbine'] = identity(template)
        truth['turbine'] = copy.deepcopy(recon['turbine'])
        for result in self.run_scorers(recon, truth, variants=('blade_recon0.2',)):
            self.assertEqual(result.returncode, 0, result.stderr)
        changed = copy.deepcopy(template)
        changed['offsets_m'][4][0][0] += .25
        truth['turbine'] = identity(changed)
        for result in self.run_scorers(recon, truth, variants=('blade_recon0.2',)):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('model identity mismatch', result.stderr)
        truth['turbine']['healthy_template_sha256'] = 'f' * 64
        for result in self.run_scorers(recon, truth, variants=('blade_recon0.2',)):
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('truth turbine identity', result.stderr)

    def test_duplicate_or_unmatched_frame_identity_is_rejected(self):
        for field in ('truth', 'recon'):
            recon, truth = self.payloads()
            target = truth if field == 'truth' else recon
            target['frames'].append(copy.deepcopy(target['frames'][0]))
            self.assert_rejected(recon, truth, 'duplicate frame')
        recon, truth = self.payloads()
        truth['frames'][0]['frame'] = 1
        self.assert_rejected(recon, truth, 'missing truth frame')

    def test_states_and_observed_sections_must_align(self):
        for state in ([0.0] * 10, [[0.0] * 13], [float('nan')] * 13):
            for field in ('truth', 'recon'):
                recon, truth = self.payloads()
                (truth if field == 'truth' else recon)['frames'][0]['state'] = state
                self.assert_rejected(recon, truth, 'state must be 13 finite values')
        for sections in ([[True] * 7] * 3, [[True] * 8] * 2,
                         [[None] * 8] * 3, [[2] * 8] * 3):
            recon, truth = self.payloads()
            recon['frames'][0]['observed_sections'] = sections
            self.assert_rejected(recon, truth, 'observed_sections must have shape (3, 8)')

    @unittest.skipUnless(importlib.util.find_spec('cv2'), 'synth output requires OpenCV')
    def test_synth_truth_always_carries_its_own_complete_model(self):
        for variant, extra in (('blade_recon', []), ('blade_recon0.2', ['--png']),
                               ('blade_recon0.2', ['--png', '--texture'])):
            with self.subTest(variant=variant, flags=extra), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run(
                    [sys.executable, str(ROOT / variant / 'synth.py'), '--out', directory,
                     '--seconds', '0', '--scale', '0.02', *extra], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = Path(directory)
                truth = json.loads((output / 'truth.json').read_text())
                cameras = json.loads((output / 'cameras.json').read_text())
                self.assertEqual(truth['turbine'], cameras['turbine'])
                self.assertEqual(set(truth['turbine']), set(CONFIG))


if __name__ == '__main__':
    unittest.main()
