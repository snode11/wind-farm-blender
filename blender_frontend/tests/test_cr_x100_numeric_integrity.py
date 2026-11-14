"""Material identities and recorded supported estimates retain their numeric contract."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.radar_x100 import schema
from wfrl_blender.radar_x100 import clearance, scene as scene_api


def material_sampler():
    sampler = scene_api.SceneSampler.__new__(scene_api.SceneSampler)
    vertices = np.array([[0., 0., 0.], [3., 0., 0.], [0., 3., 0.],
                         [0., 0., 9.], [3., 0., 9.], [0., 3., 9.]])
    sampler.triangles = np.array([[0, 1, 2], [3, 4, 5]])
    sampler.parts = [('surface', sampler.triangles)]
    sampler.triangle_offsets = np.array([0, 2])
    sampler._material_ids = sampler._material_plan = None
    sampler._validate_sampling = lambda: None
    sampler._part_vertices = lambda part, t, indices: vertices[indices]
    sampler.mount_world_at = lambda t: np.eye(4)
    sampler.mount_from_sensor = np.eye(4)
    sampler.source_id, sampler.geometry_id = 'source', 'geometry'
    return sampler


@pytest.mark.parametrize('ids', [[.5], [0.], [True], ['0'], np.array([2**32], dtype=np.uint64)])
def test_material_indices_reject_fractional_wrapped_and_noninteger_identities(ids):
    sampler = material_sampler()
    with pytest.raises(ValueError, match='material triangle'):
        sampler.material_sample(0., ids)
    assert sampler._material_ids is None


def test_material_indices_preserve_integer_order_barycentrics_and_empty_samples():
    sampler = material_sampler()
    sample = sampler.material_sample(0., np.array([1, 0], dtype=np.uint64),
        barycentric=[[.5, .5, 0.], [0., 0., 1.]])
    np.testing.assert_array_equal(sample.points, [[1.5, 0., 9.], [0., 3., 0.]])
    assert sampler.material_sample(0., []).points.shape == (0, 3)


def recorded_payload():
    frame = schema.RadarFrame('X100', 'recorded', 1, 'ENGINEERING_SIMULATION', 'DETECTIONS',
        np.zeros(20, dtype=schema.POINT_DTYPE))
    supported = dict(blade=1, status='SUPPORTED', points=20, tip_world=[1., 2., 3.],
        clearance_m=2., clearance_std_m=.1, clearance_interval95_m=[1.8, 2.2],
        fit_residual_m=.01, extrapolation_m=1., consecutive_frames=1)
    missing = dict(status='INSUFFICIENT_POINTS', points=0, tip_world=None, clearance_m=None,
        clearance_std_m=None, clearance_interval95_m=None, fit_residual_m=None,
        extrapolation_m=None, consecutive_frames=0)
    result = dict(labels=[1]*20, blades=[supported, dict(missing, blade=2), dict(missing, blade=3)],
                  uncertainty='uncalibrated model interval')
    return frame, dict(schema=clearance.SCHEMA, frame_sha256=clearance.frame_digest(frame), result=result, error='')


def write_payload(tmp_path, payload):
    directory = tmp_path / 'clearance'
    directory.mkdir(exist_ok=True)
    signed = dict(payload, sha256=schema.digest(payload))
    (directory / '00000000.json').write_text(json.dumps(signed), encoding='utf-8')
    return SimpleNamespace(path=tmp_path)


@pytest.mark.parametrize(('field', 'value'), [
    ('tip_world', None), ('tip_world', [1., 2.]), ('tip_world', [1., True, 3.]),
    ('tip_world', ['1', 2., 3.]), ('points', 0), ('points', -1), ('points', 21),
    ('points', True), ('consecutive_frames', 0), ('consecutive_frames', 1.5),
    ('clearance_m', True), ('clearance_std_m', False),
    ('fit_residual_m', None), ('fit_residual_m', -1.), ('extrapolation_m', -1.),
])
def test_recorded_supported_estimate_rejects_incomplete_or_invalid_numeric_fields(tmp_path, field, value):
    frame, payload = recorded_payload()
    payload['result']['blades'][0][field] = value
    replay = write_payload(tmp_path, payload)
    with pytest.raises(ValueError, match='记录'):
        clearance.read_recorded(replay, 0, frame)


@pytest.mark.parametrize('labels', [[True]*20, [1.]*20])
def test_recorded_classification_requires_integer_labels(tmp_path, labels):
    frame, payload = recorded_payload()
    payload['result']['labels'] = labels
    replay = write_payload(tmp_path, payload)
    with pytest.raises(ValueError, match='记录'):
        clearance.read_recorded(replay, 0, frame)


def test_recorded_supported_and_missing_estimates_round_trip_without_substitution(tmp_path):
    frame, payload = recorded_payload()
    expected = deepcopy(payload['result'])
    replay = write_payload(tmp_path, payload)
    result, error = clearance.read_recorded(replay, 0, frame)
    assert result == expected and error == ''
    assert result['blades'][1]['clearance_m'] is None
