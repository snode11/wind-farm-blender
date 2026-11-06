"""Deformation caching follows mesh consumers and reports the actual work."""
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl_blender import farm_flex, performance


@pytest.fixture
def replay():
    active = object.__new__(farm_flex.FarmFlex)
    active.scene = SimpleNamespace()
    active._deformation_batches = None
    active.export_turbines = None
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
    assert replay._deformation_batches is None
    after = performance.snapshot()['timings']
    assert after['flex.deformation_cache']['calls'] == 2
    assert after['flex.rebind_blade']['calls'] == 1
    np.testing.assert_array_equal(replay.deformed_at(0)[1], edited)


def section_reference(replay, frame):
    results = []
    for obj, _rest, world, _index, weight, k, b, _offset in replay.blades:
        if k not in replay.mesh_turbines():
            results.append(None)
            continue
        deformed = np.empty_like(world)
        for section, ids in replay.groups[obj.name]:
            lo, hi = replay.transforms[frame, k, b, section:section+2]
            a = world[ids] @ lo[:, :3].T + lo[:, 3]
            z = world[ids] @ hi[:, :3].T + hi[:, 3]
            deformed[ids] = a*(1-weight[ids]) + z*weight[ids]
        results.append(deformed)
    return results


@pytest.mark.parametrize('dtype', [np.float32, np.float64])
def test_section_batches_match_unbatched_products_exactly(replay, dtype):
    rng = np.random.default_rng(9382)
    sizes = (1, 3, 3, 10, 10, 11, 144, 144, 192, 240, 1154)
    count = sum(sizes)
    replay.transforms = rng.normal(size=(5, 3, 3, 19, 3, 4)).astype(dtype)
    for position, row in enumerate(replay.blades):
        obj, _rest, _world, _index, _weight, k, b, offset = row
        obj.data.vertices = [None] * count
        world = rng.uniform(-90, 90, size=(count, 3)).astype(dtype)
        index = rng.permutation(np.repeat(np.arange(len(sizes)), sizes))
        weight = rng.random((count, 1)).astype(dtype)
        replay.blades[position] = (obj, world.copy(), world, index, weight, k, b, offset)
        replay.groups[obj.name] = [(s, np.flatnonzero(index == s)) for s in np.unique(index)]
    for visible in ({0, 1, 2}, {2}, {0}):
        replay.visible_turbines = visible
        for frame in range(5):
            expected = section_reference(replay, frame)
            actual = replay.deformed_at(frame)
            for result, reference in zip(actual, expected):
                if reference is None:
                    assert result is None
                else:
                    assert result.dtype == reference.dtype
                    np.testing.assert_array_equal(result, reference)
            assert len(replay.cache) <= 3


@pytest.mark.parametrize('change', ['scene', 'source', 'mesh', 'world', 'weight', 'groups'])
def test_section_batches_follow_scene_source_and_geometry(replay, change):
    replay.deformed_at(0)
    cached = replay._deformation_batches
    replay.deformed_at(1)
    assert replay._deformation_batches is cached
    obj, _rest, world, _index, weight, *_ = replay.blades[1]
    if change == 'scene':
        replay.scene = SimpleNamespace()
    elif change == 'source':
        replay.transforms = replay.transforms.copy()
        replay.transforms[..., 1, 3] = 3
    elif change == 'mesh':
        obj.data = SimpleNamespace(vertices=[None, None])
    elif change == 'world':
        world[:, 0] += 1
    elif change == 'weight':
        replay.transforms[:, :, :, 1, 0, 3] += 2
        weight[:] = .5
    else:
        replay.groups[obj.name] = [(1, np.array([1, 0]))]
        replay.transforms[:, :, :, 1, 0, 3] += 2
    expected = section_reference(replay, 1)
    actual = replay.deformed_at(1)
    assert replay._deformation_batches is not cached
    np.testing.assert_array_equal(actual[1], expected[1])


def test_source_sample_edits_keep_static_batches_and_resample_motion(replay):
    before = replay.deformed_at(1)[1].copy()
    cached = replay._deformation_batches
    replay.transforms[1, :, :, :, 0, 3] += 3
    result = replay.deformed_at(1)[1]
    assert replay._deformation_batches is cached
    np.testing.assert_array_equal(result, before + [3., 0., 0.])
