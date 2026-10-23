"""Deformation caching follows mesh consumers and reports the actual work."""
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender import farm_flex, performance


@pytest.fixture
def replay():
    active = object.__new__(farm_flex.FarmFlex)
    active.cache, active.groups, active.reference_matrices = {}, {}, {}
    active.visible_turbines = {1}
    active.blades = []
    active.transforms = np.zeros((2, 3, 3, 19, 3, 4), dtype=np.float32)
    active.transforms[..., :3] = np.eye(3)
    active.transforms[1, ..., 0, 3] = 2
    for k in range(3):
        obj = SimpleNamespace(name=f'T{k+1}', data=SimpleNamespace(vertices=[None, None]))
        points = np.array([[0., 0., 1.5], [0., 0., 2.]], dtype=np.float32)
        active.blades.append((obj, points.copy(), points, np.zeros(2, dtype=int),
                              np.zeros((2, 1), dtype=np.float32), k, 0, np.zeros(3)))
        active.groups[obj.name] = [(0, np.array([0, 1]))]
        active.reference_matrices[obj.name] = np.eye(4)
    performance.configure(True)
    yield active
    performance.configure(False)


def test_same_frame_visibility_change_does_not_reuse_missing_blades(replay):
    hidden = replay.deformed_at(1)
    assert [row is not None for row in hidden] == [False, True, False]
    assert replay.deformed_at(1) is hidden
    replay.visible_turbines = {0, 1, 2}
    visible = replay.deformed_at(1)
    assert all(row is not None for row in visible)
    for row, binding in zip(visible, replay.blades):
        np.testing.assert_array_equal(row, binding[2] + [2., 0., 0.])
    replay.visible_turbines = {1}
    assert replay.deformed_at(1) is hidden
    counts = performance.snapshot()['counts']
    assert counts['flex.cache_miss'] == 2
    assert counts['flex.cache_hit'] == 2


def test_export_set_takes_precedence_and_cache_remains_bounded(replay):
    replay.visible_turbines = {0, 1, 2}
    for target in (0, 1, 2, 0):
        replay.export_turbines = {target}
        rows = replay.deformed_at(0)
        assert [row is not None for row in rows] == [k == target for k in range(3)]
        replay.deformed_at(1)
        assert len(replay.cache) <= 3


def test_runtime_cache_timing_is_separate_from_edit_rebinding(replay):
    replay.deformed_at(0)
    replay.deformed_at(0)
    before = performance.snapshot()['timings']
    assert before['flex.deformation_cache']['calls'] == 2
    assert 'flex.rebind_blade' not in before
    obj, rest, *_ = replay.blades[1]
    edited = rest + [1., 0., 0.]
    replay.rebind_blade(obj, edited)
    assert replay.cache == {}
    after = performance.snapshot()['timings']
    assert after['flex.deformation_cache']['calls'] == 2
    assert after['flex.rebind_blade']['calls'] == 1
    np.testing.assert_array_equal(replay.deformed_at(0)[1], edited)
