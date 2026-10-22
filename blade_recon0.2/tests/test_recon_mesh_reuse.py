"""Regression coverage for residual-local geometry reuse and empty cameras."""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'blade_recon0.2'))
from camera import build_rig
from model import Rotor, TurbineConfig
from recon import Fitter, Obs, silhouettes
from synth import render


class CountedRotor(Rotor):
    def __init__(self, cfg):
        super().__init__(cfg); self.calls = 0

    def forward(self, state):
        self.calls += 1
        return super().forward(state)


class MeshReuseTests(unittest.TestCase):
    def setUp(self):
        self.rotor = CountedRotor(TurbineConfig(n_sections=8, n_ring=8))
        self.cams = build_rig(self.rotor, back_m=150, down_m=0, yaw_step_deg=10,
                             hfov_deg=60, vfov_deg=60, W=192, H=192)
        self.p = np.array([80.0] + [0.0] * 3 + [3.0] * 3 + [0.0] * 3)
        self.state = np.r_[self.p, np.zeros(3)]
        light = np.array([1.0, 0.2, 0.3]); light /= np.linalg.norm(light)
        self.obs = [Obs(render(c, self.rotor, self.state, self.rotor.tpl.faces(), light, noise=0)[1])
                    for c in self.cams]
        self.empty = [Obs(np.zeros((c.H, c.W), np.uint8)) for c in self.cams]
        self.fitter = Fitter(self.rotor, self.cams)
        self.rotor.calls = 0

    def test_forward_is_once_per_residual_with_any_image_and_zero_without(self):
        for obs, expected in ((self.obs, 1), ([self.empty[0], self.obs[1], self.empty[2]], 1),
                              (self.empty, 0)):
            with self.subTest(expected=expected, images=sum(bool(o.has) for o in obs)):
                self.rotor.calls = 0
                result = self.fitter.residuals(self.p, obs, self.p, 0.5)
                self.assertEqual(self.rotor.calls, expected)
                self.assertTrue(np.isfinite(result).all())

    def test_precomputed_silhouettes_are_bit_exact_and_do_not_mutate_mesh(self):
        for offset in (0, 36000):
            state = self.state.copy(); state[0] += offset
            geometry = self.rotor.forward(state)
            frozen = tuple(v.copy() for v in geometry)
            for camera in self.cams:
                direct = silhouettes(self.rotor, camera, state, with_rings=True)
                shared = silhouettes(self.rotor, camera, state, with_rings=True, geometry=geometry)
                for actual, expected in zip(shared, direct):
                    np.testing.assert_array_equal(actual, expected)
            for actual, expected in zip(geometry, frozen):
                np.testing.assert_array_equal(actual, expected)

    def test_geometry_is_fresh_for_each_parameter_evaluation(self):
        first = self.fitter.residuals(self.p, self.obs, self.p, 0.5)
        changed = self.p.copy(); changed[0] += 4.0
        second = self.fitter.residuals(changed, self.obs, self.p, 0.5)
        third = self.fitter.residuals(self.p, self.obs, self.p, 0.5)
        self.assertEqual(self.rotor.calls, 3)
        self.assertFalse(np.array_equal(first, second))
        np.testing.assert_array_equal(first, third)


if __name__ == '__main__':
    unittest.main(verbosity=2)
