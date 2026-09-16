"""Versioned, offline lidar replay. No physics or wall-clock computation occurs here."""
from __future__ import annotations

import bisect
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import tempfile
from .evidence import CONTRACT, ASSESSMENT, validate_numerical_evidence

SCHEMA_VERSION = '1.0'
FILES = ('motion.json', 'measurements.json', 'cumulative.json', 'statistics.json', 'report.md')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_finite(x) for x in value)


def _scan(value):
    if isinstance(value, float):
        _require(math.isfinite(value), 'non-finite number in package')
    elif isinstance(value, dict):
        for item in value.values():
            _scan(item)
    elif isinstance(value, list):
        for item in value:
            _scan(item)


def _ordered(records, name):
    times = [r.get('time_s') for r in records]
    _require(all(_finite(t) for t in times), f'{name}: invalid time_s')
    _require(all(a <= b for a, b in zip(times, times[1:])), f'{name}: unordered time')
    return times


def _stats(records):
    expected = [r for r in records if r['expected']]
    valid = [r for r in expected if r['beams']['B2']['valid']]
    errors = [r['beams']['B2']['error_m'] for r in valid]
    absolute = sorted(abs(e) for e in errors)
    passages = {r['passage_id'] for r in expected}
    measured = {r['passage_id'] for r in valid}
    return dict(expected_samples=len(expected), valid_samples=len(valid),
                valid_ratio=len(valid) / len(expected) if expected else None,
                mae_m=sum(absolute) / len(absolute) if absolute else None,
                max_abs_error_m=max(absolute) if absolute else None,
                p95_abs_error_m=absolute[max(0, math.ceil(.95 * len(absolute)) - 1)] if absolute else None,
                max_positive_bias_m=max(0., max(errors)) if errors else None,
                passage_count=len(passages), missed_passage_count=len(passages - measured))


def precompute(measurements, motion, replay):
    """B2 denominator is expected samples, including invalid/missed observations."""
    times = [m['time_s'] for m in motion]
    rows, last, state = [], None, 'waiting'
    # Coordinate-compressed Fenwick counts provide exact nearest-rank P95 in O(log n).
    values = sorted({abs(r['beams']['B2']['error_m']) for r in measurements if r['expected'] and r['beams']['B2']['valid']})
    tree = [0] * (len(values) + 1)
    expected_count = valid_count = 0
    total = maximum = positive = 0.
    passages, measured = set(), set()
    def statistics(record):
        nonlocal expected_count, valid_count, total, maximum, positive
        if record['expected']:
            expected_count += 1
            passages.add(record['passage_id'])
            if record['beams']['B2']['valid']:
                valid_count += 1
                measured.add(record['passage_id'])
                error = record['beams']['B2']['error_m']
                absolute = abs(error)
                total += absolute
                maximum = max(maximum, absolute)
                positive = max(positive, error)
                index = bisect.bisect_left(values, absolute) + 1
                while index < len(tree):
                    tree[index] += 1
                    index += index & -index
        percentile = None
        if valid_count:
            rank, index = math.ceil(.95 * valid_count), 0
            bit = 1 << (len(tree).bit_length() - 1)
            while bit:
                candidate = index + bit
                if candidate < len(tree) and tree[candidate] < rank:
                    rank -= tree[candidate]
                    index = candidate
                bit >>= 1
            percentile = values[index]
        return dict(expected_samples=expected_count, valid_samples=valid_count,
            valid_ratio=valid_count / expected_count if expected_count else None,
            mae_m=total / valid_count if valid_count else None,
            max_abs_error_m=maximum if valid_count else None,
            p95_abs_error_m=percentile,max_positive_bias_m=positive if valid_count else None,
            passage_count=len(passages),missed_passage_count=len(passages)-len(measured))
    for record in measurements:
        t = record['time_s']
        current = motion[max(0, bisect.bisect_right(times, t) - 1)]
        rpm = abs(current['rotor_speed_rpm'])
        hold = min(replay['max_hold_s'], 20. / rpm * replay['passage_margin']) if rpm else replay['max_hold_s']
        if last and t > last['expires_at_s']:
            last, state = None, 'waiting'
        beam = record['beams']['B2']
        if record['expected'] and beam['valid']:
            value = beam['estimate_m']
            if value <= replay['threshold_m']:
                state = 'near_threshold'
            elif state != 'near_threshold' or value >= replay['threshold_m'] + replay['hysteresis_m']:
                state = 'above_threshold'
            last = dict(time_s=t, blade_id=record['blade_id'], truth_m=record['truth_m'],
                        estimate_m=value, error_m=beam['error_m'], expires_at_s=t + hold)
        rows.append(dict(time_s=t, statistics=statistics(record), measurement=last.copy() if last else None, status=state))
    return rows, _stats(measurements)


