"""Versioned two-beam sidecar reader. Standard library only, vendored for Blender."""
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

SCHEMA = 'wfrl.dual-beam-review.v1'
METHOD_ALGORITHM_VERSIONS = {
    'hub-axis.v1': 'two-point-hub-extrapolation.v1',
    'hub-tls.v1': 'hub-constrained-tls.v1',
}
# Python 3.12 changed float sum's accumulation. This accepts only roundoff in
# recomputed aggregate statistics; measurements, alarms and identities stay exact.
STATISTICS_ROUNDOFF_TOLERANCE = 1e-12


def _algorithm_identity(metadata, method_key, context, *, require_tls_version):
    """Only historical absence can mean the original two-point algorithm."""
    method = metadata.get(method_key, 'hub-axis.v1')
    if not isinstance(method, str) or method not in METHOD_ALGORITHM_VERSIONS:
        raise ValueError('Unsupported dual-beam reconstruction method in ' + context)
    version = METHOD_ALGORITHM_VERSIONS[method]
    if 'algorithm_version' in metadata:
        if metadata['algorithm_version'] != version:
            raise ValueError('Dual-beam algorithm version differs from reconstruction method in ' + context)
    elif method == 'hub-tls.v1' and require_tls_version:
        raise ValueError('Missing TLS algorithm version in ' + context)
    return method, version


def _validate_algorithm_contract(config, manifest, results):
    """Check all turbines, including invalid pairs, before interpreting any state."""
    identity = _algorithm_identity(config, 'reconstruction_method', 'calibration',
                                   require_tls_version=True)
    declared = _algorithm_identity(manifest, 'reconstruction_method', 'manifest',
                                   require_tls_version=True)
    if declared != identity:
        raise ValueError('Dual-beam manifest reconstruction method differs from calibration')
    for turbine in results.values():
        for row in turbine.get('samples', []):
            recorded = _algorithm_identity(row['reconstruction'], 'method', 'sample reconstruction',
                                          require_tls_version=False)
            if recorded != identity:
                raise ValueError('Dual-beam reconstruction method differs from calibration')
    return identity


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_package(path):
    """Return verified geometry path and optional sidecar without weakening v3 checks."""
    path = Path(path).resolve()
    manifest = json.loads((path / 'manifest.json').read_text())
    if manifest.get('schema') != SCHEMA:
        return path, None
    if manifest.get('status') == 'REJECTED':
        raise ValueError('Rejected installation: ' + manifest.get('rejection_reason', 'inadmissible mount'))
    if manifest.get('status') != 'REVIEW_ONLY' or manifest.get('sample_kind') != 'source':
        raise ValueError('Unsupported dual-beam evidence status')
    source = (path / manifest['source_package']).resolve()
    if source == path:
        raise ValueError('Dual-beam source cannot refer to itself')
    source_manifest = json.loads((source / 'manifest.json').read_text())
    if source_manifest.get('schema') != 'wfrl.farm-flex-review.v3':
        raise ValueError('Dual-beam source must be v3 geometry')
    for name, expected in manifest['source_hashes'].items():
        if Path(name).name != name or digest(source / name) != expected:
            raise ValueError('Dual-beam source integrity mismatch: ' + name)
    if manifest['source_hashes'].get('manifest.json') != digest(source / 'manifest.json'):
        raise ValueError('Dual-beam source manifest missing or mismatched')
    if not isinstance(source_manifest.get('files'), dict):
        raise ValueError('Dual-beam source inventory is missing')
    expected_source = {'manifest.json': digest(source / 'manifest.json'), **source_manifest['files']}
    if manifest['source_hashes'] != expected_source:
        raise ValueError('Dual-beam source inventory is incomplete or differs from source')
    if not {'results.json', 'calibration.json'}.issubset(manifest['files']):
        raise ValueError('Dual-beam required result files are missing from inventory')
    for name, expected in manifest['files'].items():
        if Path(name).name != name or digest(path / name) != expected:
            raise ValueError('Dual-beam result integrity mismatch: ' + name)
    config = json.loads((path / 'calibration.json').read_text())
    if str(config.get('status', '')).startswith('REJECTED'):
        raise ValueError('Rejected installation calibration')
    if config.get('schema') != 'wfrl.dual-beam-calibration.v1':
        raise ValueError('Unsupported dual-beam calibration schema')
    for key in ('alarm_hold_s', 'measurement_hold_s', 'effective_length_m'):
        if not isinstance(config.get(key), (int, float)) or not math.isfinite(config[key]) or config[key] <= 0:
            raise ValueError('Invalid dual-beam calibration: ' + key)
    for key, count in (('origins_m', 3), ('angles_deg', 3)):
        values = config[key]
        if not isinstance(values, list) or len(values) != count:
            raise ValueError('Invalid dual-beam mounting: ' + key)
    if any(not isinstance(o, list) or len(o) != 3 or any(not math.isfinite(x) for x in o)
           for o in config['origins_m']):
        raise ValueError('Invalid dual-beam origins')
    angles = config['angles_deg']
    if not all(math.isfinite(a) and 0 < a < 90 for a in angles) or not angles[0] < angles[1] < angles[2]:
        raise ValueError('Invalid dual-beam directions')
    results = json.loads((path / 'results.json').read_text())
    if set(results) != set(source_manifest['turbine_ids']):
        raise ValueError('Dual-beam turbines differ from source')
    if manifest['segment'] != source_manifest['segment']:
        raise ValueError('Dual-beam segment differs from source')
    _validate_algorithm_contract(config, manifest, results)
    return source, dict(manifest=manifest, config=config, results=results)


