import numpy as np
import pytest

from wfrl_blender.nacelle_visibility import (
    CameraFrustum, clip_surface, evaluate_visibility, first_hit_matches_target,
    sample_span_bands,
)


def camera(**kwargs):
    return CameraFrustum(np.zeros(3), np.array((1, 0, 0)), np.array((0, 1, 0)),
                         np.array((0, 0, 1)), 90, 90, **kwargs)


def strip(lower=0., upper=2., depth=5.):
    vertices = np.array(((-.5, lower - 1, depth), (.5, lower - 1, depth),
                         (.5, upper - 1, depth), (-.5, upper - 1, depth)))
    return vertices, np.array(((0, 1, 2), (0, 2, 3))), np.array((lower, lower, upper, upper))


def test_triangle_covers_frame_with_every_source_vertex_outside():
    vertices = np.array(((-10, -10, 2), (10, -10, 2), (0, 10, 2)), dtype=float)
    source_span = vertices[:, 1] + 10
    frustum = camera()
    outside = (abs(vertices[:, :2]) > vertices[:, 2, None]).any(axis=1)
    assert outside.all()
    clipped = clip_surface(vertices, np.array(((0, 1, 2),)), source_span, frustum)
    assert len(clipped.polygons) == 1
    polygon = clipped.polygons[0]
    assert not clipped.full_outline_in_frame
    uv = frustum.normalized_image(polygon.camera_vertices)
    assert set(map(tuple, np.round(uv, 7))) == {(-1., -1.), (-1., 1.), (1., -1.), (1., 1.)}
    np.testing.assert_allclose(polygon.spans, polygon.world_vertices[:, 1] + 10)
    bands = sample_span_bands(clipped, (8, 9, 10, 11, 12))
    assert all(band.coverage_fraction == pytest.approx(1) for band in bands)
    assert all(band.samples for band in bands)


def test_near_plane_clipping_preserves_span_and_rejects_behind_camera():
    vertices = np.array(((-.2, -.2, .05), (.2, -.2, 1), (0, .2, 1)))
    clipped = clip_surface(vertices, np.array(((0, 1, 2),)), vertices[:, 2], camera(near=.2))
    polygon = clipped.polygons[0]
    assert polygon.camera_vertices[:, 2].min() == pytest.approx(.2)
    np.testing.assert_allclose(polygon.spans, polygon.camera_vertices[:, 2])
    assert not clipped.full_outline_in_frame
    behind = vertices.copy()
    behind[:, 2] = -.1
    assert not clip_surface(behind, np.array(((0, 1, 2),)), vertices[:, 2], camera()).polygons


