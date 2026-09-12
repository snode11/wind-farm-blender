"""Portable structural contract for completed numerical comparisons.

These checks establish complete, internally consistent evidence, not authenticity
or an accuracy tolerance. Source-run association is checked by the producer.
"""
import math
from pathlib import PurePosixPath

CONTRACT = 'numerical-comparison-v2'
ASSESSMENT = 'NOT_ASSESSED_NO_TOLERANCE'


def require(condition, message):
    if not condition:
        raise ValueError('numerical evidence: ' + message)


def number(value, name, nonnegative=True):
    require(type(value) in (int, float) and math.isfinite(value), 'invalid ' + name)
    require(not nonnegative or value >= 0, 'negative ' + name)
    return value


def same(a, b, name):
    require(math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-10), 'inconsistent ' + name)


def run_name(value):
    require(isinstance(value, str) and bool(value), 'missing run reference')
    return PurePosixPath(value.replace('\\', '/')).name


def validate_numerical_evidence(evidence, run_id):
    require(isinstance(evidence, dict) and evidence.get('run_id') == run_id, 'run mismatch')
    for key in ('analytic', 'spatial', 'temporal', 'sampling'):
        require(isinstance(evidence.get(key), dict) and bool(evidence[key]), 'missing ' + key)
    analytic = evidence['analytic']
    for key in ('analytic_truth_error_m', 'rigid_formula_error_m'):
        number(analytic.get(key), 'analytic.' + key)
    polygon = analytic.get('circle_polygon_convergence')
    require(isinstance(polygon, list) and len(polygon) >= 2, 'missing analytic polygon comparison')
    previous = 0
    for row in polygon:
        require(isinstance(row, dict), 'invalid polygon record')
        count = row.get('vertices')
        require(type(count) is int and count > previous, 'invalid polygon refinement')
        previous = count
        number(row.get('distance_m'), 'polygon distance')
        number(row.get('error_m'), 'polygon error')
    space, time = evidence['spatial'], evidence['temporal']
    require(space.get('base_run') == run_id, 'spatial base mismatch')
    require(time.get('base_run') == space.get('refined_run'), 'temporal base mismatch')
    for comparison in (space, time):
        for key in ('base_run', 'refined_run'):
            name = comparison.get(key)
            require(isinstance(name, str) and name == run_name(name) and name not in ('.', '..'), 'invalid comparison run')
        require(comparison['base_run'] != comparison['refined_run'], 'identical comparison runs')
        count = comparison.get('paired_samples')
        require(type(count) is int and count > 0, 'missing paired samples')
        for key in ('max_truth_difference_m', 'mean_truth_difference_m', 'baseline_dt_s',
                    'refined_dt_s', 'baseline_fps', 'refined_fps'):
            number(comparison.get(key), key)
        require(comparison['mean_truth_difference_m'] <= comparison['max_truth_difference_m'], 'mean exceeds maximum')
        for key in ('baseline_dt_s', 'refined_dt_s', 'baseline_fps', 'refined_fps'):
            require(comparison[key] > 0, 'nonpositive grid')
        value = comparison.get('max_paired_slant_difference_m')
        if value is not None:
            number(value, 'paired slant difference')
    require(space.get('kind') == 'span_only_19_to_37', 'invalid spatial comparison kind')
    require(time.get('kind') == 'integration_and_output_timestep_halved', 'invalid temporal comparison kind')
    require((space.get('baseline_span_sections'), space.get('refined_span_sections'),
             time.get('baseline_span_sections'), time.get('refined_span_sections')) == (19, 37, 37, 37), 'invalid spatial grids')
    require(space['baseline_dt_s'] == space['refined_dt_s'] and space['baseline_fps'] == space['refined_fps'], 'spatial comparison changes time grid')
    require(time['baseline_dt_s'] == space['refined_dt_s'] and time['baseline_fps'] == space['refined_fps'], 'temporal grid linkage mismatch')
    require(time['refined_dt_s'] < time['baseline_dt_s'] and time['refined_fps'] > time['baseline_fps'], 'temporal grid not refined')
    sampling = evidence['sampling']
    require(sampling.get('verdict') == 'NUMERICAL_COMPARISON_ONLY', 'invalid sampling verdict')
    require(run_name(sampling.get('base_run')) == time['base_run'] and run_name(sampling.get('refined_run')) == time['refined_run'], 'sampling run mismatch')
    interval = sampling.get('time_range_s')
    require(isinstance(interval, list) and len(interval) == 2, 'missing sampling interval')
    require(number(interval[0], 'sampling start') < number(interval[1], 'sampling end'), 'invalid sampling interval')
    metrics = ('mae_m', 'max_abs_error_m', 'p95_abs_error_m', 'max_positive_bias_m')
    for key, fps in (('base', time['baseline_fps']), ('refined', time['refined_fps'])):
        row = sampling.get(key)
        require(isinstance(row, dict), 'missing sampling ' + key)
        for field in ('expected_samples', 'valid_samples', 'passage_count', 'missed_passage_count'):
            require(type(row.get(field)) is int and row[field] >= 0, 'invalid ' + field)
        require(row['expected_samples'] > 0 and row['valid_samples'] <= row['expected_samples'], 'invalid sample coverage')
        require(row['missed_passage_count'] <= row['passage_count'], 'invalid passage coverage')
        for field in ('valid_ratio', 'sample_interval_s', 'expected_duration_s', 'valid_duration_s'):
            number(row.get(field), field)
        same(row['valid_ratio'], row['valid_samples'] / row['expected_samples'], 'valid ratio')
        same(row['sample_interval_s'], 1 / fps, 'sample interval')
        same(row['expected_duration_s'], row['expected_samples'] / fps, 'expected duration')
        same(row['valid_duration_s'], row['valid_samples'] / fps, 'valid duration')
        for field in metrics:
            if row['valid_samples']:
                number(row.get(field), field)
            else:
                require(row.get(field) is None, 'empty errors must be null')
        require(isinstance(row.get('passages'), dict) and len(row['passages']) == row['passage_count'], 'missing passage details')
    changes = sampling.get('refined_minus_base')
    require(isinstance(changes, dict), 'missing sampling differences')
    for key in ('valid_ratio', 'valid_duration_s', 'expected_duration_s', *metrics, 'missed_passage_count'):
        a, b = sampling['base'][key], sampling['refined'][key]
        if a is None or b is None:
            require(changes.get(key) is None, 'invalid null difference')
        else:
            same(number(changes.get(key), 'difference ' + key, False), b - a, 'difference ' + key)
    require(isinstance(sampling.get('passage_comparison'), dict) and bool(sampling['passage_comparison']), 'missing passage comparison')
    difference = number(evidence.get('truth_numerical_error_m'), 'truth numerical difference')
    same(difference, max(space['max_truth_difference_m'], time['max_truth_difference_m'], 1e-5), 'truth numerical difference')
    return evidence
