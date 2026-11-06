"""Finite-record rainflow benchmarks, DEL normalization, and BeamDyn validity."""

from collections import defaultdict
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


SPEC = importlib.util.spec_from_file_location(
    'blade_del', Path(__file__).resolve().parents[1] / 'scripts/analysis/blade_del.py')
del_analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(del_analysis)


def aggregate(cycles):
    counts = defaultdict(float)
    for cycle in cycles:
        counts[(cycle.range, cycle.mean)] += cycle.count
    return dict(counts)


def test_astm_classic_textbook_sequence():
    # Known range/count table: FFPACK ASTM E1049 example (no library dependency).
    # https://ffpack.readthedocs.io/en/latest/moduleCookbook/lccASTMCountingMethods.html
    sequence = [-2, 1, -3, 5, -1, 3, -4, 4, -2]
    cycles = del_analysis.rainflow_four_point(sequence)
    assert aggregate(cycles) == {
        (3., -.5): .5, (4., -1.): .5, (4., 1.): 1.,
        (8., 1.): .5, (9., .5): .5, (8., 0.): .5, (6., 1.): .5}
    by_range = defaultdict(float)
    for cycle in cycles:
        by_range[cycle.range] += cycle.count
    assert dict(by_range) == {3.: .5, 4.: 1.5, 6.: .5, 8.: 1., 9.: .5}
    expected_sum = .5 * 3**10 + 1.5 * 4**10 + .5 * 6**10 + 8**10 + .5 * 9**10
    assert del_analysis.damage_equivalent_load(cycles, 8.) == pytest.approx((expected_sum / 8.)**.1)


def test_nested_mathworks_astm_benchmark():
    # Range, mean, count values in the published algorithm example:
    # https://www.mathworks.com/help/signal/ref/rainflow.html
    sequence = [-2, 1, -3, 5, -1, 3, -4, 4, -3, 1, -2, 3, 2, 6]
    assert aggregate(del_analysis.rainflow_four_point(sequence)) == {
        (3., -.5): 1.5, (4., -1.): .5, (4., 1.): 1., (8., 1.): .5,
        (1., 2.5): 1., (7., .5): 1., (9., .5): .5, (10., 1.): .5}


@pytest.mark.parametrize('sequence,expected', [
    ([], {}), ([7], {}), ([7, 7, 7], {}),
    ([0, 1, 2], {(2., 1.): .5}),
    ([0, 2, 0], {(2., 1.): 1.}),
    ([0, 2, 0, 2, 0], {(2., 1.): 2.}),
    ([0, 0, 1, 2, 2, 1, 0, 0], {(2., 1.): 1.}),
])
def test_endpoints_plateaus_and_equal_ranges(sequence, expected):
    assert aggregate(del_analysis.rainflow_four_point(sequence)) == expected


@pytest.mark.parametrize('m', [8., 10., 12.])
def test_constant_amplitude_sinusoid_del_equals_range(m):
    times = np.arange(2401) / 40.
    amplitude = 1.25e6
    # A 1 Hz sinusoid spans 60 complete periods from maximum to maximum.
    loads = 3e6 + amplitude * np.sin(2 * np.pi * times + np.pi / 2)
    cycles = del_analysis.rainflow_four_point(loads)
    assert sum(c.count for c in cycles) == 60.
    assert del_analysis.damage_equivalent_load(cycles, 60., m) == pytest.approx(2 * amplitude)


def test_equivalent_frequency_duration_and_mean_offset():
    times = np.arange(2401) / 40.
    loads = 2 * np.cos(2 * np.pi * 2 * times)
    cycles = del_analysis.rainflow_four_point(loads)
    assert del_analysis.damage_equivalent_load(cycles, 60.) == pytest.approx(4 * 2**.1)
    assert del_analysis.damage_equivalent_load(cycles, 60., equivalent_frequency_hz=2.) == pytest.approx(4.)
    shifted = del_analysis.rainflow_four_point(loads + 50)
    assert del_analysis.damage_equivalent_load(shifted, 60.) == pytest.approx(
        del_analysis.damage_equivalent_load(cycles, 60.))


@pytest.mark.parametrize('values', [[0., np.nan], [np.inf], [[1., 2.]]])
def test_invalid_loads_rejected(values):
    with pytest.raises(ValueError, match='finite one-dimensional'):
        del_analysis.rainflow_four_point(values)


@pytest.mark.parametrize('duration,m,frequency', [
    (0., 10., 1.), (-1., 10., 1.), (1., 0., 1.), (1., np.nan, 1.),
    (np.inf, 10., 1.), (1., 10., 0.), (1., 10., np.inf),
])
def test_invalid_del_parameters_rejected(duration, m, frequency):
    with pytest.raises(ValueError, match='finite and positive'):
        del_analysis.damage_equivalent_load([], duration, m, frequency)