def test_far_plane_and_independent_side_planes_clip_surface_interiors():
    frustum = CameraFrustum((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
                            80, 30, near=.2, far=4)
    vertices = np.array(((-8, -8, 1), (8, -8, 1), (0, 8, 7)), dtype=float)
    clipped = clip_surface(vertices, np.array(((0, 1, 2),)), vertices[:, 2], frustum)
    assert clipped.polygons
    for polygon in clipped.polygons:
        distances = polygon.camera_vertices @ frustum.planes[:, :3].T + frustum.planes[:, 3]
        assert distances.min() >= -1e-9
        assert polygon.camera_vertices[:, 2].max() == pytest.approx(4)
        np.testing.assert_allclose(polygon.spans, polygon.camera_vertices[:, 2])
        uv = frustum.normalized_image(polygon.camera_vertices)
        assert abs(uv).max() <= 1 + 1e-9
    assert not clipped.full_outline_in_frame


def test_analytic_coverage_is_continuous_and_samples_are_bounded():
    clipped = clip_surface(*strip(), camera())
    assert clipped.full_outline_in_frame
    assert clipped.lateral_span_intervals == ((0., 2.),)
    bands = sample_span_bands(clipped, np.linspace(0, 2, 41))
    assert all(band.coverage_fraction == pytest.approx(1) for band in bands)
    assert all(1 <= len(band.samples) <= 9 for band in bands)
    for band in bands:
        assert all(band.lower < sample.span < band.upper for sample in band.samples)
        assert all(sample.point[2] == pytest.approx(5) for sample in band.samples)
    visible = evaluate_visibility(bands, lambda sample: True)
    assert visible.all_bands_partly_in_frustum
    assert visible.all_span_in_frustum
    assert visible.all_bands_sampled_visible


def test_partial_band_coverage_does_not_claim_full_span_continuity():
    v1, f1, s1 = strip(0, .9)
    v2, f2, s2 = strip(1.1, 2)
    clipped = clip_surface(np.vstack((v1, v2)), np.vstack((f1, f2 + len(v1))),
                           np.hstack((s1, s2)), camera())
    assert clipped.lateral_span_intervals == ((0., .9), (1.1, 2.))
    bands = sample_span_bands(clipped, (0, 1, 2))
    assert [band.coverage_fraction for band in bands] == pytest.approx((.9, .9))
    report = evaluate_visibility(bands, lambda sample: True)
    assert report.all_bands_partly_in_frustum
    assert not report.all_span_in_frustum
    assert report.all_bands_sampled_visible


def test_first_hit_occluder_and_no_hit_remain_distinct_from_frustum_coverage():
    bands = sample_span_bands(clip_surface(*strip(), camera()), (0, 1, 2))

    def ray_cast(sample):
        # A real nearer surface covering the lower half of this blade.
        hit = sample.point * .5 if sample.point[1] < 0 else sample.point
        return first_hit_matches_target(sample, hit)

    report = evaluate_visibility(bands, ray_cast)
    assert report.all_span_in_frustum
    assert report.bands[0].visible_count == 0
    assert report.bands[1].visible_count == report.bands[1].sample_count
    assert not report.all_bands_sampled_visible
    assert not first_hit_matches_target(bands[0].samples[0], None)


def test_constant_span_root_cap_is_not_a_lateral_strip():
    vertices = np.array(((-1, -1, 2), (1, -1, 2), (0, 1, 2)))
    clipped = clip_surface(vertices, np.array(((0, 1, 2),)), np.zeros(3), camera())
    assert clipped.polygons
    assert not clipped.lateral_span_intervals
    band = sample_span_bands(clipped, (0, 1))[0]
    assert band.coverage_fraction == 0
    assert not band.samples


def test_camera_world_basis_and_asymmetric_fov():
    rotation = np.array(((0, -1, 0), (0, 0, 1), (-1, 0, 0)))
    offset = np.array((3, -7, 11))
    frustum = CameraFrustum(offset, rotation[:, 0], rotation[:, 1], rotation[:, 2], 60, 40)
    local = np.array(((-.2, -.1, 2), (.2, -.1, 2), (0, .1, 2)))
    world = local @ rotation.T + offset
    clipped = clip_surface(world, np.array(((0, 1, 2),)), (0, 1, 2), frustum)
    assert clipped.full_outline_in_frame
    np.testing.assert_allclose(clipped.polygons[0].camera_vertices, local, atol=1e-12)


def test_empty_mesh_and_invalid_inputs():
    clipped = clip_surface(np.empty((0, 3)), [], [], camera())
    assert not clipped.polygons
    assert not clipped.full_outline_in_frame
    report = evaluate_visibility(sample_span_bands(clipped, (0, 1)), lambda sample: True)
    assert not report.all_bands_partly_in_frustum
    assert not report.all_bands_sampled_visible
    with pytest.raises(ValueError, match="orthonormal"):
        CameraFrustum(np.zeros(3), np.array((2, 0, 0)), np.array((0, 1, 0)),
                      np.array((0, 0, 1)), 59, 40)
    with pytest.raises(ValueError, match="integer"):
        clip_surface(np.zeros((3, 3)), np.array(((0., 1., 2.),)), (0, 1, 2), camera())
    with pytest.raises(ValueError, match="strictly increasing"):
        sample_span_bands(clipped, (0, 0, 1))
