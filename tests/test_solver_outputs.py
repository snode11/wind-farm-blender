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


@pytest.mark.parametrize('contents, completion', [
    ('Time RotSpeed\n', '(s) (rpm)\n1 9\n'),
    ('Time RotSpeed\n(s) (rp', 'm)\n1 9\n'),
    ('Time RotSpeed\n(s)', ' (rpm)\n1 9\n'),
], ids=['names-only', 'partial-unit', 'partial-unit-count'])
def test_incomplete_units_wait_until_header_and_sample_are_complete(tmp_path, contents, completion):
    s = stream(tmp_path)
    reader = s.readers[0]
    reader.path.write_text(contents, encoding='utf-8')

    values, records = s.sample(1., .25)

    assert reader.error is None
    assert all(record['validity'] == 'waiting' for record in records[0].values())
    assert np.isnan(values['rotor_speed'][0])
    assert np.isnan(values['rotor_speed_interval_max'][0])
    assert np.isnan(values['rotor_speed_interval_max_time'][0])
    assert values['rotor_speed_interval_samples'][0] == 0
    peak, when, count = s._interval_peak(reader, {'Time': 0, 'RotSpeed': 1}, 'rotor_speed')
    assert np.isnan(peak) and np.isnan(when) and count == 0

    with reader.path.open('a', encoding='utf-8') as f:
        f.write(completion)
    values, records = s.sample(1., .25)
    assert records[0]['rotor_speed']['validity'] == 'valid'
    assert values['rotor_speed'][0] == values['rotor_speed_interval_max'][0] == 9.
    assert values['rotor_speed_interval_max_time'][0] == 1.
    assert values['rotor_speed_interval_samples'][0] == 1


@pytest.mark.parametrize('units', [None, ['s'], ['s', 'rpm', 'rpm']])
def test_incomplete_unit_metadata_cannot_validate_existing_rows(tmp_path, monkeypatch, units):
    s = stream(tmp_path)
    reader = s.readers[0]
    reader.names, reader.units = ['Time', 'RotSpeed'], units
    reader.interval = [(1., ['1', '9'])]
    monkeypatch.setattr(reader, 'advance', lambda target: reader.interval[0])

    values, records = s.sample(1., .25)

    assert records[0]['rotor_speed']['validity'] == 'waiting'
    assert np.isnan(values['rotor_speed'][0])
    peak, when, count = s._interval_peak(reader, {'Time': 0, 'RotSpeed': 1}, 'rotor_speed')
    assert np.isnan(peak) and np.isnan(when) and count == 0


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


@pytest.mark.parametrize('header', ['Time GenSpeed\n(s) (rpm)\n', 'Time RotSpeed\n(s) (deg)\n'])
@pytest.mark.parametrize('extra_rows', ['', '3 12\n'], ids=['same-size', 'larger'])
def test_same_inode_rewrite_invalidates_cached_header_and_samples(tmp_path, header, extra_rows):
    s = stream(tmp_path)
    reader = s.readers[0]
    reader.path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n2 18\n', encoding='utf-8')
    assert s.sample(1., .25)[0]['rotor_speed'][0] == 9.
    original = reader.path.stat()
    assert reader.future

    with reader.path.open('w', encoding='utf-8') as f:
        f.write(header + '1 9\n2 18\n' + extra_rows)
    rewritten = reader.path.stat()
    assert (rewritten.st_dev, rewritten.st_ino) == (original.st_dev, original.st_ino)
    assert rewritten.st_size >= reader.offset

    values, records = s.sample(2., .25)

    assert reader.error and 'rewritten' in reader.error
    assert reader.latest is None and not reader.future and not reader.interval
    assert records[0]['rotor_speed']['validity'] == 'invalid'
    assert np.isnan(values['rotor_speed'][0])
    assert np.isnan(values['rotor_speed_interval_max'][0])
    assert np.isnan(values['rotor_speed_interval_max_time'][0])
    assert values['rotor_speed_interval_samples'][0] == 0
    assert reader.advance(3.) is None


