"""Tests cover evidence overclaim risks, not Blender rendering."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("framing_metrics", Path(__file__).resolve().parents[2] / "scripts/blender/framing_metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def test_holes_stay_holes_and_overlap_requires_same_surface():
    result = metrics.visibility_summary([{0, 2}, {1, 3}, {3}], [0, 0, 2, 3], [0, 1, 2, 3, 4])
    assert result["intervals_m"] == [[0., 1.], [2., 4.]]
    assert result["gap_intervals_m"] == [[1., 2.]]
    assert result["longest_contiguous_sampled_length_m"] == 2.
    assert result["adjacent_common_surface"][0]["length_m"] == 0.
    assert result["adjacent_common_surface"][1]["length_m"] == 1.


def test_empty_routes_cannot_qualify():
    row = {"visibility": metrics.visibility_summary([set(), set(), set()], [0], [0, 1]), "cameras": []}
    assert row["visibility"]["visible_length_m"] == 0.
    assert row["visibility"]["gap_intervals_m"] == [[0., 1.]]
    assert not metrics.qualified([row])


def test_pixel_area_cannot_overrule_continuity():
    def row(faces, pixel):
        return {"visibility": metrics.visibility_summary(faces, [0, 1, 2], [0, 1, 2, 3]),
                "cameras": [{"target_pixel_fraction": pixel}] * 3}
    assert metrics.candidate_rank([row([{0, 1, 2}] * 3, .01)]) > metrics.candidate_rank([row([{0}] * 3, .9)])


def test_cross_section_on_mesh_ring_is_not_a_false_visibility_gap():
    import numpy as np
    triangles = np.asarray([[0, 1, 2], [1, 2, 3]])
    spans = np.asarray([[0., 0., 1.], [0., 1., 1.]])
    selected, bins, sample_spans, weights = metrics.surface_cross_section_samples(triangles, spans, [.5, 1.5])
    assert len(selected) == 2
    assert set(bins) == {0}
    assert np.allclose(sample_spans, 1.)
    assert np.allclose(weights.sum(axis=1), 1.)
    assert np.allclose((weights * spans).sum(axis=1), 1.)
