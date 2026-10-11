"""A state at a bound must not falsely terminate at its unchanged cost.

These reproduce the zero-increment TRF radius failure for a bounded first
active direction, including the explicit legacy 10D API contract.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import recon


class PhysicalQuadratic(recon.Fitter):
    def residuals(self, p, obs, x_pred, w_prior):
        # One physical least-squares optimum, with empty prior rows matching
        # the Fitter image-information contract. The old zero-origin solve
        # spuriously stopped at cost0.5 after a roughly1e-10 state update.
        return np.concatenate([np.asarray(p) - self.target,
                               np.zeros(2 * len(p))])


class BoundOriginTests(unittest.TestCase):
    def check_physical_optimum(self, n_state, active_index):
        fitter = PhysicalQuadratic(SimpleNamespace(tpl=SimpleNamespace(r=[0., 1.])), [])
        x0 = recon.PRIOR_MU[:n_state].copy()
        x0[active_index] = recon.LB[active_index]
        fitter.target = x0.copy()
        fitter.target[active_index] += 1.
        active = np.zeros(n_state, dtype=bool)
        active[active_index] = True
        x, std, result = fitter.solve([], x0, x0, w_prior=0., max_nfev=150, active=active)
        self.assertTrue(result.success)
        self.assertLess(result.cost, 1e-12)
        self.assertGreater(np.linalg.norm(x - x0), .9)
        np.testing.assert_allclose(x, fitter.target, atol=1e-6, rtol=0.)
        np.testing.assert_allclose(result.x, x[active], atol=1e-12, rtol=0.)
        np.testing.assert_array_equal(x[~active], x0[~active])
        self.assertEqual(result.observation_rank, 1)
        self.assertTrue(np.isfinite(std[active]).all())
        self.assertTrue(np.isnan(std[~active]).all())

    def test_first_active_bounded_pitch_in_legacy10(self):
        self.check_physical_optimum(10, 3)

    def test_first_active_bounded_flap_in_full13(self):
        self.check_physical_optimum(13, 6)


if __name__ == '__main__':
    unittest.main()
