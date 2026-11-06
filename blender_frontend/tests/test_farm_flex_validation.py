"""Untrusted farm input must retain one verified identity through parsing and binding."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from blender_frontend.tests._platform import symlink_or_skip
from test_farm_flex_package_cache import isolated_cache, packages  # noqa: F401
from wfrl_blender import farm_flex


def rewrite_manifest(path, change):
    manifest = json.loads((path / 'manifest.json').read_bytes())
    change(manifest)
    (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')


def refresh_digest(path, name):
    rewrite_manifest(path, lambda m: m['files'].update(
        {name: hashlib.sha256((path / name).read_bytes()).hexdigest()}))


def scene_metadata(path):
    rewrite_manifest(path, lambda m: m.update(calibration=dict(
        origin_m=[0., 0., 90.], beam_directions=[[0., 0., -1.]] * 3)))


@pytest.mark.parametrize('name', ['geometry.npz', 'data.json', 'tower-motion.npz'])
def test_parse_uses_verified_bytes_when_source_changes(packages, monkeypatch, name):
    path = packages()
    parse = farm_flex._parse_package
    parsed_paths = []

    def replace_source(snapshot, manifest):
        parsed_paths.append(snapshot)
        target = path / name
        if name == 'data.json':
            data = json.loads(target.read_bytes())
            data['T1']['motion'][0]['yaw_deg'] = .0005
            target.write_text(json.dumps(data), encoding='utf-8')
        else:
            with np.load(target) as archive:
                arrays = dict(archive)
            arrays['transforms'][..., 0, 3] += .25
            np.savez(target, **arrays)
        return parse(snapshot, manifest)

    monkeypatch.setattr(farm_flex, '_parse_package', replace_source)
    loaded = farm_flex._load_package(path)
    assert np.all(loaded.transforms[..., 0, 3] == 0.)
    assert loaded.poses[0, 0, 0] == 0.
    assert np.all(loaded.tower_motion['transforms'][..., 0, 3] == 0.)
    assert all(not snapshot.exists() for snapshot in parsed_paths)
    with pytest.raises(ValueError, match='integrity mismatch'):
        farm_flex.read_package(path)


def test_tower_verification_and_npz_read_share_snapshot(packages, monkeypatch):
    path = packages()
    original = np.load

    def load_archive(file, **kwargs):
        if Path(file).name == 'tower-motion.npz':
            with original(path / 'tower-motion.npz') as archive:
                arrays = dict(archive)
            arrays['transforms'][..., 0, 3] += .25
            np.savez(path / 'tower-motion.npz', **arrays)
        return original(file, **kwargs)

    monkeypatch.setattr(np, 'load', load_archive)
    package = farm_flex._load_package(path)
    assert np.all(package.tower_motion['transforms'][..., 0, 3] == 0.)


def test_result_layer_uses_verified_bytes(tmp_path, monkeypatch):
    source = farm_flex.default_dual_package()
    manifest = json.loads((source / 'manifest.json').read_bytes())
    manifest['source_package'] = str(farm_flex.default_package())
    for name in manifest['files']:
        shutil.copyfile(source / name, tmp_path / name)
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    api = farm_flex.dual_beam_api()
    digest = api.digest

    def replace_after_hash(path):
        hashed = digest(path)
        if Path(path).name == 'calibration.json':
            target = tmp_path / 'calibration.json'
            config = json.loads(target.read_bytes())
            config['unverified_note'] = 'changed after digest'
            target.write_text(json.dumps(config), encoding='utf-8')
        return hashed

    monkeypatch.setattr(api, 'digest', replace_after_hash)
    loaded = farm_flex._load_package(tmp_path)
    assert 'unverified_note' not in loaded.overlay['config']
    assert loaded.overlay['manifest'] == manifest
    assert loaded.source_path == farm_flex.default_package().resolve()


def test_preflight_keeps_captured_identity_after_replacement(packages):
    path = packages()
    scene_metadata(path)
    captured = hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest()
    package = farm_flex.preflight(path)
    rewrite_manifest(path, lambda m: m.update(note='replacement'))
    farm_flex.validate_candidate(path, package)
    assert package.result_manifest_sha256 == captured
    assert 'note' not in package.manifest
    assert farm_flex.read_package(path)[0]['note'] == 'replacement'


@pytest.mark.parametrize('relocate', [False, True])
def test_saved_scene_identity_is_checked_against_captured_manifest(packages, monkeypatch, relocate):
    path = packages()
    expected = hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest()
    monkeypatch.setattr(farm_flex, 'default_package', lambda: path)
    scene = dict(wfrl_farm_flex_path=str(path / 'missing' if relocate else path),
                 wfrl_farm_manifest_sha256=expected)
    candidate = farm_flex.saved_package(scene)
    assert hashlib.sha256((candidate / 'manifest.json').read_bytes()).hexdigest() == expected
    rewrite_manifest(path, lambda m: m.update(note='replacement after restore check'))
    with pytest.raises(ValueError, match='Saved scene and farm package manifest differ'):
        farm_flex.read_package(candidate)


def test_constructor_rejects_candidate_for_another_path(packages, monkeypatch):
    path, other = packages(), packages('other')
    scene_metadata(path)
    package = farm_flex.preflight(path)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    with pytest.raises(ValueError, match='requested result path'):
        farm_flex.FarmFlex(None, other, _package=package)


def test_candidate_rejects_retargeted_directory_symlink(packages, tmp_path):
    path, other = packages(), packages('other')
    scene_metadata(path)
    alias = tmp_path / 'alias'
    symlink_or_skip(alias, path, target_is_directory=True)
    package = farm_flex.preflight(alias)
    alias.unlink()
    symlink_or_skip(alias, other, target_is_directory=True)
    with pytest.raises(ValueError, match='requested result path'):
        farm_flex.validate_candidate(alias, package)


@pytest.mark.parametrize('name', [
    '../outside.json', '/outside.json', 'C:\\outside.json', 'nested/../../outside.json',
    'C:outside.json', 'nested\\outside.json', '', 'nested//outside.json',
    './outside.json', 'nested/./outside.json', 'nested/',
])
def test_inventory_rejects_paths_outside_package(packages, name):
    path = packages()
    rewrite_manifest(path, lambda m: m['files'].update({name: '0' * 64}))
    with pytest.raises(ValueError, match='Unsafe farm package file path'):
        farm_flex.read_package(path)


def test_inventory_allows_external_file_symlink_with_matching_digest(packages, tmp_path):
    path = packages()
    outside = tmp_path / 'external.json'
    outside.write_text('{}', encoding='utf-8')
    symlink_or_skip(path / 'extra.json', outside)
    rewrite_manifest(path, lambda m: m['files'].update(
        {'extra.json': hashlib.sha256(outside.read_bytes()).hexdigest()}))
    farm_flex.read_package(path)
    outside.write_text('{"changed": true}', encoding='utf-8')
    with pytest.raises(ValueError, match='integrity mismatch: extra.json'):
        farm_flex.read_package(path)


@pytest.mark.parametrize('target_kind', ['missing', 'directory'])
def test_inventory_rejects_symlink_to_non_file(packages, tmp_path, target_kind):
    path = packages()
    outside = tmp_path / 'external'
    if target_kind == 'directory':
        outside.mkdir()
    symlink_or_skip(path / 'extra.json', outside, target_is_directory=target_kind == 'directory')
    rewrite_manifest(path, lambda m: m['files'].update({'extra.json': '0' * 64}))
    with pytest.raises(ValueError, match='missing or not a regular file: extra.json'):
        farm_flex.read_package(path)


@pytest.mark.parametrize('name', ['manifest.json', 'data.json', 'telemetry.json'])
def test_duplicate_json_keys_are_not_silently_overwritten(packages, name):
    path = packages()
    if name == 'telemetry.json':
        raw = '{"turbines":{"T1":{"power":{},"power":{}}}}'
    else:
        raw = (path / name).read_text(encoding='utf-8')
        raw = '{"duplicate":1,"duplicate":2,' + raw[1:]
    (path / name).write_text(raw, encoding='utf-8')
    if name != 'manifest.json':
        refresh_digest(path, name)
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        farm_flex.read_package(path)


@pytest.mark.parametrize('corruption', ['zero_rotation', 'reflection', 'boolean_poses', 'scalar_time'])
def test_invalid_geometry_is_rejected_before_binding(packages, corruption):
    path = packages()
    with np.load(path / 'geometry.npz') as archive:
        arrays = dict(archive)
    if corruption == 'zero_rotation':
        arrays['transforms'][..., :3] = 0
    elif corruption == 'reflection':
        arrays['transforms'][..., 0, 0] = -1
    elif corruption == 'boolean_poses':
        arrays['poses'] = arrays['poses'].astype(bool)
    else:
        arrays['times'] = np.array(0.)
    np.savez(path / 'geometry.npz', **arrays)
    refresh_digest(path, 'geometry.npz')
    with pytest.raises(ValueError, match='farm (blade rotation|geometry numeric arrays)'):
        farm_flex.read_package(path)


def test_reader_input_mutation_cannot_poison_cached_source(packages):
    path = packages()
    first, second = farm_flex._load_package(path), farm_flex._load_package(path)
    first.readers['T1'].package.motion[0]['yaw_deg'] = 20.
    first.readers['T1'].package.statistics['valid_samples'] = 99
    third = farm_flex._load_package(path)
    for package in (second, third):
        assert package.readers['T1'].at(0.)['motion']['yaw_deg'] == 0.
        assert package.readers['T1'].package.statistics['valid_samples'] == 0


@pytest.mark.parametrize('missing', ['layout_m', 'calibration'])
def test_preflight_requires_metadata_used_by_scene(packages, missing):
    path = packages()
    scene_metadata(path)
    rewrite_manifest(path, lambda m: m.pop(missing))
    with pytest.raises(ValueError, match=missing):
        farm_flex.preflight(path)


@pytest.mark.parametrize('fault', ['unit', 'source_channel', 'sources', 'boolean_values'])
def test_preflight_rejects_unusable_telemetry(packages, fault):
    path = packages()
    scene_metadata(path)
    manifest = json.loads((path / 'manifest.json').read_bytes())
    extra = dict(schema='wfrl.farm-telemetry.v1', times=[0., 1., 2.],
                 geometry_sha256=manifest['files']['geometry.npz'],
                 turbines={tid: dict(power=dict(unit='MW', source_channel='GenPwr', values=[0., 0., 0.]))
                           for tid in manifest['turbine_ids']},
                 sources={tid: dict(sha256='0' * 64) for tid in manifest['turbine_ids']})
    if fault == 'sources':
        del extra['sources']['T1']
    elif fault == 'boolean_values':
        extra['turbines']['T1']['power']['values'] = [0., True, 0.]
    else:
        del extra['turbines']['T1']['power'][fault]
    (path / 'telemetry.json').write_text(json.dumps(extra), encoding='utf-8')
    refresh_digest(path, 'telemetry.json')
    with pytest.raises(ValueError, match='[Tt]elemetry'):
        farm_flex.preflight(path)


def test_unknown_metadata_and_zero_channels_remain_valid(packages):
    path = packages()
    scene_metadata(path)
    rewrite_manifest(path, lambda m: m.update(provenance_note='extension metadata'))
    package = farm_flex.preflight(path)
    assert package.manifest['provenance_note'] == 'extension metadata'
    assert np.all(package.poses == 0)


def test_bad_candidate_displays_cause_and_retains_current_replay(monkeypatch):
    from test_scene_playback_lifecycle import Scene
    from wfrl_blender import clearance_replay
    scene = Scene('current')
    active = SimpleNamespace(scene=scene, enabled=True)
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace())
    monkeypatch.setattr(farm_flex, '_ACTIVES', {scene.as_pointer(): active})
    reader = object()
    monkeypatch.setattr(clearance_replay, '_READERS', {scene.as_pointer(): reader})

    def invalid(path):
        raise ValueError('Invalid telemetry samples: T2/power')

    monkeypatch.setattr(farm_flex, '_load_package', invalid)
    with pytest.raises(ValueError, match='T2/power'):
        farm_flex.attach(scene, 'candidate')
    assert 'T2/power' in scene['wfrl_home_load_error']
    assert farm_flex.active_for(scene) is active
    assert clearance_replay._READERS[scene.as_pointer()] is reader


def test_boolean_support_transforms_are_not_numeric_motion(packages):
    path = packages()
    with np.load(path / 'tower-motion.npz') as archive:
        arrays = dict(archive)
    arrays['transforms'] = arrays['transforms'].astype(bool)
    np.savez(path / 'tower-motion.npz', **arrays)
    refresh_digest(path, 'tower-motion.npz')
    with pytest.raises(ValueError, match='tower numeric arrays'):
        farm_flex.read_package(path)


@pytest.mark.parametrize('value', [False, '0.0'])
def test_deflection_rejects_nonnumeric_displacement_channels(packages, value):
    from wfrl_blender.turbine_geometry import geometry_data

    path = packages()
    manifest = json.loads((path / 'manifest.json').read_bytes())
    comparison = dict(schema='wfrl.tip-deflection.t1.v2', turbine_id='T1', unit='m',
        reference='ElastoDyn structural tip; local (0,0,TipRad)',
        frame='ElastoDyn unpitched coned xc,yc,zc',
        geometry_sha256=manifest['files']['geometry.npz'],
        tower_motion_sha256=manifest['files']['tower-motion.npz'],
        scalars=geometry_data()['scalars'], times=[0., 1., 2.],
        poses=np.zeros((3, 6)).tolist(), simulation=np.zeros((3, 3, 2)).tolist())
    comparison['simulation'][0][0][0] = value
    (path / 'deflection-t1.json').write_text(json.dumps(comparison), encoding='utf-8')
    refresh_digest(path, 'deflection-t1.json')
    with pytest.raises(ValueError, match='deflection samples: simulation'):
        farm_flex.read_package(path)


@pytest.mark.parametrize('variant', ['default_package', 'default_dual_package'])
def test_cache_hit_hashes_every_file_without_copying_or_staging(monkeypatch, variant):
    from wfrl_blender import farm_flex_package as module

    path = getattr(farm_flex, variant)()
    first = farm_flex._load_package(path)
    captured, reads = [], []
    capture, read = module._capture_package, module._file_bytes

    def capture_package(path):
        snapshot = capture(path)
        assert all(file.raw is None for file in snapshot.files.values())
        captured.append(snapshot.path)
        return snapshot

    def read_file(path, name):
        reads.append((path, name))
        return read(path, name)

    def no_temporary_files(*args, **kwargs):
        pytest.fail('A cache hit must not create or write a package snapshot')

    monkeypatch.setattr(module, '_capture_package', capture_package)
    monkeypatch.setattr(module, '_file_bytes', read_file)
    monkeypatch.setattr(module, 'TemporaryDirectory', no_temporary_files)
    monkeypatch.setattr(Path, 'write_bytes', no_temporary_files)
    second = farm_flex._load_package(path)
    expected = [(package, name) for package in captured
                for name in ('manifest.json', *json.loads((package / 'manifest.json').read_bytes())['files'])]
    assert reads == expected
    assert second.transforms is first.transforms
    assert second.readers['T1'].at(second.times[0]) == first.readers['T1'].at(first.times[0])


@pytest.mark.parametrize('token', ['NaN', 'Infinity', '-Infinity', '1e309', '-1e309', '9' * 400 + '.0'])
def test_cached_source_rejects_new_nonfinite_extension_json(packages, token):
    path = packages()
    farm_flex.read_package(path)
    (path / 'extra.json').write_text('{"audit":[' + token + ']}', encoding='utf-8')
    refresh_digest(path, 'extra.json')
    with pytest.raises(ValueError, match='Nonfinite JSON number'):
        farm_flex.read_package(path)


def test_reader_fields_are_lazy_private_mutable_copies(packages):
    first, second = (farm_flex._load_package(path) for path in [packages()] * 2)
    a, b = first.readers['T1'].package, second.readers['T1'].package
    assert not {'motion', 'measurements', 'cumulative', 'statistics'} & vars(a).keys()
    a.motion[0]['pitch_deg'][0] = 42.
    a.cumulative.append({'statistics': {'valid_samples': 42}})
    a.statistics['valid_samples'] = 42
    a.measurements.append({'private': True})
    assert b.motion[0]['pitch_deg'][0] == 0.
    assert b.cumulative == []
    assert b.statistics['valid_samples'] == 0
    assert b.measurements == []


@pytest.fixture
def dual_snapshot_package(packages, tmp_path):
    source = packages()
    rewrite_manifest(source, lambda m: m.update(schema='wfrl.farm-flex-review.v3'))
    source_manifest = json.loads((source / 'manifest.json').read_bytes())
    path = tmp_path / 'dual'
    path.mkdir()
    config = dict(schema='wfrl.dual-beam-calibration.v1', status='REVIEW_ONLY',
                  alarm_hold_s=1., measurement_hold_s=1., effective_length_m=1.,
                  origins_m=[[0., 0., 0.]] * 3, angles_deg=[10., 20., 30.])
    results = {tid: {'samples': []} for tid in source_manifest['turbine_ids']}
    for name, data in [('calibration.json', config), ('results.json', results)]:
        (path / name).write_text(json.dumps(data), encoding='utf-8')
    manifest = dict(schema='wfrl.dual-beam-review.v1', status='REVIEW_ONLY', sample_kind='source',
                    turbine_ids=source_manifest['turbine_ids'], segment=source_manifest['segment'],
                    source_package=str(source),
                    source_hashes={'manifest.json': hashlib.sha256((source / 'manifest.json').read_bytes()).hexdigest(),
                                   **source_manifest['files']},
                    files={file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in path.iterdir()})
    (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return path, source


@pytest.mark.parametrize('fault', [None, 'rejected', 'status', 'sample_kind', 'source_digest',
    'source_inventory', 'source_manifest', 'required_result', 'segment', 'config_rejected',
    'config_schema', 'hold', 'origins', 'angles', 'turbines', 'algorithm'])
def test_captured_dual_resolver_preserves_path_validator_contract(dual_snapshot_package, fault):
    from wfrl_blender import farm_flex_package as module

    path, source = dual_snapshot_package
    manifest = json.loads((path / 'manifest.json').read_bytes())
    config = json.loads((path / 'calibration.json').read_bytes())
    if fault in ('rejected', 'status', 'sample_kind'):
        key = 'sample_kind' if fault == 'sample_kind' else 'status'
        manifest[key] = 'REJECTED' if fault == 'rejected' else 'unsupported'
    elif fault == 'source_digest':
        manifest['source_hashes']['data.json'] = '0' * 64
    elif fault in ('source_inventory', 'source_manifest'):
        manifest['source_hashes'].pop('data.json' if fault == 'source_inventory' else 'manifest.json')
    elif fault == 'required_result':
        manifest['files'].pop('results.json')
    elif fault == 'segment':
        manifest['segment']['end_s'] += 1
    elif fault == 'config_rejected':
        config['status'] = 'REJECTED'
    elif fault == 'config_schema':
        config['schema'] = 'unsupported'
    elif fault == 'hold':
        config['alarm_hold_s'] = 0
    elif fault == 'origins':
        config['origins_m'][0] = [0., 0.]
    elif fault == 'angles':
        config['angles_deg'] = [30., 20., 10.]
    elif fault == 'algorithm':
        manifest['reconstruction_method'] = 'hub-tls.v1'
    elif fault == 'turbines':
        (path / 'results.json').write_text('{"T1":{"samples":[]}}', encoding='utf-8')
        manifest['files']['results.json'] = hashlib.sha256((path / 'results.json').read_bytes()).hexdigest()
    (path / 'calibration.json').write_text(json.dumps(config), encoding='utf-8')
    manifest['files']['calibration.json'] = hashlib.sha256((path / 'calibration.json').read_bytes()).hexdigest()
    (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    api = farm_flex.dual_beam_api()
    if fault is None:
        expected_source, expected_overlay = api.core.resolve_package(path)
        with module._snapshot(path) as captured:
            actual_source, actual_overlay = api.resolve_package(captured)
            assert actual_source.path == expected_source == source
            assert actual_overlay == expected_overlay
    else:
        with pytest.raises(ValueError) as expected:
            api.core.resolve_package(path)
        with module._snapshot(path) as captured, pytest.raises(ValueError) as actual:
            api.resolve_package(captured)
        assert str(actual.value) == str(expected.value)
