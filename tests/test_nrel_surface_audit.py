"""Synthetic-only checks for the frozen RGB audit; no truth artifacts."""
import numpy as np
from scipy.spatial import cKDTree

from wfrl.nrel_reconstruction.surface_audit import (
    STATES, contour_associations, edge_adjacency, first_blockers, image_segment_interval,
    inspect_points, pixel_samples, quadrature, section_samples,
)


def test_exact_ray_rejects_back_surface_even_if_projection_is_foreground():
    # Both triangles project into the same foreground; the near one hides the far.
    vertices = np.array([[-2., -2., 2.], [2., -2., 2.], [0., 2., 2.],
                         [-2., -2., 4.], [2., -2., 4.], [0., 2., 4.]])
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    points = np.array([[0., 0., 2.], [0., 0., 4.], [10., 0., 4.], [0., 0., -2.]])
    obs = {'K': [[5, 0, 5], [0, 5, 5], [0, 0, 1]],
           'T_camera_cv_from_world': np.eye(4), 'T_world_from_blade_root': np.eye(4)}
    info = inspect_points(points, vertices, faces, obs, np.ones((11, 11), dtype=int),
                          np.ones((11, 11), dtype=bool), None)
    assert [STATES[i] for i in info['state']] == ['visible_F', 'self_occluded', 'outside_image', 'behind_camera']
    assert info['pixel'][1] == 1
    assert info['blockers'][1] == 0


def test_rays_are_double_sided_and_ignore_endpoint_intersections():
    triangle = np.array([[[-2., -2., 3.], [2., -2., 3.], [0., 2., 3.]]])
    points = np.array([[0., 0., 3.], [0., 0., 4.], [10., 0., 4.]])
    expected = [-1, 0, -1]
    assert first_blockers(points, triangle).tolist() == expected
    assert first_blockers(points, triangle[:, ::-1]).tolist() == expected


def test_area_quadrature_preserves_triangle_area_and_centroid():
    vertices = np.array([[0., 0., 0.], [2., 0., 0.], [0., 3., 0.]])
    points, weights, ids = quadrature(vertices, np.array([[0, 1, 2]]))
    assert np.isclose(weights.sum(), 3.)
    assert np.allclose(np.average(points, axis=0, weights=weights), vertices.mean(axis=0))
    assert ids.tolist() == [0, 0, 0]


def test_section_line_quadrature_on_cube_has_correct_perimeter():
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [1., 1., 0.], [0., 1., 0.],
                         [0., 0., 1.], [1., 0., 1.], [1., 1., 1.], [0., 1., 1.]])
    faces = np.array([[0, 1, 4], [1, 5, 4], [1, 2, 5], [2, 6, 5],
                      [2, 3, 6], [3, 7, 6], [3, 0, 7], [0, 4, 7]])
    points, weights, face_ids = section_samples(vertices, faces, .5)
    assert np.isclose(weights.sum(), 4.)
    assert np.allclose(points[:, 2], .5)
    assert len(face_ids) == 24


def test_section_exact_mesh_ring_does_not_duplicate_segments():
    # Two adjacent squares sharing a horizontal edge. At z=1 only +z triangle remains.
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 0., 1.], [1., 0., 1.],
                         [0., 0., 2.], [1., 0., 2.]])
    faces = np.array([[0, 1, 2], [1, 3, 2], [2, 3, 4], [3, 5, 4]])
    points, weights, face_ids = section_samples(vertices, faces, 1.)
    assert np.isclose(weights.sum(), 1.)
    assert len(points) == 3 and set(face_ids) == {2}


def test_pixel_centres_use_half_up_rounding_and_image_domain_clip():
    labels = np.array([[0, 1, 2]])
    inside, pixel = pixel_samples(np.array([[.5, 0.], [1.5, 0.], [-.1, 0.], [2.1, 0.]]), labels)
    assert pixel.tolist() == [1, 2, -1, -1]
    assert inside.tolist() == [True, True, False, False]
    assert image_segment_interval(np.array([[-1., 1.], [3., 1.]]), 3, 3) == (.25, .75)
    assert image_segment_interval(np.array([[-2., 1.], [-1., 1.]]), 3, 3) is None


def test_contour_eligibility_is_distinct_from_deadband_and_excludes_hidden_cube():
    outer = np.array([[-2., -2., 3.], [2., -2., 3.], [2., 2., 3.], [-2., 2., 3.],
                      [-2., -2., 8.], [2., -2., 8.], [2., 2., 8.], [-2., 2., 8.]])
    faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                      [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    inner = outer.copy()
    inner[:, :2] *= .2
    inner[:, 2] = 4+(inner[:, 2]-3)*.2
    vertices = np.concatenate([outer, inner])
    both_faces = np.concatenate([faces, faces+8])
    obs = {'K': [[25, 0, 31.5], [0, 25, 31.5], [0, 0, 1]],
           'T_camera_cv_from_world': np.eye(4), 'T_world_from_blade_root': np.eye(4),
           'contour_uncertainty_px': 3.}
    labels, valid = np.ones((64, 64), dtype=int), np.ones((64, 64), dtype=bool)
    # Distant observed point: still eligible, but every valid edge is outside deadband.
    rows, eligible, matched = contour_associations(vertices, both_faces, obs, labels, valid,
                                                  cKDTree([[1., 1.]]), edge_adjacency(both_faces))
    assert eligible[:12].any() and not eligible[12:].any()
    assert not matched.any()
    assert any(row.get('active_residual') == 1 for row in rows)
    assert any(row['reason'] == 'self_occluded' for row in rows)
    assert sum(row['eligible'] for row in rows) > 0
    invalid = np.zeros_like(valid)
    _, no_eligible, _ = contour_associations(vertices, both_faces, obs, labels, invalid,
                                            cKDTree([[1., 1.]]), edge_adjacency(both_faces))
    assert not no_eligible.any()
