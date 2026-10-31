"""Declared shared files preserve source identity through selection materialization."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from test_input_recon import bundle
from input_adapter import InputSequence, _load_bundle, _proper_camera


class SharedInputTests(unittest.TestCase):
    def test_loaded_skew_camera_preserves_projection_and_pixel_centre_resize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            meta = bundle(root, frames=(7, 19))
            for frame in meta['frames']:
                frame['cameras'][0]['K'][0][1] = 2.5
            (root / 'input_bundle.json').write_text(json.dumps(meta))
            sequence = InputSequence(root, selected_frames=[7])
            loaded = sequence.read().cameras[0]
            for camera in (loaded, _proper_camera(loaded.to_dict())):
                points = np.array([[1., 2., 10.], [-1., 3., 15.]])
                uv, depth = camera.project(points)
                expected = camera.to_cam(points) @ camera.K.T
                np.testing.assert_allclose(uv, expected[:, :2] / expected[:, 2:])
                resized = camera.scaled(.5)
                actual, _ = resized.project(points)
                np.testing.assert_allclose(actual, (uv + .5) * [resized.W / camera.W, resized.H / camera.H] - .5)

    def check_shared(self, copy_only, sharing, forced=False, full=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            meta = bundle(root, frames=(7, 19, 42))
            first = meta['frames'][0]['cameras'][0]
            for index, frame in enumerate(meta['frames']):
                cam = frame['cameras'][0]
                if sharing in ('both', 'rgb'):
                    cam['rgb'] = first['rgb']
                if sharing in ('both', 'mask'):
                    cam['mask'] = first['mask']
                if sharing == 'partial' and index != 1:
                    cam['mask'] = first['mask']
                if sharing == 'cross-camera':
                    other = deepcopy(cam); other['name'] = 'B'
                    other['rgb'], other['mask'] = first['rgb'], first['mask']
                    frame['cameras'].append(other)
            # A normalized alias names the same declared file.
            if sharing == 'alias':
                for frame in meta['frames']:
                    frame['cameras'][0]['rgb'] = 'A/../' + first['rgb']
            (root / 'input_bundle.json').write_text(json.dumps(meta))
            before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            original = deepcopy(meta)
            indices = [0, 1, 2] if full else [0, 1]
            link = Path.hardlink_to
            calls = []
            def create_link(dest, source):
                calls.append(str(dest))
                if copy_only:
                    raise OSError('copy fallback')
                return link(dest, source)
            with patch.object(Path, 'hardlink_to', create_link):
                loaded = _load_bundle(root, meta, indices, force_manifest=forced)
            self.assertEqual(meta, original)
            self.assertEqual(loaded.frame_ids, [meta['frames'][i]['frame'] for i in indices])
            self.assertEqual(loaded.times.tolist(), [meta['frames'][i]['t'] for i in indices])
            for i, selected in enumerate(indices):
                for j, camera in enumerate(meta['frames'][selected]['cameras']):
                    raw = np.frombuffer((root / camera['rgb']).read_bytes(), np.uint8)
                    expected = cv2.cvtColor(cv2.imdecode(raw, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
                    np.testing.assert_array_equal(loaded.rgbs[i][j], expected)
            self.assertEqual({p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)
            if full and not forced:
                self.assertFalse(calls)
            else:
                refs = [row for row in loaded.access_log if row['role'] in ('RGB', 'RGB_derived_mask')]
                self.assertEqual(len(refs), sum(len(meta['frames'][i]['cameras']) * 2 for i in indices))
                self.assertTrue(all('frame' in row and 'camera' in row for row in refs))
                for row in refs:
                    self.assertEqual(row['sha256'], hashlib.sha256((root / row['path']).read_bytes()).hexdigest())
                unique = {(root / cam[role]).resolve() for i in indices for cam in meta['frames'][i]['cameras'] for role in ('rgb', 'mask')}
                self.assertEqual(len(calls), len(unique))

    def test_shared_selection_link_and_copy(self):
        for copy_only in (False, True):
            for sharing in ('both', 'rgb', 'mask', 'partial', 'cross-camera', 'alias'):
                with self.subTest(copy=copy_only, sharing=sharing):
                    self.check_shared(copy_only, sharing)

    def test_complete_bypass_and_forced_manifest(self):
        for forced in (False, True):
            for copy_only in (False, True):
                self.check_shared(copy_only, 'both', forced=forced, full=True)
