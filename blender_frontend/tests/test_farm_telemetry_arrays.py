"""Telemetry interpolation retains validated arrays and package identity."""
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender import farm_flex


@pytest.fixture
def telemetry(tmp_path):
    times = np.array([0., 1., 2.])
    manifest = {'files': {'geometry.npz': 'geometry-digest', 'telemetry.json': 'digest'}}
    payload = {
        'schema': 'wfrl.farm-telemetry.v1',
        'geometry_sha256': 'geometry-digest',
        'times': times.tolist(),
        'turbines': {'T1': {
            'power': {'values': [0, 4, 2], 'unit': 'kW', 'source_channel': 'GenPwr'},
            'load': {'values': [-1.25, 0., 2.5], 'unit': 'kN', 'source_channel': 'Load'},
        }},
        'sources': {'T1': {'sha256': 'source-digest'}},
    }
    path = tmp_path / 'telemetry.json'
    path.write_text(json.dumps(payload))
    return tmp_path, manifest, times, {'T1': object()}, payload


def load_fixture(telemetry):
    path, manifest, times, readers, _payload = telemetry
    return farm_flex._read_telemetry(path, manifest, times, readers)


def test_bundled_interpolation_matches_source_lists():
    path = farm_flex.default_package()
    manifest = json.loads((path / 'manifest.json').read_text())
    source = json.loads((path / 'telemetry.json').read_text())
    with np.load(path / 'geometry.npz', allow_pickle=False) as geometry:
        times = geometry['times'].copy()
    loaded = farm_flex._read_telemetry(path, manifest, times, manifest['turbine_ids'])
    query = np.r_[times, (times[:-1] + times[1:]) / 2, times[0] - 1, times[-1] + 1]
    for tid, channels in loaded['turbines'].items():
        for name, record in channels.items():
            values = record['values']
            original = source['turbines'][tid][name]
            assert isinstance(values, np.ndarray)
            assert values.dtype == np.dtype(float)
            np.testing.assert_array_equal(values, original['values'])
            np.testing.assert_array_equal(
                np.interp(query, times, values), np.interp(query, times, original['values']))
            assert record['unit'] == original['unit']
            assert record['source_channel'] == original['source_channel']


@pytest.mark.parametrize('values', [[1., 2.], [[1.], [2.], [3.]], [1., float('nan'), 3.],
                                   [1., float('inf'), 3.], [1., -float('inf'), 3.]])
def test_invalid_samples_are_rejected(telemetry, values):
    path, _manifest, _times, _readers, payload = telemetry
    payload['turbines']['T1']['power']['values'] = values
    (path / 'telemetry.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='Invalid telemetry samples'):
        load_fixture(telemetry)


@pytest.mark.parametrize('values', [['1', '2', '3'], [None, 2., 3.]])
def test_conversion_does_not_accept_nonnumeric_samples(telemetry, values):
    path, _manifest, _times, _readers, payload = telemetry
    payload['turbines']['T1']['power']['values'] = values
    (path / 'telemetry.json').write_text(json.dumps(payload))
    with pytest.raises(TypeError):
        load_fixture(telemetry)


@pytest.mark.parametrize(('field', 'value'), [
    ('schema', 'unsupported'), ('geometry_sha256', 'other-geometry'),
    ('times', [0., 1.5, 2.]), ('turbines', {}),
])
def test_telemetry_identity_must_match_geometry(telemetry, field, value):
    path, _manifest, _times, _readers, payload = telemetry
    payload[field] = value
    (path / 'telemetry.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='Telemetry and geometry do not match'):
        load_fixture(telemetry)


def test_reload_replaces_arrays_and_unlisted_telemetry(telemetry):
    path, manifest, _times, _readers, payload = telemetry
    first = load_fixture(telemetry)
    first_values = first['turbines']['T1']['power']['values']
    payload['turbines']['T1']['power']['values'] = [7., 8., 9.]
    (path / 'telemetry.json').write_text(json.dumps(payload))
    second = load_fixture(telemetry)
    second_values = second['turbines']['T1']['power']['values']
    assert second_values is not first_values
    np.testing.assert_array_equal(first_values, [0., 4., 2.])
    np.testing.assert_array_equal(second_values, [7., 8., 9.])
    del manifest['files']['telemetry.json']
    assert load_fixture(telemetry) is None


def test_null_optional_telemetry_remains_absent(telemetry):
    (telemetry[0] / 'telemetry.json').write_text('null')
    assert load_fixture(telemetry) is None


def test_recording_reuses_arrays_when_seeking(telemetry, monkeypatch):
    from wfrl_blender import charts, clearance_replay

    _path, manifest, times, readers, payload = telemetry
    replay = object.__new__(farm_flex.FarmFlex)
    replay.telemetry = load_fixture(telemetry)
    replay.times, replay.manifest, replay.readers = times, manifest, readers
    replay.last_telemetry_frame = None
    samples = iter([1.5, .5])
    monkeypatch.setattr(clearance_replay, 'sample', lambda _scene: {'time_s': next(samples)})
    recorded, cleared, inputs = [], [], []
    monkeypatch.setattr(charts, 'record_farm', lambda *args, scene: recorded.append(args))
    monkeypatch.setattr(charts, 'clear', lambda *, scene: cleared.append(True))
    interpolate = np.interp

    def observe_interpolation(t, xp, fp):
        inputs.append(fp)
        return interpolate(t, xp, fp)

    monkeypatch.setattr(farm_flex.np, 'interp', observe_interpolation)
    scene = {}
    for _ in range(2):
        replay.record_telemetry(scene, np.zeros((1, 6)))
    arrays = [record['values'] for record in replay.telemetry['turbines']['T1'].values()]
    assert all(actual is expected for actual, expected in zip(inputs, arrays * 2, strict=True))
    assert cleared == [True]
    assert scene['wfrl_telemetry_time_s'] == .5
    for t, _pose, _manifest, extra in recorded:
        for name, original in payload['turbines']['T1'].items():
            assert extra['T1'][name] == {
                'value': float(interpolate(t, times, original['values'])),
                'unit': original['unit'], 'source_channel': original['source_channel'],
                'source_sha256': 'source-digest',
            }


def test_package_loading_still_rejects_telemetry_checksum_mismatch(tmp_path, monkeypatch):
    files = {}
    for name in ('geometry.npz', 'data.json', 'source-surfaces.json', 'telemetry.json'):
        content = b'{}'
        (tmp_path / name).write_bytes(content)
        files[name] = hashlib.sha256(content).hexdigest()
    manifest = {'schema': 'wfrl.farm-flex-review.v1', 'status': 'REVIEW_ONLY',
                'turbine_ids': ['T1', 'T2', 'T3'], 'files': files}
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    (tmp_path / 'telemetry.json').write_text('{"changed": true}')
    api = SimpleNamespace(resolve_package=lambda path: (path, None))
    monkeypatch.setattr(farm_flex, 'dual_beam_api', lambda: api)
    with pytest.raises(ValueError, match='Farm package integrity mismatch: telemetry.json'):
        farm_flex._load_package(tmp_path)
