"""Accuracy diagnostics preserve defaults and reject incomplete or misleading evidence."""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from scripts.experiments.audit_fastfarm_physics import (
    airfoil_audit, classify_log, compare_metrics, phase_binned_difference,
    prepare_diagnostics, read_native, statistics,
)
from scripts.experiments.check_tower_decay import decay_frequency
from scripts.experiments.compare_fastfarm_physics import summarize
from wfcrl.simul_utils import FASTInputFile, create_ff_case
from wfrl.solver_outputs import OutputReader, prepare_case


def case(tmp_path):
    return Path(create_ff_case(dict(num_turbines=1, xcoords=[0.], ycoords=[0.],
        max_iter=15, dt=3, speed=8, wind_time_series=None), str(tmp_path)))


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def native(path, times):
    path.write_text('Time GenPwr RootMyc1 RootMxc1 TwrBsMxt TwrBsMyt TTDspFA TTDspSS\n'
        '(s) (kW) (kN-m) (kN-m) (kN-m) (kN-m) (m) (m)\n' +
        ''.join(f'{t} {100+t} 10 2 4 5 0 0\n' for t in times), encoding='utf-8')


def test_diagnostic_defaults_leave_entire_generated_case_byte_identical(tmp_path):
    farm = case(tmp_path)
    prepare_case(farm, tower_outputs=True)
    before = hashes(tmp_path)
    assert prepare_diagnostics(farm) == {}
    assert prepare_diagnostics(farm, uniform_wind=False, step_scale=1., output_dt=None, aero_nodes=False) == {}
    assert hashes(tmp_path) == before


def test_explicit_diagnostics_change_only_generated_inputs_and_record_hashes(tmp_path):
    farm = case(tmp_path)
    record = prepare_diagnostics(farm, uniform_wind=True, step_scale=.5, output_dt=.025, aero_nodes=True)
    fst = FASTInputFile(str(farm.with_name('FFTest_WT1.fst')))
    ad = FASTInputFile(str(tmp_path / '5MW_Baseline/AD.dat'))
    assert fst['DT'] == ad['DTAero'] == .003125
    assert fst['DT_Out'] == .025
    assert FASTInputFile(str(farm.with_name('InflowWind.dat')))['PLExp'] == 0
    text = (tmp_path / '5MW_Baseline/AD.dat').read_text(encoding='utf-8')
    assert ad['BldNd_BladesOut'] == 1
    assert '"Alpha"' in text and '"Fy"' in text
    for name, digest in record['inputs_sha256'].items():
        assert hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() == digest


def test_completed_large_output_is_read_across_all_bounded_chunks(tmp_path, monkeypatch):
    output = tmp_path / 'native.out'
    native(output, np.arange(181))
    monkeypatch.setattr(OutputReader, 'READ_LIMIT', 137)
    result = summarize(output, 180, discard=60)
    assert result['samples'] == 121
    assert result['window_seconds'] == [60, 180]
    assert result['channels']['GenPwr']['mean'] == 220
    assert summarize(output, 180)['samples'] == 91


@pytest.mark.parametrize('discard', [-1, 180, float('nan')])
def test_invalid_statistics_window_rejected(tmp_path, discard):
    output = tmp_path / 'native.out'
    native(output, [0, 60, 180])
    with pytest.raises(ValueError, match='discard'):
        summarize(output, 180, discard=discard)


@pytest.mark.parametrize('problem', ['truncated', 'nan', 'early', 'duplicate_time'])
def test_native_audit_rejects_bad_or_partial_rows(tmp_path, problem):
    output = tmp_path / 'native.out'
    native(output, [0, 60, 180])
    text = output.read_text(encoding='utf-8')
    if problem == 'truncated':
        text = text.rstrip('\n')
    elif problem == 'nan':
        text = text.replace('280', 'nan')
    elif problem == 'early':
        text = text.replace('180 ', '179 ')
    else:
        text = text.replace('60 ', '0 ')
    output.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        read_native(output, 180)


def test_del_counts_half_cycles_and_keeps_zero_denominators_undefined():
    values = statistics(np.arange(5.), np.array([0., 2., 0., 2., 0.]), m=4)
    assert values['del_1hz'] == pytest.approx(8.**.25)
    zero = statistics(np.arange(5.), np.zeros(5))
    assert zero['del_1hz'] == 0
    assert compare_metrics(values, zero)['std'] is None
    assert compare_metrics(values, values)['mean'] == 0


def test_airfoil_audit_handles_sparse_nonlifting_cylinders(tmp_path):
    case(tmp_path)
    result = airfoil_audit(tmp_path)
    assert len(result) == 8
    cylinders = [x for x in result if x['file'].startswith('Cylinder')]
    assert len(cylinders) == 2
    assert all(x['fitted_cn_slope_per_rad'] == 0 and x['fitted_zero_cn_deg'] is None for x in cylinders)
    du25 = next(x for x in result if x['file'] == 'DU25_A17.dat')
    assert du25['fitted_cn_slope_per_rad'] > du25['ua']['C_nalpha']


def test_tower_frequency_recovers_damped_response_with_static_offset():
    t = np.arange(0., 180.025, .025)
    v = -.015 + .1*np.exp(-.01*t)*np.cos(2*np.pi*.324*t)
    assert decay_frequency(t, v)['frequency_hz'] == pytest.approx(.324, abs=.0003)


def test_azimuth_comparison_handles_different_rotor_speeds():
    t = np.arange(0., 180.025, .025)
    a, b = (t*54) % 360, (t*53.8) % 360
    reference = dict(Time=t, Azimuth=a, RootMyc1=5000+100*np.cos(np.deg2rad(a)))
    current = dict(Time=t, Azimuth=b,
                   RootMyc1=5000+100*np.cos(np.deg2rad(b))-200*np.exp(-((b-180)/15)**2))
    result = phase_binned_difference(current, reference)
    assert abs(result['minimum_azimuth_deg']-180) <= 2.5
    assert -205 < result['minimum_kNm'] < -190


def test_nonlifting_disable_is_not_ua_cutout():
    report = classify_log('Warning: Turning off Unsteady Aerodynamics because polar has constant data. '
                          '(node 4, blade 2)\nFAST.Farm terminated normally.\n')
    assert report['constant_polar_ua_disabled'] == [dict(node=4, blade=2)]
    assert report['cutout_mentions'] == report['invalid_nan_mentions'] == 0
    assert classify_log('UA cutout NaN')['cutout_mentions'] == 1
