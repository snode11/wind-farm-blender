import json

import numpy as np
import pytest

from scripts.lidar import probe_flex_policy as script


@pytest.mark.parametrize('key', ['yaw', 'load', 'yaw_command', 'yaw_measured'])
@pytest.mark.parametrize('value, valid', [(np.nan, True), (10., False)])
def test_flex_probe_rejects_invalid_observations_before_policy_call(key, value, valid):
    class Policy:
        metadata = {'obs_keys': [key]}

        def infer(self, measure):
            pytest.fail('invalid observation reached the policy')

    with pytest.raises(ValueError, match=rf'{key}.*机组 \[2\].*不做插补'):
        script.infer_validated(Policy(), {key: [0., value, 0.], f'{key}_valid': [True, valid, True]})


def test_flex_probe_records_null_masks_counts_and_yaw_in_degrees():
    measure = dict(yaw=[10., np.nan, 99.], yaw_valid=[True, True, False],
                   yaw_command=[10., np.nan, 99.], yaw_command_valid=[True, False, False],
                   yaw_measured=[12., 33., np.nan], yaw_measured_valid=[True, False, True],
                   load=[np.nan, 99., np.nan], load_valid=[True, False, False],
                   power=[1., np.nan, 3.])
    row = script.record_measurement(measure)
    assert row['returned_yaw_deg'] == [10., None, None]
    assert row['command_yaw_deg'] == [10., None, None]
    assert row['measured_yaw_deg'] == [12., None, None]
    assert row['yaw_measured_valid_count'] == 1
    assert row['load'] == [None] * 3
    assert row['load_status'] == '无有效数据'
    assert row['load_valid_count'] == 0
    assert row['power_mw'] == [1., None, 3.]
    json.dumps(row, allow_nan=False)


def test_flex_policy_receives_valid_degree_values_without_unused_load():
    measure = dict(yaw=[0., 10., -20.], yaw_valid=[True] * 3,
                   load=[np.nan] * 3, load_valid=[False] * 3)

    class Policy:
        metadata = {'obs_keys': ['yaw']}

        def infer(self, received):
            assert received is measure
            np.testing.assert_array_equal(received['yaw'], [0., 10., -20.])
            return 'valid'

    assert script.infer_validated(Policy(), measure) == 'valid'


def test_flex_physical_extrema_exclude_missing_and_empty_is_not_zero():
    columns = dict(RotSpeed=np.array([8., np.nan, 10.]),
                   BlPitch1=np.array([np.nan] * 3), YawPzn=np.array([12., 14., np.nan]))
    result = script.physical_check(columns, np.array([True] * 3), 'T1')
    assert result['rpm_range'] == [8., 10.]
    assert result['rpm_valid_count'] == 2
    assert result['pitch_deg_range'] is None
    assert result['pitch_valid_count'] == 0
    assert result['pitch_status'] == '无有效数据'
    assert result['final_actual_yaw_deg'] is None
    assert not result['passed']
    json.dumps(result, allow_nan=False)
    empty = script.physical_check(columns, np.array([False] * 3), 'T1')
    assert empty['rpm_range'] is None
    assert empty['rpm_status'] == '无有效数据'
