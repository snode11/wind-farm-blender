"""Run the delivered blade_recon solver on timestamped moving-camera inputs.

The optimization flow is copied from the delivered recon.py main function.
Only input loading, per-frame camera calibration, timestamps, and output
diagnostics are adapted. MAPPO geometry and evaluation artifacts are never read.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time

ALGORITHM_ROOT = Path('/Users/eason/Desktop/wfcrl/blade_recon')
CAMERA_NAMES = ['T1Down', 'NacelleT1']
sys.path.insert(0, str(ALGORITHM_ROOT))

import cv2
import numpy as np

import camera
import model
import recon
from camera import Camera
from model import Rotor, TurbineConfig, N_STATE, STATE_NAMES, load_json, save_json
from recon import Fitter, Obs, PhysPrior, init_psi, segment, silhouettes


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_atomic(path, data):
    """Keep a complete reviewable JSON file if the process is interrupted."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    save_json(str(temporary), data)
    temporary.replace(path)


def validate_records(records, input_dir):
    previous_t = None
    for record in records:
        t = float(record['t'])
        if not math.isfinite(t) or (previous_t is not None and t <= previous_t):
            raise ValueError('calibration frames require finite, strictly increasing t')
        previous_t = t
        if [c['name'] for c in record['cameras']] != CAMERA_NAMES:
            raise ValueError('this experiment requires exactly [T1Down, NacelleT1] in that order')
        frame = int(record['frame'])
        for name in CAMERA_NAMES:
            for path in (input_dir / name / ('%06d.png' % frame),
                         input_dir / 'masks' / name / ('%06d.png' % frame)):
                if not path.is_file():
                    raise FileNotFoundError(str(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--max-frames', type=int, default=0)
    ap.add_argument('--fit-scale', type=float, default=1.0)
    ap.add_argument('--overlay', action='store_true')
    args = ap.parse_args()
    if not math.isfinite(args.fit_scale) or args.fit_scale <= 0:
        ap.error('--fit-scale must be finite and positive')
    if args.max_frames < 0:
        ap.error('--max-frames must be nonnegative')
    input_dir, out_dir = Path(args.input).resolve(), Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    calibration_path = input_dir / 'calibration.json'
    meta = load_json(str(calibration_path))
    records = meta['frames'][:args.max_frames or None]
    if not records:
        raise ValueError('no calibration frames')
    validate_records(records, input_dir)
    cfg = TurbineConfig.from_dict(meta.get('turbine'))
    fps = float(meta.get('fps', 10.0))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('fps must be finite and positive')
    rotor = Rotor(cfg)
    cams_full = [Camera.from_dict(d) for d in records[0]['cameras']]
    cams = [c.scaled(args.fit_scale) for c in cams_full] if args.fit_scale != 1.0 else cams_full
    if any(c.W <= 0 or c.H <= 0 for c in cams):
        raise ValueError('fit scale produced an empty image')
    fitter = Fitter(rotor, cams)
    writers = None
    frames, x_prev, omega = [], None, None
    phys = PhysPrior()
    last_obs = np.array([np.nan] + [0.0] * 3 + [np.nan] * 3 + [0.0] * 3)
    last_obs[4:7] = 3.0
    t0 = time.time()
    previous_t = None
    rpm_max = 20.0  # Unmodified default from the delivered recon.py.
    manifest = {
        'status': 'RUNNING', 'algorithm_root': str(ALGORITHM_ROOT),
        'algorithm_sources': {name: {'path': str(Path(module.__file__).resolve()),
                                    'sha256': sha256(module.__file__)}
                              for name, module in [('recon.py', recon), ('model.py', model),
                                                   ('camera.py', camera)]},
        'wrapper': {'path': str(Path(__file__).resolve()), 'sha256': sha256(__file__)},
        'input': str(input_dir), 'calibration_sha256': sha256(calibration_path),
        'expected_frames': len(records), 'fit_scale': args.fit_scale,
        'input_camera_count': 2, 'camera_names': CAMERA_NAMES,
        'original_solver_parameters': {'rpm_max': rpm_max, 'first_max_nfev': 150,
                                       'second_max_nfev': 150, 'later_max_nfev': 60,
                                       'w_prior_first': 0.0, 'w_prior_second': 0.2,
                                       'w_prior_later': 0.5},
        'adaptations': [
            'Required external PNG masks; no automatic segmentation fallback',
            'Camera instantiated from each frame calibration; transform already maps MODEL to CV',
            'dt from successive relative timestamps; first-frame dt is 1/fps',
            'Original optimizer, initialization, PhysPrior, last_obs and observability gating retained',
            'Additional solver diagnostics and incremental progress output',
        ],
        'forbidden_input_sources': 'No evaluation artifacts, MAPPO geometry or MAPPO states read',
    }
    save_atomic(out_dir / 'solver_manifest.json', manifest)
    output = {
        'turbine': cfg.to_dict(), 'fps': fps, 'state_names': STATE_NAMES,
        'note': 'MAPPO T1 Down + nacelle two-camera image reconstruction. observed_sections=1 means original '
                'silhouette proximity criterion; not full circumference visibility or depth validation.',
        'source': {'kind': 'MAPPO_T1_TWO_CAMERA_IMAGE_EXPERIMENT', 'input': str(input_dir),
                   'camera_names': CAMERA_NAMES, 'input_camera_count': 2,
                   'calibration_sha256': manifest['calibration_sha256'],
                   'coordinate_frame': meta.get('coordinate_frame'),
                   'dynamic_extrinsics': True, 'fit_scale': args.fit_scale},
        'run_status': 'RUNNING', 'frames': frames,
    }
    try:
        with (out_dir / 'progress.jsonl').open('w', encoding='utf-8') as progress:
            for k, record in enumerate(records):
                frame_started = time.time()
                frame_id, frame_t = int(record['frame']), float(record['t'])
                cams_full = [Camera.from_dict(d) for d in record['cameras']]
                cams = [c.scaled(args.fit_scale) for c in cams_full] if args.fit_scale != 1.0 else cams_full
                fitter.cams = cams
                imgs, obs, mask_diagnostics = [], [], []
                for c, cf in zip(cams, cams_full):
                    image_path = input_dir / cf.name / ('%06d.png' % frame_id)
                    mask_path = input_dir / 'masks' / cf.name / ('%06d.png' % frame_id)
                    rgb = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                    mask_image = cv2.imread(str(mask_path), cv2.IMREAD_GRAYSCALE)
                    if rgb is None or mask_image is None:
                        raise ValueError('unreadable RGB or mask for frame %d' % frame_id)
                    if rgb.shape[:2] != (cf.H, cf.W) or mask_image.shape != (cf.H, cf.W):
                        raise ValueError('image, mask and calibration dimensions disagree at frame %d' % frame_id)
                    imgs.append(rgb)
                    gray = cv2.cvtColor(rgb, cv2.COLOR_BGR2GRAY)
                    m = segment(gray, str(mask_path))
                    full_mask_pixels = int(np.count_nonzero(m))
                    if args.fit_scale != 1.0:
                        m = cv2.resize(m, (c.W, c.H), interpolation=cv2.INTER_NEAREST)
                    observation = Obs(m)
                    obs.append(observation)
                    mask_diagnostics.append({'camera': cf.name, 'mask_pixels': full_mask_pixels,
                                             'fit_mask_pixels': int(np.count_nonzero(m)),
                                             'mask_fraction': full_mask_pixels / float(cf.W * cf.H),
                                             'obs_points': len(observation.pts),
                                             'has_edge': bool(observation.has),
                                             'rgb_sha256': sha256(image_path),
                                             'mask_sha256': sha256(mask_path)})

                dt = 1.0 / fps if previous_t is None else frame_t - previous_t
                active = np.ones(10, bool)
                if x_prev is None:
                    psi0 = init_psi(fitter, obs)
                    x0 = np.array([psi0] + [0.0] * 3 + [3.0] * 3 + [0.0] * 3)
                    x, std, res = fitter.solve(obs, x0, x0, w_prior=0.0, max_nfev=150)
                elif omega is None:
                    span = rpm_max * 6.0 * dt
                    psi1 = init_psi(fitter, obs, x_prev[0] - span, x_prev[0] + span,
                                    step=max(0.5, span / 40), pitch=float(np.mean(x_prev[1:4])),
                                    flap=float(np.mean(x_prev[4:7])))
                    x0 = x_prev.copy(); x0[0] = psi1
                    x, std, res = fitter.solve(obs, x0, x0, w_prior=0.2, max_nfev=150)
                    omega = (x[0] - x_prev[0]) / dt
                else:
                    xp = x_prev.copy(); xp[0] += omega * dt
                    phys.apply(fitter, xp[0])
                    n_all, n_out = fitter.in_frame(xp)
                    active = np.ones(10, bool)
                    ready = phys.flap.n_upd > 6
                    for b in range(3):
                        if n_out[b] < 3:
                            active[[4 + b, 7 + b]] = False
                            xp[4 + b] = fitter.mu[4 + b] if ready else last_obs[4 + b]
                            xp[7 + b] = fitter.mu[7 + b] if ready else last_obs[7 + b]
                        if n_all[b] < 3:
                            active[1 + b] = False
                            xp[1 + b] = fitter.mu[1 + b] if ready else last_obs[1 + b]
                    x, std, res = fitter.solve(obs, xp, xp, w_prior=0.5, active=active)
                    omega = 0.8 * omega + 0.2 * (x[0] - x_prev[0]) / dt
                seen = fitter.observed_sections(x, obs)
                phys.update(x, seen, rotor.tpl.xi)
                outer = rotor.tpl.xi > 0.4
                for b in range(3):
                    if seen[b, outer].mean() >= 0.15:
                        last_obs[[4 + b, 7 + b]] = x[[4 + b, 7 + b]]
                    if seen[b].mean() >= 0.15:
                        last_obs[1 + b] = x[1 + b]
                if not np.isfinite(last_obs[4:7]).all() and np.isfinite(last_obs[4:7]).any():
                    for g in (1, 4, 7):
                        blk = last_obs[g:g + 3]
                        blk[~np.isfinite(blk)] = np.nanmean(blk)
                st = fitter.full(x)
                n_all_fit, n_out_fit = fitter.in_frame(x)
                empty_obs = not any(o.has for o in obs)
                diagnostics = {
                    'status': int(res.status), 'success': bool(res.success),
                    'optimality': float(res.optimality), 'message': str(res.message),
                    'empty_obs': empty_obs, 'active': active.astype(int).tolist(),
                    'obs_points': sum(len(o.pts) for o in obs),
                    'mask_pixels': sum(d['mask_pixels'] for d in mask_diagnostics),
                    'cameras': mask_diagnostics, 'in_frame_sections': n_all_fit.tolist(),
                    'in_frame_outer_sections': n_out_fit.tolist(),
                    'observed_sections_count': seen.sum(1).astype(int).tolist(),
                    'prior_updates': {'flap': phys.flap.n_upd, 'edge': phys.edge.n_upd,
                                      'pitch': phys.pitch.n_upd},
                    'elapsed_s': time.time() - frame_started,
                    'diagnostic_status': 'EMPTY_OBSERVATION_PRIOR_ONLY' if empty_obs else
                                         ('SOLVER_TERMINATED' if res.success else 'SOLVER_NOT_CONVERGED'),
                }
                frames.append({
                    'frame': frame_id, 't': frame_t, 'sim_t': float(record['sim_t']),
                    'blender_frame': int(record['blender_frame']), 'dt': dt,
                    'T_world_from_model': record['T_world_from_model'],
                    'state': st.tolist(),
                    'std': np.concatenate([std, np.zeros(N_STATE - len(std))]).tolist(),
                    'rpm': abs(omega or 0.0) / 6.0, 'cost': float(res.cost), 'nfev': int(res.nfev),
                    'observed_sections': seen.astype(int).tolist(),
                    **diagnostics,
                })
                x_prev, previous_t = x, frame_t
                progress.write(json.dumps({'frame': frame_id, 't': frame_t, 'sim_t': record['sim_t'],
                                           'cost': float(res.cost), 'nfev': int(res.nfev),
                                           'rpm': abs(omega or 0.0) / 6.0, **diagnostics}) + '\n')
                progress.flush()
                if args.overlay:
                    if writers is None:
                        writers = [cv2.VideoWriter(str(out_dir / ('overlay_%s.mp4' % c.name)),
                                                  cv2.VideoWriter_fourcc(*'mp4v'), fps, (c.W, c.H))
                                   for c in cams_full]
                        if not all(w.isOpened() for w in writers):
                            raise RuntimeError('overlay video writer failed to open')
                    for c, img, writer in zip(cams_full, imgs, writers):
                        vis = img.copy()
                        sil = silhouettes(rotor, c, st)
                        for b, col in enumerate([(0, 0, 255), (0, 200, 0), (255, 120, 0)]):
                            for side in range(2):
                                pts = sil[b, :, side]
                                okp = np.isfinite(pts).all(1)
                                if okp.sum() > 1:
                                    cv2.polylines(vis, [np.round(pts[okp]).astype(np.int32)], False, col, 1)
                        cv2.putText(vis, 'frame %d t %.3f sim %.3f' % (frame_id, frame_t, record['sim_t']),
                                    (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
                        writer.write(vis)
                if k % 10 == 0:
                    save_atomic(out_dir / 'recon.partial.json', output)
                    print('frame %4d t=%7.3f psi=%7.2f rpm=%.2f success=%s empty=%s (%.2fs/frame)'
                          % (frame_id, frame_t, x[0] % 360, (omega or 0) / 6.0,
                             res.success, empty_obs, (time.time() - t0) / (k + 1)), flush=True)
        output['run_status'] = 'COMPLETED'
        manifest['status'] = 'COMPLETED'
    except Exception as exc:
        output['run_status'] = 'FAILED'
        output['failure'] = {'type': type(exc).__name__, 'message': str(exc)}
        manifest['status'] = 'FAILED'
        manifest['failure'] = output['failure']
        raise
    finally:
        if writers:
            for writer in writers:
                writer.release()
        manifest['completed_frames'] = len(frames)
        manifest['elapsed_s'] = time.time() - t0
        save_atomic(out_dir / 'recon.json', output)
        save_atomic(out_dir / 'solver_manifest.json', manifest)
    print('completed %d frames, %.2f s/frame -> %s'
          % (len(frames), (time.time() - t0) / max(len(frames), 1), out_dir / 'recon.json'), flush=True)


if __name__ == '__main__':
    main()