def _validate(manifest, motion, measurements, *, require_current_evidence=False):
    _scan([manifest, motion, measurements])
    _require(manifest.get('schema_version') == SCHEMA_VERSION, 'unsupported schema_version')
    _require(manifest.get('status') == 'READY', '回放数据未就绪: status is not READY')
    for key in ('run_id', 'turbine_id', 'coordinate_system', 'model', 'controller', 'calibration', 'postprocess_version'):
        _require(bool(manifest.get(key)), f'missing provenance: {key}')
    _require(manifest.get('source') == 'FAST.Farm', 'source must be FAST.Farm')
    _require(manifest.get('units') == dict(time='s', distance='m', angle='deg'), 'unsupported units')
    _require(bool(manifest.get('algorithm', {}).get('version')), 'missing algorithm version')
    flex = manifest.get('flexibility', {})
    _require(flex.get('blade_enabled') is True and flex.get('reconstruction_validated') is True and bool(flex.get('tower_assumption')), 'missing validated flexible geometry provenance')
    sources = manifest.get('raw_sources', [])
    _require(bool(sources), 'missing raw_sources')
    for source in sources:
        digest = source.get('sha256', '')
        _require(bool(source.get('path')) and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest), 'invalid raw source digest')
    validation = manifest.get('validation', {})
    current = validation.get('evidence_contract') == CONTRACT
    _require(not require_current_evidence or current, 'new publication requires numerical-comparison-v2 evidence')
    if current:
        for key in ('spatial_convergence_verified', 'temporal_convergence_verified'):
            _require(key not in validation, 'comparison completion is not convergence acceptance')
        _require(validation.get('convergence_assessment') == ASSESSMENT, 'missing explicit convergence assessment')
        evidence = validate_numerical_evidence(validation.get('numerical_evidence'), manifest['run_id'])
        _require(validation.get('truth_numerical_error_m') == evidence['truth_numerical_error_m'], 'inconsistent numerical difference')
        keys = ('analytic_verified', 'spatial_comparison_completed', 'temporal_comparison_completed', 'collision_excluded')
    else:
        _require('evidence_contract' not in validation, 'unsupported evidence contract')
        keys = ('analytic_verified', 'spatial_convergence_verified', 'temporal_convergence_verified', 'collision_excluded')
    for key in keys:
        _require(validation.get(key) is True, f'missing evidence: {key}')
    _require(_finite(validation.get('truth_numerical_error_m')) and validation['truth_numerical_error_m'] >= 0, 'missing truth numerical error')
    segment = manifest.get('segment', {})
    _require(segment.get('id') in ('normal', 'close') and bool(segment.get('selection_basis')), 'invalid segment definition')
    start, end = segment.get('start_s'), segment.get('end_s')
    _require(_finite(start) and _finite(end) and start < end, 'invalid segment range')
    if current:
        _require(evidence['sampling']['time_range_s'] == [start, end], 'numerical evidence segment mismatch')
    original = manifest.get('original_time_range_s', [])
    _require(len(original) == 2 and all(_finite(x) for x in original) and original[0] <= start < end <= original[1], 'invalid original time range')
    replay = manifest.get('replay', {})
    for key in ('threshold_m', 'hysteresis_m', 'max_hold_s', 'passage_margin'):
        _require(_finite(replay.get(key)), f'invalid replay {key}')
    _require(replay['hysteresis_m'] >= 0 and replay['max_hold_s'] > 0 and replay['passage_margin'] >= 1, 'invalid replay timing/hysteresis')
    _require(manifest.get('motion_azimuth') == 'unwrapped_deg', 'motion azimuth must declare unwrapped_deg')
    _require(bool(motion), 'missing motion')
    times = _ordered(motion, 'motion')
    _require(times[0] == start and times[-1] == end and all(a < b for a, b in zip(times, times[1:])), 'motion must cover segment with unique times')
    for row in motion:
        _require(_vector(row.get('nacelle_position_m')) and _vector(row.get('nacelle_orientation_deg')), 'missing nacelle pose')
        _require(all(_finite(row.get(k)) for k in ('azimuth_deg', 'yaw_deg', 'rotor_speed_rpm')), 'invalid motion pose')
        _require(len(row.get('pitch_deg', [])) == 3 and all(_finite(p) for p in row['pitch_deg']), 'invalid pitch_deg')
    _ordered(measurements, 'measurements')
    identities = set()
    for row in measurements:
        identity = (row['time_s'], row.get('blade_id'))
        _require(identity not in identities, 'duplicate measurement')
        identities.add(identity)
        _require(start <= row['time_s'] <= end and type(row.get('blade_id')) is int and row['blade_id'] in (1, 2, 3), 'measurement outside segment or invalid blade')
        _require(type(row.get('expected')) is bool and bool(row.get('passage_id')), 'missing evaluation grid/passage')
        for name in ('B1', 'B2', 'B3'):
            beam = row.get('beams', {}).get(name, {})
            _require(type(beam.get('valid')) is bool, f'missing {name} validity')
            if beam['valid']:
                _require(_vector(beam.get('hit_point_m')) and _vector(row.get('truth_tip_point_m')) and _vector(row.get('truth_wall_point_m')), 'missing geometry audit endpoints')
                _require(row['expected'] and all(_finite(beam.get(k)) for k in ('slant_range_m', 'estimate_m', 'error_m')) and _finite(row.get('truth_m')), 'invalid valid measurement')
                _require(beam['slant_range_m'] >= 0 and math.isclose(beam['error_m'], beam['estimate_m'] - row['truth_m'], abs_tol=1e-9), 'inconsistent signed error')
                tip, wall = row['truth_tip_point_m'], row['truth_wall_point_m']
                _require(math.isclose(tip[2], wall[2], abs_tol=1e-7) and math.isclose(math.dist(tip, wall), abs(row['truth_m']), abs_tol=1e-6), 'inconsistent truth endpoints')
            else:
                _require(bool(beam.get('reason')) and beam.get('estimate_m') is None and beam.get('error_m') is None, 'invalid beam needs reason and null estimate/error')
    _require(_stats(measurements)['valid_samples'] > 0, 'no valid B2 observations; cannot publish customer READY package')


