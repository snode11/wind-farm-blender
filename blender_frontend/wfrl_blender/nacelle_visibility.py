"""Surface and finite-ray visibility for a rectangular perspective camera.

Coordinates and span values are in metres. Camera depth is positive along
``forward``; ``right`` and ``up`` are independent orthonormal world axes.
Triangles are clipped against all six frustum planes before span coverage is
measured. First-hit samples describe sampled visibility, not image quality or
proof that an entire visible surface band is connected.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Iterable

import numpy as np


def _array(value, shape, name):
    result = np.asarray(value, dtype=float)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError(f"{name} must have finite shape {shape}")
    result = result.copy()
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class CameraFrustum:
    eye: np.ndarray
    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray
    hfov: float
    vfov: float
    near: float = .005
    far: float = 30000.

    def __post_init__(self):
        for name in ("eye", "right", "up", "forward"):
            object.__setattr__(self, name, _array(getattr(self, name), (3,), name))
        basis = np.column_stack((self.right, self.up, self.forward))
        if not np.allclose(basis.T @ basis, np.eye(3), atol=1e-7, rtol=0):
            raise ValueError("camera axes must be orthonormal unit vectors")
        values = (self.hfov, self.vfov, self.near, self.far)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("camera parameters must be finite")
        if not (0 < self.hfov < 180 and 0 < self.vfov < 180):
            raise ValueError("FOV values must be full angles between 0 and 180 degrees")
        if not 0 < self.near < self.far:
            raise ValueError("clip distances must satisfy 0 < near < far")

    @property
    def tangents(self):
        return np.tan(np.radians((self.hfov, self.vfov)) / 2)

    @property
    def planes(self):
        """Unit plane normals and offsets; inside has nonnegative distance."""
        h, v = self.tangents
        planes = np.array(((1, 0, h, 0), (-1, 0, h, 0),
                           (0, 1, v, 0), (0, -1, v, 0),
                           (0, 0, 1, -self.near), (0, 0, -1, self.far)), dtype=float)
        return planes / np.linalg.norm(planes[:, :3], axis=1)[:, None]

    def camera_coordinates(self, points):
        return (np.asarray(points, dtype=float) - self.eye) @ np.column_stack(
            (self.right, self.up, self.forward))

    def normalized_image(self, camera_points):
        """Return perspective coordinates whose frame edges are -1 and +1."""
        points = np.asarray(camera_points, dtype=float)
        return points[..., :2] / (points[..., 2, None] * self.tangents)


@dataclass(frozen=True)
class ClippedPolygon:
    source_face: int
    world_vertices: np.ndarray
    camera_vertices: np.ndarray
    spans: np.ndarray

    @property
    def span_range(self):
        return float(self.spans.min()), float(self.spans.max())


@dataclass(frozen=True)
class SurfaceClip:
    camera: CameraFrustum
    polygons: tuple[ClippedPolygon, ...]
    source_face_count: int
    full_outline_in_frame: bool
    tolerance: float

    @property
    def lateral_span_intervals(self):
        """Analytic scalar coverage by nonconstant-span surface polygons."""
        return _union_intervals((polygon.span_range for polygon in self.polygons),
                                self.tolerance)


@dataclass(frozen=True)
class RaySample:
    point: np.ndarray
    camera_point: np.ndarray
    source_face: int
    span: float
    band_index: int


@dataclass(frozen=True)
class SpanBand:
    index: int
    lower: float
    upper: float
    coverage_intervals: tuple[tuple[float, float], ...]
    coverage_fraction: float
    samples: tuple[RaySample, ...]

    @property
    def partly_in_frustum(self):
        return self.coverage_fraction > 0


@dataclass(frozen=True)
class BandVisibility:
    band: SpanBand
    visible: tuple[bool, ...]

    @property
    def visible_count(self):
        return sum(self.visible)

    @property
    def sample_count(self):
        return len(self.visible)

    @property
    def visible_fraction(self):
        return self.visible_count / self.sample_count if self.sample_count else None


@dataclass(frozen=True)
class VisibilityReport:
    bands: tuple[BandVisibility, ...]

    @property
    def all_bands_partly_in_frustum(self):
        return bool(self.bands) and all(item.band.partly_in_frustum for item in self.bands)

    @property
    def all_span_in_frustum(self):
        """Every span value is represented by some clipped lateral surface."""
        return bool(self.bands) and all(item.band.coverage_fraction >= 1 - 1e-9
                                        for item in self.bands)

    @property
    def all_bands_sampled_visible(self):
        """At least one first-hit sample per band; finite sampling only."""
        return bool(self.bands) and all(item.visible_count > 0 for item in self.bands)


def _union_intervals(intervals: Iterable[tuple[float, float]], tolerance):
    ordered = sorted((float(a), float(b)) for a, b in intervals if b - a > tolerance)
    merged = []
    for lower, upper in ordered:
        if merged and lower <= merged[-1][1] + tolerance:
            merged[-1] = (merged[-1][0], max(merged[-1][1], upper))
        else:
            merged.append((lower, upper))
    return tuple(merged)


def _deduplicate_polygon(vertices, tolerance):
    cleaned = []
    for vertex in vertices:
        if not cleaned or np.linalg.norm(vertex[:3] - cleaned[-1][:3]) > tolerance:
            cleaned.append(vertex)
    if len(cleaned) > 1 and np.linalg.norm(cleaned[0][:3] - cleaned[-1][:3]) <= tolerance:
        cleaned.pop()
    return np.asarray(cleaned)


def _clip_polygon(polygon, plane, tolerance):
    distances = polygon[:, 3:6] @ plane[:3] + plane[3]
    result = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        current_distance, previous_distance = distances[index], distances[index - 1]
        current_inside = current_distance >= -tolerance
        previous_inside = previous_distance >= -tolerance
        if current_inside != previous_inside:
            t = np.clip(previous_distance / (previous_distance - current_distance), 0, 1)
            result.append(previous + t * (current - previous))
        if current_inside:
            result.append(current)
    return _deduplicate_polygon(result, tolerance)


def _polygon_area(world_vertices):
    vectors = world_vertices[1:] - world_vertices[0]
    return .5 * np.linalg.norm(np.cross(vectors[:-1], vectors[1:]), axis=1).sum()


def clip_surface(vertices, faces, vertex_span, camera: CameraFrustum, *, tolerance=1e-9):
    """Clip world triangles and interpolate source span at every cut edge.

    ``faces`` is an integer (N, 3) array. Referenced source vertices determine
    ``full_outline_in_frame`` independently of partial surface coverage. Root
    caps with constant span are retained as polygons, but do not contribute
    lateral span intervals or ray targets.
    """
    vertices = np.asarray(vertices, dtype=float)
    spans = np.asarray(vertex_span, dtype=float)
    faces = np.asarray(faces)
    if faces.size == 0:
        faces = np.empty((0, 3), dtype=int)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ValueError("vertices must be a finite (N, 3) array")
    if spans.shape != (len(vertices),) or not np.isfinite(spans).all():
        raise ValueError("vertex_span must contain one finite scalar per vertex")
    if faces.ndim != 2 or faces.shape[1] != 3 or not np.issubdtype(faces.dtype, np.integer):
        raise ValueError("faces must be an integer (N, 3) array")
    if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
        raise ValueError("face index is outside the vertex array")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance must be finite and positive")
    camera_vertices = camera.camera_coordinates(vertices)
    planes = camera.planes
    distances = camera_vertices @ planes[:, :3].T + planes[:, 3]
    triangle_distances = distances[faces]
    rejected = (triangle_distances < -tolerance).all(axis=1).any(axis=1)
    contained = (triangle_distances >= -tolerance).all(axis=(1, 2))
    attributes = np.column_stack((vertices, camera_vertices, spans))
    polygons = []
    for index in np.flatnonzero(~rejected):
        polygon = attributes[faces[index]]
        if not contained[index]:
            for plane in planes:
                polygon = _clip_polygon(polygon, plane, tolerance)
                if len(polygon) < 3:
                    break
        if len(polygon) < 3 or _polygon_area(polygon[:, :3]) <= tolerance ** 2:
            continue
        polygons.append(ClippedPolygon(int(index), polygon[:, :3].copy(),
                                       polygon[:, 3:6].copy(), polygon[:, 6].copy()))
    full_outline = bool(faces.size) and bool((distances[np.unique(faces)] >= -tolerance).all())
    return SurfaceClip(camera, tuple(polygons), len(faces), full_outline, tolerance)


def _span_segment(polygon, span, tolerance):
    points = []
    attributes = np.column_stack((polygon.world_vertices, polygon.camera_vertices, polygon.spans))
    for index, a in enumerate(attributes):
        b = attributes[index - 1]
        if abs(a[6] - span) <= tolerance:
            points.append(a)
        if (a[6] < span < b[6]) or (b[6] < span < a[6]):
            points.append(a + (span - a[6]) / (b[6] - a[6]) * (b - a))
    if len(points) < 2:
        return None
    points = np.asarray(points)
    distances = np.linalg.norm(points[:, None, :3] - points[None, :, :3], axis=2)
    first, second = np.unravel_index(np.argmax(distances), distances.shape)
    if distances[first, second] <= tolerance:
        return None
    return points[first], points[second]


def _covered_span_levels(intervals, count):
    lengths = np.array([upper - lower for lower, upper in intervals])
    cumulative = np.cumsum(lengths)
    levels = (np.arange(count) + .5) / count * cumulative[-1]
    result = []
    for level in levels:
        index = min(int(np.searchsorted(cumulative, level)), len(intervals) - 1)
        before = cumulative[index - 1] if index else 0.
        result.append(intervals[index][0] + level - before)
    return result


def _distributed_samples(candidates, camera, count):
    if not candidates:
        return ()
    candidates.sort(key=lambda sample: sample.camera_point[2])
    unique = {}
    for candidate in candidates:
        uv = camera.normalized_image(candidate.camera_point)
        unique.setdefault(tuple(np.round(uv, 10)), candidate)
    candidates = list(unique.values())
    uv = camera.normalized_image(np.array([sample.camera_point for sample in candidates]))
    chosen = [0]
    distances = np.sum((uv - uv[0]) ** 2, axis=1)
    while len(chosen) < min(count, len(candidates)):
        index = int(np.argmax(distances))
        if distances[index] <= 1e-20:
            break
        chosen.append(index)
        distances = np.minimum(distances, np.sum((uv - uv[index]) ** 2, axis=1))
    return tuple(candidates[index] for index in chosen)


def sample_span_bands(clipped: SurfaceClip, span_edges, *, samples_across=3, samples_along=3):
    """Return analytic band coverage and bounded, distributed surface targets.

    Each band has at most ``samples_across * samples_along`` targets. Span
    levels cover the union of in-frustum intervals. At each level candidates
    come from all clipped lateral polygons; image-plane distribution starts
    at the nearest surface and retains distinct target rays. This finite
    sampling can miss a narrow visible or occluded patch.
    """
    edges = np.asarray(span_edges, dtype=float)
    if edges.ndim != 1 or len(edges) < 2 or not np.isfinite(edges).all() or not (np.diff(edges) > 0).all():
        raise ValueError("span_edges must be finite and strictly increasing")
    for count in (samples_across, samples_along):
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 1:
            raise ValueError("sample counts must be positive integers")
    tolerance = clipped.tolerance
    lateral = [(polygon, *polygon.span_range) for polygon in clipped.polygons
               if polygon.span_range[1] - polygon.span_range[0] > tolerance]
    intervals = clipped.lateral_span_intervals
    bands = []
    for band_index, (lower, upper) in enumerate(zip(edges[:-1], edges[1:])):
        covered = _union_intervals(((max(lower, a), min(upper, b)) for a, b in intervals), tolerance)
        fraction = min(1., sum(b - a for a, b in covered) / (upper - lower))
        targets = []
        if covered:
            polygons = [(polygon, a, b) for polygon, a, b in lateral if a < upper and b > lower]
            for span in _covered_span_levels(covered, samples_along):
                candidates = []
                for polygon, a, b in polygons:
                    if span < a - tolerance or span > b + tolerance:
                        continue
                    segment = _span_segment(polygon, span, tolerance)
                    if segment is None:
                        continue
                    start, end = segment
                    for t in np.linspace(.1, .9, max(3, 2 * samples_across)):
                        point = start + t * (end - start)
                        candidates.append(RaySample(point[:3], point[3:6], polygon.source_face,
                                                    float(span), band_index))
                targets.extend(_distributed_samples(candidates, clipped.camera, samples_across))
        bands.append(SpanBand(band_index, float(lower), float(upper), covered,
                              float(fraction), tuple(targets)))
    return tuple(bands)


def first_hit_matches_target(sample: RaySample, hit_point, *, absolute_tolerance=1e-4,
                             relative_tolerance=1e-6):
    """Compare a scene ray's first hit with its sampled blade surface target.

    ``None`` means no hit. Tolerance scales with optical target depth and does
    not permit a nearer root cap, nacelle, tower or other blade to count as the
    target. The caller must cast from the camera eye toward ``sample.point``.
    """
    if hit_point is None:
        return False
    if min(absolute_tolerance, relative_tolerance) < 0 or not all(
            math.isfinite(value) for value in (absolute_tolerance, relative_tolerance)):
        raise ValueError("hit tolerances must be finite and nonnegative")
    point = _array(hit_point, (3,), "hit_point")
    tolerance = absolute_tolerance + relative_tolerance * sample.camera_point[2]
    return bool(np.linalg.norm(point - sample.point) <= tolerance)


def evaluate_visibility(bands, first_hit_visible: Callable[[RaySample], bool]):
    """Evaluate bounded targets with the caller's real scene first-hit test."""
    return VisibilityReport(tuple(BandVisibility(band, tuple(bool(first_hit_visible(sample))
                                  for sample in band.samples)) for band in bands))
