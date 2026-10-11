"""Focused stage 1/2 regressions; no geometry acceptance or performance claims.

Run: blade_recon/.venv-trial/bin/python blade_recon0.2/test_texture_correctness.py
"""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from atlas import Atlas, validate_atlas_meta
from camera import Camera
from eval_texture import truth_atlas_metadata, resample_truth_to_atlas, window, centroid
from input_adapter import InputSequence, FrameRecord
from model import Rotor, TurbineConfig
from raster import rasterize, sample
from texture import Accum, register, shift_span_cols, run_pass, validate_saved_cameras, validate_input_provenance

EVIDENCE = {}


def overlapping_triangles():
    uv = np.array([[2, 2], [15, 2], [2, 15]], float)
    z = [np.array([1., 10., 10.]), np.full(3, 5.), np.full(3, 100.)]
    vertices = np.stack([np.column_stack([(uv + (1000 if b == 2 else 0)) * zz[:, None], zz])
                         for b, zz in enumerate(z)])[:, None]
    normals = np.broadcast_to([0., 0., -1.], vertices.shape).copy()
    return Camera('counterexample', 20, 20, np.eye(3), np.eye(4)), vertices, normals


class TextureCorrectness(unittest.TestCase):
    def test_pixelwise_perspective_depth(self):
        cam, vertices, normals = overlapping_triangles()
        result = rasterize(cam, vertices, np.array([[0, 1, 2]]), normals)
        k = np.flatnonzero((result.ys == 3) & (result.xs == 3))[0]
        lam = np.array([11/13, 1/13, 1/13])
        z0 = vertices[0, 0, :, 2]
        expected_depth = 1 / (lam / z0).sum()
        self.assertEqual(int(result.blade[k]), 0)
        self.assertAlmostEqual(float(result.depth[k]), expected_depth, places=6)
        np.testing.assert_allclose(result.bary[k], lam / z0 / (lam / z0).sum(), atol=1e-7)
        # At the far corner the other triangle actually wins; no face-wide ordering works.
        q = np.flatnonzero((result.ys == 2) & (result.xs == 14))[0]
        self.assertEqual(int(result.blade[q]), 1)
        self.assertAlmostEqual(float(result.depth[q]), 5., places=6)
        EVIDENCE['perspective_z_test'] = {'pixel_xy': [3, 3], 'selected_blade_zero_based': 0,
            'depth': float(result.depth[k]), 'expected_depth': float(expected_depth),
            'far_corner_xy': [14, 2], 'far_corner_blade_zero_based': 1,
            'scope': 'generic overlapping triangles; supplied-video incidence not quantified'}

    def test_raster_empty_degenerate_and_near_plane(self):
        cam, v, n = overlapping_triangles()
        near = v.copy()
        near[..., 2] = .05
        self.assertEqual(len(rasterize(cam, near, np.array([[0, 1, 2]]), n).ys), 0)
        degenerate = v.copy()
        degenerate[:, :, 2] = degenerate[:, :, 1]
        self.assertEqual(len(rasterize(cam, degenerate, np.array([[0, 1, 2]]), n).ys), 0)
        self.assertEqual(len(rasterize(cam, v, np.array([[0, 1, 2]]), -n).ys), 0)
        front = v.copy()
        for b in range(3):
            front[b, 0] = np.column_stack([np.array([[2, 2], [15, 2], [2, 15]]) * .11,
                                          np.full(3, .11)])
        r = rasterize(cam, front, np.array([[0, 1, 2]]), n)
        self.assertGreater(len(r.ys), 0)
        np.testing.assert_allclose(r.depth, .11, atol=1e-7)
        EVIDENCE['raster_rejection'] = {'all_near_plane_rejected': True, 'degenerate_rejected': True,
            'backface_rejected': True, 'in_front_of_near_plane_depth': .11,
            'existing_near_plane_policy': 'triangle requires all vertices z>0.1m; no clipped crossing triangles'}

    def test_sample_wraps_only_circumference(self):
        image = np.array([[100., 200.], [0., 20.]], np.float32)
        rows, cols = np.array([1.5, -.5, .5, .5, .5]), np.array([0., 0., -.5, 1.5, 3.5])
        got = sample(image, rows, cols)
        np.testing.assert_allclose(got, [0., 100., 80., 80., 80.])
        np.testing.assert_allclose(sample(image, rows, cols, wrap_cols=False), [0., 100., 50., 110., 110.])
        EVIDENCE['span_sampling'] = {'tip_outside_value': float(got[0]), 'root_outside_value': float(got[1]),
            'expected_tip': 0., 'periodic_seam_values': got[2:].tolist()}

    def test_registration_global_destination_and_crops(self):
        small = SimpleNamespace(H=8, W=8)
        value = np.zeros((4, 8), np.float32); weight = np.zeros_like(value)
        value[-1, 3] = 2.; weight[-1, 3] = 1.
        acc = Accum(small)
        acc.add(0, slice(2, 6), value, value, weight, np.ones_like(value), np.ones_like(value), (1, 0))
        result, _, has = acc.result()
        self.assertEqual(np.argwhere(result[0] > 0).tolist(), [[6, 3]])
        self.assertEqual(np.argwhere(has[0]).tolist(), [[6, 3]])
        boundary = Accum(small)
        boundary.add(0, slice(4, 8), value, value, weight, value, value, (1, 0))
        self.assertFalse(boundary.result()[2].any())
        circ = Accum(small)
        circ.add(0, slice(2, 6), value, value, weight, value, value, (0, 6))
        self.assertEqual(np.argwhere(circ.result()[2][0]).tolist(), [[5, 1]])
        direct = shift_span_cols(value, 1, 0)
        self.assertEqual(float(direct[0].sum()), 0.)
        self.assertEqual(float(direct.sum()), 0.)
        EVIDENCE['registration'] = {'source_global_row': 5, 'shift_span_rows': 1,
            'written_global_row_col': np.argwhere(has[0]).tolist(), 'beyond_atlas_discarded': True,
            'circumference_wrap_target_row_col': [[5, 1]],
            'delivered_sample_register': False, 'scope': 'optional --register correctness only'}

    def test_registration_search_finds_nonperiodic_span_shift(self):
        atlas = SimpleNamespace(dr=.1, W=64, perim_rows=np.full(32, 1.))
        rng = np.random.default_rng(12)
        value = (1. + rng.normal(0, .1, (32, 64))).astype(np.float32)
        weight = np.ones_like(value)
        ref = shift_span_cols(value, 3, -2)
        ref_mask = shift_span_cols(weight > 0, 3, -2, fill=False)
        found = register(value, weight, ref, ref_mask, atlas, slice(0, 32))
        self.assertEqual(found[:2], (3, -2))
        self.assertGreater(found[2], .03)
        EVIDENCE['registration']['search_known_shift'] = list(found)

    def test_physical_truth_alignment_custom_atlas(self):
        rotor = Rotor(TurbineConfig())
        truth = Atlas(rotor.tpl)
        custom = Atlas(rotor.tpl, dr=.05, n_cols=384)
        r = truth.r_rows[:, None]
        # A defect at 57 m on the suction surface, represented in physical r/c coordinates.
        signal = np.exp(-((r - 57.)/.15)**2 - ((truth.c_grid - .2)/.12)**2).astype(np.float32)
        field = np.repeat(signal[None], 3, axis=0)
        aligned = resample_truth_to_atlas(field, truth.meta(), custom)
        np.testing.assert_array_equal(resample_truth_to_atlas(field, truth.meta(), truth), field)
        rows, cols, per = window(custom, 57., .2)
        loc = centroid(aligned[0][rows][:, cols], 1., custom, rows, cols, per)
        self.assertIsNotNone(loc)
        self.assertLess(abs(loc[0] - 57.), .03)
        # centroid returns chord offset relative to this local window, used for paired localization.
        self.assertLess(abs(loc[1]), per/custom.W * 1.1)
        old_row = int(custom.row_of_r(57.))
        self.assertLess(float(field[0, old_row].max()), 1e-8)
        self.assertGreater(float(aligned[0, old_row].max()), .8)
        # An analytic periodic field catches circumference index scaling too.
        analytic = np.repeat((r + np.cos(2*np.pi*truth.tau_cols)[None])[None], 3, 0).astype(np.float32)
        expected = custom.r_rows[:, None] + np.cos(2*np.pi*custom.tau_cols)[None]
        actual = resample_truth_to_atlas(analytic, truth.meta(), custom)
        np.testing.assert_allclose(actual[0], expected, atol=1e-4)
        EVIDENCE['custom_truth_alignment'] = {'source_shape': list(field.shape),
            'target_shape': list(aligned.shape), 'defect_expected_span_m': 57.,
            'defect_resampled_centroid_span_m': float(loc[0]),
            'default_metadata_alignment_pixel_identical': True,
            'old_reused_row_truth_span_m': float(truth.r_rows[old_row]),
            'analytic_r_tau_max_error': float(np.abs(actual[0]-expected).max())}

    def test_truth_metadata_legacy_and_rejections(self):
        rotor = Rotor(TurbineConfig())
        default = Atlas(rotor.tpl)
        shape = (3, default.H, default.W)
        archive = {'albedo': np.empty(shape, np.float16), 'height': np.empty(shape, np.float16)}
        meta, origin = truth_atlas_metadata(archive, {}, rotor)
        self.assertEqual(origin, 'legacy_default_inferred')
        with self.assertRaisesRegex(ValueError, 'requires saved atlas metadata'):
            truth_atlas_metadata({'albedo': np.empty((3, 12, 8)), 'height': np.empty((3, 12, 8))}, {}, rotor)
        archive['atlas_meta'] = np.array(json.dumps(default.meta()))
        self.assertEqual(truth_atlas_metadata(archive, {'atlas': default.meta()}, rotor)[1], 'embedded_npz')
        bad = dict(default.meta(), W=12)
        with self.assertRaisesRegex(ValueError, 'differs between'):
            truth_atlas_metadata(archive, {'atlas': bad}, rotor)
        with self.assertRaisesRegex(ValueError, 'shape'):
            validate_atlas_meta(default.meta(), (3, 12, 8))
        EVIDENCE['truth_metadata'] = {'legacy_defaults_accepted': True, 'custom_without_metadata_rejected': True,
                                    'contradictory_metadata_rejected': True, 'bad_shape_rejected': True}

    def test_texture_uses_true_frame_time_dynamic_camera_and_exact_masks(self):
        rotor = Rotor(TurbineConfig(n_sections=4, n_ring=8))
        atlas = Atlas(rotor.tpl, dr=1., n_cols=8)
        cameras = [Camera('C1', 8, 8, np.eye(3), np.eye(4))]
        later_T = np.eye(4); later_T[0, 3] = .7
        second = [Camera('C1', 8, 8, np.eye(3), later_T)]
        masks = [np.zeros((8, 8), np.uint8)]
        masks[0][2, 2] = 255
        records = [FrameRecord(17, 3.2, cameras, [np.zeros((8, 8, 3), np.uint8)], masks, [0]),
                   FrameRecord(29, 3.5, second, [np.zeros((8, 8, 3), np.uint8)], masks, [1])]
        class Sequence:
            cfg = rotor.cfg
            metadata = {'mode': 'controlled_dynamic_fixture'}
            access_log = [{'path': 'fixture_rgb', 'role': 'RGB'}]
            def __init__(self): self.k = 0; self.closed = False
            def read_frame(self, f, t):
                row = records[self.k]; self.k += 1
                if (f, t) != (row.frame, row.t): raise ValueError('identity mismatch')
                return row
            def close(self): self.closed = True
        sequence = Sequence()
        result = {'frames': [{'frame': r.frame, 't': r.t, 'state': np.zeros(13).tolist(),
            'observed_sections': np.ones((3, 4), int).tolist(), 'rpm': 12.,
            'cameras': [c.to_dict() for c in r.cameras]} for r in records]}
        args = SimpleNamespace(data='not-used', masks=None, exposure_ms=0., max_frames=0, skip=0)
        calls = []
        def views(rot, atl, cams, imgs, masks_arg, *other):
            calls.append((float(cams[0].t[0]), masks_arg[0].copy()))
            return []
        with patch('texture.frame_views', side_effect=views), redirect_stdout(io.StringIO()):
            acc, _ = run_pass(args, rotor, atlas, result, sequence=sequence)
        self.assertTrue(sequence.closed)
        self.assertEqual([c[0] for c in calls], [0., .7])
        for _, used_mask in calls: np.testing.assert_array_equal(used_mask, masks[0])
        self.assertEqual([(r['frame'], r['t']) for r in acc.input_contract['frames']], [(17,3.2),(29,3.5)])
        changed = dict(result['frames'][1]); changed['cameras'] = [c.to_dict() for c in cameras]
        with self.assertRaisesRegex(ValueError, 'per-frame calibration mismatch'):
            validate_saved_cameras(changed, second)
        EVIDENCE['texture_input_contract'] = {'frame_t': [[17,3.2],[29,3.5]],
            'per_frame_camera_translation_x': [0.,.7], 'exact_shared_masks_preserved': True,
            'saved_wrong_camera_rejected': True, 'truth_input_read': False,
            'scope': 'controlled frame_views seam fixture; bundle disk validation covered by shared adapter suite'}

    def test_texture_missing_mask_size_and_time_fail(self):
        cam = Camera('C1', 8, 8, np.eye(3), np.eye(4))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'C1').mkdir(); (root/'masks'/'C1').mkdir(parents=True)
            (root/'cameras.json').write_text(json.dumps({'fps':10., 'cameras':[cam.to_dict()]}))
            cv2.imwrite(str(root/'C1'/'000017.png'), np.zeros((8,8,3), np.uint8))
            seq = InputSequence(root, masks_dir=root/'masks')
            with self.assertRaisesRegex(ValueError, 'missing or unreadable explicit mask'): seq.read_frame(17,1.7)
            seq.close()
            cv2.imwrite(str(root/'masks'/'C1'/'000017.png'), np.ones((7,8), np.uint8)*255)
            seq = InputSequence(root, masks_dir=root/'masks')
            with self.assertRaisesRegex(ValueError, 'size mismatch'): seq.read_frame(17,1.7)
            seq.close()
            cv2.imwrite(str(root/'masks'/'C1'/'000017.png'), np.ones((8,8), np.uint8)*255)
            seq = InputSequence(root, masks_dir=root/'masks')
            with self.assertRaisesRegex(ValueError, 'timestamp mismatch'): seq.read_frame(17,1.8)
            seq.close()
        EVIDENCE['texture_bad_input'] = {'explicit_mask_missing_rejected':True,
            'mask_size_mismatch_rejected':True, 'timestamp_mismatch_rejected':True}

    def test_texture_invalid_reconstruction_is_not_sampled(self):
        rotor = Rotor(TurbineConfig(n_sections=4, n_ring=8))
        atlas = Atlas(rotor.tpl, dr=1., n_cols=8)
        camera = Camera('C1', 8, 8, np.eye(3), np.eye(4))
        class Sequence:
            cfg = rotor.cfg
            metadata, access_log = {}, []
            def read_frame(self, f, t):
                return FrameRecord(f, t, [camera], [np.zeros((8,8,3),np.uint8)],
                                   [np.ones((8,8),np.uint8)*255], [0])
            def close(self): pass
        frame = {'frame':17,'t':1.7,'state':np.zeros(13).tolist(),
            'observed_sections':np.ones((3,4),int).tolist(), 'reconstruction_valid':False,
            'reconstruction_status':'prior_prediction'}
        args = SimpleNamespace(data='unused',masks=None,exposure_ms=0,max_frames=0,skip=0)
        with patch('texture.frame_views') as views:
            acc, _ = run_pass(args, rotor, atlas, {'frames':[frame]}, sequence=Sequence())
            views.assert_not_called()
        self.assertFalse(acc.result()[2].any())
        EVIDENCE['texture_invalid_result'] = {'prior_prediction_not_textured':True,
                                             'invalid_result_has_no_coverage':True}

    def test_texture_provenance_rejects_file_hash_or_identity_changes(self):
        source = SimpleNamespace(access_log=[{'path':'rgb/C1/17.png','role':'RGB','sha256':'abc'},
            {'path':'mask/C1/17.png','role':'RGB_derived_mask','sha256':'def'}])
        saved = {'input_access_log':[dict(i) for i in source.access_log]}
        validate_input_provenance(saved, source, [{'frame':17}])
        wrong_hash = {'input_access_log':[dict(saved['input_access_log'][0],sha256='other')]}
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            validate_input_provenance(wrong_hash, source, [{'frame':17}])
        wrong_path = {'input_access_log':[dict(saved['input_access_log'][0],path='different.png')]}
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            validate_input_provenance(wrong_path, source, [{'frame':17}])
        wrong_segment = dict(saved, input_contract={'mask_polarity':'bright'})
        with self.assertRaisesRegex(ValueError,'mask parameter mismatch'):
            validate_input_provenance(wrong_segment, source, [{'frame':17}])
        validate_input_provenance({}, source, [{'frame':17}])
        EVIDENCE['texture_provenance'] = {'changed_rgb_hash_rejected':True,
            'changed_declared_path_rejected':True,'changed_automatic_mask_polarity_rejected':True,
            'legacy_without_declaration_accepted':True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output')
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TextureCorrectness)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'kind':'focused correctness and input contract regressions; no reconstruction acceptance',
              'tests_run':result.testsRun, 'failures':len(result.failures), 'errors':len(result.errors),
              'passed':result.wasSuccessful(), 'checks':EVIDENCE}
    if args.output:
        path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
