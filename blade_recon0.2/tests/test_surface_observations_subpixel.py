"""Counterexamples for frozen RGB subpixel observation and camera-ray binding."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera import Camera, build_rig
from model import Rotor, TurbineConfig
from raster import rasterize
from surface_observations import project_binding
from surface_observations_subpixel import bind_points, refine


class SubpixelSurfaceObservations(unittest.TestCase):
    def test_half_contrast_boundary_survives_multiplicative_rgb_lighting(self):
        h, w = 384, 512
        yy, xx = np.mgrid[:h, :w]
        core = np.zeros((h, w), bool)
        core[160:220, 210:310] = True
        boundaries = []
        for background in (110 + 20 * xx / w + 5 * yy / h,
                           140 + 30 * xx / w + 10 * yy / h):
            gray = np.where(core, background * .6, background)
            rgb = np.repeat(np.clip(gray, 0, 255).astype(np.uint8)[..., None], 3, 2)
            _, detail = refine(rgb, core, {'polarity': 'dark', 'status': 'RGB_FEATURE_OBSERVED'})
            self.assertEqual(detail['v3_status'], 'REFINED_RGB_REGION')
            self.assertLess(abs(detail['local_relative_contrast'] - .4), .015)
            boundaries.append(np.asarray(detail['boundary_uv']))
        distances = np.r_[cKDTree(boundaries[0]).query(boundaries[1])[0],
                          cKDTree(boundaries[1]).query(boundaries[0])[0]]
        self.assertLess(np.percentile(distances, 95), .2)

    def test_exact_camera_ray_binding_preserves_subpixel_coordinates(self):
        rotor = Rotor(TurbineConfig(n_sections=8, n_ring=8))
        state = np.zeros(13)
        vertices, _ = rotor.forward(state)
        camera = build_rig(rotor, back_m=150, W=320, H=250,
                           hfov_deg=50, vfov_deg=40)[1]
        raster = rasterize(camera, vertices, rotor.tpl.faces())
        take = np.arange(len(raster.xs))[::max(1, len(raster.xs) // 30)]
        observed = np.column_stack([raster.xs[take] + .173, raster.ys[take] + .291])
        binding = bind_points(camera, rotor, state, observed)
        self.assertGreater(binding['bound_pixels'], 3)
        projected, _, _ = project_binding(camera, rotor, state, binding)
        self.assertLess(np.max(np.linalg.norm(projected - np.asarray(binding['anchor_uv']), axis=1)), 1e-8)

    def test_legal_rounded_extrinsic_uses_declared_inverse(self):
        rotor = Rotor(TurbineConfig(n_sections=8, n_ring=8))
        state = np.zeros(13)
        vertices, _ = rotor.forward(state)
        base = build_rig(rotor, back_m=150, W=320, H=250,
                         hfov_deg=50, vfov_deg=40)[1]
        transform = np.eye(4)
        transform[:3, :3] = base.R @ np.diag([1+5e-8, 1-5e-8, 1+8e-8])
        transform[:3, 3] = base.t
        camera = Camera(base.name, base.W, base.H, base.K, transform)
        raster = rasterize(camera, vertices, rotor.tpl.faces())
        take = np.arange(len(raster.xs))[::max(1, len(raster.xs)//30)]
        observed = np.column_stack([raster.xs[take]+.173, raster.ys[take]+.291])
        binding = bind_points(camera, rotor, state, observed)
        self.assertGreater(binding['bound_pixels'], 3)
        projected, _, _ = project_binding(camera, rotor, state, binding)
        np.testing.assert_allclose(projected, binding['anchor_uv'], atol=1e-8, rtol=0)

    def test_coincident_other_blade_does_not_create_unique_material_binding(self):
        rotor = Rotor(TurbineConfig(n_sections=8, n_ring=8))
        state = np.zeros(13)
        vertices, _ = rotor.forward(state)
        camera = build_rig(rotor, back_m=150, W=320, H=250,
                           hfov_deg=50, vfov_deg=40)[1]
        raster = rasterize(camera, vertices, rotor.tpl.faces())
        select = np.flatnonzero(raster.blade == 0)
        take = select[::max(1, len(select)//20)]
        observed = np.column_stack([raster.xs[take], raster.ys[take]])
        ambiguous = vertices.copy()
        ambiguous[1] = ambiguous[0]
        ambiguous[2] += [1000, 1000, 1000]
        fixture = SimpleNamespace(tpl=rotor.tpl, forward=lambda s: (ambiguous, None))
        result = bind_points(camera, fixture, state, observed)
        self.assertEqual(result['coverage'], 0.)
        self.assertEqual(result['boundary_pixels'], len(observed))
        self.assertEqual(result['ambiguous_other_blade_boundary_pixels'], len(observed))


if __name__ == '__main__':
    unittest.main()