def test_decimation_recounts_cycles_with_fixed_exposure():
    times = np.arange(2401) / 40. + 117.
    loads = np.cos(2 * np.pi * 3 * (times - times[0]))
    result = del_analysis.analyze_series(times, loads)
    for label, samples in [('40_Hz', 2401), ('4_Hz', 241), ('1_Hz', 61), ('1_over_3_Hz', 21)]:
        row = result['sampling'][label]
        assert row['sample_count'] == samples
        assert row['duration_s'] == row['equivalent_cycles'] == 60.
        assert row['sampled_interval_s'] == [117., 177.]
        assert sum(row['range_histogram_weighted_counts']) == row['weighted_cycle_count']
    # 3 Hz aliases to 1 Hz at 4 Hz sampling and to DC at 1 Hz sampling.
    assert result['sampling']['40_Hz']['weighted_cycle_count'] == 180.
    assert result['sampling']['4_Hz']['weighted_cycle_count'] == 60.
    assert result['sampling']['1_Hz']['del_Nm']['10'] < 1e-12
    assert result['comparison_to_40_Hz']['1_Hz']['del_change_pct']['10'] == pytest.approx(-100.)


def test_zero_signal_has_zero_del_and_undefined_relative_error():
    result = del_analysis.analyze_series(np.arange(121) / 40., np.ones(121))
    for label in del_analysis.SAMPLING:
        assert result['sampling'][label]['del_Nm']['10'] == 0.
        assert result['comparison_to_40_Hz'][label]['del_change_pct']['10'] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('times', [np.arange(121) / 39., [0., .025, .075], [0., .025, .025]])
def test_wrong_or_gapped_clock_rejected(times):
    with pytest.raises(ValueError, match='40 Hz'):
        del_analysis.validate_clock(times)


def test_missing_window_endpoint_and_incomplete_stride_rejected():
    times = np.arange(122) / 40.
    with pytest.raises(ValueError, match='endpoints are missing'):
        del_analysis.window_slice(times, 0., 4.)
    with pytest.raises(ValueError, match='every downsampling grid'):
        del_analysis.analyze_series(times, np.ones(len(times)))


def write_output(path, unit_override=None, nonfinite=False, missing=False):
    names = ['Time', 'RootMyc1'] + list(del_analysis.CHANNELS)
    units = ['(s)', 'INVALID'] + ['(N-m)'] * 6
    if unit_override:
        units[2] = unit_override
    if missing:
        names[2] = 'RootMxc1'
    times = np.arange(7321) / 40.
    signal = 4e6 + 1e6 * np.cos(2 * np.pi * times)
    rows = ['\t'.join(names), '\t'.join(units)]
    for index, (time, value) in enumerate(zip(times, signal)):
        loads = [str(value * scale) for scale in (1, 2, 3, 4, 5, 6)]
        if nonfinite and index == 12:
            loads[0] = 'nan'
        # Invalid ElastoDyn values must never be parsed as numeric loads.
        rows.append('\t'.join([str(time), 'not-a-number'] + loads))
    path.write_text('\n' * 6 + '\n'.join(rows) + '\n', encoding='utf-8')


def test_beamdyn_units_and_invalid_elastodyn_exclusion(tmp_path):
    path = tmp_path / 'Case.T1.out'
    write_output(path)
    times, channels, source = del_analysis.read_beamdyn(path)
    assert len(times) == 7321
    assert set(channels) == set(del_analysis.CHANNELS)
    assert source['excluded_invalid_channels'] == ['RootMyc1']
    assert source['selected_units']['B1RootMxr'] == '(N-m)'
    assert source['time_span_s'] == [0., 183.]


@pytest.mark.parametrize('options', [
    {'unit_override': 'INVALID'}, {'unit_override': '(kN-m)'},
    {'nonfinite': True}, {'missing': True},
])
def test_unusable_beamdyn_channel_rejected(tmp_path, options):
    path = tmp_path / 'Case.T1.out'
    write_output(path, **options)
    with pytest.raises(ValueError, match='channel|Non-finite'):
        del_analysis.read_beamdyn(path)


def test_json_export_all_turbines_blades_windows_and_exponents(tmp_path):
    for tid in ('T1', 'T2', 'T3'):
        write_output(tmp_path / f'Case.{tid}.out')
    output = tmp_path / 'results.json'
    assert del_analysis.main(['--input-dir', str(tmp_path), '--output', str(output)]) == output
    result = json.loads(output.read_text(encoding='utf-8'))
    assert result['status'] == 'research_estimate'
    assert result['method']['mean_load_correction'] == 'none'
    for name, window in result['windows'].items():
        assert window['duration_s'] == (60. if name == 'replay' else 183.)
        assert set(window['turbines']) == {'T1', 'T2', 'T3'}
        for blades in window['turbines'].values():
            assert set(blades) == {'B1', 'B2', 'B3'}
            for blade, channels in blades.items():
                assert set(channels) == {'RootMxr', 'RootMyr'}
                for axis, row in channels.items():
                    scale = (int(blade[1]) - 1) * 2 + (1 if axis == 'RootMxr' else 2)
                    assert set(row['sampling']) == set(del_analysis.SAMPLING)
                    for m in ('8', '10', '12'):
                        assert row['sampling']['40_Hz']['del_Nm'][m] == pytest.approx(2e6 * scale)
    with pytest.raises(FileExistsError):
        del_analysis.main(['--input-dir', str(tmp_path), '--output', str(output)])
