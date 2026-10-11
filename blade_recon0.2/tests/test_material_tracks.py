import copy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera import Camera
from model import TurbineConfig, STATE_NAMES
from material_tracks import (local_homography, transform_pixel, _footprint_known,
                             match_rgb, track_fixed_anchors, validate_seed,
                             material_gray, measured_instance_support, _footprint_diagnostic,
                             _image_visibility_diagnostic)
from surface_tracks import TrackConfig, _gray, track_sequence
from texture_geometry import _forbidden_keys


def image(shift=(0, 0), duplicate=False):
    rgb = np.full((160, 220, 3), 160, np.uint8)
    x, y = 90+shift[0], 70+shift[1]
    cv2.rectangle(rgb, (x, y), (x+22, y+28), (45, 45, 45), -1)
    cv2.rectangle(rgb, (x+22, y+10), (x+30, y+19), (45, 45, 45), -1)
    if duplicate:
        cv2.rectangle(rgb, (x+55, y), (x+77, y+28), (45, 45, 45), -1)
        cv2.rectangle(rgb, (x+77, y+10), (x+85, y+19), (45, 45, 45), -1)
    return rgb


def camera(name='A', tx=0.):
    T = np.eye(4); T[0, 3] = tx
    return Camera(name, 220, 160, [[100., 0., 110.], [0., 100., 80.], [0., 0., 1.]], T)


def row(frame, name, rgb):
    config = TrackConfig(affine_rgb_seed=False)
    return {'frame': frame, 't': float(frame), 'camera': name, 'cam': camera(name),
        'rgb': rgb, 'gray': material_gray(rgb, config), 'rgb_sha256': str(frame)*64,
        'known': np.ones(rgb.shape[:2], bool), 'occupancy': np.ones(rgb.shape[:2], bool),
        'estimated_blade': np.zeros(rgb.shape[:2], int)}


def anchor():
    tracks, _ = track_sequence([row(1, 'A', image())])
    return next(t for t in tracks if t['observations'][0]['accept'])


