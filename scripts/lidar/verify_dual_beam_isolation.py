"""Recompute every saved pair with deleted/polluted truth and guarded inputs.

This checks software input isolation on the supplied saved grid. Known hub,
nacelle pose, blade identity and moving tower remain declared simulation inputs;
it does not establish their availability from field sensors or physical accuracy.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from wfrl.camera_video.data import SourceGeometry, blade_root_frame
from wfrl.lidar.dual_beam import reconstruct, validate_calibration
from wfrl.lidar.dual_beam_replay import DualBeamReader, digest, error_statistics, resolve_package
from wfrl.lidar.moving_tower import horizontal_clearance

OBSERVATION_FIELDS = frozenset((
    'time_s', 'origin_m', 'direction', 'slant_range_m', 'first_object',
    'blade_id', 'valid', 'observed', 'reason', 'time_support',
))
SOURCE_FIELDS = frozenset((
    'times', 'scalars', 'poses', 'nacelles', 'layout', 'tower_transforms',
    'tower_station', 'tower_reference', 'tower_triangles',
))
SCALAR_FIELDS = frozenset(('ShftTilt', 'OverHang', 'TowerHt', 'Twr2Shft', 'PreCone(1)'))
POISON_FIELDS = {
    'point_m': [1e12, -1e12, 1e12],
    'evaluation': {'tip_reference_m': [-1e12] * 3, 'clearance_reference_m': -1e12},
    'tip_reference_m': [-1e12] * 3,
    'truth_tip': [1e12] * 3,
    'true_tip_m': [-1e12] * 3,
    'clearance_reference_m': -1e12,
    'clearance_error_m': 1e12,
    'tip_estimate_m': [1e12] * 3,
    'old_estimate': {'clearance_estimate': 1e12},
    'true_axis': [0., 1., 0.],
    'span_station': 1e12,
    'terminal_flexible_transform': [[1e12] * 4] * 3,
    'blade_transforms': [[1e12] * 4] * 3,
    'turbine_id': 'forbidden-truth-derived-selection',
}


class ForbiddenRead(AssertionError):
    pass


class GuardedMapping(Mapping):
    """Record access, including absent keys; disallow every non-allowlist read."""
    def __init__(self, values, allowed, label, reads):
        self._values = values
        self.allowed = frozenset(allowed)
        self.label = label
        self.reads = reads

    def __getitem__(self, key):
        self.reads[f'{self.label}.{key}'] += 1
        if key not in self.allowed:
            raise ForbiddenRead(f'Forbidden estimator input read: {self.label}.{key}')
        return self._values[key]

    def __iter__(self):
        # Iteration cannot quietly expose the names of forbidden fields.
        self.reads[f'{self.label}.__iter__'] += 1
        if set(self._values) - self.allowed:
            raise ForbiddenRead(f'Forbidden estimator input iteration: {self.label}')
        return iter(self._values)

    def __len__(self):
        self.reads[f'{self.label}.__len__'] += 1
        if set(self._values) - self.allowed:
            raise ForbiddenRead(f'Forbidden estimator input size read: {self.label}')
        return len(self._values)


class GuardedSource:
    """Prevent the recomputation path from using saved blade truth geometry."""
    def __init__(self, source, reads):
        self._source = source
        self._reads = reads

    def __getattr__(self, name):
        self._reads[f'source.{name}'] += 1
        if name not in SOURCE_FIELDS:
            raise ForbiddenRead(f'Forbidden reconstruction geometry read: source.{name}')
        value = getattr(self._source, name)
        if name == 'scalars':
            return GuardedMapping(value, SCALAR_FIELDS, 'fixed_scalars', self._reads)
        return value


def observation_variant(row, variant):
    """Keep permitted raw inputs exact; delete or corrupt all auxiliary values."""
    if variant == 'original':
        return row
    stripped = dict(time_s=row['time_s'], observations={})
    for name in ('S2', 'S3'):
        stripped['observations'][name] = {
            key: deepcopy(value) for key, value in row['observations'][name].items()
            if key in OBSERVATION_FIELDS
        }
    if variant == 'deleted':
        return stripped
    if variant != 'polluted':
        raise ValueError('Unsupported isolation variant: ' + variant)
    stripped.update(deepcopy(POISON_FIELDS))
    stripped['reconstruction'] = {'valid': True, 'reason': 'poison',
                                  'tip_estimate_m': [-1e12] * 3,
                                  'clearance_estimate': 1e12}
    for observation in stripped['observations'].values():
        observation.update(deepcopy(POISON_FIELDS))
    return stripped


def known_hub_and_tower(source, index):
    """Recreate declared known inputs without any saved reconstruction values."""
    # Compute the hub anew. Never trust the old reconstruction's saved hub.
    hub, _ = blade_root_frame(source.scalars, source.poses[index], 1, source.nacelles[index])
    hub = hub + source.layout
    transform = source.tower_transforms[index, source.tower_station]
    tower = (np.einsum('nij,nj->ni', transform[..., :3], source.tower_reference)
             + transform[..., 3] + source.layout)
    tower_triangles = source.tower_triangles
    return hub, tower, tower_triangles


def guarded_row_inputs(row, source, index, reads):
    source = GuardedSource(source, reads)
    guarded_row = GuardedMapping(row, ('time_s', 'observations'), 'row', reads)
    time_s = guarded_row['time_s']
    if time_s != float(source.times[index]):
        raise ValueError('Result rows must cover the identical source time grid')
    observations = GuardedMapping(guarded_row['observations'], ('S2', 'S3'), 'observations', reads)
    s2, s3 = (GuardedMapping(observations[name], OBSERVATION_FIELDS, name, reads)
              for name in ('S2', 'S3'))
    return source, time_s, s2, s3


def recompute_row(row, source, index, config, limits, reads):
    """Only raw S2/S3, known rigid support, fixed length and tower enter here."""
    source, time_s, s2, s3 = guarded_row_inputs(row, source, index, reads)
    hub, tower, tower_triangles = known_hub_and_tower(source, index)
    return reconstruct(s2, s3, hub, config['effective_length_m'],
                       lambda point: horizontal_clearance(point, tower, tower_triangles)[0],
                       time_s=time_s, limits=limits,
                       method=config.get('reconstruction_method', 'hub-axis.v1'))


def independent_eigen_tls(row, source, index, config, reads):
    """Independent eigensolver for the uncentered hub-constrained objective.

    This function never calls reconstruct or the production SVD branch. The
    caller supplies an already-valid raw pair and scores only after return.
    """
    source, _, s2, s3 = guarded_row_inputs(row, source, index, reads)
    hub, tower, tower_triangles = known_hub_and_tower(source, index)
    relative = np.array([
        np.asarray(observation['origin_m'], float)
        + observation['slant_range_m'] * np.asarray(observation['direction'], float) - hub
        for observation in (s2, s3)
    ])
    scale = float(np.max(np.abs(relative)))
    if not np.isfinite(relative).all() or not math.isfinite(scale) or scale <= 0:
        raise ValueError('Invalid independent TLS relative geometry')
    matrix = relative / scale
    values, axes = np.linalg.eigh(matrix.T @ matrix)
    direction = axes[:, int(np.argmax(values))]
    if direction @ matrix.sum(axis=0) < 0:
        direction = -direction
    tip = hub + config['effective_length_m'] * direction
    clearance = float(horizontal_clearance(tip, tower, tower_triangles)[0])
    if not np.isfinite(direction).all() or not np.isfinite(tip).all() or not math.isfinite(clearance):
        raise ValueError('Nonfinite independent TLS prediction')
    return dict(direction=direction.tolist(), tip_estimate_m=tip.tolist(), clearance_estimate=clearance)


def prediction_difference(saved, recomputed, tolerance=1e-8):
    """Compare numerical coordinates/metres and exact categories recursively."""
    differences = []
    maximum = 0.

    def compare(a, b, path):
        nonlocal maximum
        if path.rsplit('.', 1)[-1] in {'blade_id', 'valid', 'reason', 'method', 'algorithm_version'}:
            if type(a) != type(b) or a != b:
                differences.append(path + ': discrete type/value differs')
        elif isinstance(a, dict) and isinstance(b, dict):
            # Historical rows may omit method; algorithm identity is verified
            # by the package reader and is not a distance reproduction error.
            keys = set(b) - {'method', 'algorithm_version'}
            if keys != set(a) - {'method', 'algorithm_version'}:
                differences.append(path + ': keys differ')
            for key in keys & set(a):
                compare(a[key], b[key], path + '.' + key)
        elif isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if len(a) != len(b):
                differences.append(path + ': lengths differ')
            for index, (left, right) in enumerate(zip(a, b)):
                compare(left, right, f'{path}[{index}]')
        elif (type(a) in (int, float) and type(b) in (int, float)
              and not isinstance(a, bool) and not isinstance(b, bool)):
            if not math.isfinite(a) or not math.isfinite(b):
                differences.append(path + ': nonfinite')
                return
            difference = abs(a - b)
            maximum = max(maximum, difference)
            if difference > tolerance:
                differences.append(path + ': numerical tolerance exceeded')
        elif type(a) != type(b) or a != b:
            differences.append(path + ': categorical/null value differs')

    compare(saved, recomputed, 'reconstruction')
    return differences, maximum


def validate_replay_contract(source, overlay, turbine_id):
    """Use the real reader for the full grid, row and independent S1 contract.

    The minimal motion adapter contains only saved motion and archive metadata;
    no estimator, source truth scorer or interpolation runs during validation.
    """
    motion = SimpleNamespace(start_s=float(source.times[0]), end_s=float(source.times[-1]),
                             package=SimpleNamespace(manifest=source.manifest, motion=source.motion))
    DualBeamReader(motion, overlay, turbine_id)


def audit_postprocess_boundary(path=ROOT / 'scripts/lidar/postprocess_dual_beam.py'):
    """Check the actual direct dependencies, hub provenance and branch order."""
    tree = ast.parse(Path(path).read_text())
    process = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'process')
    calls = [node for node in ast.walk(process)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'reconstruct']
    failures = []
    if len(calls) != 1:
        return dict(passed=False, failures=['Expected one explicit reconstruct call in process'])
    call = calls[0]
    direct_attributes = sorted({node.attr for node in ast.walk(call)
                                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                                and node.value.id == 'source'})
    if direct_attributes != ['layout', 'tower_triangles']:
        failures.append('Estimator call has undeclared source-geometry dependencies')
    direct_names = {node.id for node in ast.walk(call) if isinstance(node, ast.Name)}
    permitted_names = {'reconstruct', 'obs', 'hub', 'source', 'config', 'point',
                       'horizontal_clearance', 'tower', 'float', 'time', 'limits', 'method'}
    if direct_names - permitted_names:
        failures.append('Estimator call reads undeclared state')
    if len(call.args) != 5 or not isinstance(call.args[4], ast.Lambda):
        failures.append('Estimator does not receive the isolated known-tower callback')
    assignments = [node for node in ast.walk(process) if isinstance(node, ast.Assign)]
    hub_assignments = [node for node in assignments if any(
        isinstance(target, ast.Tuple) and any(isinstance(value, ast.Name) and value.id == 'hub'
                                              for value in target.elts) for target in node.targets)]
    active_hub = [node for node in hub_assignments if node.lineno < call.lineno]
    if not active_hub:
        failures.append('Hub provenance is not an explicit blade_root_frame call')
    else:
        assignment = max(active_hub, key=lambda node: node.lineno)
        value = assignment.value
        if (not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name)
                or value.func.id != 'blade_root_frame'):
            failures.append('Hub is not computed from rigid support before estimation')
        attributes = {node.attr for node in ast.walk(value)
                      if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                      and node.value.id == 'source'}
        if attributes != {'scalars', 'poses'}:
            failures.append('Hub reads source fields other than fixed scalars and rigid pose')
        names = {node.id for node in ast.walk(value) if isinstance(node, ast.Name)}
        if names - {'blade_root_frame', 'source', 'index', 'nacelle'}:
            failures.append('Hub provenance includes undeclared hidden state')
    evaluation_lines = [node.lineno for node in assignments if any(
        isinstance(target, ast.Name) and target.id in ('evaluation', 'transform', 'tip')
        for target in node.targets)]
    if not evaluation_lines or min(evaluation_lines) <= call.lineno:
        failures.append('Truth/evaluation branch must follow reconstruction')
    observe_lines = [node.lineno for node in ast.walk(process)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                     and node.func.id == 'observe']
    if not observe_lines or max(observe_lines) >= call.lineno:
        failures.append('Forward raw observation generation must precede reconstruction')
    return dict(passed=not failures, failures=failures, path=str(Path(path).resolve()),
                sha256=digest(path), estimator_call_line=call.lineno,
                estimator_direct_source_attributes=direct_attributes,
                forward_observe_lines=observe_lines, separate_evaluation_lines=evaluation_lines,
                dataflow=['saved dynamic blade/tower surfaces -> observe -> raw S2/S3 fields',
                          'fixed scalars + rigid pose/nacelle -> blade_root_frame -> known hub',
                          'raw S2/S3 + known hub + fixed reference length + known moving tower -> reconstruct',
                          'terminal blade transform + independent reference tip -> evaluation after reconstruct'],
                scope='AST direct dependency and ordering check combined with runtime guarded reads; no physical validation')


def verify_turbine(source, data, config, *, independent_score_sink=None):
    _, _, limits = validate_calibration(config)
    rows = data['samples']
    if len(rows) != len(source.times):
        raise ValueError('Result rows must cover the complete source grid')
    reads = {name: Counter() for name in ('original', 'deleted', 'polluted')}
    failures = []
    failed_rows = Counter()
    maxima = Counter()
    reasons = Counter()
    eigen_reads = Counter()
    eigen_count = 0
    eigen_errors = []
    eigen_maxima = dict(direction=0., tip_estimate_m=0., clearance_estimate=0.)
    compare_eigen = config.get('reconstruction_method', 'hub-axis.v1') == 'hub-tls.v1'
    for index, row in enumerate(rows):
        predictions = {}
        for variant in ('original', 'deleted', 'polluted'):
            try:
                prediction = recompute_row(observation_variant(row, variant), source, index,
                                           config, limits, reads[variant])
                predictions[variant] = prediction
                reference = row['reconstruction'] if variant == 'original' else predictions['original']
                differences, maximum = prediction_difference(reference, prediction)
                maxima[variant] = max(maxima[variant], maximum)
                if differences:
                    raise AssertionError('; '.join(differences))
                if variant != 'original' and prediction['method'] != predictions['original']['method']:
                    raise AssertionError('Method changed after deleting/polluting truth')
            except (AssertionError, ValueError, KeyError, TypeError) as error:
                failed_rows[variant] += 1
                if len(failures) < 20:
                    failures.append(dict(index=index, time_s=row['time_s'], variant=variant, error=str(error)))
        if 'original' in predictions:
            reasons[predictions['original']['reason']] += 1
            if predictions['original']['valid']:
                try:
                    eigen = independent_eigen_tls(row, source, index, config, eigen_reads)
                    eigen_count += 1
                    if compare_eigen:
                        production = {key: predictions['original'][key] for key in eigen}
                        differences, _ = prediction_difference(production, eigen)
                        for key in eigen:
                            difference = float(np.max(np.abs(np.asarray(production[key]) - eigen[key])))
                            eigen_maxima[key] = max(eigen_maxima[key], difference)
                        if differences:
                            raise AssertionError('Independent eigensolver: ' + '; '.join(differences))
                    # The estimator/eigensolver has returned. Reference values
                    # enter only this separate scoring branch, never its inputs.
                    reference = row.get('evaluation', {}).get('clearance_reference_m')
                    if type(reference) in (int, float) and math.isfinite(reference):
                        error = eigen['clearance_estimate'] - reference
                        eigen_errors.append(error)
                        if independent_score_sink is not None:
                            independent_score_sink.append(error)
                except (AssertionError, ValueError, KeyError, TypeError) as error:
                    failed_rows['independent_tls'] += 1
                    if len(failures) < 20:
                        failures.append(dict(index=index, time_s=row['time_s'],
                                             variant='independent_tls', error=str(error)))
    return dict(passed=not failed_rows, rows=len(rows), tested_variants=list(reads),
                reconstruction_calls=3 * len(rows), failed_rows=dict(failed_rows),
                maximum_absolute_prediction_difference=dict(maxima),
                recomputed_reasons=dict(reasons), recorded_reads={name: dict(value) for name, value in reads.items()},
                independent_tls=dict(solver='numpy.linalg.eigh of uncentered scaled A.T@A; no reconstruct/SVD call',
                                     valid_pairs_recomputed=eigen_count, scored_pairs=len(eigen_errors),
                                     comparison_to_package=compare_eigen,
                                     maximum_direction_absolute_difference=eigen_maxima['direction'] if compare_eigen else None,
                                     maximum_tip_coordinate_absolute_difference_m=eigen_maxima['tip_estimate_m'] if compare_eigen else None,
                                     maximum_clearance_absolute_difference_m=eigen_maxima['clearance_estimate'] if compare_eigen else None,
                                     error_statistics=error_statistics(eigen_errors), recorded_reads=dict(eigen_reads),
                                     scoring_scope='reference clearance read only after independent prediction; signed error recomputed from clearance fields'),
                failures=failures, failures_truncated=sum(failed_rows.values()) > len(failures))


def verify_packages(packages):
    boundary = audit_postprocess_boundary()
    report = dict(schema='wfrl.dual-beam-isolation.v1', status='PASS',
                  absolute_reproduction_tolerance_m=1e-8, dataflow=boundary,
                  allowed_observation_fields=sorted(OBSERVATION_FIELDS),
                  allowed_recomputation_source_fields=sorted(SOURCE_FIELDS),
                  allowed_fixed_scalar_fields=sorted(SCALAR_FIELDS),
                  polluted_forbidden_fields=sorted(POISON_FIELDS), packages=[],
                  source_loader_scope='SourceGeometry validates the archive; guarded recomputation cannot access blade transforms, source tips, reference blade surfaces or saved source measurements',
                  evidence_scope='All supplied saved-grid rows, including invalid pairs; software input isolation only, not physical accuracy acceptance')
    for package in packages:
        entry = dict(package=str(Path(package).resolve()), passed=False, turbines={})
        report['packages'].append(entry)
        try:
            source_path, overlay = resolve_package(package)
            if overlay is None:
                raise ValueError('Expected a verified dual-beam result package')
            entry.update(reconstruction_method=overlay['config'].get('reconstruction_method', 'hub-axis.v1'),
                         manifest_sha256=digest(Path(package) / 'manifest.json'),
                         source=str(source_path), source_hashes=overlay['manifest']['source_hashes'])
            pooled_eigen_errors = []
            for turbine_id, data in overlay['results'].items():
                source = SourceGeometry(source_path, turbine_id)
                validate_replay_contract(source, overlay, turbine_id)
                result = verify_turbine(source, data, overlay['config'],
                                        independent_score_sink=pooled_eigen_errors)
                result['replay_contract_validated'] = True
                entry['turbines'][turbine_id] = result
                print(turbine_id, entry['reconstruction_method'], result['rows'],
                      'PASS' if result['passed'] else 'FAIL', flush=True)
            entry['passed'] = all(result['passed'] for result in entry['turbines'].values())
            entry['rows'] = sum(result['rows'] for result in entry['turbines'].values())
            entry['independent_tls_pooled_error_statistics'] = error_statistics(pooled_eigen_errors)
        except (AssertionError, ValueError, KeyError, TypeError, OSError) as error:
            entry['error'] = str(error)
    report['rows'] = sum(entry.get('rows', 0) for entry in report['packages'])
    report['reconstruction_calls'] = report['rows'] * 3
    if not boundary['passed'] or any(not entry['passed'] for entry in report['packages']):
        report['status'] = 'FAIL'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path, action='append',
                        help='Verified sidecar package; repeat for old method and TLS')
    parser.add_argument('--output', required=True, type=Path, help='JSON evidence file')
    args = parser.parse_args()
    report = verify_packages(args.package)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
    print(report['status'], report['rows'], 'rows', args.output, flush=True)
    raise SystemExit(0 if report['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
