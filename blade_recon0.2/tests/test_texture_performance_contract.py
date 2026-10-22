"""Quality and frame-identity invariants for measured texture optimizations."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ALG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ALG))
from atlas import Atlas
from camera import Camera
from input_adapter import InputSequence
from model import Rotor, TurbineConfig
from texture import Accum, frame_views, input_sequence_for_texture, run_pass, validate_input_provenance
from test_input_recon import bundle


class TexturePerformanceContract(unittest.TestCase):
    def test_sparse_selection_retains_original_indices_camera_and_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); meta = bundle(root, frames=(7, 19, 42, 99))
            # Unselected broken RGB must never be decoded or read for a hash.
            (root / meta['frames'][1]['cameras'][0]['rgb']).write_bytes(b'broken')
            sequence = InputSequence(root, selected_frames=[7, 99])
            first, last = sequence.read_frame(7, .7), sequence.read_frame(99, 9.9)
            self.assertEqual((first.frame, last.frame), (7, 99))
            self.assertEqual((first.source_indices, last.source_indices), ([0], [3]))
            self.assertAlmostEqual(last.cameras[0].t[0], .69)
            self.assertTrue(np.all(first.rgbs[0] == 100))
            self.assertTrue(np.all(last.rgbs[0] == 103))
            self.assertIsNone(sequence.read())
            paths = [r['path'] for r in sequence.access_log]
            self.assertNotIn('A/000019.png', paths)
            self.assertNotIn('A/000042.png', paths)
            self.assertIn('A/000099.png', paths)
            with self.assertRaisesRegex(ValueError, 'unreadable image'):
                InputSequence(root, selected_frames=[19])
            with self.assertRaisesRegex(ValueError, 'absent'):
                InputSequence(root, selected_frames=[100])
            with self.assertRaisesRegex(ValueError, 'strictly increasing'):
                InputSequence(root, selected_frames=[99, 7])

    def test_annotation_indices_remap_without_changing_original_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); meta = bundle(root, frames=(7, 19, 42, 99))
            annotations = {'schema': 'blade-surface-annotations.v1', 'author': 'fixture',
                'method': 'manual', 'source': 'declared_RGB', 'pixel_coordinates': 'original_image_pixel_centers',
                'tracks': [{'id': 'region', 'blade_id': 0, 'observations': [
                    {'frame_index': k, 'camera_index': 0, 'uv': [7., 5.], 'image_visible': True}
                    for k in range(4)]}]}
            path = root / 'annotations.json'; path.write_text(json.dumps(annotations))
            original = path.read_bytes(); meta['annotations'] = path.name
            (root / 'input_bundle.json').write_text(json.dumps(meta))
            sequence = InputSequence(root, selected_frames=[19, 99])
            self.assertEqual([o['frame_index'] for o in sequence.bundle.tracks[0]['observations']], [0, 1])
            self.assertEqual(sequence.read().source_indices, [1])
            self.assertEqual(sequence.read().source_indices, [3])
            self.assertEqual(path.read_bytes(), original)
            access = next(a for a in sequence.access_log if a['role'] == 'human_2D_annotations')
            self.assertEqual(access['sha256'], hashlib.sha256(original).hexdigest())

    def test_provenance_skips_only_declared_unselected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle(root, frames=(7, 19, 42, 99))
            full = InputSequence(root)
            saved = {'input_access_log': full.access_log}
            selected = InputSequence(root, selected_frames=[7, 99])
            validate_input_provenance(saved, selected, [{'frame': 7}, {'frame': 99}])
            forged = {'input_access_log': [{'path': 'A/unknown.png', 'role': 'RGB', 'sha256': 'fake'}]}
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                validate_input_provenance(forged, selected, [{'frame': 7}])
            changed = {'input_access_log': [dict(a, sha256='changed') if a['path'] == 'A/000099.png'
                                           else a for a in full.access_log]}
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validate_input_provenance(changed, selected, [{'frame': 99}])
            manifest = {'input_access_log': [dict(full.access_log[0], sha256='changed')]}
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validate_input_provenance(manifest, selected, [{'frame': 7}])

    def test_texture_limit_selects_reconstruction_rows_not_bundle_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle(root, frames=(7, 19, 42, 99))
            args = SimpleNamespace(data=root, masks=None, max_frames=2, exposure_ms=0., skip=0, fusion='best')
            cfg = TurbineConfig(n_sections=8, n_ring=8)
            rotor = Rotor(cfg); atlas = Atlas(rotor.tpl, dr=1., n_cols=8)
            rows = [{'frame': frame, 't': frame/10, 'state': np.zeros(13).tolist(),
                     'observed_sections': np.zeros((3, 8), int).tolist()} for frame in (7, 99)]
            source = input_sequence_for_texture(args, {'frames': rows})
            self.assertEqual(source.bundle.frame_ids, [7, 99])
            with redirect_stdout(io.StringIO()):
                acc, _ = run_pass(args, rotor, atlas, {'frames': rows}, sequence=source)
            self.assertEqual([(f['frame'], f['source_indices']) for f in acc.input_contract['frames']],
                             [(7, [0]), (99, [3])])
            self.assertFalse(acc.result()[2].any())

    def test_bundle_numeric_unknown_files_cannot_use_legacy_mask_shortcut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle(root, frames=(7, 19, 42, 99))
            selected = InputSequence(root, selected_frames=[7, 99])
            for role in ('RGB', 'explicit_mask'):
                with self.subTest(role=role):
                    forged = {'input_access_log': [{'path': 'A/000088.png', 'role': role, 'sha256': 'fake'}]}
                    with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                        validate_input_provenance(forged, selected, [{'frame': 7}, {'frame': 99}])

    def test_mode_specific_arrays_keep_values_coverage_and_evidence_exact(self):
        atlas = SimpleNamespace(H=32, W=64)
        rng = np.random.default_rng(23)
        dual = Accum(atlas)
        best, mean = Accum(atlas, mode='best'), Accum(atlas, mode='mean')
        for k in range(5):
            rows = slice(2 + k, 20 + k)
            shape = (rows.stop - rows.start, atlas.W)
            val = (1 + rng.normal(0, .1, shape)).astype(np.float32)
            raw = (150 * val).astype(np.float32)
            w = rng.random(shape, dtype=np.float32); w[w < .3] = 0
            # Preserve den>0 semantics even where w**POW underflows to zero.
            w[0, 0] = 1e-30
            rs = np.where(w > 0, .03, np.nan).astype(np.float32)
            rc = np.where(w > 0, .04, np.nan).astype(np.float32)
            shift = (k - 2, k * 17)
            for acc in (dual, best, mean):
                acc.add(k % 3, rows, val, raw, w, rs, rc, shift)
        for mode, specific in (('best', best), ('mean', mean)):
            for x, y in zip(dual.result(mode), specific.result(mode)):
                np.testing.assert_array_equal(x, y)
            for name in ('den', 'wmax', 'res_s', 'res_c', 'nview', 'wsel'):
                np.testing.assert_array_equal(getattr(dual, name), getattr(specific, name))
            live_bytes = sum(a.nbytes for a in vars(specific).values() if isinstance(a, np.ndarray))
            dual_bytes = sum(a.nbytes for a in vars(dual).values() if isinstance(a, np.ndarray))
            self.assertEqual(dual_bytes - live_bytes, 2 * 3 * atlas.H * atlas.W * 4)
        self.assertIsNone(best.num); self.assertIsNone(best.raw)
        self.assertIsNone(mean.best_val); self.assertIsNone(mean.best_raw)
        with self.assertRaisesRegex(ValueError, 'not allocated'):
            best.result('mean')

    def test_absent_raster_blade_never_builds_its_atlas_surface(self):
        rotor = Rotor(TurbineConfig(n_sections=4, n_ring=8))
        atlas = Atlas(rotor.tpl, dr=1., n_cols=8)
        camera = Camera('fixture', 16, 12, np.eye(3), np.eye(4))
        masks = [np.ones((12, 16), np.uint8) * 255]
        raster = SimpleNamespace(ys=np.array([3]), xs=np.array([4]), blade=np.array([1]),
                                 fid=np.full((12, 16), -1, np.int32),
                                 depth_map=lambda: np.full((12, 16), np.inf, np.float32))
        raster.fid[3, 4] = 1
        with patch('texture.rasterize', return_value=raster), patch('texture.sample_view', return_value=None) as sample:
            views = frame_views(rotor, atlas, [camera], [np.zeros((12, 16), np.uint8)], masks,
                                np.zeros(13), np.ones((3, 4), bool), 0., 0., rotor.tpl.faces())
        self.assertEqual(views, [])
        self.assertEqual(sample.call_count, 1)
        self.assertEqual(sample.call_args.args[6], 1)

    def test_selected_truth_symlink_still_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); bundle(root, frames=(7, 99))
            (root / 'evaluation').mkdir()
            source = root / 'A/000099.png'
            source.rename(root / 'evaluation/truth.png')
            source.symlink_to(root / 'evaluation/truth.png')
            with self.assertRaisesRegex(ValueError, 'forbidden solver input'):
                InputSequence(root, selected_frames=[99])


if __name__ == '__main__':
    unittest.main()