class ReplayPackage:
    def __init__(self, manifest, motion, measurements, cumulative, statistics):
        self.manifest, self.motion, self.measurements = manifest, motion, measurements
        self.cumulative, self.statistics = cumulative, statistics

    @classmethod
    def load(cls, path):
        path = Path(path)
        try:
            manifest = json.loads((path / 'manifest.json').read_text())
            payload = {}
            for name in FILES:
                raw = (path / name).read_bytes()
                _require(hashlib.sha256(raw).hexdigest() == manifest.get('files', {}).get(name), f'integrity mismatch: {name}')
                if name == 'report.md':
                    _require(bool(raw.decode('utf-8').strip()), 'missing precision report')
                if name.endswith('.json'):
                    payload[name] = json.loads(raw)
            _validate(manifest, payload['motion.json'], payload['measurements.json'])
            cumulative, statistics = precompute(payload['measurements.json'], payload['motion.json'], manifest['replay'])
            _require(cumulative == payload['cumulative.json'] and statistics == payload['statistics.json'], 'inconsistent precomputed statistics/state')
            return cls(manifest, payload['motion.json'], payload['measurements.json'], cumulative, statistics)
        except (OSError, KeyError, TypeError, AttributeError, IndexError, OverflowError, json.JSONDecodeError) as exc:
            raise ValueError(f'回放数据未就绪: {exc}') from exc


