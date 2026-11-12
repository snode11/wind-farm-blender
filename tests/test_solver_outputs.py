"""Native measurements retain file time, physical units and run ownership."""
from pathlib import Path

import numpy as np
import pytest

from wfrl.blender_bridge.messages import SOLVER_OUTPUT_CHANNELS
from wfrl.solver_outputs import OutputReader, SolverOutputs


def test_generated_output_configuration_roundtrips_without_changing_physics(tmp_path):
    import shutil
    from wfrl.inflow import template_dir
    from wfcrl.simul_utils import FASTInputFile
    from wfrl.solver_outputs import prepare_case
    source = Path(template_dir())
    for name in ('Case.fstf', 'FFTest_WT1.fst', 'InflowWind.dat', 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat'):
        shutil.copy2(source / name, tmp_path / name)
    ed_path = tmp_path / 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat'
    physics = ('FlapDOF1', 'FlapDOF2', 'EdgeDOF', 'TwFADOF1', 'TwFADOF2', 'TwSSDOF1', 'TwSSDOF2')
    original = FASTInputFile(str(ed_path))
    expected = {key: original[key] for key in physics}
    definitions = prepare_case(tmp_path / 'Case.fstf')
    ed = FASTInputFile(str(ed_path))
    assert ed['NBlGages'] == 3 and definitions[0]['gauge_nodes'] == [1, 9, 17]
    assert {key: ed[key] for key in physics} == expected
    names = {word.strip('"').upper() for line in ed['OutList'] for word in line.replace(',', ' ').split()}
    assert all(spec['native'].upper() in names for spec in SOLVER_OUTPUT_CHANNELS.values())
    before = ed_path.read_bytes()
    prepare_case(tmp_path / 'Case.fstf')
    assert ed_path.read_bytes() == before
    primary = FASTInputFile(str(tmp_path / 'FFTest_WT1.fst'))
    assert primary['DT_Out'] == .25 and primary['OutFileFmt'] == 1
    external = tmp_path / 'external wind source' / 'gust.bts'
    external.parent.mkdir()
    external.write_bytes(b'unchanged wind content')
    inflow = FASTInputFile(str(tmp_path / 'InflowWind.dat'))
    inflow['WindType'], inflow['FileName_BTS'] = 3, '"' + str(external) + '"'
    inflow.write(str(tmp_path / 'InflowWind.dat'))
    prepare_case(tmp_path / 'Case.fstf')
    assert (tmp_path / 'wfrl_inflow.bts').read_bytes() == external.read_bytes()
    assert FASTInputFile(str(tmp_path / 'InflowWind.dat'))['FileName_BTS'] == '"wfrl_inflow.bts"'


def output(path, rows, *, units=None):
    specs = list(SOLVER_OUTPUT_CHANNELS.values())
    header = 'Time ' + ' '.join(s['native'] for s in specs) + '\n'
    header += '(s) ' + ' '.join('(' + (units or {}).get(s['native'], s['native_unit']) + ')' for s in specs) + '\n'
    path.write_text(header + ''.join(str(t) + ' ' + ' '.join(str(v) for v in values) + '\n'
                                    for t, values in rows), encoding='utf-8')


def stream(tmp_path):
    reader = SolverOutputs.__new__(SolverOutputs)
    reader.definitions = [dict(gauge_nodes=[1, 9, 17], comp_elast=1, output_interval_seconds=.25)]
    reader.readers = []
    reader.start({'farm_file': {'path': str(tmp_path / 'Case.fstf')}})
    return reader


def test_all_native_channels_preserve_values_scales_frames_and_sample_time(tmp_path):
    s = stream(tmp_path)
    count = len(SOLVER_OUTPUT_CHANNELS)
    output(s.readers[0].path, [(110.75, np.arange(count) + 30.)])
    values, records = s.sample(111., 3.)
    for index, (key, spec) in enumerate(SOLVER_OUTPUT_CHANNELS.items()):
        r = records[0][key]
        assert values[key][0] == r['value'] == (index + 30.) * spec['scale']
        assert r['validity'] == 'valid' and r['fidelity'] == 'DIRECT'
        assert r['timestamp']['value'] == 110.75
        assert r['unit'] == spec['unit']
        assert r['provenance']['coordinate_frame'] == spec['frame']
        assert r['provenance']['simulation_lag_seconds'] == .25
        if spec['gauge']:
            assert r['provenance']['elastodyn_node'] == [1, 9, 17][spec['gauge'] - 1]
    assert values['rotor_speed'][0] == 30.


def test_partial_rows_future_rows_and_missing_files(tmp_path):
    path = tmp_path / 'data.out'
    r = OutputReader(path)
    assert r.advance(1.) is None and r.error is None
    path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n2 10\n3 ', encoding='utf-8')
    assert r.advance(1.) == (1., ['1', '9'])
    assert r.advance(2.) == (2., ['2', '10'])
    with path.open('a', encoding='utf-8') as f:
        f.write('11\n')
    assert r.advance(3.) == (3., ['3', '11'])


@pytest.mark.parametrize('contents', [
    'Time RotSpeed RotSpeed\n', 'Time RotSpeed\n(s)\n',
    'Time RotSpeed\n(s) (rpm)\n1 9 extra\n',
    'Time RotSpeed\n(s) (rpm)\n1 9\n1 10\n',
    'Time RotSpeed\n(s) (rpm)\nnan 9\n',
])
def test_malformed_output_is_invalid(tmp_path, contents):
    path = tmp_path / 'data.out'
    path.write_text(contents, encoding='utf-8')
    r = OutputReader(path)
    assert r.advance(10.) is None and r.error


@pytest.mark.parametrize('replace', [False, True])
def test_output_replacement_or_truncation_is_not_a_new_run(tmp_path, replace):
    path = tmp_path / 'data.out'
    path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n', encoding='utf-8')
    r = OutputReader(path)
    assert r.advance(1.)
    if replace:
        path.rename(path.with_suffix('.old'))
    path.write_text('', encoding='utf-8')
    assert r.advance(2.) is None and r.error


def test_read_and_future_backlogs_are_bounded(tmp_path):
    path = tmp_path / 'data.out'
    path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n2 10\n', encoding='utf-8')
    r = OutputReader(path)
    r.FUTURE_LIMIT = 1
    assert r.advance(0.) is None and 'bounded' in r.error
    r = OutputReader(path)
    r.READ_LIMIT = 10
    assert r.advance(0.) is None and r.offset == 10


def test_units_nonfinite_stale_and_episode_reset_remain_explicit(tmp_path):
    s = stream(tmp_path)
    nums = [1.] * len(SOLVER_OUTPUT_CHANNELS)
    nums[1] = float('nan')
    output(s.readers[0].path, [(110., nums)], units={'TipDxc1': 'deg'})
    values, records = s.sample(111., 3.)
    assert records[0]['generator_speed']['validity'] == 'invalid'
    assert records[0]['tip_dx_1']['validity'] == 'invalid'
    assert records[0]['tip_flap_1']['validity'] == 'valid'
    assert s.sample(114., 3.)[1][0]['rotor_speed']['validity'] == 'waiting'
    s.start({'farm_file': {'path': str(tmp_path / 'Case_iter1.fstf')}})
    assert s.readers[0].path.name == 'Case_iter1.T1.out'
    values, records = s.sample(102., 2.)
    assert np.isnan(values['rotor_speed']).all()
    assert records[0]['rotor_speed']['validity'] == 'waiting'
    assert 'timestamp' not in records[0]['rotor_speed']


def test_missing_native_channel_is_not_an_inferred_value(tmp_path):
    s = stream(tmp_path)
    s.readers[0].path.write_text('Time GenSpeed\n(s) (rpm)\n1 970\n', encoding='utf-8')
    values, records = s.sample(1., 3.)
    assert records[0]['rotor_speed']['validity'] == 'unsupported'
    assert records[0]['rotor_speed']['value'] is None
    assert values['generator_speed'][0] == 970.
