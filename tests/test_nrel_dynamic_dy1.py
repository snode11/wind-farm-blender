"""DY1 must not turn missing image terms into successful measurements."""
import pytest

from scripts.nrel_dynamic_dy1 import ambiguity_summary


def row(q, distance, d, valid=True):
    return {'q_m': q, 'native_mean_distance_px': distance,
            'candidate_d_m': d, 'valid_image_terms': valid}


def test_missing_candidates_do_not_win_or_make_zero_error():
    values = [row([0, 0], None, 12, False), row([.25, 0], 4, 5)]
    result = ambiguity_summary(values, 3)
    assert result['best_q_m'] == [.25, 0]
    assert result['d_range_m'] == [5, 5]
    assert result['equivalent_state_count'] == 1


def test_range_uses_all_equivalent_states_not_only_optimizer_choice():
    result = ambiguity_summary([row([0, 0], 4, 1), row([.5, 0], 2, 4),
                                row([-.5, 0], 7, -2)], 3)
    assert result['d_range_m'] == [1, 4]
    assert result['search_boundary_best'] is True
    assert result['not_a_confidence_interval'] is True


def test_unavailable_is_null_and_invalid_tolerance_rejected():
    assert ambiguity_summary([row([0, 0], None, 0, False)], 3)['d_range_m'] is None
    with pytest.raises(ValueError):
        ambiguity_summary([], -1)
