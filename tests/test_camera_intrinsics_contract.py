"""Pinhole projection and pixel-centre resizing use the complete intrinsic matrix."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np


class CameraIntrinsicTests(unittest.TestCase):
    def test_full_intrinsics_projection_inverse_and_resize(self):
        for directory in ('blade_recon', 'blade_recon0.2'):
            spec = importlib.util.spec_from_file_location('_camera_contract', Path(__file__).resolve().parents[1] / directory / 'camera.py')
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            for skew in (0., 10., -12.5):
                K = np.array([[100., skew, 50.], [3., 110., 40.], [0., 0., 1.]])
                camera = module.Camera('A', 100, 80, K, np.eye(4))
                points = np.array([[1., 2., 10.], [-2., 3., 8.]])
                uv, depth = camera.project(points)
                expected = points @ K.T
                np.testing.assert_allclose(uv, expected[:, :2] / expected[:, 2:])
                rays = np.c_[uv, np.ones(2)] @ np.linalg.inv(K).T
                np.testing.assert_allclose(rays * depth[:, None], points)
                scaled = camera.scaled(.63)
                sx, sy = scaled.W / camera.W, scaled.H / camera.H
                resized, _ = scaled.project(points)
                np.testing.assert_allclose(resized, (uv + .5) * [sx, sy] - .5)
                self.assertAlmostEqual(scaled.K[0, 1], skew * sx)
                if skew == 0:
                    np.testing.assert_allclose(uv[:, 0], 100 * points[:, 0] / depth + 50)
