"""Synthetic root-band/crop evidence checks; no experimental truth input."""
import numpy as np
import pytest

from wfrl.nrel_reconstruction.root_rgb_audit import clip_span, perspective_points, summarize_native
from wfrl.nrel_reconstruction.surface_audit import image_segment_interval, project


def test_root_band_is_clipped_before_image_domain():
    # A full edge enters the image only beyond z=12.30. The root part cannot
    # inherit that full-edge visibility or be mislabeled a mask/backend loss.
    edge = np.array([[0., -2., 10.], [0., 2., 14.]])
    clipped = clip_span(edge, 6.15, 12.3)
    assert np.allclose(clipped[:, 2], [10., 12.3])
    K = np.array([[100., 0., 20.], [0., 100., -4.], [0., 0., 1.]])
    assert image_segment_interval(project(edge, K), 41, 41) is not None
    assert image_segment_interval(project(clipped, K), 41, 41) is None


def test_span_clip_handles_reversed_edges_and_constant_z():
    edge = np.array([[1., 2., 15.], [3., 4., 5.]])
    result = clip_span(edge)
    assert np.allclose(result[:, 2], [12.3, 6.15])
    assert np.allclose(clip_span(edge[::-1]), result[::-1])
    assert clip_span(np.array([[0., 0., 3.], [1., 1., 3.]])) is None
    assert clip_span(np.array([[0., 0., 9.], [1., 1., 9.]])).shape == (2, 3)
    with pytest.raises(ValueError): clip_span(edge, 10., 9.)


def test_perspective_mapping_lands_on_uniform_image_samples():
    end = np.array([[1., 0., 2.], [4., 1., 8.]])
    fractions = np.linspace(0, 1, 10)
    points = perspective_points(end, end, fractions)
    uv = project(end, np.eye(3))
    assert np.allclose(project(points, np.eye(3)), uv[0]+fractions[:, None]*(uv[1]-uv[0]))
    assert not np.allclose(points, end[0]+fractions[:, None]*(end[1]-end[0]))


def test_generic_U_retains_independent_contour_eligibility():
    rows = [{'projected_line_weight_px': 2., 'ray_visible': True, 'independent_contour_valid': True,
             'reason': 'eligible', 'FBU': 'U', 'old_union_probe_boundary': True},
            {'projected_line_weight_px': 3., 'ray_visible': True, 'independent_contour_valid': False,
             'reason': 'excluded_contour_domain', 'FBU': 'U', 'old_union_probe_boundary': True}]
    result = summarize_native([], rows)
    assert result['native_ray_visible_U_and_independent_valid_samples'] == 1
    assert result['native_ray_visible_eligible_length_px'] == 2.
    assert result['native_ray_visible_excluded_length_px'] == 3.
