"""Independent numerical counterexamples for the correspondence chain."""
from pathlib import Path
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from atlas import Atlas
from camera import Camera
from model import Rotor, TurbineConfig
from raster import rasterize, triangulated_atlas_surface
from surface_observations import detect_region


class SurfaceCoordinates(unittest.TestCase):
    def test_atlas_samples_share_triangle_r_tau_contract(self):
        rotor = Rotor(TurbineConfig(n_sections=8, n_ring=8))
        atlas = Atlas(rotor.tpl, dr=.2, n_cols=64)
        state = np.zeros(13); state[1] = 8.; state[4] = 2.
        vertices, _ = rotor.forward(state)
        faces = rotor.tpl.faces()
        corners = atlas.corner_r_tau(faces)
        surface = triangulated_atlas_surface(atlas, vertices[0])
        # Reconstruct the interpolation on each of the two triangle branches
        # from its declared r/tau. Also includes last-cell tau=1 unwrapping.
        max_error = 0.
        for row in range(0, atlas.H, 11):
            for col in range(0, atlas.W, 3):
                i, j = atlas.i0[row], atlas.j0[row, col]
                rt = np.array([atlas.r_rows[row], atlas.tau_cols[col]])
                for side in (0, 1):
                    face = (i * atlas.N + j) * 2 + side
                    tri = corners[face]
                    lam = np.linalg.solve(np.vstack([tri.T, np.ones(3)]), np.r_[rt, 1.])
                    if lam.min() >= -1e-7:
                        expected = lam @ vertices[0].reshape(-1, 3)[faces[face]]
                        max_error = max(max_error, float(np.linalg.norm(expected - surface[row, col])))
                        break
                else:
                    self.fail('atlas texel did not belong to either declared side triangle')
        self.assertLess(max_error, 5e-6)
        # The old bilinear quad is observably a different surface.
        old_gap = np.linalg.norm(atlas.surface(vertices[0]) - surface, axis=-1)
        self.assertGreater(float(old_gap.max()), 1e-4)

    def test_slice_preserves_surface_coordinates(self):
        rotor = Rotor(TurbineConfig(n_sections=8, n_ring=8))
        atlas = Atlas(rotor.tpl, dr=.5, n_cols=32)
        vertices, _ = rotor.forward(np.zeros(13))
        whole = triangulated_atlas_surface(atlas, vertices[0])
        np.testing.assert_array_equal(triangulated_atlas_surface(atlas, vertices[0], slice(7, 23)), whole[7:23])

    def test_rgb_only_detector_keeps_missing_and_rejects_sensor_border(self):
        rgb = np.full((200, 300, 3), 160, np.uint8)
        mask, info = detect_region(rgb, 'dark')
        self.assertEqual(info['status'], 'MISSING_RGB_FEATURE')
        self.assertFalse(mask.any())
        cv2.rectangle(rgb, (140, 75), (146, 125), (80, 80, 80), -1)
        mask, info = detect_region(rgb, 'dark')
        self.assertEqual(info['status'], 'RGB_FEATURE_OBSERVED')
        np.testing.assert_allclose(info['centroid_uv'], [143, 100], atol=.01)
        rgb[:, :100] = 10
        mask, info = detect_region(rgb, 'dark')
        self.assertEqual(info['status'], 'RGB_FEATURE_OBSERVED')
        self.assertLess(int(mask.sum()), 1000)

    def test_rgb_only_detector_rejects_ambiguous_identical_regions(self):
        rgb = np.full((200, 300, 3), 160, np.uint8)
        cv2.rectangle(rgb, (110, 75), (116, 125), (80, 80, 80), -1)
        cv2.rectangle(rgb, (180, 75), (186, 125), (80, 80, 80), -1)
        mask, info = detect_region(rgb, 'dark')
        self.assertEqual(info['status'], 'AMBIGUOUS_RGB_FEATURE')
        self.assertFalse(mask.any())

    def test_missing_background_fallback_recovers_feature_on_rgb_illumination_slope(self):
        # The global median falsely connects this dark feature to the left
        # boundary. The local RGB background recovers the isolated region.
        gray = np.linspace(80, 170, 600)[None].repeat(400, axis=0).astype(np.uint8)
        gray[170:230, 190:250] -= 50
        mask, info = detect_region(np.repeat(gray[..., None], 3, axis=2), 'dark')
        self.assertEqual(info['status'], 'RGB_FEATURE_OBSERVED')
        self.assertEqual(info['background_method'], 'fixed local RGB median after missing global component')
        np.testing.assert_allclose(info['centroid_uv'], [219.5, 199.5], atol=2.)
        self.assertTrue(mask[200, 220])


if __name__ == '__main__':
    unittest.main()