def error_statistics(errors):
    n = len(errors)
    ordered = sorted(abs(x) for x in errors)
    return dict(error_samples=n, bias_m=sum(errors)/n if n else None,
                mae_m=sum(ordered)/n if n else None,
                max_abs_error_m=ordered[-1] if n else None,
                max_overestimate_m=max(0., max(errors)) if n else None,
                p95_abs_error_m=ordered[math.ceil(.95*n)-1] if n else None,
                p95_method='nearest-rank')


def precompute(rows, config):
    """Prefix state from all saved samples; event end times stay out of past states."""
    cumulative, events, errors = [], [], []
    held, last_hit, current_event = None, None, None
    expected, valid_expected, valid_total = 0, 0, 0
    passages = {}
    previous_passage, completed, missed = None, 0, 0
    initial_partial = rows[0].get('passage_id') if rows else None
    for row in rows:
        t = row['time_s']
        pair, evaluation = row['reconstruction'], row['evaluation']
        alarm = row['s1_observation_state']
        passage = row.get('passage_id')
        if previous_passage is not None and passage != previous_passage and previous_passage != initial_partial:
            completed += 1
            missed += not passages[previous_passage]
        previous_passage = passage
        if passage is not None:
            expected += 1
            passages.setdefault(passage, False)
        if pair['valid']:
            valid_total += 1
            if passage is not None and pair['blade_id'] == row['expected_blade_id']:
                valid_expected += 1
                passages[passage] = True
            err = evaluation.get('clearance_error_m')
            if err is not None:
                errors.append(err)
            held = dict(time_s=t, blade_id=pair['blade_id'], estimate_m=pair['clearance_estimate'],
                        truth_m=evaluation.get('clearance_reference_m'), error_m=err,
                        expires_at_s=t + config['measurement_hold_s'])
        if alarm == 'triggered':
            last_hit = t
            blade = row['observations']['S1']['blade_id']
            if current_event is None or current_event['blade_id'] != blade:
                current_event = dict(start_s=t, last_hit_s=t, output_time_s=t,
                                     blade_id=blade, hit_times_s=[], observation_delay_s=0.)
                events.append(current_event)
            current_event['last_hit_s'] = t
            current_event['hit_times_s'].append(t)
        else:
            current_event = None
        alarm_active = last_hit is not None and t <= last_hit + config['alarm_hold_s']
        state = dict(observation_state=alarm, active=alarm_active, last_hit_s=last_hit,
                     expires_at_s=None if last_hit is None else last_hit+config['alarm_hold_s'],
                     event_count=len(events))
        statistics = dict(expected_samples=expected, valid_samples=valid_expected,
                          all_valid_samples=valid_total,
                          valid_ratio=valid_expected/expected if expected else None,
                          passage_count=len(passages), completed_passage_count=completed,
                          missed_passage_count=missed,
                          **error_statistics(errors))
        cumulative.append(dict(time_s=t, measurement=deepcopy(held), alarm=state, statistics=statistics))
    return cumulative, events


def _exact_equal(actual, saved):
    """Structural equality preserving boolean/count/time type and value."""
    if type(actual) is not type(saved):
        return False
    if isinstance(actual, dict):
        return (actual.keys() == saved.keys()
                and all(_exact_equal(value, saved[key]) for key, value in actual.items()))
    if isinstance(actual, list):
        return len(actual) == len(saved) and all(_exact_equal(a, b) for a, b in zip(actual, saved))
    return actual == saved


def _prefix_states_equal(actual, saved):
    """Only flat statistics floats allow fixed absolute summation roundoff."""
    if not isinstance(saved, list) or len(actual) != len(saved):
        return False
    for computed, stored in zip(actual, saved):
        if not isinstance(stored, dict) or computed.keys() != stored.keys():
            return False
        for key, value in computed.items():
            if key != 'statistics':
                if not _exact_equal(value, stored[key]):
                    return False
                continue
            statistics = stored[key]
            if not isinstance(statistics, dict) or value.keys() != statistics.keys():
                return False
            for field, result in value.items():
                recorded = statistics[field]
                if type(result) is float and type(recorded) is float:
                    if (not math.isfinite(result) or not math.isfinite(recorded)
                            or abs(result-recorded) > STATISTICS_ROUNDOFF_TOLERANCE):
                        return False
                elif not _exact_equal(result, recorded):
                    return False
    return True


