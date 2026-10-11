#!/usr/bin/env python3
"""Post-freeze conditional mechanical-tip scoring, independent of the estimator.

The target is the fixed BeamDyn mechanical material tip. Errors include the
predeclared mapping hypothesis; they do not identify physical correspondence.
"""
from pathlib import Path
import argparse
import hashlib
import json
import time
import numpy as np


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')


def rotation(axis, degrees):
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    return (np.array([[1., 0., 0.], [0., c, -s], [0., s, c]]),
            np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]]),
            np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]]))[axis]


def mechanical_truth(scalars, pose, nacelle, displacement, tip_hub_local):
    """Independent NumPy implementation of the registered BeamDyn channel frame."""
    nacelle = np.asarray(nacelle, float)
    if nacelle.shape != (3, 4) or not np.isfinite(nacelle).all():
        raise ValueError('Invalid scoring nacelle pose')
    R, translation = nacelle[:, :3], nacelle[:, 3]
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-5, rtol=0) or np.linalg.det(R) < .99999:
        raise ValueError('Scoring nacelle must be a proper rigid transform')
    tilt = np.radians(scalars['ShftTilt'])
    hub = R @ np.array([scalars['OverHang'] * np.cos(tilt), 0.,
                       scalars['TowerHt'] + scalars['Twr2Shft'] + scalars['OverHang'] * np.sin(tilt)]) + translation
    rotor = R @ rotation(1, -scalars['ShftTilt'])
    axes = rotor @ rotation(0, pose[1]) @ rotation(1, scalars['PreCone(1)']) @ rotation(2, -pose[3])
    point = hub + axes @ (np.asarray(tip_hub_local) + displacement)
    normal = rotor[:, 0]
    normal = normal / np.linalg.norm(normal)
    return point, hub, normal


def scalar_metrics(values):
    x = np.asarray(values, float)
    if x.ndim != 1 or len(x) == 0 or not np.isfinite(x).all():
        raise ValueError('Metrics require nonempty finite values')
    return {'count': len(x), 'mean': float(x.mean()), 'rmse': float(np.sqrt(np.mean(x*x))),
            'mean_absolute': float(abs(x).mean()), 'p95_absolute': float(np.quantile(abs(x), .95)),
            'max_absolute': float(abs(x).max())}


def exact_source_indices(times, targets, tolerance_s=1e-8):
    """Select unique saved samples within tolerance, never interpolate truth."""
    times, targets = np.asarray(times, float), np.asarray(targets, float)
    if (times.ndim != 1 or len(times) == 0 or not np.isfinite(times).all()
            or np.any(np.diff(times) <= 0) or targets.ndim != 1
            or not np.isfinite(targets).all()):
        raise ValueError('Source and target clocks must be finite and source times strictly increasing')
    left = np.searchsorted(times, targets - tolerance_s, side='left')
    right = np.searchsorted(times, targets + tolerance_s, side='right')
    if (np.any(right - left != 1) or np.any(left >= len(times))
            or not np.allclose(times[left], targets, atol=tolerance_s, rtol=0)):
        raise ValueError('Image centers must uniquely match saved source times; no truth interpolation')
    return left


def verify_estimator_freeze(freeze, package, package_manifest, signatures):
    """Reject other hash inventories before opening any independent source."""
    if (freeze.get('schema') != 'nrel-dynamic-estimator-freeze.v1'
            or freeze.get('status') != 'FROZEN_BEFORE_INDEPENDENT_SCORE'
            or freeze.get('truth_read') is not False
            or freeze.get('independent_score_read') is not False
            or freeze.get('package_path') != str(package)
            or freeze.get('manifest_sha256') != signatures[str(package / 'manifest.json')]
            or 'sequence.json' not in package_manifest['files']):
        raise ValueError('Invalid estimator freeze provenance or unbound sequence')
    bound = freeze.get('files')
    if not isinstance(bound, dict) or any(bound.get(path) != signature for path, signature in signatures.items()):
        raise ValueError('Estimator freeze does not bind the score input package')


