#!/usr/bin/env python3
"""Bounded, truth-isolated DY1 candidate sensitivity; not a dynamic fit."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write('\n')


def checked_observations(input_root, observation_root, native_root, selected):
    """Check the historical cache against signed cameras and freshly decoded RGB."""
    import numpy as np
    from PIL import Image
    from wfrl.nrel_reconstruction.dataset import load_bundle
    from wfrl.nrel_reconstruction.artifact_paths import relocated_path
    from wfrl.nrel_reconstruction.observations import segment_rgb

    data, calibration, moves = load_bundle(input_root)
    observations = json.loads((observation_root / 'observations.json').read_text())
    annotation_data = json.loads((observation_root / 'annotations.json').read_text())
    annotations = {(a['camera_id'], a['frame_id']): a for a in annotation_data['annotations']}
    expected = set(itertools.product(('C1', 'C2', 'C3'), selected))
    pairs = [(a['camera_id'], a['frame_id']) for a in observations]
    if (len(pairs) != len(set(pairs)) or set(pairs) != expected or set(annotations) != expected
            or len(annotation_data['annotations']) != len(annotations)):
        raise ValueError('Expected exactly the registered camera/frame observations')
    signatures = {str(input_root / name): digest(input_root / name)
                  for name in ['dataset.json', *data['files']]}
    for observation in observations:
        camera, frame = observation['camera_id'], observation['frame_id']
        key = camera, frame
        cal, motion, annotation = calibration[key], moves[frame], annotations[key]
        if (observation['sim_time_s'] != motion['sim_time_s']
                or annotation['sim_time_s'] != motion['sim_time_s']
                or annotation.get('truth_segmentation_used') is not False
                or annotation['source_video_sha256'] != data['files'][f'{camera}.mp4']):
            raise ValueError('Observation provenance mismatch')
        for name, truth in [('K', cal['K']), ('T_camera_cv_from_world', cal['T_camera_cv_from_world']),
                            ('T_world_from_blade_root', motion['T_world_from_blade_root'])]:
            if not np.array_equal(observation[name], truth):
                raise ValueError('Observation differs from allowed calibration/root pose')
        native_path = native_root / f'{camera}_{frame:06d}.png'
        with Image.open(native_path) as im:
            rgb = np.array(im.convert('RGB'))
        if rgb.shape != (cal['height'], cal['width'], 3):
            raise ValueError('Actual decoded native RGB dimensions mismatch')
        rgb_hash = hashlib.sha256(rgb.tobytes()).hexdigest()
        if rgb_hash != annotation['rgb_sha256']:
            raise ValueError('Historical annotation does not match actual MP4 RGB')
        for field in ('labels_path', 'contour_points_path', 'contour_valid_path'):
            path = relocated_path(observation[field])
            if path.parent != observation_root.resolve():
                raise ValueError('Observation escaped the allowed cache')
            observation[field] = str(path)
            signatures[str(path)] = digest(path)
        signatures[str(native_path)] = digest(native_path)
        source = annotation.get('trusted_contour', {})
        if source.get('source') != 'thresholded MP4 RGB component before FBU uncertainty band':
            raise ValueError('Unexpected contour origin')
        if source.get('point_correspondence') != 'none; per-frame silhouette only':
            raise ValueError('Unexpected correspondence claim')
        if observation['contour_uncertainty_px'] != 3. or source.get('uncertainty_native_px') != 3.:
            raise ValueError('Historical contour uncertainty changed')
        # Reproduction is allowed only for these already reviewed 12 images.
        # It does not extend the legacy segmenter to other passages.
        labels, _, points, valid = segment_rgb(rgb, camera, with_contours=True)
        with Image.open(observation['labels_path']) as im:
            if im.size != (cal['width'], cal['height']):
                raise ValueError('Native label dimensions mismatch')
            if not np.array_equal(np.asarray(im), labels):
                raise ValueError('FBU cache is not reproducible from the signed RGB')
        if not np.array_equal(np.load(observation['contour_points_path'], allow_pickle=False), points):
            raise ValueError('Contour cache is not reproducible from the signed RGB')
        with Image.open(observation['contour_valid_path']) as im:
            if not np.array_equal(np.asarray(im), valid.astype(np.uint8) * 255):
                raise ValueError('Contour domain is not reproducible from the signed RGB')
        observation['split'] = 'development_nonblind'
    for p in (observation_root / 'observations.json', observation_root / 'annotations.json'):
        signatures[str(p)] = digest(p)
    return observations, data, calibration, moves, signatures


def ambiguity_summary(rows, tolerance_px):
    """Finite-grid range, retaining missingness; never a confidence interval."""
    import numpy as np
    if not np.isfinite(tolerance_px) or tolerance_px < 0:
        raise ValueError('Invalid equivalence tolerance')
    valid = [r for r in rows if r['valid_image_terms'] and np.isfinite(r['native_mean_distance_px'])]
    if not valid:
        return {'status': 'UNAVAILABLE', 'reason': 'no valid candidate image terms', 'd_range_m': None}
    best = min(valid, key=lambda r: r['native_mean_distance_px'])
    equivalent = [r for r in valid if r['native_mean_distance_px'] <= best['native_mean_distance_px'] + tolerance_px]
    ds = [r['candidate_d_m'] for r in equivalent]
    return {'status': 'FINITE_GRID_DIAGNOSTIC_ONLY', 'best_q_m': best['q_m'],
            'best_native_mean_distance_px': best['native_mean_distance_px'],
            'equivalent_state_count': len(equivalent), 'equivalent_q_m': [r['q_m'] for r in equivalent],
            'd_range_m': [min(ds), max(ds)], 'd_span_m': max(ds) - min(ds),
            'search_boundary_best': any(abs(v) == .5 for v in best['q_m']),
            'not_a_confidence_interval': True}


def run(args):
    import numpy as np
    import torch
    from wfrl.nrel_reconstruction.dynamic_geometry import DynamicBladeReference, transform_points, plane_readout
    from wfrl.nrel_reconstruction.optimize import _prepare
    from wfrl.nrel_reconstruction.contour import projected_contour_loss

    start = time.monotonic()
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    protocol_path = Path(args.protocol).resolve()
    protocol = json.loads(protocol_path.read_text())
    if protocol['status'] != 'FROZEN_BEFORE_COMPUTATION' or protocol['frames'] != [42, 43, 44, 45]:
        raise ValueError('Unregistered DY1 protocol')
    binding_path = Path(args.binding).resolve()
    binding = json.loads(binding_path.read_text())
    if binding['protocol_sha256'] != digest(protocol_path):
        raise ValueError('Protocol changed after input binding')
    for name in ('input', 'observations', 'native', 'model', 'plane'):
        if str(Path(getattr(args, name)).resolve()) != binding['paths'][name]:
            raise ValueError('Input path differs from pre-computation binding: ' + name)
    for path, signature in binding['files'].items():
        if digest(path) != signature:
            raise ValueError('Input changed after pre-computation binding: ' + path)
    torch.set_num_threads(protocol['budget']['threads'])
    selected = protocol['frames']
    observations, data, calibration, moves, input_signatures = checked_observations(
        Path(args.input).resolve(), Path(args.observations).resolve(), Path(args.native).resolve(), selected)
    model = DynamicBladeReference.from_model_state(args.model, expected_sha256=binding['files'][str(Path(args.model).resolve())])
    plane_payload = json.loads(Path(args.plane).read_text())
    supplied_hash = plane_payload.get('payload_sha256')
    unhashed = {k: v for k, v in plane_payload.items() if k != 'payload_sha256'}
    actual_hash = hashlib.sha256(json.dumps(unhashed, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if (plane_payload.get('schema') != 'nrel-dynamic-rigid-plane.v1'
            or plane_payload.get('condition') != 'IDEAL_SHARED_RIGID_PLANE_ASSISTANCE'
            or supplied_hash != actual_hash):
        raise ValueError('Ideal plane identity or payload signature mismatch')
    plane_rows = plane_payload['records']
    planes = {r['frame_id']: r for r in plane_rows}
    if len(planes) != data['sampling']['frame_count_per_camera'] or len(plane_rows) != len(planes):
        raise ValueError('Incomplete or duplicate ideal plane sequence')
    size = tuple(protocol['budget']['image_size'])
    prepared = _prepare(observations, size, 'cpu', require_contours=True)
    grid = list(itertools.product(protocol['candidate_model']['q_grid_per_axis_m'], repeat=2))
    signatures = {str(p): digest(p) for p in [binding_path, protocol_path, Path(args.model).resolve(), Path(args.plane).resolve(), Path(__file__).resolve()]}
    signatures.update(binding['files'])
    signatures.update(input_signatures)
    for name in ('dynamic_geometry.py', 'contour.py', 'optimize.py', 'dataset.py', 'model.py', 'renderer.py', 'artifact_paths.py', 'observations.py'):
        p = ROOT / 'wfrl/nrel_reconstruction' / name
        signatures[str(p)] = digest(p)
    write_new(out / 'input_freeze.json', {'schema': 'nrel-dy1-input-freeze.v1', 'files': signatures,
                                         'truth_read': False, 'optimizer_iterations': 0})
    all_rows, invariant_rows = [], []
    base = model.evaluate([0., 0.])
    for frame in selected:
        plane = planes[frame]
        plane_time = plane.get('time_s', plane.get('sim_time_s'))
        if not np.isclose(plane_time, moves[frame]['sim_time_s'], atol=1e-9, rtol=0):
            raise ValueError('Plane and image time mismatch')
        root = np.asarray(moves[frame]['T_world_from_blade_root'])
        frame_obs = [p for p in prepared if p['frame_id'] == frame]
        for q in grid:
            if time.monotonic() - start > protocol['budget']['time_limit_s']:
                write_new(out / 'failure.json', {'status': 'BUDGET_STOP', 'completed_states': len(all_rows)})
                raise RuntimeError('Registered DY1 time budget exceeded')
            geom = model.evaluate(q)
            vertices = torch.as_tensor(geom['vertices'], dtype=torch.float32)
            faces = torch.as_tensor(geom['faces'], dtype=torch.long)
            image_rows = []
            with torch.no_grad():
                for obs in frame_obs:
                    loss, terms = projected_contour_loss(vertices, faces, obs['K'], obs['camera'], obs['root'],
                        size, obs['labels'], obs['contour_points'], candidate_valid_mask=obs['contour_valid'],
                        uncertainty_px=obs['contour_uncertainty_px'])
                    valid = bool(terms['contour_terms_available'].item())
                    metric = .5 * (float(terms['candidate_to_observed_mean_px']) + float(terms['observed_to_candidate_mean_px'])) * 1920 / size[0]
                    image_rows.append({'camera_id': obs['camera_id'], 'valid': valid,
                                       'loss': float(loss) if valid else None,
                                       'native_mean_distance_px': metric if valid else None})
            tip = transform_points(geom['axis_tip'], root)
            c = plane.get('c_world_m', plane.get('center_world_m'))
            n = plane.get('n_world_unit', plane.get('normal_world_unit'))
            reading = plane_readout(tip, c, n)
            valid = all(r['valid'] for r in image_rows)
            all_rows.append({'frame_id': frame, 'sim_time_s': moves[frame]['sim_time_s'], 'q_m': list(q),
                'valid_image_terms': valid,
                'native_mean_distance_px': float(np.mean([r['native_mean_distance_px'] for r in image_rows])) if valid else None,
                'loss': float(np.mean([r['loss'] for r in image_rows])) if valid else None,
                'candidate_d_m': float(reading['d']), 'camera_metrics': image_rows})
        # q=0 is the exact same local surface at every frame. World motion is applied once.
        world = transform_points(base['vertices'], root)
        max_error = 0.
        for camera in ('C1', 'C2', 'C3'):
            cal = calibration[(camera, frame)]
            C = np.asarray(cal['T_camera_cv_from_world']); K = np.asarray(cal['K'])
            direct = transform_points(base['vertices'], C @ root)
            composed = transform_points(world, C)
            max_error = max(max_error, float(np.max(np.abs(direct - composed))))
            a, b = direct @ K.T, composed @ K.T
            good = (a[:, 2] > .01) & (b[:, 2] > .01)
            if good.any() and not np.allclose(a[good, :2] / a[good, 2:], b[good, :2] / b[good, 2:], atol=1e-6):
                raise ValueError('Zero-deformation projection chain mismatch')
        invariant_rows.append({'frame_id': frame, 'max_chain_difference_m': max_error,
                               'q0_local_geometry_unchanged': True})
        print(f'DY1 frame {frame}: {len(grid)} candidates x 3 cameras complete', flush=True)
    summaries = {str(f): ambiguity_summary([r for r in all_rows if r['frame_id'] == f], 3.) for f in selected}
    result = {'schema': 'nrel-dynamic-dy1-grid.v1', 'status': 'DIAGNOSTIC_COMPLETE_NOT_STRUCTURAL_ACCEPTANCE',
              'target': 'T1/B1', 'structural_mapping': protocol['candidate_structural_point'],
              'calibration_uncertainty': protocol['calibration_uncertainty'],
              'rows': all_rows, 'frame_summary': summaries, 'coordinate_invariants': invariant_rows,
              'image_evaluations': len(all_rows) * 3, 'elapsed_s': time.monotonic() - start,
              'engineering_acceptance': 'PENDING_USE_CASE_TARGETS', 'truth_read': False}
    write_new(out / 'sensitivity.json', result)
    # Check all inputs after the finite grid; no post-score or post-grid edits are hidden.
    changed = [p for p, h in signatures.items() if digest(p) != h]
    write_new(out / 'post_run_integrity.json', {'unchanged': not changed, 'changed': changed})
    if changed:
        raise ValueError('Inputs changed during DY1')
    print(json.dumps({'status': result['status'], 'elapsed_s': result['elapsed_s'], 'frames': summaries}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('input', 'observations', 'native', 'model', 'plane', 'protocol', 'binding', 'output'):
        p.add_argument('--' + name, required=True)
    run(p.parse_args())
