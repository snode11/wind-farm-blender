"""Audit mount-search broad phase against the unfiltered world-space observer."""
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.lidar.search_dual_beam_mount import mount_hits
from wfrl.camera_video.data import SourceGeometry
from wfrl.lidar.dual_beam import PairLimits, observe

SOURCE = Path(__file__).resolve().parents[2] / 'blender_frontend/wfrl_blender/assets/mappo'
CANDIDATES = (
    (-3.5, 0., 87.6),
    (-5.2, 0., 87.6),
    (-5.2, -2., 87.6),
    (-5.2, -3., 86.6),
    (-4.6, 2.5, 86.6),
    (-4.6, 3., 87.6),
    (-7.3, -2., 89.6),
    (0., 0., 87.6),
)


def unfiltered_pair(time, candidate, nacelle, blades, blade_triangles,
                    tower, tower_triangles, min_separation=.25):
    """Use full world surfaces and observed first returns, without plane filtering."""
    origin = nacelle[:3, :3] @ np.asarray(candidate) + nacelle[:3, 3]
    observations = []
    for angle in (4., 6.):
        direction = nacelle[:3, :3] @ np.array(
            [-math.sin(math.radians(angle)), 0., -math.cos(math.radians(angle))])
        direction /= np.linalg.norm(direction)
        observations.append(observe(time, origin, direction, blades, blade_triangles,
                                    tower, tower_triangles, PairLimits()))
    a, b = observations
    if not (a['valid'] and b['valid'] and a['blade_id'] == b['blade_id']):
        return 0, None
    separation = float(np.linalg.norm(np.asarray(a['point_m']) - b['point_m']))
    return (a['blade_id'], separation) if separation >= min_separation else (0, None)


def assert_same_pair(actual, expected, context):
    assert actual[0] == expected[0], context
    if expected[0]:
        # Saved rotations are float32 and near orthogonal, so the search's
        # transpose-based inverse and normalized world rays differ by roundoff.
        assert actual[1] == pytest.approx(expected[1], abs=2e-4), context
    else:
        assert actual[1] is None, context


@pytest.mark.parametrize('turbine_id', ('T1', 'T2', 'T3'))
def test_source_mount_search_matches_unfiltered_world_observations(turbine_id):
    source = SourceGeometry(SOURCE, turbine_id)
    indices = {0, len(source.times) // 2, len(source.times) - 1}
    # Exercise each blade near entry, center and exit of a measurement passage,
    # in addition to source endpoints and a fixed interior timestamp.
    for blade in range(3):
        phase = (source.poses[:, 1] + blade * 120) % 360 - 180
        for target in (-12., 0., 12.):
            indices.add(int(np.argmin(abs(phase - target))))
    for index in sorted(indices):
        time = float(source.times[index])
        world = source.sample(time, include_blades=True)
        actual = mount_hits(source, index, CANDIDATES)
        for candidate, result in zip(CANDIDATES, actual):
            expected = unfiltered_pair(
                time, candidate, world['nacelle_world_transform'],
                world['blade_points_world_m'], source.blade_triangles,
                world['tower_points_world_m'], source.tower_triangles)
            assert_same_pair(result, expected, (turbine_id, time, candidate))


def synthetic_source(near_root):
    def plane(height, offset=0):
        return np.array([[offset - 30, -30, height],
                         [offset + 30, -30, height],
                         [offset, 30, height]], float)

    identity = np.column_stack((np.eye(3), np.zeros(3)))
    blades = np.array([plane(50), plane(88, 0 if near_root else 100), plane(40, 100)])
    tower = plane(10, 100)
    source = SimpleNamespace(
        nacelles=identity[None], transforms=np.tile(identity, (1, 3, 1, 1, 1)),
        blade_reference=blades[:, None], blade_triangles=np.array([[0, 1, 2]]),
        tower_transforms=identity[None, None], tower_reference=tower,
        tower_triangles=np.array([[0, 1, 2]]), tower_station=np.zeros(3, dtype=int))
    return source, blades, tower


@pytest.mark.parametrize('near_root', (False, True))
def test_search_preserves_nearest_out_of_range_occlusion(near_root):
    source, blades, tower = synthetic_source(near_root)
    candidates = [(0., 0., 90.), (0., -3., 90.), (0., 3., 90.)]
    actual = mount_hits(source, 0, candidates)
    nacelle = np.eye(4)
    for candidate, result in zip(candidates, actual):
        expected = unfiltered_pair(0., candidate, nacelle, blades, source.blade_triangles,
                                   tower, source.tower_triangles)
        assert expected[0] == (0 if near_root else 1)
        assert_same_pair(result, expected, (near_root, candidate))
