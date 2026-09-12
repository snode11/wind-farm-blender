"""Explicit mathematical expectations for nearest-rank P95 and missing data."""
import pytest
from wfrl.lidar.replay import _stats, precompute


def compute(errors):
    rows = [dict(time_s=i, blade_id=1, expected=True, passage_id=str(i),
                 truth_m=100, beams={'B2': dict(valid=e is not None,
                 estimate_m=100 + e if e is not None else None, error_m=e)})
            for i, e in enumerate(errors)]
    cumulative, total = precompute(rows, [{'time_s': 0, 'rotor_speed_rpm': 0}],
                                  dict(max_hold_s=2, passage_margin=1.2,
                                       threshold_m=5, hysteresis_m=.2))
    return rows, cumulative, total


@pytest.mark.parametrize('errors,p95,mae,maximum,positive', [
    (list(range(1, 21)), 19, 10.5, 20, 20),
    (list(range(1, 22)), 20, 11, 21, 21),
    ([2] * 19 + [100], 2, 6.9, 100, 100),
    ([2] * 19 + [100, 100], 100, 238 / 21, 100, 100),
    ([-1, -2, -3, -4], 4, 2.5, 4, 0),
])
def test_exact_percentile_edges(errors, p95, mae, maximum, positive):
    rows, cumulative, total = compute(errors)
    # Check both the standalone and cumulative Fenwick implementations against
    # constants, not against one another.
    for stats in (_stats(rows), total, cumulative[-1]['statistics']):
        assert stats['p95_abs_error_m'] == p95
        assert stats['mae_m'] == pytest.approx(mae)
        assert stats['max_abs_error_m'] == maximum
        assert stats['max_positive_bias_m'] == positive
        assert stats['valid_samples'] == len(errors)


def test_prefix_crosses_twenty_sample_rank_boundary():
    _, cumulative, _ = compute(list(range(1, 22)))
    assert cumulative[19]['statistics']['p95_abs_error_m'] == 19
    assert cumulative[20]['statistics']['p95_abs_error_m'] == 20


@pytest.mark.parametrize('errors,ratio,missed', [([], None, 0), ([None] * 21, 0, 21)])
def test_no_valid_errors_have_no_precision_statistics(errors, ratio, missed):
    rows, cumulative, total = compute(errors)
    results = [_stats(rows), total]
    if cumulative:
        results.append(cumulative[-1]['statistics'])
    for stats in results:
        assert stats['valid_samples'] == 0
        assert stats['expected_samples'] == len(errors)
        assert stats['valid_ratio'] == ratio
        assert stats['missed_passage_count'] == missed
        for key in ('mae_m', 'max_abs_error_m', 'p95_abs_error_m', 'max_positive_bias_m'):
            assert stats[key] is None
