"""Regression tests for phase 1 inputs and phase 2 absolute-angle differences."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ALG = ROOT / 'blade_recon0.2'
sys.path.insert(0, str(ALG))
from input_adapter import InputSequence, RGBSource
from camera import Camera
from model import Rotor, TurbineConfig
from recon import Fitter, FrameSource, Obs, segment


def bundle(root, frames=(42, 44)):
    cfg = TurbineConfig(n_sections=8, n_ring=8)
    records = []
    for k, frame in enumerate(frames):
        T = np.eye(4); T[0, 3] = k * 0.23; T[2, 3] = 200
        rgb = 'A/%06d.png' % frame
        mask = 'masks/A/%06d.png' % frame
        (root / 'A').mkdir(exist_ok=True)
        (root / 'masks/A').mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(root / rgb), np.full((12, 16, 3), 100 + k, np.uint8))
        cv2.imwrite(str(root / mask), np.zeros((12, 16), np.uint8))
        records.append({'frame': frame, 't': frame / 10,
             'cameras': [{'name': 'A', 'W': 16, 'H': 12, 'K': [[20, 0, 7.5], [0, 20, 5.5], [0, 0, 1]],
                          'T_cv_from_model': T.tolist(), 'rgb': rgb, 'mask': mask}]})
    meta = {'schema': 'blade-surface-input.v1', 'extrinsic_from_frame': 'model',
            'pixel_coordinates': 'original_image_pixel_centers', 'color_order': 'RGB',
            'turbine': cfg.to_dict(), 'frames': records}
    (root / 'input_bundle.json').write_text(json.dumps(meta))
    return meta


def legacy(root, identities):
    cams = []
    for name, frames in identities.items():
        cam = Camera(name, 16, 12, [[20, 0, 7.5], [0, 20, 5.5], [0, 0, 1]], np.eye(4))
        cams.append(cam.to_dict()); (root / name).mkdir()
        for frame in frames:
            cv2.imwrite(str(root / name / ('%06d.png' % frame)), np.full((12, 16, 3), 100, np.uint8))
    (root / 'cameras.json').write_text(json.dumps({'turbine': TurbineConfig(n_sections=8, n_ring=8).to_dict(),
                                                 'fps': 10, 'cameras': cams}))


class InputContractTests(unittest.TestCase):
    def test_explicit_masks_never_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); gray = np.full((12, 16), 100, np.uint8)
            with self.assertRaisesRegex(ValueError, 'missing explicit mask'):
                segment(gray, str(root / 'missing.png'))
            cv2.imwrite(str(root / 'bad.png'), np.zeros((4, 5), np.uint8))
            with self.assertRaisesRegex(ValueError, 'size mismatch'):
                segment(gray, str(root / 'bad.png'))
            (root / 'broken.png').write_bytes(b'not an image')
            with self.assertRaisesRegex(ValueError, 'unreadable explicit mask'):
                segment(gray, str(root / 'broken.png'))

    def test_true_ids_dynamic_cameras_and_identity_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle(root)
            source = InputSequence(root)
            first, second = source.read(), source.read_frame(44, 4.4)
            self.assertEqual((first.frame, first.t), (42, 4.2))
            self.assertAlmostEqual(second.cameras[0].t[0] - first.cameras[0].t[0], 0.23)
            self.assertEqual(first.rgbs[0].shape, (12, 16, 3))
            self.assertTrue(np.all(first.masks[0] == 0))
            self.assertIsNone(source.read())
            source = InputSequence(root)
            with self.assertRaisesRegex(ValueError, 'timestamp mismatch'):
                source.read_frame(42, 0.0)

    def test_invalid_frame_clock_and_camera_slots_rejected(self):
        for mode in ('duplicate_id', 'float_id', 'reversed_time', 'camera_slot'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); meta = bundle(root)
                if mode == 'duplicate_id':
                    meta['frames'][1]['frame'] = 42
                elif mode == 'float_id':
                    meta['frames'][0]['frame'] = 42.5
                elif mode == 'reversed_time':
                    meta['frames'][1]['t'] = 3.0
                else:
                    meta['frames'][1]['cameras'][0]['name'] = 'B'
                (root / 'input_bundle.json').write_text(json.dumps(meta))
                with self.assertRaises(ValueError):
                    InputSequence(root)

    def test_model_extrinsics_cannot_be_silently_static(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); legacy(root, {'A': [0]})
            meta = json.loads((root / 'cameras.json').read_text())
            camera = meta['cameras'][0]
            camera['T_cv_from_model'] = camera.pop('T_cv_from_world')
            (root / 'cameras.json').write_text(json.dumps(meta))
            with self.assertRaisesRegex(ValueError, 'per-frame records'):
                InputSequence(root, segmenter=segment)

    def test_bounded_selection_does_not_read_later_bad_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); meta = bundle(root)
            (root / meta['frames'][1]['cameras'][0]['rgb']).write_bytes(b'broken')
            source = InputSequence(root, max_frames=1)
            self.assertEqual(source.count, 1)
            self.assertEqual(source.read().frame, 42)
            paths = [r['path'] for r in source.access_log]
            self.assertNotIn('A/000044.png', paths)
            with self.assertRaisesRegex(ValueError, 'unreadable image'):
                InputSequence(root)

    def test_bounded_resolved_truth_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); meta = bundle(root)
            (root / 'evaluation').mkdir()
            (root / 'A/000042.png').rename(root / 'evaluation/truth.png')
            (root / 'A/000042.png').symlink_to(root / 'evaluation/truth.png')
            with self.assertRaisesRegex(ValueError, 'forbidden solver input'):
                InputSequence(root, max_frames=1)

    def test_missing_bad_and_wrong_size_bundle_inputs(self):
        for mode in ('missing_mask', 'wrong_mask', 'bad_rgb'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); meta = bundle(root)
                camera = meta['frames'][0]['cameras'][0]
                if mode == 'missing_mask':
                    (root / camera['mask']).unlink()
                    error = FileNotFoundError
                elif mode == 'wrong_mask':
                    cv2.imwrite(str(root / camera['mask']), np.zeros((3, 4), np.uint8))
                    error = ValueError
                else:
                    (root / camera['rgb']).write_bytes(b'bad png')
                    error = ValueError
                with self.assertRaises(error):
                    InputSequence(root)

    def test_cross_camera_source_frames_and_bad_frames_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); legacy(root, {'A': [1, 2], 'B': [1, 3]})
            with self.assertRaisesRegex(ValueError, 'cross-camera frame identities'):
                InputSequence(root, segmenter=segment)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); legacy(root, {'A': [1, 2]})
            (root / 'A/000002.png').write_bytes(b'bad frame')
            source = InputSequence(root, segmenter=segment)
            self.assertEqual(source.read().frame, 1)
            with self.assertRaisesRegex(ValueError, 'unreadable image'):
                source.read()

    def test_cross_camera_video_clock_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); legacy(root, {'A': [0], 'B': [0]})
            for name, fps in [('A', 10), ('B', 20)]:
                writer = cv2.VideoWriter(str(root / (name + '.mp4')), cv2.VideoWriter_fourcc(*'mp4v'), fps, (16, 12))
                self.assertTrue(writer.isOpened())
                writer.write(np.full((12, 16, 3), 100, np.uint8)); writer.release()
            with self.assertRaisesRegex(ValueError, 'timestamps differ'):
                InputSequence(root, segmenter=segment)

    def test_video_frame_presentation_time_is_checked(self):
        class OffsetCapture:
            def read(self):
                return True, np.zeros((12, 16, 3), np.uint8)
            def get(self, prop):
                return 120.0  # frame 1 at declared 10 Hz must be t=0.1
        source = RGBSource.__new__(RGBSource)
        source.k, source.count, source.cap = 1, 2, OffsetCapture()
        source.name, source.fps = 'A', 10.0
        with self.assertRaisesRegex(ValueError, 'presentation timestamp differs'):
            source.read()

    def test_explicit_legacy_masks_use_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); legacy(root, {'A': [42, 44]})
            maskroot = root / 'masks'; (maskroot / 'A').mkdir(parents=True)
            for frame in (42, 44):
                cv2.imwrite(str(maskroot / 'A' / ('%06d.png' % frame)), np.zeros((12, 16), np.uint8))
            source = InputSequence(root, masks_dir=maskroot)
            self.assertEqual((source.read().frame, source.read().t), (42, 4.4))
            (maskroot / 'A/000042.png').unlink()
            with self.assertRaisesRegex(ValueError, 'missing or unreadable explicit mask'):
                InputSequence(root, masks_dir=maskroot).read()

    def test_cli_zero_observations_stays_explicit_and_compatible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle(root)
            output = root / 'out'
            run = subprocess.run([sys.executable, str(ALG / 'recon.py'), '--data', str(root), '--out', str(output)],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            payload = (output / 'recon.json').read_text()
            self.assertNotIn('NaN', payload); self.assertNotIn('Infinity', payload)
            result = json.loads(payload); a, b = result['frames']
            self.assertEqual((a['frame'], a['t'], b['frame'], b['t']), (42, 4.2, 44, 4.4))
            self.assertEqual(a['reconstruction_status'], 'no_observations')
            self.assertEqual(b['reconstruction_status'], 'prior_prediction')
            self.assertFalse(a['reconstruction_valid'])
            self.assertEqual(a['observation_rank'], 0)
            self.assertIsNone(a['observation_std'][0])
            self.assertIsNone(a['posterior_std'][0])
            self.assertNotIn('std', a)
            self.assertTrue(a['solver']['success'])
            self.assertEqual(a['solver']['status'], 1)
            self.assertEqual(a['observed_sections'], [[0]*8]*3)
            self.assertAlmostEqual(b['cameras'][0]['T_cv_from_model'][0][3], 0.23)
            self.assertTrue(np.isfinite(a['state']).all())


class NumericalReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        meta = json.loads((ALG / 'data/synth20/cameras.json').read_text())
        cls.cams = [Camera.from_dict(d) for d in meta['cameras']]
        cls.rotor = Rotor(TurbineConfig.from_dict(meta['turbine']))
        cls.fitter = Fitter(cls.rotor, cls.cams)
        cls.p = np.array([17.3] + [0.0] * 3 + [3.0] * 3 + [0.0] * 3)
        cls.obs = []
        for cam in cls.cams:
            src = FrameSource(ALG / 'data/synth20', cam.name)
            cls.obs.append(Obs(segment(src.read())))
            src.close()

    def test_empty_directions_are_unbounded_despite_optimizer_success(self):
        empty = [Obs(np.zeros((c.H, c.W), np.uint8)) for c in self.cams]
        x, std, result = self.fitter.solve(empty, self.p, self.p, w_prior=0, max_nfev=2)
        self.assertTrue(result.success)
        self.assertFalse(result.reconstruction_valid)
        self.assertEqual(result.reconstruction_status, 'no_observations')
        self.assertEqual(result.observation_rank, 0)
        self.assertTrue(np.isnan(std).all())
        self.assertTrue(np.isnan(result.posterior_std[0]))
        np.testing.assert_allclose(x, self.p)

    def test_unfolded_angle_jacobian_is_invariant(self):
        base_residual, base_jac = None, None
        for offset in (0, 3600, 36000):
            p = self.p.copy(); p[0] += offset
            residual = self.fitter.residuals(p, self.obs, p, 0)
            x, std, result = self.fitter.solve(self.obs, p, p, w_prior=0, max_nfev=1)
            if base_jac is None:
                base_residual, base_jac = residual, result.jac[:, 0]
            np.testing.assert_allclose(residual, base_residual, atol=1e-7, rtol=1e-9)
            delta = np.linalg.norm(result.jac[:, 0] - base_jac) / np.linalg.norm(base_jac)
            self.assertLess(delta, 1e-5)
            self.assertEqual(x[0], p[0])  # continuous state is never reduced modulo 360

    def test_underdetermined_mixed_image_direction_is_unbounded(self):
        class OneImageRow(Fitter):
            def residuals(self, p, obs, x_pred, w_prior):
                # One image observation measures a sum of all ten unknowns.
                return np.r_[[np.sum(p - x_pred)], np.zeros(20)]
        fit = OneImageRow(self.rotor, self.cams)
        x, std, result = fit.solve(self.obs, self.p, self.p, max_nfev=1)
        self.assertEqual(result.observation_rank, 1)
        self.assertTrue(result.reconstruction_valid)
        self.assertTrue(np.isnan(std).all())
        self.assertTrue(np.isnan(result.posterior_std).all())
        self.assertFalse(result.identifiable.any())
        self.assertEqual(result.reconstruction_status, 'prior_assisted')

    def test_inactive_prior_direction_is_not_image_identified(self):
        active = np.ones(10, bool); active[4] = False
        x, std, result = self.fitter.solve(self.obs, self.p, self.p, max_nfev=1, active=active)
        self.assertTrue(np.isnan(std[4]))
        self.assertFalse(result.identifiable[4])
        self.assertEqual(result.reconstruction_status, 'prior_assisted')


if __name__ == '__main__':
    unittest.main()
