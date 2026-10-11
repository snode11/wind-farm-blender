"""Explicit optimization budgets preserve availability and default behavior."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_startup_recon import ALG, make_case, run_traced, recon


class SolverBudgetTests(unittest.TestCase):
    def test_nonpositive_budgets_are_rejected_before_input_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            for flag in ('--initial-max-nfev', '--tracking-max-nfev'):
                for value in ('0', '-1'):
                    with self.subTest(flag=flag, value=value):
                        run = subprocess.run([sys.executable, str(ALG / 'recon.py'),
                                              '--data', '/nonexistent-input', '--out', str(Path(tmp) / 'out'),
                                              flag, value], capture_output=True, text=True)
                        self.assertNotEqual(run.returncode, 0)
                        self.assertIn('must be >= 1', run.stderr)

    def test_real_main_uses_and_records_explicit_phase_budgets(self):
        persistent = os.environ.get('BLADE_RECON_BUDGET_EVIDENCE')
        if persistent:
            folder = Path(persistent); folder.mkdir(parents=True, exist_ok=False)
            temporary = contextlib.nullcontext(folder)
        else:
            temporary = tempfile.TemporaryDirectory()
        with temporary as tmp:
            root = Path(tmp)
            truth = make_case(root / 'input', [(7, 0.0), (9, 0.1), (12, 0.2), (16, 0.3)],
                              [False, True, True, True])
            payload, trace = run_traced(recon, root / 'input', root / 'result',
                                        ('--initial-max-nfev', '40', '--tracking-max-nfev', '25'))
        self.assertEqual([r['max_nfev'] for r in trace['solves']], [2, 40, 40, 25])
        self.assertEqual(payload['params'], {'initial_max_nfev': 40, 'tracking_max_nfev': 25,
                                            'no_observation_max_nfev': 2})
        self.assertFalse(payload['frames'][0]['reconstruction_valid'])
        for actual, target in zip(payload['frames'][1:], truth[1:]):
            self.assertTrue(actual['solver']['success'])
            self.assertLess(abs(actual['state'][0] - target['psi']), 1.0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
