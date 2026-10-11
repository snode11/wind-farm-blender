"""Rejected image fits cannot become observed motion or physical training data."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from test_startup_recon import make_case, recon, run_traced


def state(psi, flap=3.0, pitch=0.0):
    return np.array([psi] + [pitch] * 3 + [flap] * 3 + [0.0] * 6)


class RejectedFrameTests(unittest.TestCase):
    def controlled_run(self, times, results, visible=None, hidden_after=None):
        """Exercise main's state transitions with prescribed solver outcomes."""
        visible = [True] * len(times) if visible is None else visible
        solves, searches, updates, observed_inputs = [], [], [], []
        real_update = recon.PhysPrior.update

        def search(fitter, obs, lo=0.0, hi=120.0, **kwargs):
            searches.append((lo, hi))
            fitter.phase_search = {'lo_deg': lo, 'hi_deg': hi}
            return 80.0 if kwargs.get('state') is None else kwargs['state'][0]

        def solve(fitter, obs, x0, x_pred, **kwargs):
            index = len(solves)
            solves.append({'x0': x0.copy(), 'prediction': x_pred.copy(), **kwargs})
            value, success, valid = results[index]
            value = x0.copy() if value is None else value.copy()
            result = SimpleNamespace(
                success=success, reconstruction_valid=valid,
                reconstruction_status='prior_assisted', observation_rank=1 if valid else 0,
                observation_camera_count=int(visible[index]), posterior_std=np.ones(recon.N_STATE),
                status=1 if success else 0, message='controlled solver outcome', optimality=0.0,
                grad=np.zeros(recon.N_STATE), coordinate_reference=np.zeros(recon.N_STATE),
                cost=1.0, nfev=1)
            return value, np.ones(recon.N_STATE), result

        def observed(fitter, value, obs):
            observed_inputs.append(value.copy())
            return np.full((3, fitter.S), any(o.has for o in obs), bool)

        def update(phys, value, seen, xi):
            updates.append(value.copy())
            real_update(phys, value, seen, xi)

        def in_frame(fitter, value):
            counts = np.full(3, fitter.S)
            if hidden_after is not None and len(solves) >= hidden_after:
                counts[0] = 0
            return counts, counts.copy()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_case(root / 'input', list(enumerate(times)), visible)
            with patch.object(recon, 'init_psi', search), \
                    patch.object(recon.Fitter, 'solve', solve), \
                    patch.object(recon.Fitter, 'observed_sections', observed), \
                    patch.object(recon.Fitter, 'in_frame', in_frame), \
                    patch.object(recon.PhysPrior, 'update', update), \
                    patch.object(sys, 'argv', [recon.__file__, '--data', str(root / 'input'),
                                               '--out', str(root / 'out')]), \
                    contextlib.redirect_stdout(io.StringIO()):
                recon.main()
            payload = json.loads((root / 'out' / 'recon.json').read_text())
        return payload['frames'], solves, searches, updates, observed_inputs

    def test_real_budget_exhaustion_does_not_initialize_observation_history(self):
        updates = []
        real_update = recon.PhysPrior.update

        def update(phys, value, seen, xi):
            updates.append(value.copy())
            real_update(phys, value, seen, xi)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_case(root / 'input', [(0, 0.0), (1, 0.1)], [True, True])
            with patch.object(recon.PhysPrior, 'update', update):
                payload, trace = run_traced(recon, root / 'input', root / 'out',
                                           ('--initial-max-nfev', '1'))
        self.assertTrue(all(not row['reconstruction_valid'] for row in payload['frames']))
        self.assertTrue(all(not row['solver']['success'] for row in payload['frames']))
        self.assertTrue(all(not row['image_initialized'] for row in payload['frames']))
        self.assertTrue(all(row['rpm_status'] == 'not_initialized' for row in payload['frames']))
        self.assertTrue(all(row['rpm_observation_dt'] is None for row in payload['frames']))
        self.assertTrue(all(not np.any(row['observed_sections']) for row in payload['frames']))
        self.assertEqual(updates, [])
        self.assertEqual([row['max_nfev'] for row in trace['solves']], [1, 1])

    def test_failed_second_image_keeps_bootstrap_and_full_observation_interval(self):
        rows, solves, searches, updates, _ = self.controlled_run(
            [0.0, 0.2, 0.8], [(state(80), True, True), (state(95, 8), False, False),
                              (state(84.8), True, True)])
        self.assertEqual(rows[1]['rpm_status'], 'not_initialized')
        self.assertIsNone(rows[1]['rpm_observation_dt'])
        self.assertAlmostEqual(rows[2]['rpm_observation_dt'], 0.8)
        self.assertAlmostEqual(rows[2]['rpm'], 1.0)
        self.assertEqual(searches[-1], (-16.0, 176.0))
        self.assertEqual([call['max_nfev'] for call in solves], [150, 150, 150])
        self.assertEqual(len(updates), 2)

    def test_failed_tracking_fit_is_only_a_candidate_and_does_not_train_priors(self):
        rows, solves, _, updates, observed = self.controlled_run(
            [0.0, 1.0, 1.4, 1.5, 2.5],
            [(state(80, 4, 2), True, True), (state(86, 4, 2), True, True),
             (state(110, 8, 7), False, False), (None, True, False),
             (state(95, 4, 2), True, True)],
            visible=[True, True, True, False, True], hidden_after=4)
        self.assertAlmostEqual(rows[2]['rpm'], 1.0)
        self.assertIsNone(rows[2]['rpm_observation_dt'])
        self.assertFalse(np.any(rows[2]['observed_sections']))
        self.assertAlmostEqual(rows[3]['state'][0], 89.0)
        self.assertAlmostEqual(rows[4]['rpm_observation_dt'], 1.5)
        self.assertAlmostEqual(rows[4]['rpm'], 1.0)
        self.assertAlmostEqual(solves[4]['prediction'][0], 95.0)
        self.assertAlmostEqual(solves[4]['x0'][0], 116.6)
        self.assertAlmostEqual(solves[4]['prediction'][1], 2.0)
        self.assertAlmostEqual(solves[4]['prediction'][4], 4.0)
        self.assertFalse(solves[4]['active'][4])
        self.assertEqual(len(updates), 3)
        self.assertEqual(len(observed), 3)

    def test_success_without_identifiable_image_does_not_initialize(self):
        rows, _, _, updates, _ = self.controlled_run(
            [0.0, 0.1], [(state(60, 8), True, False), (state(80), True, True)])
        self.assertFalse(rows[0]['image_initialized'])
        self.assertTrue(rows[1]['image_initialized'])
        self.assertEqual(rows[1]['rpm_status'], 'not_initialized')
        self.assertEqual(len(updates), 1)

    def test_nonfinite_candidate_is_rejected_and_recovery_remains_finite(self):
        for nonfinite in (float('nan'), float('inf')):
            with self.subTest(nonfinite=nonfinite):
                bad = state(90); bad[4] = nonfinite
                rows, solves, _, updates, observed = self.controlled_run(
                    [0.0, 1.0, 1.5, 2.0],
                    [(state(80), True, True), (state(86), True, True),
                     (bad, True, True), (state(92), True, True)])
                self.assertFalse(rows[2]['reconstruction_valid'])
                self.assertEqual(rows[2]['reconstruction_status'], 'nonfinite_solution')
                self.assertTrue(np.isfinite(rows[2]['state']).all())
                self.assertTrue(all(value is None for value in rows[2]['observation_std']))
                self.assertNotIn('std', rows[2])
                self.assertTrue(np.isfinite(solves[3]['x0']).all())
                self.assertAlmostEqual(rows[3]['rpm_observation_dt'], 1.0)
                self.assertAlmostEqual(rows[3]['rpm'], 1.0)
                self.assertEqual(len(updates), 3)
                self.assertTrue(all(np.isfinite(value).all() for value in observed))


if __name__ == '__main__':
    unittest.main(verbosity=2)
