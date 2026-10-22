"""Small real-geometry regressions for first-image initialization and timing.

The bundle contains RGB and masks rendered from a rotor. Truth is written in
a separate evaluation directory and is never passed to InputSequence/main.
Only tracing wrappers are patched: grid search and SciPy least_squares both
run their real implementations, without replacing results or active states.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ALG = ROOT / 'blade_recon0.2'
sys.path.insert(0, str(ALG))
import recon
from camera import build_rig
from model import Rotor, TurbineConfig
from synth import render


def make_case(root, frame_times, visible):
    """Known phase is outside the incorrect old +/-12 degree search."""
    root.mkdir(parents=True)
    cfg = TurbineConfig(n_sections=8, n_ring=8)
    rotor = Rotor(cfg)
    camera = build_rig(rotor, back_m=150, down_m=0, yaw_step_deg=0,
                       hfov_deg=60, vfov_deg=60, W=192, H=192)[1]
    (root / camera.name).mkdir()
    (root / 'masks' / camera.name).mkdir(parents=True)
    first_t = next(t for (_, t), has_image in zip(frame_times, visible) if has_image)
    rows, truths = [], []
    light = np.array([1.0, 0.2, 0.3]); light /= np.linalg.norm(light)
    for (frame, t), has_image in zip(frame_times, visible):
        state = np.array([80.0 + 6.0 * (t - first_t)] + [0.0] * 3
                         + [3.0] * 3 + [0.0] * 6)
        gray, mask = render(camera, rotor, state, rotor.tpl.faces(), light, noise=0)
        if not has_image:
            mask.fill(0); gray.fill(190)
        rgb = '%s/%06d.png' % (camera.name, frame)
        mask_path = 'masks/%s/%06d.png' % (camera.name, frame)
        cv2.imwrite(str(root / rgb), cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR))
        cv2.imwrite(str(root / mask_path), mask)
        cam = camera.to_dict(); cam['T_cv_from_model'] = cam.pop('T_cv_from_world')
        cam.update(rgb=rgb, mask=mask_path)
        rows.append({'frame': frame, 't': t, 'cameras': [cam]})
        truths.append({'frame': frame, 't': t, 'has_image': bool(has_image), 'psi': float(state[0])})
    (root / 'input_bundle.json').write_text(json.dumps({
        'schema': 'blade-surface-input.v1', 'extrinsic_from_frame': 'model',
        'pixel_coordinates': 'original_image_pixel_centers', 'color_order': 'RGB',
        'turbine': cfg.to_dict(), 'frames': rows}, indent=2), encoding='utf-8')
    evaluation = root.parent / (root.name + '-evaluation'); evaluation.mkdir()
    (evaluation / 'truth.json').write_text(json.dumps(truths, indent=2), encoding='utf-8')
    return truths


def run_traced(module, source, output, extra_arguments=()):
    coarse, solves = [], []
    real_init, real_solve = module.init_psi, module.Fitter.solve

    def trace_init(fitter, obs, *args, **kwargs):
        lo = args[0] if len(args) else kwargs.get('lo', 0.0)
        hi = args[1] if len(args) > 1 else kwargs.get('hi', 120.0)
        psi = real_init(fitter, obs, *args, **kwargs)
        coarse.append({'lo': float(lo), 'hi': float(hi), 'psi': float(psi)})
        return psi

    def trace_solve(fitter, obs, x0, *args, **kwargs):
        result = real_solve(fitter, obs, x0, *args, **kwargs)
        x, std, solver = result
        solves.append({'has_image': any(bool(o.has) for o in obs),
                       'x0_psi': float(x0[0]), 'fitted_psi': float(x[0]),
                       'max_nfev': kwargs.get('max_nfev', 60),
                       'observation_rank': solver.observation_rank,
                       'solver_success': bool(solver.success), 'nfev': int(solver.nfev)})
        return result

    stream = io.StringIO(); start = time.perf_counter()
    with patch.object(module, 'init_psi', trace_init), \
            patch.object(module.Fitter, 'solve', trace_solve), \
            patch.object(sys, 'argv', [module.__file__, '--data', str(source), '--out', str(output),
                                       *extra_arguments]), \
            contextlib.redirect_stdout(stream):
        module.main()
    elapsed = time.perf_counter() - start
    payload = json.loads((output / 'recon.json').read_text())
    evidence = {'method': 'real rendered RGB/masks, real InputSequence, real grid search and SciPy least_squares',
                'source': str(module.__file__),
                'source_sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
                'elapsed_s': elapsed, 'coarse_searches': coarse, 'solves': solves,
                'input_sha256': {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in source.rglob('*') if p.is_file()}}
    (output / 'trace.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    (output / 'run.log').write_text(stream.getvalue(), encoding='utf-8')
    return payload, evidence


class StartupMainTests(unittest.TestCase):
    def run_case(self, name, frame_times, visible):
        persistent = os.environ.get('BLADE_RECON_STARTUP_EVIDENCE')
        if persistent:
            directory = Path(persistent) / name
            directory.mkdir(parents=True, exist_ok=False)
            cleanup = contextlib.nullcontext(directory)
        else:
            cleanup = tempfile.TemporaryDirectory()
        with cleanup as tmp:
            directory = Path(tmp)
            truth = make_case(directory / 'input', frame_times, visible)
            payload, evidence = run_traced(recon, directory / 'input', directory / 'result')
        rows = payload['frames']
        self.assertEqual([(r['frame'], r['t']) for r in rows], list(frame_times))
        self.assertEqual(len(evidence['solves']), len(rows))
        for row, target in zip(rows, truth):
            if target['has_image']:
                # Numeric recovery of a rendered known angle; termination alone
                # is insufficient and image nullspace directions remain honest.
                self.assertLess(abs(row['state'][0] - target['psi']), 1.0)
                self.assertGreater(row['observation_rank'], 0)
                self.assertTrue(row['solver']['success'])
            else:
                self.assertFalse(row['reconstruction_valid'])
                self.assertEqual(row['observation_camera_count'], 0)
                self.assertEqual(row['observation_rank'], 0)
                self.assertTrue(all(v is None for v in row['observation_std']))
                self.assertTrue(all(v == 'unbounded_or_unobserved' for v in row['uncertainty_status'][:10]))
                self.assertNotIn('std', row)
        first_index = visible.index(True)
        first = rows[first_index]
        self.assertEqual([evidence['coarse_searches'][0][v] for v in ('lo', 'hi')], [0.0, 120.0])
        self.assertGreater(evidence['solves'][first_index]['x0_psi'], 70.0)
        self.assertEqual(first['rpm_status'], 'not_initialized')
        self.assertIsNone(first['rpm_observation_dt'])
        for row in rows[:first_index]:
            self.assertFalse(row['image_initialized'])
            self.assertEqual(row['rpm_status'], 'not_initialized')
        return rows, evidence

    def assert_bootstrap(self, rows, evidence, first_index, second_index):
        a, b = rows[first_index], rows[second_index]
        dt = b['t'] - a['t']
        self.assertAlmostEqual(b['rpm_observation_dt'], dt)
        self.assertEqual(b['rpm_status'], 'image_estimated')
        self.assertAlmostEqual(b['rpm'], abs(b['state'][0] - a['state'][0]) / dt / 6.0)
        call = evidence['coarse_searches'][1]
        self.assertAlmostEqual(call['lo'], a['state'][0] - 120.0 * dt)
        self.assertAlmostEqual(call['hi'], a['state'][0] + 120.0 * dt)
        self.assertLess(abs(b['rpm'] - 1.0), 0.25)

    def test_first_frame_visible_uses_global_init_and_real_fit(self):
        rows, evidence = self.run_case('first-visible', [(0, 0.0), (1, 0.1)], [True, True])
        self.assert_bootstrap(rows, evidence, 0, 1)

    def test_leading_empty_then_two_visible_uses_global_init(self):
        rows, evidence = self.run_case('leading-empty', [(0, 0.0), (1, 0.1), (2, 0.2),
                                                       (3, 0.3), (4, 0.4)],
                                       [False, False, False, True, True])
        self.assertEqual([r['reconstruction_status'] for r in rows[:3]],
                         ['no_observations', 'prior_prediction', 'prior_prediction'])
        self.assert_bootstrap(rows, evidence, 3, 4)

    def test_sparse_unequal_intervals_estimate_speed_between_observed_states(self):
        times = [(3, 0.0), (8, 0.04), (20, 0.11), (27, 0.16),
                 (44, 0.43), (57, 0.54), (79, 0.91)]
        rows, evidence = self.run_case('sparse-unequal', times,
                                      [False, False, True, False, True, False, True])
        self.assert_bootstrap(rows, evidence, 2, 4)
        self.assertEqual(len(evidence['coarse_searches']), 2)
        a, b = rows[4], rows[6]
        dt = b['t'] - a['t']
        self.assertAlmostEqual(b['rpm_observation_dt'], dt)
        expected = 0.8 * a['rpm'] + 0.2 * (b['state'][0] - a['state'][0]) / dt / 6.0
        self.assertAlmostEqual(b['rpm'], expected)
        # The intermediate no-image frame extrapolates solely for display.
        self.assertEqual(rows[5]['reconstruction_status'], 'prior_prediction')
        self.assertIsNone(rows[5]['rpm_observation_dt'])
        self.assertAlmostEqual(rows[5]['rpm'], a['rpm'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