def source_turbine_index(manifest):
    ids = manifest.get('turbine_ids')
    if (manifest.get('schema') != 'wfrl.farm-flex-review.v3'
            or manifest.get('status') != 'REVIEW_ONLY'
            or manifest.get('structural_module') != 'BeamDyn'
            or manifest.get('tower_model') != 'elastodyn-flexible'
            or manifest.get('includes_rigid_motion') is not True
            or not isinstance(ids, list) or not ids or any(not isinstance(tid, str) for tid in ids)
            or len(set(ids)) != len(ids) or 'T1' not in ids):
        raise ValueError('Wrong independent BeamDyn source schema or turbine identity')
    return ids.index('T1')


def validate_source_links(manifest, channels, reference):
    """Use the existing v3 sidecar/reference contract without source meshes."""
    if (channels.get('schema') != 'wfrl.tip-deflection.t1.v3' or channels.get('unit') != 'm'
            or channels.get('turbine_id') != 'T1'
            or channels.get('reference') != 'BeamDyn structural tip; independent unloaded prebend'
            or channels.get('frame') != 'BeamDyn pitched root xyz'
            or channels.get('geometry_sha256') != manifest['files'].get('geometry.npz')
            or channels.get('tower_motion_sha256') != manifest['files'].get('tower-motion.npz')
            or channels.get('reference_sha256') != manifest['files'].get('blade-reference.json')):
        raise ValueError('Wrong independent mechanical-tip channel definition or source links')
    scalars = channels.get('scalars', {})
    keys = ('TipRad', 'HubRad', 'OverHang', 'TowerHt', 'Twr2Shft', 'ShftTilt', 'PreCone(1)')
    if (any(key not in scalars for key in keys) or not np.isfinite([scalars[key] for key in keys]).all()
            or scalars['TipRad'] != 63. or scalars['HubRad'] != 1.5
            or scalars['PreCone(1)'] != -2.5):
        raise ValueError('Invalid registered mechanical-tip scalars')
    curve, tip = np.asarray(reference.get('curve'), float), np.asarray(reference.get('tip_local_m'), float)
    if (reference.get('schema') != 'wfrl.blade-reference.v1'
            or reference.get('structural_module') != 'BeamDyn'
            or not manifest.get('model') or reference.get('model') != manifest['model']
            or reference.get('definition') != 'x=-(z/61.5)^2; y=0; metres; z from blade root'
            or reference.get('reference_state') != 'independent zero-load stationary solve'
            or reference.get('precone_deg') != -2.5
            or curve.shape != (49, 4) or not np.isfinite(curve).all()
            or np.any(np.diff(curve[:, 2]) <= 0)
            or not np.allclose(curve[[0, -1], 2], [0., 61.5], atol=1e-8, rtol=0)
            or not np.allclose(curve[:, 0], -(curve[:, 2] / 61.5)**2, atol=1e-8, rtol=0)
            or not np.allclose(curve[:, 1], 0., atol=1e-8, rtol=0)
            or not np.array_equal(tip, [-1., 0., 63.])):
        raise ValueError('Wrong independent unloaded structural reference')
    return tip


