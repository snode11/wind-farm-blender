"""Actual pointing endpoint replay on frozen saved windows; no new simulation.

Directions change before first-hit ray intersection. This experiment therefore
allows objects, blade identities, ranges and pair membership to change, unlike
the fixed-hit range/direction endpoint probes in audit_dual_beam_accuracy.py.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import platform
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.lidar.compare_dual_beam_methods import (
    NUMERICAL_TOLERANCE_M, coverage, expected_window, independent_clearance,
    load_verified, statistics, structural_reference_points, tower_at,
)
from wfrl.camera_video.data import blade_root_frame
from wfrl.lidar.dual_beam import METHOD_VERSIONS, observe, reconstruct, s1_state, validate_calibration
from wfrl.lidar.dual_beam_replay import digest
from wfrl.lidar.moving_tower import horizontal_clearance

ANGLE_AMPLITUDE_DEG = .2
NEAR_RANGE_M = 12.
POINTING_COMBINATIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def combination_id(signs):
    return 'S2%s_S3%s' % tuple('minus' if s < 0 else 'plus' for s in signs)


def rotate_about_y(direction, angle_deg):
    theta = math.radians(angle_deg)
    c, s = math.cos(theta), math.sin(theta)
    return np.array(((c, 0., s), (0., 1., 0.), (-s, 0., c))) @ np.asarray(direction, float)


def forward_observations(time_s, nacelle, layout, origins, directions, signs, blades,
                         blade_triangles, tower, tower_triangles, limits, *, nominal_s1=None):
    """Apply material-frame pointing changes before computing physical returns."""
    observations = {}
    for i, name in enumerate(('S1', 'S2', 'S3')):
        if name == 'S1' and nominal_s1 is not None:
            observations[name] = deepcopy(nominal_s1)
            continue
        local = directions[i] if i == 0 else rotate_about_y(directions[i], signs[i-1]*ANGLE_AMPLITUDE_DEG)
        origin = nacelle[:, :3] @ origins[i] + nacelle[:, 3] + layout
        direction = nacelle[:, :3] @ local
        direction /= np.linalg.norm(direction)
        observations[name] = observe(time_s, origin, direction, blades, blade_triangles,
                                     tower, tower_triangles, limits)
    return observations


def range_branch(observation):
    value = observation.get('slant_range_m')
    if observation.get('first_object') != 'blade' or value is None:
        return 'non_blade'
    return 'near' if value < NEAR_RANGE_M else 'far'


def observation_changes(nominal, actual):
    result = {name+'_changed': nominal.get(name) != actual.get(name)
              for name in ('first_object', 'blade_id', 'valid', 'observed', 'reason')}
    result['range_branch_changed'] = range_branch(nominal) != range_branch(actual)
    result.update(nominal_range_branch=range_branch(nominal), actual_range_branch=range_branch(actual))
    return result


def freeze_windows(sources, overlay, config):
    """Use source phase alone; nominal or perturbed validity cannot select windows."""
    plans = {}
    for turbine, source in sources.items():
        saved = overlay['results'][turbine]['samples']
        windows = []
        for index, (time_s, pose) in enumerate(zip(source.times, source.poses)):
            blade, passage = expected_window(pose, config['expected_window_half_angle_deg'])
            if (saved[index]['expected_blade_id'], saved[index]['passage_id']) != (blade, passage):
                raise ValueError('Baseline fixed windows differ from source phase: '+turbine)
            if blade is not None:
                windows.append(dict(source_index=index, time_s=float(time_s), expected_blade_id=blade,
                                    passage_id=passage))
        truncated = {w['passage_id'] for w in windows
                     if w['source_index'] in (0, len(source.times)-1)}
        for row in windows:
            row['boundary_truncated'] = row['passage_id'] in truncated
        plans[turbine] = windows
    return plans


def verify_nominal_observation(actual, saved):
    """Nominal ray reproduction uses the same frozen numerical tolerance."""
    categorical = ('time_s', 'first_object', 'blade_id', 'valid', 'observed', 'reason')
    if any(actual.get(key) != saved.get(key) for key in categorical):
        raise ValueError('Nominal actual-ray replay differs from saved observation identity')
    residuals = []
    for key in ('origin_m', 'direction', 'slant_range_m', 'point_m'):
        a, b = actual.get(key), saved.get(key)
        if a is None or b is None:
            if a != b:
                raise ValueError('Nominal actual-ray replay differs from saved null contract')
        else:
            residual = float(np.max(np.abs(np.asarray(a)-np.asarray(b))))
            if not math.isfinite(residual) or residual > NUMERICAL_TOLERANCE_M:
                raise ValueError('Nominal actual-ray replay differs from saved numeric observation')
            residuals.append(residual)
    return max(residuals, default=0.)


def evaluate_pair(pair, reference_points, tower, triangles):
    """Scoring branch alone reads transported structural reference tip points."""
    result = dict(reference_clearance_m=None, error_m=None, independent_estimate_residual_m=None,
                  reason='no_paired_blade')
    blade = pair['blade_id']
    if blade is None:
        return result
    try:
        reference = independent_clearance(reference_points[blade-1], tower, triangles)
    except ValueError:
        result['reason'] = 'no_tower_section_at_reference_height'
        return result
    result.update(reference_clearance_m=reference, reason='valid')
    if pair['valid']:
        independent = independent_clearance(pair['tip_estimate_m'], tower, triangles)
        residual = abs(independent-pair['clearance_estimate'])
        if residual > NUMERICAL_TOLERANCE_M:
            raise ValueError('Pointing pair clearance differs from independent tower section scorer')
        result.update(error_m=pair['clearance_estimate']-reference,
                      independent_estimate_residual_m=residual)
    return result


def summarize_samples(samples, combo, dt):
    methods = {}
    for method, version in METHOD_VERSIONS.items():
        records, errors, expected_errors, reasons = [], [], [], Counter()
        valid_pairs = 0
        for row in samples:
            value = row['combinations'][combo]['methods'][method]
            pair, evaluation = value['reconstruction'], value['evaluation']
            fresh = pair['valid'] and pair['blade_id'] == row['expected_blade_id']
            valid_pairs += pair['valid']
            if not pair['valid']:
                reasons[pair['reason']] += 1
            error = evaluation['error_m']
            if error is not None:
                errors.append(error)
                if fresh:
                    expected_errors.append(error)
            records.append(dict(time_s=row['time_s'], expected_blade_id=row['expected_blade_id'],
                                passage_id=row['passage_id'], boundary_truncated=row['boundary_truncated'],
                                fresh_valid=fresh, error_m=error if fresh else None,
                                unknown_reason=None if fresh else pair['reason'] if not pair['valid'] else 'paired_wrong_blade'))
        methods[method] = dict(algorithm_version=version, valid_pairs=valid_pairs,
                              rejected_pairs=len(samples)-valid_pairs, rejection_reasons=dict(reasons),
                              own_valid_error_statistics=statistics(errors),
                              correct_expected_blade_error_statistics=statistics(expected_errors),
                              coverage=coverage(records, dt))
    common = [r for r in samples if all(
        r['combinations'][combo]['methods'][m]['evaluation']['error_m'] is not None for m in METHOD_VERSIONS)]
    beam_changes = {}
    for beam in ('S1', 'S2', 'S3'):
        counts = Counter()
        first_objects, branches, observation_reasons = Counter(), Counter(), Counter()
        for row in samples:
            experiment = row['combinations'][combo]
            counts.update(key for key, changed in experiment['beam_changes'][beam].items()
                          if key.endswith('_changed') and changed)
            obs = row['nominal_observations']['S1'] if beam == 'S1' else experiment['observations'][beam]
            first_objects[obs['first_object'] or 'none'] += 1
            branches[range_branch(obs)] += 1
            observation_reasons[obs['reason']] += 1
        beam_changes[beam] = dict(changes=dict(counts), first_objects=dict(first_objects),
                                  range_branches=dict(branches), observation_reasons=dict(observation_reasons))
    return dict(methods=methods, beam_changes=beam_changes, common_scored_samples=len(common),
                common_valid_error_statistics={m: statistics(
                    r['combinations'][combo]['methods'][m]['evaluation']['error_m'] for r in common)
                    for m in METHOD_VERSIONS})


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')


def run(baseline, output):
    verified = load_verified(baseline)
    overlay, sources = verified['overlay'], verified['sources']
    config = overlay['config']
    if config.get('reconstruction_method', 'hub-axis.v1') != 'hub-axis.v1':
        raise ValueError('Pointing experiment requires the original-method baseline package')
    origins, directions, limits = validate_calibration(config)
    plans = freeze_windows(sources, overlay, config)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    declaration = dict(schema='wfrl.dual-beam-actual-pointing-declaration.v1', status='FROZEN_BEFORE_RAY_LOOPS',
                       angle_amplitude_deg=ANGLE_AMPLITUDE_DEG, sign_order=['S2_angle', 'S3_angle'],
                       combinations=[dict(id=combination_id(s), signs=list(s)) for s in POINTING_COMBINATIONS],
                       coordinate_frame='rotation about source nacelle material +y, then source nacelle world rotation',
                       perturbation_location='directions changed before first-hit observe; resulting ranges are re-intersected',
                       range_perturbation_m=0., s1='nominal re-hit once per window; identical observation reused in every combination',
                       source_grid='original saved grid only; no interpolation or new physical simulation',
                       expected_window_half_angle_deg=config['expected_window_half_angle_deg'],
                       expected_window_rule='each source blade azimuth within fixed half-angle of downward 180 degrees',
                       expected_samples=sum(map(len, plans.values())),
                       expected_samples_by_turbine={t: len(rows) for t, rows in plans.items()},
                       fixed_windows=plans, effective_calibration=deepcopy(config),
                       methods=METHOD_VERSIONS, baseline_manifest_sha256=digest(verified['path']/'manifest.json'),
                       source_hashes=overlay['manifest']['source_hashes'], numerical_tolerance_m=NUMERICAL_TOLERANCE_M,
                       s3_branch_definition='first-object blade slant range < 12 m: near; >= 12 m: far; other objects: non_blade; descriptive only',
                       scope='deterministic +/-0.2 degree endpoint combinations; no probability model, hardware guarantee, independent acceptance or whole-system bound')
    write_json(output/'declaration.json', declaration)
    declaration_hash = digest(output/'declaration.json')
    result = dict(schema='wfrl.dual-beam-actual-pointing-stress.v1', status='REVIEW_ONLY',
                  performance_status='PENDING_ACCEPTANCE', declaration_sha256=declaration_hash,
                  declaration=declaration, source_fps=overlay['manifest']['source_fps'], turbines={},
                  scoring_definition='estimate minus independent transported structural reference clearance; each at its own global height',
                  integrity=dict(s1_unchanged=True, nominal_observations_reproduced=True,
                                 max_nominal_numeric_residual_m=0., max_independent_estimate_residual_m=0.))
    pooled = []
    for turbine, source in sources.items():
        references = structural_reference_points(source)
        samples = []
        for window_index, planned in enumerate(plans[turbine]):
            index, time_s = planned['source_index'], planned['time_s']
            nacelle, transforms = source.nacelles[index], source.transforms[index]
            blades = (np.einsum('bsij,bsvj->bsvi', transforms[..., :3], source.blade_reference)
                      + transforms[:, :, None, :, 3]).reshape(3, -1, 3)+source.layout
            tower = tower_at(source, index)
            nominal = forward_observations(time_s, nacelle, source.layout, origins, directions, (0, 0),
                                           blades, source.blade_triangles, tower, source.tower_triangles, limits)
            saved = overlay['results'][turbine]['samples'][index]
            for beam in ('S1', 'S2', 'S3'):
                residual = verify_nominal_observation(nominal[beam], saved['observations'][beam])
                result['integrity']['max_nominal_numeric_residual_m'] = max(
                    result['integrity']['max_nominal_numeric_residual_m'], residual)
            if s1_state(nominal['S1']) != saved['s1_observation_state']:
                raise ValueError('Nominal S1 state differs from independent baseline observation')
            row = dict(planned, turbine_id=turbine, nominal_observations=nominal,
                       s1_observation_state=s1_state(nominal['S1']), combinations={})
            hub, _ = blade_root_frame(source.scalars, source.poses[index], 1, nacelle)
            clearance = lambda point: horizontal_clearance(point, tower, source.tower_triangles)[0]
            for signs in POINTING_COMBINATIONS:
                actual = forward_observations(time_s, nacelle, source.layout, origins, directions, signs,
                                              blades, source.blade_triangles, tower, source.tower_triangles,
                                              limits, nominal_s1=nominal['S1'])
                pairs = {method: reconstruct(actual['S2'], actual['S3'], hub+source.layout,
                                             config['effective_length_m'], clearance, time_s=time_s,
                                             limits=limits, method=method) for method in METHOD_VERSIONS}
                # Real terminal transforms enter only this independent scoring
                # branch, after both estimators have consumed identical returns.
                methods = {method: dict(reconstruction=pair, evaluation=evaluate_pair(
                    pair, references[index], tower, source.tower_triangles)) for method, pair in pairs.items()}
                for value in methods.values():
                    residual = value['evaluation']['independent_estimate_residual_m']
                    if residual is not None:
                        result['integrity']['max_independent_estimate_residual_m'] = max(
                            result['integrity']['max_independent_estimate_residual_m'], residual)
                row['combinations'][combination_id(signs)] = dict(
                    signs=list(signs), observations={s: actual[s] for s in ('S2', 'S3')},
                    beam_changes={s: observation_changes(nominal[s], actual[s]) for s in ('S1', 'S2', 'S3')},
                    methods=methods)
            samples.append(row)
            if window_index % 100 == 0:
                print(turbine, window_index+1, '/', len(plans[turbine]), flush=True)
        result['turbines'][turbine] = dict(samples=samples, combinations={
            combination_id(s): summarize_samples(samples, combination_id(s), 1./source.manifest['source_fps'])
            for s in POINTING_COMBINATIONS})
        # Prefix passage IDs for pooled coverage so distinct turbine passages
        # cannot merge. Numeric metrics are weighted by individual samples.
        for row in samples:
            pooled.append({**row, 'passage_id': turbine+'/'+row['passage_id']})
    result['pooled_combinations'] = {combination_id(s): summarize_samples(
        pooled, combination_id(s), 1./result['source_fps']) for s in POINTING_COMBINATIONS}
    result['s1'] = dict(nominal_window_samples=len(pooled),
                       nominal_hits=sum(r['s1_observation_state'] == 'triggered' for r in pooled),
                       unknown_samples=sum(r['s1_observation_state'] == 'unknown' for r in pooled),
                       unchanged_for_all_combinations=True,
                       scope='independent raw S1 on fixed windows; no new full-grid alarm or hold package is fabricated')
    result['runtime'] = dict(python=platform.python_version(), numpy=np.__version__)
    names = ('scripts/lidar/stress_dual_beam_pointing.py', 'tests/lidar/test_dual_beam_pointing.py',
             'scripts/lidar/compare_dual_beam_methods.py', 'wfrl/lidar/dual_beam.py',
             'wfrl/lidar/dual_beam_replay.py', 'wfrl/lidar/physics.py',
             'wfrl/lidar/moving_tower.py', 'wfrl/camera_video/data.py')
    result['implementation_hashes'] = {name: digest(ROOT/name) for name in names}
    if digest(output/'declaration.json') != declaration_hash:
        raise ValueError('Pointing declaration changed during the experiment')
    write_json(output/'pointing-stress.json', result)
    print(output/'pointing-stress.json', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.baseline, args.output)


if __name__ == '__main__':
    main()
