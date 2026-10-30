"""Offline response identity, numerical meaning and playback-window contracts."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender import farm_flex, imbalance

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'blender_frontend/wfrl_blender/assets/mappo'
ANALYSIS = SOURCE.parent / 'blade_response'
spec = importlib.util.spec_from_file_location('export_blade_response', ROOT / 'scripts/analysis/export_blade_response.py')
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


def test_known_offsets_and_identical_phase_functions():
    azimuth = np.arange(0, 360 * 7 + .5, .5)
    values = np.tile([1., 2., 3.], (len(azimuth), 1))
    cycles, _grid, curves = producer.phase_sample(azimuth, values)
    assert len(cycles) == 6
    result = producer.describe(curves.mean(axis=0))
    assert result['rms_spread'] == pytest.approx(np.sqrt(2 / 3))
    assert result['blade_offsets'] == pytest.approx([-1, 0, 1])
    phase = azimuth[:, None] + np.array([0, 120, 240])
    _, _, curves = producer.phase_sample(azimuth, 4 + np.cos(np.radians(phase)))
    assert producer.describe(curves.mean(axis=0))['rms_spread'] < 1e-12
    with pytest.raises(ValueError, match='increasing azimuth'):
        producer.phase_sample(azimuth[::-1], values)


def test_saved_results_match_independent_reference_values():
    data = imbalance.load_for_source(SOURCE)
    expected = {'T1': (9, .13081992677238252, 232115.3806),
                'T2': (9, .0851317986, 151564.0191),
                'T3': (8, .1039588444, 188056.9751)}
    for tid, (count, tip, moment) in expected.items():
        row = data['turbines'][tid]
        assert len(row['turns']) == count
        assert row['metrics']['tip_x']['rms_spread'] == pytest.approx(tip)
        assert row['metrics']['root_My']['rms_spread'] == pytest.approx(moment)
        assert row['physical'] == dict(mass=dict(status='unavailable'), aerodynamic=dict(status='unavailable'))


def test_turn_visibility_follows_completed_window_and_backward_seek():
    row = imbalance.load_for_source(SOURCE)['turbines']['T1']
    first, last = row['turns'][0], row['turns'][-1]
    assert imbalance.latest_turn(row, first['end_s'] - 1e-8) is None
    assert imbalance.latest_turn(row, first['end_s']) is first
    assert imbalance.latest_turn(row, 177.) is last
    assert imbalance.latest_turn(row, 117.) is None
    assert imbalance.latest_turn(row, float('nan')) is None


def test_unrelated_source_never_inherits_builtin_analysis(tmp_path):
    (tmp_path / 'manifest.json').write_text('{}')
    assert imbalance.load_for_source(tmp_path) is None
    with pytest.raises(ValueError, match='source identity mismatch'):
        imbalance.read_package(ANALYSIS, tmp_path)


def test_declared_analysis_corruption_is_not_replaced_with_builtin(tmp_path):
    local = tmp_path / 'blade-response'
    local.mkdir()
    (local / 'manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='analysis manifest'):
        imbalance.load_for_source(tmp_path)


def test_malformed_builtin_reports_analysis_error(tmp_path, monkeypatch):
    builtin = tmp_path / 'assets/blade_response'
    builtin.mkdir(parents=True)
    (builtin / 'manifest.json').write_text('[]')
    monkeypatch.setattr(imbalance, '__file__', str(tmp_path / 'imbalance.py'))
    with pytest.raises(ValueError, match='built-in analysis manifest'):
        imbalance.load_for_source(SOURCE)


def test_tampered_result_fails_integrity(tmp_path):
    (tmp_path / 'manifest.json').write_bytes((ANALYSIS / 'manifest.json').read_bytes())
    (tmp_path / 'results.json').write_bytes((ANALYSIS / 'results.json').read_bytes() + b' ')
    with pytest.raises(ValueError, match='result integrity'):
        imbalance.read_package(tmp_path, SOURCE)


def test_verified_load_reuses_source_reads_only_within_its_transaction(monkeypatch):
    package = farm_flex._load_package(SOURCE)
    expected = json.loads((ANALYSIS / 'results.json').read_bytes())
    source_reads = []
    read_bytes = Path.read_bytes

    def read(path):
        if path.parent == SOURCE:
            source_reads.append(path.name)
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    result = imbalance.load_for_source(SOURCE, _source=package.verified_source)
    telemetry = farm_flex._read_telemetry(SOURCE, package.manifest, package.times,
                                        package.readers, _source=package.verified_source)
    assert result == expected
    assert telemetry['sources']['T1']['sha256'] == result['turbines']['T1']['source']['sha256']
    assert source_reads == []


def test_verified_source_cannot_be_used_for_another_path(tmp_path):
    package = farm_flex._load_package(SOURCE)
    with pytest.raises(ValueError, match='verified source'):
        imbalance.read_package(ANALYSIS, tmp_path, _source=package.verified_source)


def test_verified_source_does_not_skip_analysis_integrity(tmp_path):
    package = farm_flex._load_package(SOURCE)
    (tmp_path / 'manifest.json').write_bytes((ANALYSIS / 'manifest.json').read_bytes())
    (tmp_path / 'results.json').write_bytes((ANALYSIS / 'results.json').read_bytes() + b' ')
    with pytest.raises(ValueError, match='result integrity'):
        imbalance.read_package(tmp_path, SOURCE, _source=package.verified_source)


def test_new_load_rejects_changed_analysis_source_even_with_same_size_and_mtime(tmp_path):
    for file in SOURCE.iterdir():
        if file.is_file():
            if file.name == 'telemetry.json':
                (tmp_path / file.name).write_bytes(file.read_bytes())
            else:
                (tmp_path / file.name).symlink_to(file)
    package = farm_flex._load_package(tmp_path)
    assert imbalance.load_for_source(tmp_path, _source=package.verified_source) is not None
    target = tmp_path / 'telemetry.json'
    stat, raw = target.stat(), target.read_bytes()
    target.write_bytes(raw[:-1] + (b' ' if raw[-1:] != b' ' else b'\n'))
    os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ValueError, match='Farm package integrity mismatch: telemetry.json'):
        farm_flex._load_package(tmp_path)


def test_telemetry_arrays_remain_private_to_each_verified_load():
    a, b = farm_flex._load_package(SOURCE), farm_flex._load_package(SOURCE)
    first = farm_flex._read_telemetry(SOURCE, a.manifest, a.times, a.readers, _source=a.verified_source)
    second = farm_flex._read_telemetry(SOURCE, b.manifest, b.times, b.readers, _source=b.verified_source)
    assert first is not second
    for tid, channels in first['turbines'].items():
        for name, record in channels.items():
            assert not np.shares_memory(record['values'], second['turbines'][tid][name]['values'])


@pytest.mark.parametrize('fault', ['unit', 'nan', 'turn', 'offset', 'curve', 'physical', 'source'])
def test_wrong_semantics_rejected_even_with_well_formed_json(fault):
    data = json.loads((ANALYSIS / 'results.json').read_bytes())
    row = data['turbines']['T1']
    if fault == 'unit': row['metrics']['root_My']['unit'] = 'kN m'
    if fault == 'nan': row['metrics']['tip_x']['blade_offsets'][0] = float('nan')
    if fault == 'turn': row['turns'][0]['end_s'] = 999.
    if fault == 'offset': row['metrics']['tip_x']['blade_offsets'][0] += 1
    if fault == 'curve': row['metrics']['tip_x']['phase_mean'][0][0] += 1
    if fault == 'physical': row['physical']['mass']['magnitude_kg_m'] = 0.
    if fault == 'source': row['source']['sha256'] = 'wrong'
    with pytest.raises(ValueError):
        imbalance.validate(data, json.loads((SOURCE / 'manifest.json').read_bytes()),
                           json.loads((SOURCE / 'telemetry.json').read_bytes()))


def test_physical_references_retain_type_units_and_blade_identity():
    mass = dict(status='available', source_kind='simulation_reference', magnitude_kg_m=20.,
                phase_deg=120., phase_reference='root reference at zero azimuth', blades=[2],
                provenance='synthetic test fixture')
    aero = dict(status='available', source_kind='simulation_reference',
                parameter='aerodynamic_twist_offset', blade_offsets_deg=[0., 1., 0.],
                provenance='synthetic test fixture')
    imbalance._physical(mass, 'mass')
    imbalance._physical(aero, 'aerodynamic')
    with pytest.raises(ValueError):
        imbalance._physical(dict(aero, parameter='generic_fault_percent'), 'aerodynamic')
    with pytest.raises(ValueError):
        imbalance._physical(dict(mass, magnitude_kg_m=-1), 'mass')


def test_curves_use_selected_scene_and_convert_load_units(monkeypatch):
    data = imbalance.load_for_source(SOURCE)
    active = SimpleNamespace(imbalance=data, imbalance_plots={})
    owner = SimpleNamespace(wfrl_imbalance_turbine='T2', wfrl_imbalance_metric='root_My')
    monkeypatch.setattr(farm_flex, 'active_for', lambda scene: active if scene is owner else None)
    plotted = imbalance.plot_data(owner)
    assert plotted.units == ('kN·m',)
    assert plotted.series[0][1][0][0][1] == pytest.approx(data['turbines']['T2']['metrics']['root_My']['phase_mean'][0][0] * .001)
    assert imbalance.plot_data(owner) is plotted
    assert imbalance.plot_data(SimpleNamespace(wfrl_imbalance_turbine='T2', wfrl_imbalance_metric='root_My')) is None


def test_export_is_reproducible_from_saved_sources():
    telemetry = json.loads((SOURCE / 'telemetry.json').read_bytes())
    if not all((ROOT / row['path']).is_file() for row in telemetry['sources'].values()):
        pytest.skip('Original OpenFAST output files are not distributed')
    data, hashes = producer.build(SOURCE, ROOT)
    expected = json.loads((ANALYSIS / 'results.json').read_bytes())

    def compare(actual, saved):
        assert type(actual) is type(saved)
        if isinstance(saved, dict):
            assert actual.keys() == saved.keys()
            for key in saved:
                compare(actual[key], saved[key])
        elif isinstance(saved, list):
            assert len(actual) == len(saved)
            for value, reference in zip(actual, saved):
                compare(value, reference)
        elif isinstance(saved, float):
            assert actual == pytest.approx(saved, rel=1e-12, abs=1e-12)
        else:
            assert actual == saved

    compare(data, expected)
    assert hashes == json.loads((ANALYSIS / 'manifest.json').read_bytes())['source_hashes']
