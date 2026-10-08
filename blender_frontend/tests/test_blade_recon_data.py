"""Saved boss-model geometry, frame alignment, and invalid-input regressions."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

from wfrl_blender.blade_recon_data import (
    EXTERNAL_SOURCE_LABEL, MODEL_SHA256, ReconSequence, default_recon_path,
)
from wfrl_blender._vendor.blade_recon import model


EXPECTED_MODEL_SHA256 = '9253fdfb7bb1e76790129511a24b98311c08c7c14a2a54b0c012d260842b888c'


def sample_data():
    turbine = model.TurbineConfig(n_sections=4, n_ring=8).to_dict()
    return dict(turbine=turbine, fps=10, state_names=model.STATE_NAMES.copy(), frames=[
        dict(frame=2, t=.2, state=[14.5] + [1.] * 12,
             std=[.1] * 13, observed_sections=[[True, False, True, False]] * 3),
        dict(frame=7, t=.7, state=[50.8] + [2.] * 12,
             observed_sections=[[0, 1, 1, 0]] * 3),
    ])


def sample_camera():
    return dict(cameras=[dict(name='C1', W=640, H=480,
                             K=[[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                             T_cv_from_world=[[1, 0, 0, -5], [0, 1, 0, 0], [0, 0, 1, -90]])])


class ReconDataTests(unittest.TestCase):
    def test_reading_keeps_source_times_and_boolean_observations(self):
        data = sample_data()
        sequence = ReconSequence.from_data(data)
        self.assertEqual(sequence.source_label, EXTERNAL_SOURCE_LABEL)
        self.assertEqual(sequence.source_sha256, '')
        self.assertEqual(sequence.model_sha256, EXPECTED_MODEL_SHA256)
        np.testing.assert_array_equal(sequence.source_frames, [2, 7])
        np.testing.assert_array_equal(sequence.times, [.2, .7])
        self.assertEqual(sequence.states.shape, (2, 13))
        self.assertEqual(sequence.observed.shape, (2, 3, 4))
        self.assertEqual(sequence.observed.dtype, np.dtype(bool))
        self.assertFalse(sequence.states.flags.writeable)
        self.assertIsNone(sequence.truth_states)
        self.assertIsNone(sequence.truth_geometry(0))
        self.assertEqual(sequence.cameras, [])
        data['frames'][0]['state'][0] = 9000
        self.assertEqual(sequence.states[0, 0], 14.5)
        self.assertEqual(sequence.frames[0]['state'][0], 14.5)

    def test_geometry_is_exact_original_forward_model(self):
        sequence = ReconSequence.from_data(sample_data())
        expected = model.Rotor(model.TurbineConfig(**sample_data()['turbine']))
        for i in range(2):
            V, A = sequence.geometry(i)
            EV, EA = expected.forward(sequence.states[i])
            np.testing.assert_array_equal(V, EV)
            np.testing.assert_array_equal(A, EA)
            self.assertEqual(V.shape, (3, 4, 8, 3))
            self.assertEqual(A.shape, (3, 4, 3))
        self.assertGreater(np.linalg.norm(sequence.geometry(0)[0] - sequence.geometry(1)[0]), 1.)

    def test_truth_uses_original_frame_ids_after_skips(self):
        data = sample_data()
        truth = dict(frames=[dict(frame=i, t=i / 10, state=[float(i)] * 13) for i in range(10)])
        sequence = ReconSequence.from_data(data, truth_data=truth)
        np.testing.assert_array_equal(sequence.truth_states, [[2.] * 13, [7.] * 13])
        V, A = sequence.truth_geometry(1)
        EV, EA = sequence.rotor.forward([7.] * 13)
        np.testing.assert_array_equal(V, EV)
        np.testing.assert_array_equal(A, EA)
        # The original frame 7 is not the second record of truth.
        self.assertFalse(np.array_equal(sequence.truth_states[1], truth['frames'][1]['state']))

    def test_missing_truth_is_explicit_and_does_not_fill_reconstruction(self):
        data = sample_data()
        sequence = ReconSequence.from_data(data, truth_data=dict(frames=[dict(frame=2, state=[3.] * 13)]))
        self.assertIsNotNone(sequence.truth_geometry(0))
        self.assertIsNone(sequence.truth_geometry(1))
        self.assertTrue(np.isnan(sequence.truth_states[1]).all())
        np.testing.assert_array_equal(sequence.states[1], data['frames'][1]['state'])

    def test_rejects_truth_frame_time_disagreement(self):
        truth = dict(frames=[dict(frame=2, t=.3, state=[1.] * 13)])
        with self.assertRaisesRegex(ValueError, 'truth time'):
            ReconSequence.from_data(sample_data(), truth_data=truth)

    def test_rejects_malformed_frame_arrays_and_numbers(self):
        mutations = [
            lambda d: d.update(frames=[]),
            lambda d: d['frames'][0].update(state=[0.] * 12),
            lambda d: d['frames'][0]['state'].__setitem__(3, float('nan')),
            lambda d: d['frames'][0]['state'].__setitem__(3, float('inf')),
            lambda d: d['frames'][0]['state'].__setitem__(3, '0'),
            lambda d: d['frames'][0]['state'].__setitem__(3, True),
            lambda d: d['frames'][0].update(t=float('nan')),
            lambda d: d['frames'][0].pop('t'),
            lambda d: d['frames'][0].update(t=-1),
            lambda d: d['frames'][1].update(t=.2),
            lambda d: d['frames'][1].update(t=.1),
            lambda d: d['frames'][1].update(frame=2),
            lambda d: d['frames'][1].update(frame=1),
            lambda d: d['frames'][1].update(frame=7.0),
            lambda d: d['frames'][1].update(frame=True),
            lambda d: d['frames'][1].update(frame=2 ** 80),
            lambda d: d['frames'][0].update(std=[.1] * 12),
            lambda d: d['frames'][0]['std'].__setitem__(0, -.1),
            lambda d: d['frames'][0]['std'].__setitem__(0, float('inf')),
            lambda d: d.update(state_names=list(reversed(model.STATE_NAMES))),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                data = sample_data()
                mutate(data)
                with self.assertRaises(ValueError):
                    ReconSequence.from_data(data)

    def test_rejects_bad_fps(self):
        for fps in (0, -1, float('nan'), float('inf'), True, '10', None):
            with self.subTest(fps=fps):
                data = sample_data()
                data['fps'] = fps
                with self.assertRaisesRegex(ValueError, 'fps'):
                    ReconSequence.from_data(data)

    def test_rejects_bad_config_before_sampling(self):
        for key, value in (('n_sections', 1), ('n_sections', 4.5), ('n_sections', True),
                           ('n_ring', 7), ('n_ring', 2), ('n_ring', 2048),
                           ('spin', 0), ('spin', 1.0), ('spin', True),
                           ('hub_radius_m', 0), ('tip_radius_m', 1.5),
                           ('hub_height_m', -1), ('tilt_deg', 90), ('cone_deg', -90),
                           ('prebend_tip_m', float('inf'))):
            with self.subTest(key=key, value=value):
                data = sample_data()
                data['turbine'][key] = value
                with self.assertRaises(ValueError):
                    ReconSequence.from_data(data)

    def test_rejects_truthy_or_malformed_observation_masks(self):
        for value in (2, -1, 1.0, '1', None):
            with self.subTest(value=value):
                data = sample_data()
                data['frames'][0]['observed_sections'] = [[value] * 4] * 3
                with self.assertRaisesRegex(ValueError, 'observed_sections'):
                    ReconSequence.from_data(data)
        for mask in ([[True] * 3] * 3, [[True] * 4] * 2, None):
            data = sample_data()
            data['frames'][0]['observed_sections'] = mask
            with self.assertRaisesRegex(ValueError, 'observed_sections'):
                ReconSequence.from_data(data)

    def test_camera_homogeneous_transform_is_validated_and_normalized(self):
        data = sample_camera()
        data['cameras'][0]['T_cv_from_world'].append([0, 0, 0, 1])
        sequence = ReconSequence.from_data(sample_data(), cameras_data=data)
        self.assertEqual(np.asarray(sequence.cameras[0]['T_cv_from_world']).shape, (3, 4))
        self.assertEqual(len(data['cameras'][0]['T_cv_from_world']), 4)
        self.assertEqual(sequence.cameras[0]['name'], 'C1')

    def test_rejects_bad_cameras(self):
        mutations = [
            lambda d: d.update(cameras=[]),
            lambda d: d['cameras'][0].update(W=0),
            lambda d: d['cameras'][0].update(H=480.0),
            lambda d: d['cameras'][0].update(K=[[0] * 3] * 3),
            lambda d: d['cameras'][0]['K'][0].__setitem__(0, float('nan')),
            lambda d: d['cameras'][0].update(K=[[1] * 3] * 2),
            lambda d: d['cameras'][0].update(T_cv_from_world=[[0] * 4] * 3),
            lambda d: d['cameras'][0]['T_cv_from_world'][0].__setitem__(3, float('inf')),
            lambda d: d['cameras'][0]['T_cv_from_world'].append([0, 0, 0, 2]),
            lambda d: d['cameras'][0].update(T_cv_from_world=[[1] * 3] * 3),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(mutation=i):
                cameras = sample_camera()
                mutate(cameras)
                with self.assertRaises(ValueError):
                    ReconSequence.from_data(sample_data(), cameras_data=cameras)

    def test_rejects_companion_metadata_for_another_turbine(self):
        cameras = sample_camera()
        cameras['turbine'] = dict(sample_data()['turbine'], tip_radius_m=92)
        with self.assertRaisesRegex(ValueError, 'turbine does not match'):
            ReconSequence.from_data(sample_data(), cameras_data=cameras)
        cameras.pop('turbine')
        cameras['fps'] = 20
        with self.assertRaisesRegex(ValueError, 'fps does not match'):
            ReconSequence.from_data(sample_data(), cameras_data=cameras)

    def test_index_errors_and_nonfinite_generated_geometry_are_explicit(self):
        sequence = ReconSequence.from_data(sample_data())
        for index in (-1, 2, 0.0, True):
            with self.subTest(index=index), self.assertRaises(IndexError):
                sequence.geometry(index)
        data = sample_data()
        data['frames'][0]['state'][4] = 1e300
        sequence = ReconSequence.from_data(data)
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            sequence.geometry(0)


class BundledProvenanceTests(unittest.TestCase):
    def test_original_saved_data_and_model_hashes(self):
        assets = default_recon_path().parent
        manifest = json.loads((assets / 'manifest.json').read_text())
        self.assertEqual(manifest['source_classification'], 'SYNTHETIC')
        self.assertEqual(manifest['model']['sha256'], EXPECTED_MODEL_SHA256)
        self.assertEqual(MODEL_SHA256, EXPECTED_MODEL_SHA256)
        for name, record in manifest['files'].items():
            content = (assets / name).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), record['sha256'])
            self.assertEqual(len(content), record['size_bytes'])
            source = Path(record['source_path'])
            if source.is_file():
                self.assertEqual(content, source.read_bytes())
        source_model = Path(manifest['model']['source_path'])
        if source_model.is_file():
            self.assertEqual(Path(model.__file__).read_bytes(), source_model.read_bytes())

    def test_bundled_sequence_has_original_200_frames_and_companions(self):
        sequence = ReconSequence(default_recon_path())
        self.assertEqual(sequence.states.shape, (200, 13))
        self.assertEqual(sequence.observed.shape, (200, 3, 40))
        self.assertEqual(sequence.fps, 10)
        self.assertAlmostEqual(sequence.times[-1], 19.9)
        self.assertTrue(sequence.source_label.startswith('SYNTHETIC'))
        self.assertEqual(sequence.source_sha256, sequence.manifest['files']['recon.json']['sha256'])
        self.assertEqual(len(sequence.cameras), 3)
        self.assertTrue(np.isfinite(sequence.truth_states).all())
        for i in (0, 50, 100, 199):
            V, A = sequence.geometry(i)
            self.assertEqual(V.shape, (3, 40, 32, 3))
            self.assertEqual(A.shape, (3, 40, 3))

    def test_geometry_equals_original_source_import(self):
        manifest = json.loads(default_recon_path().with_name('manifest.json').read_text())
        source = Path(manifest['model']['source_path'])
        if not source.is_file():
            self.skipTest('Original boss source is unavailable; portable bundle hash tested separately')
        spec = importlib.util.spec_from_file_location('boss_blade_recon_original_test', source)
        original = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = original
        try:
            spec.loader.exec_module(original)
            sequence = ReconSequence(default_recon_path())
            rotor = original.Rotor(original.TurbineConfig(**sequence.data['turbine']))
            for i in (0, 100, 199):
                V, A = sequence.geometry(i)
                expected_V, expected_A = rotor.forward(sequence.states[i])
                np.testing.assert_array_equal(V, expected_V)
                np.testing.assert_array_equal(A, expected_A)
        finally:
            sys.modules.pop(spec.name, None)

    def test_unmatched_manifest_cannot_claim_synthetic_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'recon.json'
            path.write_text(json.dumps(sample_data()))
            manifest = dict(source_label='SYNTHETIC misleading', files={'recon.json': {'sha256': '0' * 64}})
            (root / 'manifest.json').write_text(json.dumps(manifest))
            sequence = ReconSequence(path)
            self.assertEqual(sequence.source_label, EXTERNAL_SOURCE_LABEL)
            self.assertIsNone(sequence.manifest)

    def test_matching_manifest_verifies_companions_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'recon.json'
            path.write_text(json.dumps(sample_data()))
            (root / 'truth.json').write_text(json.dumps(dict(frames=[dict(frame=2, state=[1.] * 13)])))
            manifest = dict(source_label='SYNTHETIC test', files={
                'recon.json': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()},
                'truth.json': {'sha256': '0' * 64},
            }, model={'sha256': EXPECTED_MODEL_SHA256})
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'hash mismatch for truth'):
                ReconSequence(path)
            manifest['files'].pop('truth.json')
            manifest['model']['sha256'] = '0' * 64
            (root / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'model hash'):
                ReconSequence(path)

    def test_bad_path_or_json_is_a_reader_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recon.json'
            with self.assertRaisesRegex(ValueError, 'Cannot read'):
                ReconSequence(path)
            path.write_text('{broken')
            with self.assertRaisesRegex(ValueError, 'Cannot read'):
                ReconSequence(path)


if __name__ == '__main__':
    unittest.main()
