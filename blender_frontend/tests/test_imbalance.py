"""Offline response identity, numerical meaning and playback-window contracts."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from blender_frontend.tests._platform import symlink_or_skip
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
                symlink_or_skip(tmp_path / file.name, file)
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

    imbalance.validate(data, json.loads((SOURCE / 'manifest.json').read_bytes()), telemetry)
    for row in data['turbines'].values():
        assert set(row['metrics']) == set(imbalance.METRICS) | set(imbalance.ROOT_FORCES)
        for key in imbalance.ROOT_FORCES:
            assert row['metrics'][key]['unit'] == 'N'
    compare(data, expected)
    assert hashes == json.loads((ANALYSIS / 'manifest.json').read_bytes())['source_hashes']


@pytest.mark.parametrize('tid', ['T1', 'T2', 'T3'])
def test_builtin_nine_metrics_have_complete_turns_and_raw_source_identity(tid):
    data = imbalance.load_for_source(SOURCE)
    row = data['turbines'][tid]
    keys = set(imbalance.METRICS) | set(imbalance.ROOT_FORCES)
    assert len(keys) == 9 and set(row['metrics']) == keys
    assert row['source']['saved_samples'] == 2401
    assert all(set(turn['metrics']) == keys for turn in row['turns'])
    telemetry = json.loads((SOURCE / 'telemetry.json').read_bytes())
    source = telemetry['sources'][tid]
    archive = ROOT / 'outputs/dual-beam-tls-20261002/stages/02-diagnostics/raw-evidence' / source['path']
    if archive.exists():
        assert producer.digest(archive) == source['sha256'] == row['source']['sha256']


def test_six_metric_package_still_loads_without_inventing_root_forces():
    data = json.loads((ANALYSIS / 'results.json').read_bytes())
    for row in data['turbines'].values():
        for key in imbalance.ROOT_FORCES:
            row['metrics'].pop(key)
            for turn in row['turns']:
                turn['metrics'].pop(key)
    imbalance.validate(data, json.loads((SOURCE / 'manifest.json').read_bytes()),
                       json.loads((SOURCE / 'telemetry.json').read_bytes()))
    assert all(set(row['metrics']) == set(imbalance.METRICS) for row in data['turbines'].values())


def _synthetic_replay(path, *, fault=None, rotation=0.):
    path.mkdir()
    times = np.arange(0., 16.25, .25)
    azimuth = times * 90.
    columns = {'Time': times, 'Azimuth': azimuth % 360}
    units = {'Time': '(s)', 'Azimuth': '(deg)'}
    tips = np.empty((len(times), 3, 3))
    for blade, offset in enumerate((1., 2., 4.), 1):
        for axis, factor in zip('xyz', (1., 2., 3.)):
            for suffix, unit, value in ((f'RootF{axis}r', '(N)', 1000. * offset * factor),
                                        (f'RootM{axis}r', '(N-m)', 10000. * offset * factor),
                                        (f'TipTD{axis}r', '(m)', .1 * offset * factor),
                                        (f'TipRD{axis}r', '(-)', rotation * offset * factor)):
                name = f'B{blade}{suffix}'
                columns[name] = np.full(len(times), value)
                units[name] = unit
            tips[:, blade - 1, 'xyz'.index(axis)] = .1 * offset * factor
        for suffix in ('RootMyc', 'RootMxc', 'OoPDefl', 'IPDefl'):
            name = f'{suffix}{blade}'
            columns[name] = np.zeros(len(times))
            units[name] = 'INVALID'
    if fault is not None:
        name, kind = fault
        if kind == 'missing':
            columns.pop(name)
            units.pop(name)
        elif kind in ('nan', 'inf'):
            columns[name][5] = float(kind)
        else:
            units[name] = kind
            columns[name][:] = 0.
    output = path / 'synthetic.out'
    output.write_text('\n' * 6 + '\t'.join(columns) + '\n' + '\t'.join(units.values()) + '\n'
                      + '\n'.join('\t'.join(str(value) for value in values)
                                  for values in zip(*columns.values())) + '\n', encoding='utf-8')
    replay = {'T1': {'motion': [dict(time_s=float(t), azimuth_deg=float(a)) for t, a in zip(times, azimuth)]}}
    telemetry = {'sources': {'T1': dict(path=str(output), sha256=producer.digest(output))}}
    for name, data in (('data.json', replay), ('telemetry.json', telemetry),
                       ('deflection-t1.json', {'simulation': tips.tolist()})):
        (path / name).write_text(json.dumps(data), encoding='utf-8')
    (path / 'geometry.npz').write_bytes(b'synthetic geometry identity')
    manifest = dict(structural_module='BeamDyn', schema='wfrl.farm-flex-review.v3', turbine_ids=['T1'],
                    source_fps=4., segment=dict(start_s=0., end_s=16.),
                    files={name: producer.digest(path / name) for name in
                           ('data.json', 'geometry.npz', 'telemetry.json', 'deflection-t1.json')})
    (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return path, manifest, telemetry


def test_invalid_elastodyn_values_are_absent_not_zero(tmp_path):
    package, _manifest, _telemetry = _synthetic_replay(tmp_path / 'source')
    columns, units = producer.read_output(package / 'synthetic.out')
    assert units['RootMyc1'] == 'INVALID'
    assert 'RootMyc1' not in columns
    assert 'OoPDefl2' not in columns
    np.testing.assert_array_equal(columns['B1RootMyr'], 20000.)
    output = tmp_path / 'invalid-text.out'
    output.write_text('\n' * 6 + 'Time\tRootMyc1\n(s)\tINVALID\n0\tunavailable\n', encoding='utf-8')
    columns, units = producer.read_output(output)
    assert set(columns) == {'Time'}
    assert units['RootMyc1'] == 'INVALID'


@pytest.mark.parametrize('name, fault', [
    ('B1RootFxr', 'INVALID'), ('B2RootMyr', 'INVALID'),
    ('B3RootMzr', '(INVALID)'), ('B1RootFyr', '(kN)'), ('B2RootMxr', '(kN-m)'),
    ('B2RootFzr', 'missing'), ('B1RootMyr', 'missing'),
    ('B3RootFxr', 'nan'), ('B3RootMzr', 'inf'),
])
def test_export_rejects_unusable_beamdyn_channels_without_elastodyn_fallback(tmp_path, name, fault):
    package, _manifest, _telemetry = _synthetic_replay(tmp_path / 'source', fault=(name, fault))
    with pytest.raises(ValueError, match='BeamDyn channel'):
        producer.export(package, ROOT, tmp_path / 'analysis')
    assert not (tmp_path / 'analysis').exists()


def test_valid_zero_is_kept_with_its_beamdyn_identity(tmp_path):
    package, _manifest, _telemetry = _synthetic_replay(tmp_path / 'source', fault=('B1RootMzr', '(N-m)'))
    result, _hashes = producer.build(package, ROOT)
    metric = result['turbines']['T1']['metrics']['root_Mz']
    assert metric['blade_means'] == pytest.approx([0., 60000., 120000.])
    assert metric['rms_spread'] > 0


def test_root_forces_share_phase_statistics_and_package_validation(tmp_path, monkeypatch):
    package, _manifest, _telemetry = _synthetic_replay(tmp_path / 'source')
    output = tmp_path / 'analysis'
    exported = producer.export(package, ROOT, output)
    data = imbalance.read_package(output, package)
    assert data == exported
    row = data['turbines']['T1']
    assert len(row['turns']) == 3
    for key, factor in zip(imbalance.ROOT_FORCES, (1., 2., 3.)):
        means = np.array([1000., 2000., 4000.]) * factor
        metric = row['metrics'][key]
        assert metric['unit'] == 'N'
        assert metric['blade_means'] == pytest.approx(means)
        assert metric['blade_offsets'] == pytest.approx(means - means.mean())
        assert metric['rms_spread'] == pytest.approx(means.std())
        np.testing.assert_allclose(metric['phase_mean'], np.repeat(means[:, None], 72, axis=1))
        for turn in row['turns']:
            assert turn['metrics'][key]['blade_means'] == pytest.approx(means)
            assert turn['metrics'][key]['rms_spread'] == pytest.approx(means.std())
    active = SimpleNamespace(imbalance=data, imbalance_plots={})
    monkeypatch.setattr(farm_flex, 'active_for', lambda scene: active)
    plot = imbalance.plot_data(SimpleNamespace(wfrl_imbalance_turbine='T1', wfrl_imbalance_metric='root_Fx'))
    assert plot.units == ('kN',)
    assert plot.series[0][1][0][0][1] == pytest.approx(1.)


@pytest.mark.parametrize('fault', ['unit', 'nan', 'partial', 'turn', 'rotation'])
def test_root_force_and_rotation_semantics_are_validated(tmp_path, fault):
    package, manifest, telemetry = _synthetic_replay(tmp_path / 'source')
    data, _hashes = producer.build(package, ROOT)
    row = data['turbines']['T1']
    if fault == 'unit': row['metrics']['root_Fx']['unit'] = 'INVALID'
    if fault == 'nan': row['metrics']['root_Fy']['phase_mean'][0][0] = float('nan')
    if fault == 'partial': row['metrics'].pop('root_Fz')
    if fault == 'turn': row['turns'][0]['metrics'].pop('root_Fx')
    if fault == 'rotation': row['metrics']['elastic_twist_deg'] = row['metrics']['tip_z']
    with pytest.raises(ValueError):
        imbalance.validate(data, manifest, telemetry)


def test_rotation_parameters_never_become_elastic_twist(tmp_path):
    still, _manifest, _telemetry = _synthetic_replay(tmp_path / 'still')
    rotated, _manifest, _telemetry = _synthetic_replay(tmp_path / 'rotated', rotation=4. * np.tan(np.pi / 8.))
    first, _hashes = producer.build(still, ROOT)
    second, _hashes = producer.build(rotated, ROOT)
    a, b = first['turbines']['T1'], second['turbines']['T1']
    assert a['metrics'] == b['metrics']
    assert a['turns'] == b['turns']
    assert set(b['metrics']) == set(imbalance.METRICS) | set(imbalance.ROOT_FORCES)


class _ResponseLayout:
    def __init__(self):
        self.labels, self.choices, self.properties = [], [], []

    def row(self, **kwargs): return self
    def column(self, **kwargs): return self
    def box(self): return self
    def panel(self, *args, **kwargs): return self, self
    def label(self, *, text='', **kwargs): self.labels.append(text)
    def prop(self, data, name, **kwargs): self.properties.append(name)
    def prop_enum(self, data, name, value, *, text='', **kwargs): self.choices.append((name, value, text))


@pytest.mark.parametrize('forces', [False, True])
def test_response_panel_labels_coordinates_and_unavailable_twist(tmp_path, frontend_module, monkeypatch, forces):
    panel = frontend_module('wfrl_blender.panels.farm_replay')
    shared_panel = frontend_module('wfrl_blender.panels.imbalance')
    if forces:
        package, _manifest, _telemetry = _synthetic_replay(tmp_path / 'source')
        data, _hashes = producer.build(package, ROOT)
    else:
        data = imbalance.load_for_source(SOURCE)
        for row in data['turbines'].values():
            for key in imbalance.ROOT_FORCES:
                row['metrics'].pop(key)
                for turn in row['turns']:
                    turn['metrics'].pop(key)
    active = SimpleNamespace(imbalance=data)
    scene = SimpleNamespace(wfrl_imbalance_turbine='T1', wfrl_imbalance_metric='root_Mz')
    monkeypatch.setattr(farm_flex, 'active_for', lambda owner: active if owner is scene else None)
    monkeypatch.setattr(panel.clearance_replay, 'sample', lambda owner: {'time_s': 16.})
    for target in (panel, shared_panel):
        monkeypatch.setattr(target, 'message', lambda layout, text, **kwargs: layout.label(text=text))
    layout = _ResponseLayout()
    shared_panel.draw(panel._BladeResponseLayout(layout), scene)
    panel.draw_blade_mechanics(layout, scene)
    assert 'wfrl_imbalance_metric' not in layout.properties
    assert ('wfrl_imbalance_metric', 'root_Mz', '叶根扭矩 Mz（r系）') in layout.choices
    text = '\n'.join(layout.labels)
    assert '叶根弯矩 Mz' not in text
    assert 'RootMzr 为叶根扭矩' in text and 'IEC 叶片 b 系' in text
    assert '弹性扭转：未提供数值' in layout.labels
    assert 'Wiener–Milenković' in text and '已扣除参考预扭' in text
    assert '局部展向不等同 r 系 z 轴' in text and '不能唯一分离' in text
    assert '当前数据未提供展向截面旋转或扭转应变' in text
    assert '不作为弹性扭转角（°）' in text
    assert '刚体转动、变桨、预扭和弯曲引起的转动尚未分离' not in text
    if forces:
        assert 'B1 均值 +1.0000 / 偏差 -1.3333 kN' in layout.labels
        assert any(label.startswith('最近完整转差异：') for label in layout.labels)
    else:
        assert '当前分析包未提供叶根力统计' in layout.labels
        assert not any(label.endswith(' kN') for label in layout.labels)
        scene.wfrl_imbalance_metric = 'root_Fx'
        assert imbalance.plot_data(scene) is None
