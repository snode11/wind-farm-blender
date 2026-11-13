"""Invalid flexible geometry is rejected before publishing a successful frame."""
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from test_farm_flex_world_matrices import Object, Vertices, mathutils_module, replay  # noqa: F401
from test_farm_flex_package_cache import isolated_cache, packages  # noqa: F401
from test_farm_flex_validation import refresh_digest
from wfrl_blender import clearance_replay, farm_flex, playback


@pytest.mark.parametrize('high_precision,value', [
    (False, np.nan), (False, np.inf), (False, -np.inf), (False, 1e300),
    (True, np.nan), (True, np.inf), (True, -np.inf),
])
def test_deform_points_rejects_nonfinite_coordinates(replay, value, high_precision):
    active, _scene, _ = replay
    obj = active.blades[0][0]
    with np.errstate(over='ignore', invalid='ignore'):
        with pytest.raises(ValueError, match='finite'):
            active.deform_points(obj, [[value, 0., 1.5]], .5, world=True,
                                 high_precision=high_precision)


@pytest.mark.parametrize('time_s', [np.nan, np.inf, -1., 2.])
def test_empty_point_query_still_rejects_invalid_time(replay, time_s):
    active, _scene, _ = replay
    with pytest.raises(ValueError, match='time outside'):
        active.deform_points(active.blades[0][0], [], time_s, world=True)


def test_edit_mode_tower_is_rejected_before_any_frame_write(replay, monkeypatch):
    active, scene, _ = replay
    active.comparison = None
    rest = np.array([[0., 0., 0.], [0., 0., 87.6]], dtype=np.float32)
    tower = Object('WFRL.Turbine.T1.Tower', np.eye(4))
    tower.data = SimpleNamespace(vertices=Vertices(rest), is_editmode=True, update=Mock())
    scene.objects[tower.name] = tower
    active.towers = [(tower, rest, np.zeros(2, dtype=int), np.zeros((2, 1)), 0)]
    active.tower_motion = {'present': True}
    before = [np.array([v.co.copy() for v in row[0].data.vertices]) for row in active.blades]
    sampled = Mock(side_effect=AssertionError('Frame calculation must follow preflight'))
    monkeypatch.setattr(active, '_geometry_sample', sampled)
    monkeypatch.setattr(playback, 'cancel', Mock())
    monkeypatch.setattr(clearance_replay, 'clear', lambda scene, reason: None)
    monkeypatch.setattr(active, 'record_telemetry', Mock())

    with pytest.raises(ValueError, match='edit mode.*Tower'):
        active.update(scene, sim_time_s=.5)

    sampled.assert_not_called()
    active.record_telemetry.assert_not_called()
    assert scene['wfrl_clearance_time_s'] == 0.
    assert not scene['wfrl_flex_frame_valid'] and active.frame_error
    tower.data.update.assert_not_called()
    for row, coordinates in zip(active.blades, before):
        np.testing.assert_array_equal([v.co for v in row[0].data.vertices], coordinates)


@pytest.mark.parametrize('fault', [
    'section_count', 'nan_transform', 'infinite_transform', 'nan_pose',
    'missing_frame', 'duplicate_time', 'degenerate_tower_sections',
])
def test_source_geometry_rejects_invalid_sections_and_missing_frames(packages, fault):
    path = packages()
    name = 'tower-motion.npz' if fault == 'degenerate_tower_sections' else 'geometry.npz'
    with np.load(path / name, allow_pickle=False) as archive:
        arrays = dict(archive)
    if fault == 'section_count':
        arrays['transforms'] = arrays['transforms'][:, :, :, :-1]
    elif fault in ('nan_transform', 'infinite_transform'):
        arrays['transforms'][1, 0, 0, 0, 0, 3] = np.nan if fault == 'nan_transform' else np.inf
    elif fault == 'nan_pose':
        arrays['poses'][1, 0, 0] = np.nan
    elif fault == 'missing_frame':
        arrays['transforms'] = arrays['transforms'][:-1]
    elif fault == 'duplicate_time':
        arrays['times'][1] = arrays['times'][0]
    else:
        arrays['heights'] = np.array([0., 0., 87.6])
        arrays['transforms'] = arrays['transforms'][:, :, [0, 0, 1]]
    np.savez(path / name, **arrays)
    refresh_digest(path, name)
    with pytest.raises(ValueError, match='geometry|tower motion'):
        farm_flex.read_package(path)