def publish_package(path, manifest, motion, measurements, report):
    """Never overwrites an existing package; publish only after complete validation."""
    path = Path(path)
    _require(not path.exists(), 'destination already exists')
    _require(isinstance(report, str) and bool(report.strip()), 'missing precision report')
    _validate(manifest, motion, measurements, require_current_evidence=True)
    cumulative, statistics = precompute(measurements, motion, manifest['replay'])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.lidar-', dir=path.parent))
    try:
        content = dict(zip(FILES, (motion, measurements, cumulative, statistics, report)))
        for name, value in content.items():
            (temporary / name).write_text(value if name.endswith('.md') else json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        complete = dict(manifest, files={name: hashlib.sha256((temporary / name).read_bytes()).hexdigest() for name in FILES})
        (temporary / 'manifest.json').write_text(json.dumps(complete, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        ReplayPackage.load(temporary)
        temporary.rename(path)
    except Exception as exc:
        (temporary / 'failure.json').write_text(json.dumps({'error': str(exc), 'status': 'FAILED'}), encoding='utf-8')
        raise
    return path


class ReplayReader:
    def __init__(self, package):
        self.package = package
        self.start_s = package.manifest['segment']['start_s']
        self.end_s = package.manifest['segment']['end_s']
        self._motion_times = [r['time_s'] for r in package.motion]
        self._times = [r['time_s'] for r in package.cumulative]
        # Resolve held B2 readings to their original sample without changing
        # the serialized cumulative-state contract of existing packages.
        self._b2_ranges = {
            (r['time_s'], r['blade_id']): r['beams']['B2'].get('slant_range_m')
            for r in package.measurements
            if r.get('expected') and r['beams']['B2'].get('valid')
        }

    def at(self, time_s):
        _require(_finite(time_s), 'invalid replay time')
        t = min(self.end_s, max(self.start_s, time_s))
        index = bisect.bisect_right(self._motion_times, t) - 1
        motion = deepcopy(self.package.motion[index])
        if index + 1 < len(self._motion_times):
            following = self.package.motion[index + 1]
            ratio = (t - motion['time_s']) / (following['time_s'] - motion['time_s'])
            for key in ('azimuth_deg', 'yaw_deg', 'rotor_speed_rpm'):
                delta = following[key] - motion[key]
                if key == 'yaw_deg':
                    delta = (delta + 180) % 360 - 180
                motion[key] += ratio * delta
            motion['pitch_deg'] = [a + ratio * (b - a) for a, b in zip(motion['pitch_deg'], following['pitch_deg'])]
        motion['time_s'] = t
        index = bisect.bisect_right(self._times, t) - 1
        row = self.package.cumulative[index] if index >= 0 else dict(measurement=None, status='waiting', statistics=_stats([]))
        measurement = row['measurement']
        age = t - measurement['time_s'] if measurement else None
        expired = measurement is None or t > measurement['expires_at_s']
        displayed = None if expired else deepcopy(measurement)
        if displayed is not None:
            distance = self._b2_ranges.get((displayed['time_s'], displayed['blade_id']))
            displayed['slant_range_m'] = distance if _finite(distance) and distance >= 0 else None
        return dict(time_s=t, motion=motion, measurement=displayed,
                    measurement_age_s=age, status='waiting' if expired else row['status'], statistics=deepcopy(row['statistics']))