def test_file_identity_belongs_to_open_handle_when_path_is_replaced(tmp_path, monkeypatch):
    path = tmp_path / 'data.out'
    path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n', encoding='utf-8')
    original = path.stat()
    open_path = Path.open
    replaced = False

    def open_then_replace(self, *args, **kwargs):
        nonlocal replaced
        handle = open_path(self, *args, **kwargs)
        if self == path and args == ('rb',) and not replaced:
            replaced = True
            path.rename(tmp_path / 'old.out')
            path.write_text('Time GenSpeed\n(s) (rpm)\n2 900\n', encoding='utf-8')
        return handle

    monkeypatch.setattr(Path, 'open', open_then_replace)
    reader = OutputReader(path)
    assert reader.advance(1.) == (1., ['1', '9'])
    assert reader.identity == (original.st_dev, original.st_ino)
    assert reader.advance(2.) is None and reader.error


def test_read_and_future_backlogs_are_bounded(tmp_path):
    path = tmp_path / 'data.out'
    path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n2 10\n', encoding='utf-8')
    r = OutputReader(path)
    r.FUTURE_LIMIT = 1
    assert r.advance(0.) is None and 'bounded' in r.error
    r = OutputReader(path)
    r.READ_LIMIT = 10
    assert r.advance(0.) is None and r.offset == 10


def test_due_future_rows_are_consumed_before_new_backlog_is_bounded(tmp_path):
    s = stream(tmp_path)
    reader = s.readers[0]
    reader.FUTURE_LIMIT = 2
    reader.path.write_text('Time RotSpeed\n(s) (rpm)\n1 9\n2 18\n3 10\n', encoding='utf-8')
    assert s.sample(1., .25)[0]['rotor_speed'][0] == 9.
    assert [row[0] for row in reader.future] == [2., 3.]
    with reader.path.open('a', encoding='utf-8') as f:
        f.write('4 12\n5 11\n6 13\n')

    values, records = s.sample(4., .25)

    assert reader.error is None
    assert records[0]['rotor_speed']['validity'] == 'valid'
    assert values['rotor_speed'][0] == 12.
    assert values['rotor_speed_interval_max'][0] == 18.
    assert values['rotor_speed_interval_max_time'][0] == 2.
    assert values['rotor_speed_interval_samples'][0] == 3
    assert [row[0] for row in reader.interval] == [2., 3., 4.]
    assert [row[0] for row in reader.future] == [5., 6.]
    values, _ = s.sample(5., .25)
    assert values['rotor_speed_interval_max'][0] == 11.
    assert values['rotor_speed_interval_samples'][0] == 1
    with reader.path.open('a', encoding='utf-8') as f:
        f.write('7 14\n8 15\n')
    assert reader.advance(5.) is None and 'bounded' in reader.error


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


def test_interval_peak_keeps_overspeed_between_control_samples(tmp_path):
    s = stream(tmp_path)
    count = len(SOLVER_OUTPUT_CHANNELS)
    rotor = list(SOLVER_OUTPUT_CHANNELS).index('rotor_speed')
    rows = []
    for k in range(25):                       # 105.0 .. 111.0 s at the 0.25 s output interval
        t = 105. + .25 * k
        values = np.full(count, 1.)
        values[rotor] = 18. if t == 109.5 else 12.1
        rows.append((t, values))
    output(s.readers[0].path, rows)
    first, _ = s.sample(108., 3.)
    assert first['rotor_speed_interval_max'][0] == 12.1
    values, _ = s.sample(111., 3.)
    # Display keeps the latest sample; the alarm input keeps the peak and its native time.
    assert values['rotor_speed'][0] == 12.1
    assert values['rotor_speed_interval_max'][0] == 18.
    assert values['rotor_speed_interval_max_time'][0] == 109.5
    assert values['rotor_speed_interval_samples'][0] == 12
    empty, _ = s.sample(111., 3.)
    assert empty['rotor_speed_interval_samples'][0] == 0 and np.isnan(empty['rotor_speed_interval_max'][0])
