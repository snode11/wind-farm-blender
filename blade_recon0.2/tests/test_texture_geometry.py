"""Mathematical/provenance regression for the optional RGB feature objective.

Ideal point fixtures test the optimizer contract, not the real RGB observation
gate. Real image/held-out accuracy is scored by separate experiment outputs.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

ALG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ALG))
from camera import Camera, build_rig
from input_adapter import FrameRecord
from model import Rotor, TurbineConfig, STATE_NAMES
from raster import rasterize
from recon import Fitter, Obs
from texture_geometry import (TextureGeometry, FrameFeatures, ray_binding,
                              quality_reason, image_information, support_reason)


QUALITY = {'localization_sigma_px': .5, 'forward_backward_error_px': .1,
           'ncc': .95, 'contrast_gray': 30., 'identity_margin': 3.}


class TextureGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rotor = Rotor(TurbineConfig(n_sections=12, n_ring=16))
        cls.cam = build_rig(cls.rotor, back_m=150, down_m=0, yaw_step_deg=10,
                           hfov_deg=60, vfov_deg=60, W=384, H=384)[1]
        cls.p = np.array([80.] + [0.] * 3 + [3.] * 3 + [0.] * 6)
        cls.p1 = cls.p.copy(); cls.p1[0] += .7
        cls.faces = cls.rotor.tpl.faces()
        cls.V = cls.rotor.forward(cls.p)[0]
        ras = rasterize(cls.cam, cls.V, cls.faces)
        # Spread across each blade, with original pixel-center rays.
        points = []
        for b in range(3):
            selected = np.flatnonzero(ras.blade == b)
            for i in selected[np.linspace(0, len(selected) - 1, 6).astype(int)]:
                points.append(np.array([ras.xs[i], ras.ys[i]], float))
        cls.points = points

    def fixture(self, directory, weight=1.):
        cams = [self.cam]
        masks = [np.full((self.cam.H, self.cam.W), 255, np.uint8)]
        images = [np.full((self.cam.H, self.cam.W, 3), 100, np.uint8)]
        records = [FrameRecord(f, t, cams, images, masks, [i], [np.ones(masks[0].shape, bool)])
                   for i, (f, t) in enumerate(((42, 4.2), (47, 4.7)))]
        manifest = {'frames': [{'frame': r.frame, 't': r.t,
                               'cameras': [{'name': self.cam.name, 'rgb': 'A/%06d.png' % r.frame}]}
                              for r in records]}
        hashes = [hashlib.sha256(bytes([i])).hexdigest() for i in range(2)]
        inputs = SimpleNamespace(bundle=SimpleNamespace(manifest=manifest, frame_ids=[42, 47]),
            access_log=[{'role': 'RGB', 'path': 'A/%06d.png' % r.frame, 'sha256': h}
                        for r, h in zip(records, hashes)])
        tracks = []
        target_vertices = self.rotor.forward(self.p1)[0]
        for i, uv in enumerate(self.points):
            binding = ray_binding(self.cam, self.V, self.faces, uv)
            triangle = target_vertices[binding['blade_0based']].reshape(-1, 3)[self.faces[binding['face']]]
            target = self.cam.project((np.asarray(binding['barycentric']) @ triangle)[None])[0][0]
            anchor = {'frame': 42, 't': 4.2, 'camera': self.cam.name,
                      'uv': uv.tolist(), 'rgb_sha256': hashes[0]}
            a = dict(anchor, quality=QUALITY.copy(), status='IDEAL_POINT_UNIT_FIXTURE', accept=True)
            b = {'frame': 47, 't': 4.7, 'camera': self.cam.name, 'uv': target.tolist(),
                 'rgb_sha256': hashes[1], 'quality': QUALITY.copy(), 'accept': True,
                 'status': 'IDEAL_POINT_UNIT_FIXTURE'}
            tracks.append({'id': 'point_%d' % i, 'anchor': anchor, 'observations': [a, b]})
        payload = {'schema': 'blade-texture-geometry.v1',
                   'pixel_coordinates': 'original_image_pixel_centers',
                   'provenance': {'role': 'solver_rgb_observations', 'method': 'mathematical unit fixture',
                                  'allowed_inputs': ['declared_rgb']}, 'tracks': tracks}
        path = Path(directory) / 'tracks.json'; path.write_text(json.dumps(payload))
        return inputs, records, payload, path

    def test_subpixel_ray_uses_estimate_and_replays_exact_triangle(self):
        uv = self.points[5] + [.123, .067]
        binding = ray_binding(self.cam, self.V, self.faces, uv)
        self.assertIsNotNone(binding)
        tri = self.V[binding['blade_0based']].reshape(-1, 3)[self.faces[binding['face']]]
        projected = self.cam.project((np.asarray(binding['barycentric']) @ tri)[None])[0][0]
        np.testing.assert_allclose(projected, uv, atol=1e-9)
        self.assertGreaterEqual(min(binding['barycentric']), -1e-10)

    def test_declared_camera_rounding_uses_exact_inverse_not_transpose(self):
        # A legal Blender-like float32 drift: camera input accepts R R.T
        # within 1e-6, while project() always uses the declared (rounded) R.
        # The previous ray's R.T inverse made its <1e-5 px loop gate fail.
        transform = np.eye(4)
        transform[:3, :3] = self.cam.R @ np.diag([1. + 5e-8, 1. - 5e-8, 1. + 8e-8])
        transform[:3, 3] = self.cam.t
        camera = Camera(self.cam.name, self.cam.W, self.cam.H, self.cam.K, transform)
        self.assertLess(np.max(abs(camera.R @ camera.R.T - np.eye(3))), 1e-6)
        uv = self.points[5] + [.123, .067]
        binding = ray_binding(camera, self.V, self.faces, uv)
        self.assertIsNotNone(binding)
        tri = self.V[binding['blade_0based']].reshape(-1, 3)[self.faces[binding['face']]]
        point = np.asarray(binding['barycentric']) @ tri
        projected = camera.project(point[None])[0][0]
        np.testing.assert_allclose(projected, uv, atol=1e-9)
        # This is the original erroneous transposed inverse of that same
        # declared camera ray, not a source geometry or a relaxed pixel gate.
        approximate_center = -camera.R.T @ camera.t
        approximate_direction = camera.R.T @ np.linalg.solve(camera.K, np.r_[uv, 1.])
        exact_center = np.linalg.solve(camera.R, -camera.t)
        exact_direction = np.linalg.solve(camera.R, np.linalg.solve(camera.K, np.r_[uv, 1.]))
        along_ray = np.dot(point-exact_center, exact_direction)/np.dot(exact_direction, exact_direction)
        approximate_point = approximate_center + along_ray*approximate_direction
        old_loop_error = np.linalg.norm(camera.project(approximate_point[None])[0][0]-uv)
        self.assertGreater(old_loop_error, 1e-5)

    def test_binding_is_once_and_anchor_self_observation_is_not_information(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            manager = TextureGeometry(path, inputs, self.rotor)
            first = manager.prepare(records[0], [self.cam])
            self.assertEqual(first.count, 0)
            self.assertEqual(len(first.decisions), len(self.points))
            self.assertTrue(all(d['reason'] == 'ANCHOR_SELF_OBSERVATION' for d in first.decisions))
            manager.bind_anchors(records[0], self.p)
            frozen = copy.deepcopy(manager.bindings)
            second = manager.prepare(records[1], [self.cam])
            self.assertEqual(second.count, len(self.points))
            np.testing.assert_allclose(second.residuals(self.rotor.forward(self.p1)[0]), 0., atol=1e-9)
            self.assertEqual(manager.bindings, frozen)
            with self.assertRaisesRegex(ValueError, 'cannot be reset'):
                manager.bind_anchors(records[0], self.p1)

    def test_quality_flags_cannot_force_acceptance_and_missing_is_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            payload['tracks'][0]['observations'][1]['quality']['forward_backward_error_px'] = 1.01
            payload['tracks'][1]['observations'][1]['uv'] = None
            payload['tracks'][2]['observations'][1]['quality']['localization_sigma_px'] = None
            path.write_text(json.dumps(payload))
            manager = TextureGeometry(path, inputs, self.rotor)
            manager.bind_anchors(records[0], self.p)
            feature = manager.prepare(records[1], [self.cam])
            result = feature.summary(self.rotor.forward(self.p1)[0])
            self.assertEqual(result['planned_observations'], len(self.points))
            self.assertEqual(result['used_observations'], len(self.points) - 3)
            self.assertEqual(result['missing_or_rejected_observations'], 3)
            self.assertIn('FAILED_QUALITY:forward_backward_error_px', result['decision_counts'])
            self.assertIn('RGB_NOT_OBSERVED', result['decision_counts'])
            self.assertEqual(manager.audit()['planned_observations'], 2 * len(self.points))

    def test_unknown_support_is_rejected_without_filling(self):
        mask = np.full((20, 20), 255, np.uint8)
        known = np.ones((20, 20), bool); known[7, 8] = False
        self.assertEqual(support_reason([7.9, 7.1], mask, known), 'UNKNOWN_CONTOUR_DOMAIN')
        np.testing.assert_array_equal(mask, np.full((20, 20), 255, np.uint8))

    def test_unavailable_anchor_geometry_never_binds_from_prior_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            manager = TextureGeometry(path, inputs, self.rotor)
            manager.bind_anchors(records[0], self.p, geometry_valid=False)
            self.assertEqual(len(manager.bindings), 0)
            self.assertEqual(manager.prepare(records[1], [self.cam]).count, 0)
            self.assertEqual(len(manager.binding_decisions), len(self.points))

    def test_provenance_and_undeclared_answers_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            for mutation, regex in (
                    (lambda d: d['tracks'][0]['observations'][1].update(frame=48), 'undeclared fit'),
                    (lambda d: d['tracks'][0]['anchor'].update(rgb_sha256='0' * 64), 'RGB identity'),
                    (lambda d: d['tracks'][0]['anchor'].update(t=4.21), 'timestamp identity'),
                    (lambda d: d.update(source_state=self.p.tolist()), 'forbidden answer'),
                    (lambda d: d['provenance'].update(allowed_inputs=[]), 'provenance'),
                    (lambda d: d['provenance'].update(allowed_inputs=['source_texture']), 'provenance')):
                broken = copy.deepcopy(payload); mutation(broken); path.write_text(json.dumps(broken))
                with self.subTest(regex=regex), self.assertRaisesRegex(ValueError, regex):
                    TextureGeometry(path, inputs, self.rotor)

    def test_fit_scale_transforms_pixel_sigma_with_delta(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            manager = TextureGeometry(path, inputs, self.rotor)
            manager.bind_anchors(records[0], self.p)
            full = manager.prepare(records[1], [self.cam])
            scaled = manager.prepare(records[1], [self.cam.scaled(.5)])
            test_vertices = self.rotor.forward(self.p)[0]
            np.testing.assert_allclose(full.residuals(test_vertices), scaled.residuals(test_vertices), atol=1e-10)
            self.assertEqual(full.summary(test_vertices)['original_pixel_error_p95'],
                             scaled.summary(test_vertices)['original_pixel_error_p95'])

    def test_zero_weight_is_bit_exact_contour_ablation(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            manager = TextureGeometry(path, inputs, self.rotor, weight=0.)
            manager.bind_anchors(records[0], self.p)
            feature = manager.prepare(records[1], [self.cam])
            self.assertEqual(feature.count, 0)
            empty = [Obs(np.zeros((self.cam.H, self.cam.W), np.uint8))]
            base, candidate = Fitter(self.rotor, [self.cam]), Fitter(self.rotor, [self.cam])
            candidate.texture_features = feature
            np.testing.assert_array_equal(base.residuals(self.p, empty, self.p, .5),
                                          candidate.residuals(self.p, empty, self.p, .5))
            x0, _, r0 = base.solve(empty, self.p, self.p, max_nfev=2)
            x1, _, r1 = candidate.solve(empty, self.p, self.p, max_nfev=2)
            np.testing.assert_array_equal(x0, x1)
            self.assertEqual(r0.cost, r1.cost)
            self.assertEqual(r1.image_information['texture_only']['rank'], 0)

    def test_optional_features_constrain_psi_without_claiming_all_directions(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs, records, payload, path = self.fixture(tmp)
            manager = TextureGeometry(path, inputs, self.rotor)
            manager.bind_anchors(records[0], self.p)
            fitter = Fitter(self.rotor, [self.cam]); fitter.texture_features = manager.prepare(records[1], [self.cam])
            active = np.zeros(13, bool); active[0] = True
            empty = [Obs(np.zeros((self.cam.H, self.cam.W), np.uint8))]
            fitted, std, result = fitter.solve(empty, self.p, self.p, w_prior=0., active=active, max_nfev=30)
            self.assertTrue(result.success)
            self.assertAlmostEqual(fitted[0], self.p1[0], places=7)
            self.assertEqual(result.image_information['contour_only']['rank'], 0)
            self.assertEqual(result.image_information['texture_only']['rank'], 1)
            self.assertEqual(result.image_information['combined']['rank'], 1)
            self.assertTrue(np.isfinite(std[0])); self.assertTrue(np.isnan(std[1:]).all())
            self.assertLess(result.texture_geometry['original_pixel_error_p95'], 1e-7)
            self.assertGreater(result.texture_geometry['before_solve']['original_pixel_error_p95'], .1)
            self.assertEqual(result.texture_geometry['acceptance'], 'NOT_EVALUATED')

    def test_image_nullspace_is_not_completed_by_prior_or_row_count(self):
        J = np.zeros((2, 13)); J[0, :2] = 1.; J[1, :2] = 2.
        result = image_information(J, np.ones(13), STATE_NAMES)
        self.assertEqual(result['rank'], 1)
        self.assertFalse(any(result['identified'].values()))
        self.assertAlmostEqual(result['nullspace_energy']['psi'], .5)


if __name__ == '__main__':
    unittest.main()