def run(args):
    start = time.monotonic()
    package, source, freeze_path = Path(args.package).resolve(), Path(args.source).resolve(), Path(args.freeze).resolve()
    # The estimator-freeze must exist before this reader opens any source truth.
    freeze = json.loads(freeze_path.read_text())
    package_manifest = json.loads((package / 'manifest.json').read_text())
    if (package_manifest.get('schema') != 'nrel-dynamic-review-package.v1'
            or package_manifest.get('status') != 'REVIEW_ONLY' or package_manifest.get('target') != 'T1/B1'):
        raise ValueError('Expected frozen research package for T1/B1')
    package_signatures = {str(package / 'manifest.json'): digest(package / 'manifest.json')}
    for name, signature in package_manifest['files'].items():
        if Path(name).name != name or digest(package / name) != signature:
            raise ValueError('Package integrity or local filename mismatch')
        package_signatures[str(package / name)] = signature
    verify_estimator_freeze(freeze, package, package_manifest, package_signatures)
    sequence = json.loads((package / 'sequence.json').read_text())
    if sequence.get('schema') != 'nrel-dynamic-review-sequence.v1' or sequence.get('frames') != [42, 43, 44, 45]:
        raise ValueError('Scoring scope changed from four registered research times')
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    source_manifest = json.loads((source / 'manifest.json').read_text())
    turbine_index = source_turbine_index(source_manifest)
    required = ['geometry.npz', 'tower-motion.npz', 'deflection-t1.json', 'blade-reference.json', 'interface-audit.json']
    source_hashes = {str(source / 'manifest.json'): digest(source / 'manifest.json')}
    for name in required:
        if digest(source / name) != source_manifest['files'].get(name):
            raise ValueError('Scoring source integrity mismatch: ' + name)
        source_hashes[str(source / name)] = digest(source / name)
    signatures = {**source_hashes, **package_signatures, str(freeze_path): digest(freeze_path),
                  str(Path(__file__).resolve()): digest(__file__)}
    save_new(out / 'input_freeze.json', {'schema': 'nrel-a03-post-estimate-score-inputs.v1',
              'files': signatures, 'estimates_frozen_before_truth_read': True})
    channels = json.loads((source / 'deflection-t1.json').read_text())
    reference = json.loads((source / 'blade-reference.json').read_text())
    tip = validate_source_links(source_manifest, channels, reference)
    with np.load(source / 'geometry.npz', allow_pickle=False) as archive:
        times = archive['times'].astype(float)
        all_poses = archive['poses']
        if all_poses.shape != (len(times), len(source_manifest['turbine_ids']), 6):
            raise ValueError('Invalid source rigid pose shape')
        poses = all_poses[:, turbine_index].astype(float)
    with np.load(source / 'tower-motion.npz', allow_pickle=False) as archive:
        tower_times = archive['times'].astype(float)
        all_nacelles = archive['nacelle']
        if all_nacelles.shape != (len(times), len(source_manifest['turbine_ids']), 3, 4):
            raise ValueError('Invalid source nacelle shape')
        nacelles = all_nacelles[:, turbine_index].astype(float)
    if not np.array_equal(times, tower_times) or not np.array_equal(times, np.asarray(channels['times'])):
        raise ValueError('Source clocks differ')
    channel_poses = np.asarray(channels['poses'], float)
    if (channel_poses.shape != poses.shape or not np.isfinite(poses).all()
            or not np.isfinite(channel_poses).all()
            or not np.allclose(poses, channel_poses, atol=.002, rtol=0)):
        raise ValueError('Channel and rigid pose mismatch')
    simulation = np.asarray(channels['simulation'], float)
    if simulation.shape != (len(times), 3, 3) or not np.isfinite(simulation).all():
        raise ValueError('Invalid BeamDyn tip displacement series')
    audit = json.loads((source / 'interface-audit.json').read_text())
    source_consistency = float(audit['max_component_error_m'])
    if not np.isfinite(source_consistency) or not 0. <= source_consistency <= .005:
        raise ValueError('Source structural-tip interface check exceeds declared bound')
    truth_rows, group_scores = [], {}
    expected_times = [120. + (f + .5) / 20 for f in sequence['frames']]
    ids = exact_source_indices(times, expected_times)
    for frame, time_s, i in zip(sequence['frames'], expected_times, ids):
        p, c, n = mechanical_truth(channels['scalars'], poses[i], nacelles[i], simulation[i, 0], tip)
        truth_rows.append({'frame_id': frame, 'sim_time_s': time_s, 'point_world_m': p.tolist(),
                           'center_world_m': c.tolist(), 'normal_world_unit': n.tolist(),
                           'signed_d_m': float(n @ (p-c))})
    for group in ('G1', 'G2', 'G3'):
        records = sequence['groups'][group]['records']
        if [r['frame_id'] for r in records] != sequence['frames']:
            raise ValueError('Group scoring identity differs')
        rows = []
        for estimate, truth in zip(records, truth_rows):
            if abs(estimate['sim_time_s'] - truth['sim_time_s']) > 1e-9:
                raise ValueError('Estimate/truth time mismatch')
            point = np.asarray(estimate['point_world_m'], float)
            if point.shape != (3,) or not np.isfinite(point).all():
                raise ValueError('Cannot score missing/nonfinite model hypothesis')
            error = point - truth['point_world_m']
            d_error = estimate['d_m'] - truth['signed_d_m']
            shared_truth_d = float(np.asarray(estimate['normal_world_unit']) @
                                   (np.asarray(truth['point_world_m']) - estimate['center_world_m']))
            plane_c_error = np.linalg.norm(np.asarray(estimate['center_world_m']) - truth['center_world_m'])
            plane_n_error = np.linalg.norm(np.asarray(estimate['normal_world_unit']) - truth['normal_world_unit'])
            rows.append({'frame_id': estimate['frame_id'], 'sim_time_s': estimate['sim_time_s'],
                         'status': estimate['status'], 'point_error_xyz_m': error.tolist(),
                         'point_error_norm_m': float(np.linalg.norm(error)), 'signed_d_error_m': float(d_error),
                         'estimated_d_m': estimate['d_m'], 'true_d_m': truth['signed_d_m'],
                         'true_d_in_frozen_estimate_plane_m': shared_truth_d,
                         'signed_d_error_shared_plane_m': float(estimate['d_m'] - shared_truth_d),
                         'ideal_plane_center_difference_m': float(plane_c_error),
                         'ideal_plane_normal_difference': float(plane_n_error)})
        group_scores[group] = {'rows': rows,
                    'point_norm_error_m': scalar_metrics([r['point_error_norm_m'] for r in rows]),
                    'signed_d_error_m': scalar_metrics([r['signed_d_error_m'] for r in rows]),
                    'signed_d_error_shared_plane_m': scalar_metrics([r['signed_d_error_shared_plane_m'] for r in rows])}
    score = {'schema': 'nrel-a03-conditional-structural-score.v1',
             'status': 'POST_FREEZE_RESEARCH_SCORE_NOT_ENGINEERING_ACCEPTANCE',
             'target': 'T1/B1 BeamDyn mechanical material tip', 'groups': group_scores,
             'truth': truth_rows, 'condition': 'IDEAL_SHARED_RIGID_PLANE_ASSISTANCE',
             'source_interface_max_component_difference_m': source_consistency,
             'source_interface_conservative_vector_difference_bound_m': float(np.sqrt(3.) * source_consistency),
             'source_interface_difference_is_not_calibrated_measurement_uncertainty': True,
             'mapping_status': 'PRIOR_DEFINED_STRUCTURAL_POINT_NOT_CORRESPONDENCE_VERIFIED',
             'error_scope': 'Total error of predeclared model hypothesis against mechanical tip; includes mapping and geometry bias',
             'data_use': 'Four historical nonblind development times; no unseen time/case generalization',
             'numerical_hypothesis_samples_per_group': 4, 'accepted_measurement_samples': 0,
             'comparison': {'G3_minus_G2_d_rmse_m': group_scores['G3']['signed_d_error_m']['rmse'] - group_scores['G2']['signed_d_error_m']['rmse'],
                            'G3_minus_G2_point_rmse_m': group_scores['G3']['point_norm_error_m']['rmse'] - group_scores['G2']['point_norm_error_m']['rmse']},
             'elapsed_s': time.monotonic() - start, 'engineering_acceptance': 'PENDING_USE_CASE_TARGETS',
             'dy1_measurement_gate': 'NOT_PASSED', 'retuning_after_score': False}
    save_new(out / 'score.json', score)
    changed = [p for p, h in signatures.items() if digest(p) != h]
    save_new(out / 'post_run_integrity.json', {'unchanged': not changed, 'changed': changed})
    if changed:
        raise ValueError('Scorer inputs changed during post-freeze scoring')
    print(json.dumps({'status': score['status'], 'comparison': score['comparison'],
                      'rmse_by_group': {g: x['signed_d_error_m']['rmse'] for g, x in group_scores.items()}}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'source', 'freeze', 'output'):
        p.add_argument('--' + name, required=True)
    run(p.parse_args())