class MaterialTrackTests(unittest.TestCase):
    def test_local_projection_follows_same_triangle_under_perspective(self):
        vertices = np.tile(np.asarray([[[-2., -2., 5.], [2., -2., 5.], [-2., 2., 5.]]]), (3, 1, 1))
        target = vertices.copy(); target[:, :, 2] += [0., .8, -.3]
        target[:, :, 0] += .15
        binding = {'blade_0based': 0, 'face': 0, 'barycentric': [.2, .3, .5]}
        faces = np.asarray([[0, 1, 2]])
        H, trace = local_homography(camera(), camera('B', .2), vertices, target, faces, binding)
        p = camera().project((np.asarray(binding['barycentric']) @ vertices[0])[None])[0][0]
        expected = camera('B', .2).project((np.asarray(binding['barycentric']) @ target[0])[None])[0][0]
        self.assertLess(np.linalg.norm(transform_pixel(H, p)-expected), 1e-4)
        self.assertEqual(len(trace['fixed_plane_barycentric_weights']), 4)

    def test_known_foreground_does_not_imply_same_blade(self):
        a, b = row(1, 'A', image()), row(2, 'B', image())
        p = np.asarray(anchor()['anchor']['uv'])
        b['estimated_blade'][:] = 1
        uv, quality, reason, _ = match_rgb(a, b, p, np.eye(3), 0)
        self.assertIsNone(uv)
        self.assertEqual(reason, 'TARGET_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT')
        self.assertFalse(quality)

    def test_full_warp_footprint_rejects_unknown_pixel(self):
        support = np.ones((80, 100), bool); support[36, 47] = False
        self.assertFalse(_footprint_known(support, [50., 40.], 25))
        H = np.eye(3); H[0, 2] = -30.
        self.assertFalse(_footprint_known(np.ones_like(support), [20., 40.], 25, H))

    def test_shrinking_warp_checks_reference_sampling_beyond_original_patch(self):
        a, b = row(1, 'A', image()), row(2, 'B', image())
        p = np.asarray(anchor()['anchor']['uv'])
        yy, xx = np.mgrid[:160, :220]
        a['known'] = (np.abs(xx-p[0]) < 22.) & (np.abs(yy-p[1]) < 22.)
        H = np.eye(3); H[:2, :2] *= .5; H[:2, 2] = p*.5
        self.assertTrue(_footprint_known(a['known'], p, 31))
        uv, _, reason, _ = match_rgb(a, b, p, H, 0)
        self.assertIsNone(uv)
        self.assertEqual(reason, 'REFERENCE_ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT')

    def test_warp_then_subpixel_patch_checks_both_interpolations(self):
        support = np.ones((80, 100), bool); support[40, 66] = False
        H = np.eye(3); H[0, 0] = .5
        warped = cv2.warpPerspective(support.astype(np.float32), H, (100, 80))
        measured = cv2.getRectSubPix(warped, (25, 25), (20.25, 40.))
        self.assertAlmostEqual(float(measured.min()), .75)
        self.assertFalse(_footprint_known(support, [20.25, 40.], 25, np.linalg.inv(H)))

    def test_backward_returned_anchor_footprint_is_checked_with_valid_fb(self):
        a, b = row(1, 'A', image()), row(2, 'B', image())
        p = np.asarray([40., 40.]); a['known'][40, 60] = False
        forward = (np.asarray([[[39.6, 40.]]], np.float32), np.ones((1, 1), np.uint8), None)
        backward = (np.asarray([[[40.9, 40.]]], np.float32), np.ones((1, 1), np.uint8), None)
        self.assertTrue(_footprint_known(a['known'], p, 31))
        self.assertTrue(_footprint_known(a['known'], forward[0].reshape(2), 31))
        with patch('material_tracks.cv2.calcOpticalFlowPyrLK', side_effect=[forward, backward]), \
             patch('material_tracks._quality', return_value=({}, None)):
            _, q, reason, _ = match_rgb(a, b, p, np.eye(3), 0)
        self.assertLess(q['forward_backward_error_px'], 1.)
        self.assertEqual(reason, 'LK_RETURNED_ANCHOR_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT')

    def test_gray_pixel_support_requires_complete_raw_blur_halo(self):
        a = row(1, 'A', image()); a['known'][40, 43] = False
        support = measured_instance_support(a, 0)
        self.assertFalse(support[40, 40])
        self.assertTrue(support[40, 39])
        self.assertFalse(support[0, 40])

    def test_diagnostic_names_raw_mask_failure_at_exact_blur_halo_pixel(self):
        for domain, value, check in (
                ('known', False, 'RAW_KNOWN_FALSE'),
                ('occupancy', False, 'RAW_OCCUPANCY_FALSE'),
                ('estimated_blade', 1, 'RAW_ESTIMATED_BLADE_MISMATCH')):
            with self.subTest(domain=domain):
                a = row(1, 'A', image()); a[domain][40, 43] = value
                support = measured_instance_support(a, 0)
                diagnostic = _footprint_diagnostic(support, [40., 40.], 1, row=a, blade=0)
                self.assertEqual(diagnostic['passed'], _footprint_known(support, [40., 40.], 1))
                self.assertFalse(diagnostic['passed'])
                self.assertEqual(diagnostic['first_failed_check'], check)
                first = diagnostic['first_failed_sample']
                self.assertEqual(first['support_original_pixel_xy'], [40, 40])
                failure = first['raw_blur_halo']['raw_failed_pixels'][0]
                self.assertEqual(failure['raw_original_pixel_xy'], [43, 40])
                self.assertEqual(failure['blur_halo_offset_xy'], [3, 0])
                self.assertEqual(failure['failed_checks'], [check])

    def test_diagnostic_distinguishes_original_image_and_blur_halo_boundaries(self):
        a = row(1, 'A', image()); support = measured_instance_support(a, 0)
        direct = _footprint_diagnostic(support, [-.1, 40.], 1, row=a, blade=0)
        self.assertEqual(direct['first_failed_check'], 'SOURCE_INTERPOLATION_OUT_OF_IMAGE')
        self.assertEqual(direct['first_failed_sample']['mapped_source_uv'], [-.1, 40.])
        halo = _footprint_diagnostic(support, [0., 40.], 1, row=a, blade=0)
        self.assertEqual(halo['first_failed_check'], 'RAW_BLUR_HALO_OUT_OF_IMAGE')
        failure = halo['first_failed_sample']['raw_blur_halo']['raw_failed_pixels'][0]
        self.assertEqual(failure['raw_original_pixel_xy'], [-3, 37])
        self.assertFalse(_footprint_known(support, [0., 40.], 1))

    def test_diagnostic_retains_both_interpolations_for_shrinking_warp_counterexample(self):
        support = np.ones((80, 100), bool); support[40, 66] = False
        H = np.eye(3); H[0, 0] = .5
        diagnostic = _footprint_diagnostic(support, [20.25, 40.], 25, np.linalg.inv(H))
        self.assertFalse(diagnostic['passed'])
        self.assertEqual(diagnostic['passed'], _footprint_known(support, [20.25, 40.], 25, np.linalg.inv(H)))
        first = diagnostic['first_failed_sample']
        self.assertEqual(first['requested_uv'], [32.25, 40.])
        self.assertEqual(first['warp_integer_pixel_xy'], [33, 40])
        self.assertEqual(first['warp_grid_neighbor_xy'], ['ceil', 'floor'])
        self.assertEqual(first['mapped_source_uv'], [66., 40.])
        self.assertEqual(first['support_original_pixel_xy'], [66, 40])

    def test_diagnostic_boolean_matches_original_for_finite_warps_and_boundaries(self):
        rng = np.random.default_rng(8011)
        support = rng.random((41, 51)) > .08
        centers = ([0., 0.], [25., 20.], [25.25, 20.5], [50., 40.], [-.01, 20.])
        matrices = (None, np.eye(3), np.asarray([[.5, 0., 8.], [0., 1.2, -2.], [0., 0., 1.]]),
                    np.asarray([[1., .1, 0.], [0., 1., 0.], [.001, -.002, 1.]]), np.zeros((3, 3)))
        for center in centers:
            for matrix in matrices:
                with self.subTest(center=center, matrix=matrix):
                    diagnostic = _footprint_diagnostic(support, center, 7, matrix)
                    self.assertEqual(diagnostic['passed'], _footprint_known(support, center, 7, matrix))

    def test_match_trace_separates_gate_stage_from_estimated_blade_subcheck(self):
        a, b = row(1, 'A', image()), row(2, 'B', image())
        p = np.asarray(anchor()['anchor']['uv']); b['estimated_blade'][:] = 1
        _, _, reason, trace = match_rgb(a, b, p, np.eye(3), 0)
        self.assertEqual(reason, 'TARGET_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT')
        self.assertEqual(trace['first_failed_check'], 'TARGET_PREDICTED_DESCRIPTOR_SUPPORT')
        self.assertEqual(trace['first_failed_subcheck'], 'RAW_ESTIMATED_BLADE_MISMATCH')
        self.assertTrue(all(c['boolean_diagnostic_agree'] for c in trace['footprint_checks']))
        self.assertEqual([c['stage'] for c in trace['footprint_checks']],
                         ['ANCHOR_LK_START_SUPPORT', 'TARGET_PREDICTED_DESCRIPTOR_SUPPORT'])

    def test_image_bounds_are_independent_of_descriptor_support_and_ray_visibility(self):
        center = _image_visibility_diagnostic(np.asarray([5., 40.]), (80, 100), 25)
        self.assertTrue(center['prediction_finite'])
        self.assertTrue(center['predicted_center_in_image'])
        self.assertFalse(center['predicted_descriptor_fully_in_image'])
        self.assertEqual(center['predicted_descriptor_bbox_xyxy'], [-7., 28., 17., 52.])
        outside = _image_visibility_diagnostic(np.asarray([101., 40.]), (80, 100), 25)
        self.assertFalse(outside['predicted_center_in_image'])
        invalid = _image_visibility_diagnostic(None, (80, 100), 25)
        self.assertFalse(invalid['prediction_finite'])
        self.assertIsNone(invalid['predicted_descriptor_bbox_xyxy'])

    def test_bidirectional_rgb_localizes_prediction_residual(self):
        a, b = row(1, 'A', image()), row(2, 'B', image((3, 2)))
        p = np.asarray(anchor()['anchor']['uv'])
        H = np.eye(3); H[:2, 2] = [2.5, 1.7]
        uv, quality, reason, trace = match_rgb(a, b, p, H, 0)
        self.assertIsNone(reason)
        self.assertLess(np.linalg.norm(uv-p-[3., 2.]), .15)
        self.assertLess(quality['forward_backward_error_px'], 1.)
        self.assertGreater(np.linalg.norm(trace['rgb_correction_px']), .2)

    def test_repeated_rgb_is_not_accepted_from_projection(self):
        a, b = row(1, 'A', image()), row(2, 'B', image(duplicate=True))
        p = np.asarray(anchor()['anchor']['uv'])
        config = TrackConfig(sensor_roi_fraction_xyxy=(.1, .1, .95, .9), affine_rgb_seed=False)
        _, _, reason, _ = match_rgb(a, b, p, np.eye(3), 0, config)
        self.assertIn(reason, ('AMBIGUOUS_RGB_IDENTITY', 'COMPETING_MATCH_PEAK'))

    def test_failed_camera_branch_never_reacquires_and_plan_is_complete(self):
        old = anchor(); rows = [row(f, name, image()) for f in (1, 2, 3) for name in ('A', 'B')]
        rows[3]['known'][:] = False  # B at second time, later image is restored.
        vertices = {f: np.zeros((3, 3, 3)) for f in (1, 2, 3)}
        binding = {'blade_0based': 0, 'face': 0, 'barycentric': [.2, .3, .5]}
        with patch('material_tracks.ray_binding', return_value=binding), \
             patch('material_tracks.binding_visibility', return_value={'visible': True, 'reason': 'VISIBLE'}), \
             patch('material_tracks.local_homography', return_value=(np.eye(3), {'status': 'LOCAL'})):
            result = track_fixed_anchors([old], rows, vertices, np.asarray([[0, 1, 2]]))
        obs = result[0]['observations']
        self.assertEqual(len(obs), 6)
        self.assertEqual(obs[0]['uv'], old['observations'][0]['uv'])
        self.assertTrue(obs[1]['accept'])  # cross-camera needed a real RGB match.
        self.assertEqual(obs[3]['status'], 'TARGET_UNKNOWN_OR_DIFFERENT_BLADE_FOOTPRINT')
        self.assertEqual(obs[5]['status'], 'IDENTITY_TERMINATED')
        self.assertIsNone(obs[5]['uv'])
        self.assertEqual(obs[5]['termination_origin']['track_id'], old['id'])
        self.assertEqual(obs[5]['termination_origin']['camera'], 'B')
        self.assertEqual(obs[5]['termination_origin']['frame'], 2)
        self.assertEqual(obs[5]['termination_origin']['status'], obs[3]['status'])
        self.assertEqual(obs[5]['termination_origin']['first_failed_check'],
                         'TARGET_PREDICTED_DESCRIPTOR_SUPPORT')
        self.assertEqual([(o['frame'], o['status']) for o in obs[5]['termination_chain']],
                         [(2, obs[3]['status']), (3, 'IDENTITY_TERMINATED')])
        self.assertTrue(obs[4]['accept'])  # independent A branch remains alive.
        _forbidden_keys({'tracks': result})

    def test_back_surface_anchor_rejection_preserves_original_anchor(self):
        old = anchor(); rows = [row(f, 'A', image()) for f in (1, 2)]
        binding = {'blade_0based': 0, 'face': 0, 'barycentric': [.2, .3, .5]}
        with patch('material_tracks.ray_binding', return_value=binding), \
             patch('material_tracks.binding_visibility', return_value={'visible': False, 'reason': 'BACK_FACING'}):
            result = track_fixed_anchors([old], rows, {f: np.zeros((3, 3, 3)) for f in (1, 2)},
                                        np.asarray([[0, 1, 2]]))
        self.assertEqual(result[0]['anchor'], old['anchor'])
        self.assertEqual(result[0]['observations'][0]['status'], 'ANCHOR_BACK_FACING')
        self.assertIsNone(result[0]['observations'][0]['uv'])

    def test_original_failed_anchor_is_retained_with_original_self_failure(self):
        old = anchor(); old['observations'][0].update(uv=None, accept=False, status='AMBIGUOUS_RGB_IDENTITY')
        rows = [row(f, name, image()) for f in (1, 2) for name in ('A', 'B')]
        result = track_fixed_anchors([old], rows, {f: np.zeros((3, 3, 3)) for f in (1, 2)},
                                    np.asarray([[0, 1, 2]]))
        self.assertEqual(len(result[0]['observations']), 4)
        self.assertEqual(result[0]['anchor'], old['anchor'])
        self.assertEqual(result[0]['observations'][0]['status'], 'AMBIGUOUS_RGB_IDENTITY')
        self.assertTrue(all(o['uv'] is None for o in result[0]['observations']))
        for observation in result[0]['observations']:
            origin = observation['termination_origin']
            self.assertEqual((origin['camera'], origin['frame'], origin['status']),
                             ('A', 1, 'AMBIGUOUS_RGB_IDENTITY'))
            self.assertEqual(origin['first_failed_check'], 'ORIGINAL_SELF_QUALITY')
            self.assertEqual(origin['termination_reason'], 'RGB_NOT_OBSERVED')
            self.assertEqual(origin['original_self_quality'], old['observations'][0]['quality'])
        self.assertEqual(result[0]['observations'][0]['material_trace']['original_self_status'],
                         'AMBIGUOUS_RGB_IDENTITY')

    def test_self_observation_cannot_supply_different_rgb_or_relocated_anchor(self):
        old = anchor(); rows = [row(1, 'A', image())]
        for key, value in (('rgb_sha256', 'f'*64), ('t', 1.1), ('uv', [100., 100.])):
            bad = copy.deepcopy(old); bad['observations'][0][key] = value
            with self.assertRaises(ValueError):
                track_fixed_anchors([bad], rows, {1: np.zeros((3, 3, 3))}, np.asarray([[0, 1, 2]]))

    def test_seed_requires_same_fit_provenance_and_contour_only(self):
        cam = camera(); log = [{'path': 'A.png', 'role': 'RGB', 'sha256': 'a'*64, 'bytes': 1}]
        cfg = TurbineConfig()
        inputs = types.SimpleNamespace(cfg=cfg, access_log=log)
        record = types.SimpleNamespace(frame=1, t=1., cameras=[cam])
        saved = {'name': 'A', 'W': cam.W, 'H': cam.H, 'K': cam.K.tolist(),
                 'T_cv_from_model': np.eye(4).tolist()}
        seed = {'turbine': cfg.to_dict(), 'state_names': STATE_NAMES, 'input_access_log': log,
            'frames': [{'frame': 1, 't': 1., 'state': [0.]*13, 'solver': {'success': True},
                        'reconstruction_valid': True, 'cameras': [saved]}]}
        validate_seed(seed, inputs, [record])
        for mutate in ('hash', 'time', 'camera', 'texture'):
            bad = copy.deepcopy(seed)
            if mutate == 'hash': bad['input_access_log'][0]['sha256'] = 'b'*64
            elif mutate == 'time': bad['frames'][0]['t'] = 1.1
            elif mutate == 'camera': bad['frames'][0]['cameras'][0]['K'][0][0] += 1.
            else: bad['frames'][0]['texture_geometry'] = {}
            with self.assertRaises(ValueError): validate_seed(bad, inputs, [record])


if __name__ == '__main__':
    unittest.main()
