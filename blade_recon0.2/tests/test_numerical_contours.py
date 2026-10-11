"""Regression cases for crop continuity, small edge derivatives and phase state."""
from decimal import Decimal, localcontext
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model import Rotor, TurbineConfig
from recon import Fitter, Obs, init_psi, robust


class NumericalContourTests(unittest.TestCase):
    def test_image_exit_is_continuous_and_observation_distance_stays_honest(self):
        mask = np.zeros((100, 100), np.uint8)
        mask[20:50, 20:50] = 255
        obs = Obs(mask)
        # A distant model edge crossing the image boundary used to drop a
        # nonzero DT residual to zero in one step. Crop is censored data.
        points = np.array([[-1e-4, 80.0], [0.0, 80.0], [1e-4, 80.0], [1e-5, 80.0]])
        edge = obs.model_residual(points)
        self.assertEqual(edge[0], 0.0)
        self.assertEqual(edge[1], 0.0)
        self.assertLess(abs(edge[2]), 1e-6)
        self.assertLess(abs(edge[3]), abs(edge[2]) / 50)
        # The fade applies only to the fitting objective. It cannot establish
        # observed sections, so raw image distance at the border remains large.
        self.assertGreater(obs.sample_dt(points[1:2])[0], 30.0)
        point = np.array([[10.0, 80.0]])
        np.testing.assert_array_equal(obs.model_residual(point), obs.sample_dt(point))

    def test_soft_l1_transform_retains_microresiduals_and_exact_objective(self):
        tiny = np.array([-1e-10, 1e-10])
        np.testing.assert_allclose(robust(tiny), tiny, rtol=1e-15, atol=0)
        values = np.array([-1000.0, -2.0, -1e-4, 0.0, 1e-4, 2.0, 1000.0])
        expected = []
        with localcontext() as context:
            context.prec = 50
            for value in values:
                r, f = Decimal(str(value)), Decimal('2')
                expected.append(float(2 * f * f * ((1 + (r / f) ** 2).sqrt() - 1)))
        np.testing.assert_allclose(robust(values) ** 2, expected, rtol=1e-12, atol=1e-25)

    def test_second_image_phase_search_uses_original_per_blade_state(self):
        class NarrowPhaseCost:
            def __init__(self):
                self.states = []
            def cost(self, state, obs):
                self.states.append(state.copy())
                return ((state[0] - 29.037) / 0.01) ** 2
        fitter = NarrowPhaseCost()
        previous = np.array([14.48, 1.2, 4.6, 0.2, 4.5, 2.8, 3.0, 0.18, 0.2, -0.13])
        frozen = previous.copy()
        result = init_psi(fitter, [], 28.8, 29.3, step=0.1, state=previous)
        self.assertLess(abs(result - 29.037), 0.006)
        for state in fitter.states:
            np.testing.assert_array_equal(state[1:], previous[1:])
        np.testing.assert_array_equal(previous, frozen)
        trace = fitter.phase_search
        self.assertEqual(trace['coarse_residual_calls'], 5)
        self.assertGreaterEqual(trace['fine_residual_calls'], 19)
        self.assertEqual(trace['state_source'], 'previous_per_blade_image_state')

    def test_solver_increment_tolerance_does_not_grow_with_accumulated_phase(self):
        class LinearImage(Fitter):
            def residuals(self, state, obs, target, weight):
                return np.r_[state - target, np.zeros(20)]
        fitter = LinearImage(Rotor(TurbineConfig(n_sections=8, n_ring=8)), [])
        results = []
        for phase in (17.3, 3_600_017.3):
            initial = np.array([phase] + [0.0] * 3 + [3.0] * 3 + [0.0] * 3)
            target = initial.copy(); target[0] += 0.05
            active = np.zeros(10, bool); active[0] = True
            state, _, solver = fitter.solve([], initial, target, active=active, max_nfev=20)
            self.assertTrue(solver.success)
            self.assertLess(abs(state[0] - target[0]), 1e-8)
            self.assertEqual(solver.x[0], state[0])
            results.append(state[0] - initial[0])
        self.assertLess(abs(results[0] - results[1]), 1e-8)


if __name__ == '__main__':
    unittest.main(verbosity=2)