class DualBeamReader:
    """Uses existing motion interpolation, replacing all measurement/alarm semantics."""
    def __init__(self, motion_reader, overlay, turbine_id):
        self.motion_reader = motion_reader
        self.start_s, self.end_s = motion_reader.start_s, motion_reader.end_s
        self.config = overlay['config']
        self.reconstruction_method, self.algorithm_version = _validate_algorithm_contract(
            self.config, overlay['manifest'], overlay['results'])
        self.rows = overlay['results'][turbine_id]['samples']
        self._times = [r['time_s'] for r in self.rows]
        if self._times != [m['time_s'] for m in motion_reader.package.motion]:
            raise ValueError('Dual-beam samples must cover the full source grid')
        for row in self.rows:
            if row.get('sample_kind') != 'source' or set(row['observations']) != {'S1', 'S2', 'S3'}:
                raise ValueError('Invalid dual-beam observation contract')
            pair = row['reconstruction']
            estimate = pair['clearance_estimate']
            if ((pair['valid'] and not (type(estimate) in (int, float) and math.isfinite(estimate)))
                    or (not pair['valid'] and estimate is not None)):
                raise ValueError('Invalid dual-beam estimate/null contract')
            for obs in row['observations'].values():
                if obs['time_s'] != row['time_s']:
                    raise ValueError('Dual-beam observations are not simultaneous')
            if pair['valid']:
                a, b = (row['observations'][s] for s in ('S2', 'S3'))
                if (not all(o['observed'] and o['valid'] and o['first_object']=='blade' for o in (a,b))
                        or not a['blade_id']==b['blade_id']==pair['blade_id']):
                    raise ValueError('Invalid dual-beam blade pairing')
            s1 = row['observations']['S1']
            expected_alarm = ('unknown' if not s1['observed'] or s1['reason']=='out_of_range'
                              else 'triggered' if s1['valid'] and s1['first_object']=='blade' else 'not_triggered')
            if row['s1_observation_state'] != expected_alarm:
                raise ValueError('S1 alarm differs from independent observation')
        self.cumulative, self.events = precompute(self.rows, self.config)
        if (not _prefix_states_equal(self.cumulative, overlay['results'][turbine_id]['cumulative'])
                or not _exact_equal(self.events, overlay['results'][turbine_id]['events'])):
            raise ValueError('Dual-beam precomputed state mismatch')
        self.package = SimpleNamespace(manifest={**motion_reader.package.manifest,
                                       'measurement_mode': 'dual_beam', 'dual_beam': overlay['manifest']},
                                       motion=motion_reader.package.motion, measurements=self.rows,
                                       cumulative=self.cumulative, statistics=self.cumulative[-1]['statistics'])
        self._beam_times = {name: [r['time_s'] for r in self.rows if r['observations'][name]['valid']]
                            for name in ('S1', 'S2', 'S3')}

    def beam_activity(self, time_s, pulse_s=.2):
        result = []
        for name in ('S1', 'S2', 'S3'):
            ts = self._beam_times[name]
            i = bisect_right(ts, time_s) - 1
            result.append(i >= 0 and time_s-ts[i] <= pulse_s+1e-9)
        return tuple(result)

    def at(self, time_s):
        value = self.motion_reader.at(time_s)
        t = value['time_s']
        i = bisect_right(self._times, t) - 1
        row, state = self.rows[i], self.cumulative[i]
        held = state['measurement']
        age = t-held['time_s'] if held else None
        measurement = deepcopy(held) if held and t <= held['expires_at_s'] else None
        alarm = deepcopy(state['alarm'])
        alarm['active'] = alarm['expires_at_s'] is not None and t <= alarm['expires_at_s']
        alarm['observation_time_s'] = row['time_s']
        alarm['replay_display_time_s'] = t  # requested simulation clock; not measured screen latency
        return dict(time_s=t, motion=value['motion'], measurement=measurement,
                    measurement_age_s=age, measurement_mode='dual_beam',
                    measurement_status=('valid' if row['reconstruction']['valid'] else
                                        'invalid' if any(not row['observations'][s]['observed'] for s in ('S2', 'S3'))
                                        or row['reconstruction']['reason'] in ('invalid_calibration', 'invalid_input', 'invalid_range_or_direction', 'not_same_time')
                                        else 'waiting'),
                    current_reason=row['reconstruction']['reason'], observation_time_s=row['time_s'],
                    reconstruction_method=self.reconstruction_method, algorithm_version=self.algorithm_version,
                    performance_status=self.package.manifest['dual_beam'].get('performance_status', 'PENDING_ACCEPTANCE'),
                    alarm=alarm, statistics=deepcopy(state['statistics']),
                    status='s1_triggered' if alarm['active'] else ('s1_unknown' if alarm['observation_state']=='unknown' else 's1_not_triggered'),
                    installation_status=self.config['status'])
