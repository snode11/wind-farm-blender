"""Estimated-surface visibility fixtures; no independent/evaluation data read."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np

ALG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ALG))
from camera import Camera
from texture_geometry import (FrameFeatures, TextureGeometry, VISIBILITY_PROTOCOL,
                              binding_visibility, ray_binding)


class MaterialVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.cam = Camera('fixture', 100, 100,
                          [[40., 0., 49.5], [0., 40., 49.5], [0., 0., 1.]], np.eye(4))
        cube = np.array([[-1., -1., 5.], [1., -1., 5.], [1., 1., 5.], [-1., 1., 5.],
                         [-1., -1., 7.], [1., -1., 7.], [1., 1., 7.], [-1., 1., 7.]])
        self.vertices = np.stack([cube, cube + [10., 0., 0.], cube + [-10., 0., 0.]])
        # A closed estimated surface with outward winding for flat fixtures.
        self.faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                               [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                               [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
        self.binding = {'blade_0based': 0, 'face': 0, 'barycentric': [.2, .4, .4]}

    def point(self, binding):
        tri = self.vertices[binding['blade_0based']][self.faces[binding['face']]]
        return np.asarray(binding['barycentric']) @ tri

    def features(self, binding=None, weight=1., camera=None, visibility=VISIBILITY_PROTOCOL):
        binding = copy.deepcopy(self.binding if binding is None else binding)
        uv = self.cam.project(self.point(binding)[None])[0][0]
        entry = {'id': 'fixed', 'camera_index': 0, 'binding': binding, 'uv': uv,
                 'quality': {'localization_sigma_px': .5}}
        return FrameFeatures([entry], [{'reason': 'USED'}], [self.cam],
                             [self.cam if camera is None else camera], self.faces,
                             weight, visibility=visibility)

    def manager(self, directory, camera=None, weight=1.):
        camera = self.cam if camera is None else camera
        uv = camera.project(self.point(dict(self.binding, face=2))[None])[0][0]
        quality = {'localization_sigma_px': .5, 'forward_backward_error_px': .1,
                   'ncc': .95, 'contrast_gray': 30., 'identity_margin': 3.}
        items = [{'frame': i, 't': float(i), 'camera': camera.name,
                  'uv': uv.tolist(), 'rgb_sha256': str(i) * 64, 'quality': quality,
                  'accept': True} for i in (0, 1)]
        payload = {'schema': 'blade-texture-geometry.v1',
                   'pixel_coordinates': 'original_image_pixel_centers',
                   'binding_visibility': VISIBILITY_PROTOCOL,
                   'provenance': {'role': 'solver_rgb_observations', 'method': 'unit fixture',
                                  'allowed_inputs': ['declared_rgb']},
                   'tracks': [{'id': 'fixed', 'anchor': items[0], 'observations': items}]}
        path = Path(directory) / 'observations.json'; path.write_text(json.dumps(payload))
        manifest = {'frames': [{'frame': i, 't': float(i),
                               'cameras': [{'name': camera.name, 'rgb': f'{i}.png'}]} for i in (0, 1)]}
        inputs = SimpleNamespace(bundle=SimpleNamespace(manifest=manifest, frame_ids=[0, 1]),
            access_log=[{'role': 'RGB', 'path': f'{i}.png', 'sha256': str(i) * 64} for i in (0, 1)])
        rotor = SimpleNamespace(tpl=SimpleNamespace(faces=lambda: self.faces),
                                forward=lambda state: (self.vertices, None))
        records = [SimpleNamespace(frame=i, t=float(i), cameras=[camera],
            masks=[np.full((camera.H, camera.W), 255, np.uint8)], known_contours=None) for i in (0, 1)]
        return TextureGeometry(path, inputs, rotor, weight), records

    def test_frontmost_binding_is_visible_and_has_quantitative_audit(self):
        result = binding_visibility(self.cam, self.vertices, self.faces, self.binding)
        self.assertTrue(result['visible'])
        self.assertEqual(result['reason'], 'VISIBLE_FIXED_FACE')
        self.assertEqual(result['nearest_blade_0based'], 0)
        self.assertGreater(result['front_facing_cosine'], 0)
        self.assertLess(abs(result['distance_gap']), result['tolerance_m'])

    def test_positive_depth_self_occluded_point_keeps_pixel_error_and_bad_residual(self):
        binding = dict(self.binding, face=2)
        result = binding_visibility(self.cam, self.vertices, self.faces, binding)
        self.assertGreater(result['depth'], .05)
        self.assertFalse(result['visible'])
        self.assertEqual(result['reason'], 'SELF_OCCLUDED')
        feature = self.features(binding)
        np.testing.assert_array_equal(feature.residuals(self.vertices), [60., 60.])
        summary = feature.summary(self.vertices)
        self.assertEqual(summary['original_pixel_error_count'], 1)
        self.assertEqual(summary['original_pixel_error_rms'], 0.)
        self.assertEqual(summary['visible_observations'], 0)
        self.assertEqual(summary['occluded_or_invalid_observations'], 1)
        self.assertEqual(summary['visibility_counts'], {'SELF_OCCLUDED': 1})
        self.assertGreater(summary['weighted_robust_cost'], 0.)

    def test_other_blade_in_front_never_rebinds_material_point(self):
        vertices = self.vertices.copy()
        vertices[1] = vertices[0] - [0., 0., 2.]
        frozen = copy.deepcopy(self.binding)
        result = binding_visibility(self.cam, vertices, self.faces, self.binding)
        self.assertEqual(result['reason'], 'OCCLUDED_BY_OTHER_BLADE')
        self.assertFalse(result['visible'])
        self.assertEqual(result['nearest_blade_0based'], 1)
        self.assertEqual(self.binding, frozen)
        uv = self.cam.project(self.point(self.binding)[None])[0][0]
        current_pixel_binding = ray_binding(self.cam, vertices, self.faces, uv)
        self.assertEqual(current_pixel_binding['blade_0based'], 1)
        self.assertEqual(self.binding, frozen)

    def test_coincident_other_blade_is_ambiguous_even_if_bound_blade_is_first(self):
        vertices = self.vertices.copy(); vertices[1] = vertices[0]
        result = binding_visibility(self.cam, vertices, self.faces, self.binding)
        self.assertFalse(result['visible'])
        self.assertEqual(result['reason'], 'AMBIGUOUS_OTHER_BLADE_SURFACE')

    def test_front_shared_edges_and_vertices_survive_triangle_ties(self):
        for bary in ([.5, .5, 0.], [1., 0., 0.], [0., 1., 0.]):
            with self.subTest(bary=bary):
                binding = dict(self.binding, barycentric=bary)
                result = binding_visibility(self.cam, self.vertices, self.faces, binding)
                self.assertTrue(result['visible'], result)
                self.assertLess(abs(result['distance_gap']), result['tolerance_m'])

    def test_back_facing_surface_is_not_accepted_through_open_interior(self):
        transform = np.eye(4); transform[2, 3] = -6.
        inside = Camera('inside', self.cam.W, self.cam.H, self.cam.K, transform)
        result = binding_visibility(inside, self.vertices, self.faces, dict(self.binding, face=2))
        self.assertGreater(result['depth'], .05)
        self.assertEqual(result['reason'], 'BACK_FACING_BOUND_SURFACE')
        self.assertFalse(result['visible'])

    def test_explicit_protocol_rejects_back_facing_anchor_and_keeps_decision(self):
        transform = np.eye(4); transform[2, 3] = -6.
        inside = Camera('inside', self.cam.W, self.cam.H, self.cam.K, transform)
        with tempfile.TemporaryDirectory() as tmp:
            manager, records = self.manager(tmp, camera=inside)
            manager.bind_anchors(records[0], [])
            self.assertEqual(manager.bindings, {})
            self.assertEqual(manager.binding_decisions[0]['reason'],
                             'ANCHOR_ESTIMATED_SURFACE:BACK_FACING_BOUND_SURFACE')
            self.assertIsNone(manager.binding_decisions[0]['binding'])
            self.assertEqual(manager.audit()['binding_visibility_protocol'], VISIBILITY_PROTOCOL)
            self.assertEqual(manager.prepare(records[1], [inside]).count, 0)
            with self.assertRaisesRegex(ValueError, 'cannot be reset'):
                manager.bind_anchors(records[0], [])

    def test_explicit_protocol_zero_weight_retains_empty_contour_ablation(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, records = self.manager(tmp, weight=0.)
            manager.bind_anchors(records[0], [])
            feature = manager.prepare(records[1], [self.cam])
            self.assertEqual(len(manager.bindings), 1)
            self.assertEqual(feature.count, 0)
            self.assertEqual(feature.residuals(self.vertices).size, 0)
            self.assertEqual(feature.decisions[0]['reason'], 'WEIGHT_ZERO_ABLATION')

    def test_structured_loft_orients_face_normals_from_section_centers(self):
        # The loft topology may be saved with either winding; section-center
        # orientation is deterministic and keeps its outer face visible.
        structured = self.vertices.reshape(3, 2, 4, 3)
        for faces in (self.faces, self.faces[:, ::-1]):
            binding = ray_binding(self.cam, structured, faces, np.array([53., 49.5]))
            self.assertIsNotNone(binding)
            result = binding_visibility(self.cam, structured, faces, binding)
            self.assertTrue(result['visible'], result)

    def test_rounded_camera_uses_exact_declared_transform(self):
        transform = np.eye(4)
        transform[:3, :3] = np.diag([1. + 5e-8, 1. - 5e-8, 1. + 8e-8])
        transform[:3, 3] = [.4, -.2, .1]
        camera = Camera('rounded', self.cam.W, self.cam.H, self.cam.K, transform)
        uv = camera.project(self.point(self.binding)[None])[0][0]
        binding = ray_binding(camera, self.vertices, self.faces, uv)
        result = binding_visibility(camera, self.vertices, self.faces, binding)
        self.assertTrue(result['visible'], result)
        np.testing.assert_allclose(result['predicted_uv'], uv, atol=1e-10)
        self.assertLess(abs(result['distance_gap']), result['tolerance_m'])

    def test_residual_rows_are_fixed_across_occlusion_and_image_scaling(self):
        feature, scaled = self.features(), self.features(camera=self.cam.scaled(.5))
        hidden = self.vertices.copy(); hidden[1] = hidden[0] - [0., 0., 2.]
        self.assertEqual(feature.residuals(self.vertices).shape, (2,))
        self.assertEqual(feature.residuals(hidden).shape, (2,))
        np.testing.assert_array_equal(feature.residuals(self.vertices), [0., 0.])
        np.testing.assert_array_equal(feature.residuals(hidden), [60., 60.])
        np.testing.assert_array_equal(feature.residuals(hidden), scaled.residuals(hidden))

    def test_zero_weight_is_zero_for_visible_and_hidden_fixed_rows(self):
        feature = self.features(weight=0.)
        hidden = self.vertices.copy(); hidden[1] = hidden[0] - [0., 0., 2.]
        np.testing.assert_array_equal(feature.residuals(self.vertices), [0., 0.])
        np.testing.assert_array_equal(feature.residuals(hidden), [0., 0.])
        self.assertEqual(feature.residuals(hidden).shape, (2,))

    def test_legacy_default_retains_previous_positive_depth_objective(self):
        binding = dict(self.binding, face=2)
        feature = self.features(binding, visibility='positive_depth_only')
        np.testing.assert_array_equal(feature.residuals(self.vertices), [0., 0.])
        self.assertEqual(feature.summary(self.vertices)['binding_visibility_protocol'], 'positive_depth_only')


if __name__ == '__main__':
    unittest.main()
