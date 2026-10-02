"""Pure, conservative summaries for sampled fixed-camera visibility evidence."""
from __future__ import annotations


def surface_cross_section_samples(triangles, vertex_spans, edges, max_per_bin=12):
    """Fixed barycentric samples on real triangles, including mesh-ring edges."""
    import numpy as np
    vertex_spans = np.asarray(vertex_spans)
    selected, bins, spans, weights = [], [], [], []
    for band, (low, high) in enumerate(zip(edges, edges[1:])):
        span = (low + high) / 2
        ids = np.flatnonzero((vertex_spans.min(axis=1) <= span) & (vertex_spans.max(axis=1) >= span)
                             & (np.ptp(vertex_spans, axis=1) > 1e-9))
        if not len(ids):
            continue
        for index in ids[np.unique(np.linspace(0, len(ids) - 1, min(max_per_bin, len(ids)), dtype=int))]:
            intersections = []
            values = vertex_spans[index]
            for first, second in ((0, 1), (1, 2), (2, 0)):
                a, b = values[first], values[second]
                if abs(b-a) > 1e-9 and min(a, b) <= span <= max(a, b):
                    t = (span - a) / (b - a)
                    weight = np.zeros(3)
                    weight[first], weight[second] = 1-t, t
                    intersections.append(weight)
            if len(intersections) >= 2:
                selected.append(index)
                bins.append(band)
                spans.append(span)
                weights.append(np.mean(intersections, axis=0))
    return np.asarray(triangles)[selected], np.asarray(bins), np.asarray(spans), np.asarray(weights)


def intervals_from_bins(visible_bins, edges):
    """Return separate supported span intervals; never bridge a missing bin."""
    visible = sorted(set(int(i) for i in visible_bins))
    if any(i < 0 or i >= len(edges) - 1 for i in visible):
        raise ValueError("visibility bin outside span grid")
    result = []
    for i in visible:
        if result and abs(result[-1][1] - edges[i]) < 1e-8:
            result[-1][1] = float(edges[i + 1])
        else:
            result.append([float(edges[i]), float(edges[i + 1])])
    return result


def length(intervals):
    return sum(b - a for a, b in intervals)


def visibility_summary(camera_faces, face_bins, edges):
    """Overlap requires the identical sampled surface triangle in both cameras.

    Intervals only assert that a sample in each bin is visible, not that an
    entire chord/surface is visible. Root/tip bins may be denser than midspan.
    """
    faces = [set(items) for items in camera_faces]
    bins = [{int(face_bins[i]) for i in items} for items in faces]
    union = set.union(*bins)
    intervals = intervals_from_bins(union, edges)
    gaps = intervals_from_bins(set(range(len(edges) - 1)) - union, edges)
    overlaps = []
    for first, second in zip(faces, faces[1:]):
        shared = first & second
        spans = intervals_from_bins({int(face_bins[i]) for i in shared}, edges)
        overlaps.append({"surface_sample_count": len(shared), "intervals_m": spans,
                         "length_m": length(spans)})
    return {"intervals_m": intervals, "gap_intervals_m": gaps,
            "visible_length_m": length(intervals),
            "longest_contiguous_sampled_length_m": max((b - a for a, b in intervals), default=0.),
            "adjacent_common_surface": overlaps,
            "three_cameras_see_target": all(faces),
            "camera_intervals_m": [intervals_from_bins(items, edges) for items in bins]}


def candidate_rank(samples):
    """Lexicographic priority: continuity/range, both overlaps, then pixels."""
    if not samples:
        return (0., 0., 0., 0.)
    n = len(samples)
    return tuple(sum(values) / n for values in (
        [s["visibility"]["longest_contiguous_sampled_length_m"] for s in samples],
        [s["visibility"]["visible_length_m"] for s in samples],
        [min(o["length_m"] for o in s["visibility"]["adjacent_common_surface"]) for s in samples],
        [sum(c["target_pixel_fraction"] for c in s["cameras"]) / 3 for s in samples]))


def qualified(samples):
    return any(s["visibility"]["three_cameras_see_target"]
               and all(o["surface_sample_count"] > 0
                       for o in s["visibility"]["adjacent_common_surface"])
               for s in samples)


def transition_midpoints(samples):
    """Densify whole-cycle intervals when visibility or joint overlap changes."""
    def state(row):
        v = row["visibility"]
        return (v["three_cameras_see_target"],
                tuple(bool(o["surface_sample_count"]) for o in v["adjacent_common_surface"]),
                tuple(bool(c["visible_surface_samples"]) for c in row["cameras"]))
    return [(a["requested_turn_deg"] + b["requested_turn_deg"]) / 2
            for a, b in zip(samples, samples[1:]) if state(a) != state(b)]
