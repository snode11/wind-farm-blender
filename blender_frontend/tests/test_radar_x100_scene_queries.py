"""Paired nearest-face refinement and immutable visibility geometry reuse."""
from dataclasses import replace
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from wfrl.radar_x100 import analysis, simulation
from wfrl_blender.radar_x100 import scene as scene_api


def test_paired_refinement_covers_vertices_edges_interior_and_empty_input():
    face = np.array([[0., 0., 0.], [2., 0., 0.], [0., 3., 0.]])
    points = np.array([[-1., -1., 2.], [3., -1., 2.], [-1., 4., 2.],
                       [1., -1., 2.], [-1., 1., 2.], [2., 3., 2.],
                       [.5, .5, 2.], [0., 0., 0.], [2., 0., 0.],
                       [0., 3., 0.], [1., 0., 0.], [0., 1., 0.], [1., 1.5, 0.]])
    faces = np.broadcast_to(face, (len(points), 3, 3))
    expected = analysis.closest_reference(points, face, [[0, 1, 2]])[1]
    actual = scene_api._closest_on_faces(points, faces)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert scene_api._closest_on_faces(points[:0], faces[:0]).shape == (0, 3)


def test_paired_refinement_matches_scalar_for_unrelated_selected_faces():
    rng = np.random.default_rng(100)
    faces = rng.normal(size=(512, 3, 3))
    points = rng.normal(size=(512, 3))*3
    expected = np.array([scene_api._closest_on_face(point, *face) for point, face in zip(points, faces)])
    actual = scene_api._closest_on_faces(points, faces)
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    for i in range(16):
        oracle = analysis.closest_reference(points[i:i+1], faces[i], [[0, 1, 2]])[1]
        np.testing.assert_allclose(actual[i:i+1], oracle, rtol=1e-12, atol=1e-12)


def visibility_sampler(monkeypatch):
    builds, queries = [], []
    def build(vertices, polygons, *, all_triangles):
        assert all_triangles
        tree = object()
        builds.append((tree, vertices, polygons))
        return tree
    monkeypatch.setitem(sys.modules, 'mathutils.bvhtree', SimpleNamespace(
        BVHTree=SimpleNamespace(FromPolygons=build)))
    def visibility(sample, targets, *, tree):
        queries.append((sample, targets, tree))
        return np.ones(len(targets), dtype=bool)
    monkeypatch.setattr(scene_api, 'bvh_visibility', visibility)
    sampler = scene_api.SceneSampler.__new__(scene_api.SceneSampler)
    sampler._visibility_cache = {}
    sample = simulation.SceneSample(1., 'source', 'geometry',
        np.array([[0., 0., 0.], [2., 0., 0.], [0., 3., 0.]]),
        np.array([[0, 1, 2]]), np.eye(4), 'test surface')
    return sampler, sample, builds, queries


def test_visibility_reuses_only_topology_across_times_and_queries_each_mount(monkeypatch):
    sampler, sample, builds, queries = visibility_sampler(monkeypatch)
    targets = np.array([[0., 0., 1.]])
    sampler.visibility(sample, targets)
    sampler.visibility(replace(sample), targets)
    assert len(builds) == 1
    moved = np.eye(4)
    moved[:3, 3] = [1., 2., 3.]
    installed = replace(sample, world_from_sensor=moved)
    sampler.visibility(installed, targets*2)
    assert len(builds) == 1 and len(queries) == 3
    assert queries[-1][0] is installed
    np.testing.assert_array_equal(queries[-1][1], targets*2)
    sampler.visibility(replace(sample, time_s=2.), targets)
    assert len(builds) == 2 and builds[0][2] is builds[1][2]


@pytest.mark.parametrize('change', ['source', 'geometry', 'vertices', 'transform', 'topology'])
def test_visibility_invalidates_geometry_even_with_same_time(monkeypatch, change):
    sampler, sample, builds, _ = visibility_sampler(monkeypatch)
    targets = np.array([[0., 0., 1.]])
    sampler.visibility(sample, targets)
    if change == 'source':
        changed = replace(sample, source_id='other')
    elif change == 'geometry':
        changed = replace(sample, geometry_id='other')
    elif change == 'vertices':
        vertices = sample.vertices.copy()
        vertices[0, 2] = .25
        changed = replace(sample, vertices=vertices)
    elif change == 'transform':
        changed = replace(sample, vertices=sample.vertices+[1., 2., 3.])
    else:
        changed = replace(sample, triangles=sample.triangles[:, ::-1])
    sampler.visibility(changed, targets)
    assert len(builds) == 2 and builds[0][0] is not builds[1][0]
    assert (builds[0][2] is builds[1][2]) == (change != 'topology')
    np.testing.assert_array_equal(builds[-1][1], changed.vertices)
    np.testing.assert_array_equal(builds[-1][2], changed.triangles)


def test_visibility_build_failure_preserves_the_paired_tree_and_polygon_data(monkeypatch):
    sampler, sample, builds, _ = visibility_sampler(monkeypatch)
    targets = np.array([[0., 0., 1.]])
    sampler.visibility(sample, targets)
    before = sampler._visibility_cache.copy()
    factory = sys.modules['mathutils.bvhtree'].BVHTree
    original = factory.FromPolygons
    def fail(*args, **kwargs):
        raise RuntimeError('BVH build unavailable')
    monkeypatch.setattr(factory, 'FromPolygons', fail)
    with pytest.raises(RuntimeError, match='BVH build unavailable'):
        sampler.visibility(replace(sample, triangles=sample.triangles[:, ::-1]), targets)
    assert all(sampler._visibility_cache[key] is value for key, value in before.items())
    monkeypatch.setattr(factory, 'FromPolygons', original)
    sampler.visibility(replace(sample, time_s=2.), targets)
    assert len(builds) == 2 and builds[0][2] is builds[1][2]
    np.testing.assert_array_equal(builds[-1][2], sample.triangles)
