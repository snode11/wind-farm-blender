"""Independent, metre-scale evaluation of an NREL single-blade reconstruction.

This is deliberately the only reconstruction module that accepts evaluation truth.
It never performs registration, changes model scale, or chooses fitting frames.
Distances are to triangle surfaces (not nearest vertices).  The reproducible area
samples are a numerical estimate of source-area-weighted surface statistics.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class Mesh:
    vertices: np.ndarray
    faces: np.ndarray

    def __post_init__(self) -> None:
        vertices = np.asarray(self.vertices, dtype=np.float64).reshape((-1, 3))
        faces = np.asarray(self.faces, dtype=np.int64).reshape((-1, 3))
        if not np.isfinite(vertices).all():
            raise ValueError("Mesh vertices must be finite")
        if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
            raise ValueError("Mesh face index outside vertex array")
        object.__setattr__(self, "vertices", vertices)
        object.__setattr__(self, "faces", faces)

    @property
    def triangles(self) -> np.ndarray:
        return self.vertices[self.faces]


_PLY_TYPES = {
    "char": ("b", int), "int8": ("b", int), "uchar": ("B", int),
    "uint8": ("B", int), "short": ("h", int), "int16": ("h", int),
    "ushort": ("H", int), "uint16": ("H", int), "int": ("i", int),
    "int32": ("i", int), "uint": ("I", int), "uint32": ("I", int),
    "float": ("f", float), "float32": ("f", float), "double": ("d", float),
    "float64": ("d", float),
}


def read_ply(path: str | Path) -> Mesh:
    """Read ASCII or binary PLY; polygon faces are fan-triangulated.

    Extra scalar/list properties and elements are read and ignored.  This keeps
    Blender exports with normals/colours usable without a geometry library.
    """
    vertices, faces = [], []
    with Path(path).open("rb") as handle:
        if handle.readline().strip() != b"ply":
            raise ValueError("Not a PLY file")
        format_name = None
        elements: list[dict[str, Any]] = []
        while True:
            raw = handle.readline()
            if not raw:
                raise ValueError("Truncated PLY header")
            fields = raw.decode("ascii").strip().split()
            if not fields or fields[0] in {"comment", "obj_info"}:
                continue
            if fields[0] == "end_header":
                break
            if fields[0] == "format":
                if len(fields) != 3 or fields[2] != "1.0":
                    raise ValueError("Unsupported PLY format version")
                format_name = fields[1]
            elif fields[0] == "element":
                elements.append({"name": fields[1], "count": int(fields[2]), "properties": []})
            elif fields[0] == "property":
                if not elements:
                    raise ValueError("PLY property before element")
                prop = (fields[2], fields[3], fields[4]) if fields[1] == "list" else (fields[1], fields[2])
                for kind in prop[:-1]:
                    if kind not in _PLY_TYPES:
                        raise ValueError(f"Unsupported PLY type: {kind}")
                elements[-1]["properties"].append(prop)
        if format_name not in {"ascii", "binary_little_endian", "binary_big_endian"}:
            raise ValueError("Unsupported or absent PLY format")
        endian = "<" if format_name == "binary_little_endian" else ">"

        def binary_scalar(kind: str) -> int | float:
            code = endian + _PLY_TYPES[kind][0]
            data = handle.read(struct.calcsize(code))
            if len(data) != struct.calcsize(code):
                raise ValueError("Truncated binary PLY data")
            return struct.unpack(code, data)[0]

        for element in elements:
            for _ in range(element["count"]):
                record: dict[str, Any] = {}
                if format_name == "ascii":
                    fields = handle.readline().decode("ascii").split()
                    cursor = 0
                    try:
                        for prop in element["properties"]:
                            if len(prop) == 2:
                                record[prop[1]] = _PLY_TYPES[prop[0]][1](fields[cursor])
                                cursor += 1
                            else:
                                count = int(fields[cursor])
                                cursor += 1
                                if count < 0 or cursor + count > len(fields):
                                    raise ValueError("Invalid PLY list length")
                                record[prop[2]] = [_PLY_TYPES[prop[1]][1](value) for value in fields[cursor:cursor + count]]
                                cursor += count
                    except (IndexError, TypeError) as exc:
                        raise ValueError("Truncated ASCII PLY data") from exc
                else:
                    for prop in element["properties"]:
                        if len(prop) == 2:
                            record[prop[1]] = binary_scalar(prop[0])
                        else:
                            count = int(binary_scalar(prop[0]))
                            if count < 0:
                                raise ValueError("Negative PLY list length")
                            record[prop[2]] = [binary_scalar(prop[1]) for _ in range(count)]
                if element["name"] == "vertex":
                    if not all(axis in record for axis in ("x", "y", "z")):
                        raise ValueError("PLY vertex has no x/y/z")
                    vertices.append([record[axis] for axis in ("x", "y", "z")])
                elif element["name"] == "face":
                    indices = record.get("vertex_indices", record.get("vertex_index"))
                    if indices is None:
                        raise ValueError("PLY face has no vertex indices")
                    for index in range(1, len(indices) - 1):
                        faces.append([indices[0], indices[index], indices[index + 1]])
    return Mesh(np.asarray(vertices), np.asarray(faces))


def write_ply(path: str | Path, vertices: Mesh | np.ndarray, faces: np.ndarray | None = None) -> None:
    """Write a compact ASCII triangle PLY in the caller's declared metre frame."""
    mesh = vertices if isinstance(vertices, Mesh) else Mesh(vertices, np.empty((0, 3), dtype=int) if faces is None else faces)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write(f"ply\nformat ascii 1.0\nelement vertex {len(mesh.vertices)}\n")
        handle.write("property double x\nproperty double y\nproperty double z\n")
        handle.write(f"element face {len(mesh.faces)}\nproperty list uchar int vertex_indices\nend_header\n")
        for point in mesh.vertices:
            handle.write(" ".join(format(float(value), ".17g") for value in point) + "\n")
        for face in mesh.faces:
            handle.write("3 " + " ".join(str(int(value)) for value in face) + "\n")


def triangle_areas(mesh: Mesh) -> np.ndarray:
    triangles = mesh.triangles
    return 0.5 * np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1)


def sample_surface(mesh: Mesh, count: int = 8192, seed: int = 20261002) -> tuple[np.ndarray, np.ndarray]:
    """Uniformly sample source surface area; each returned point has equal area weight."""
    if count <= 0:
        raise ValueError("Surface sample count must be positive")
    areas = triangle_areas(mesh)
    total = float(areas.sum())
    if not np.isfinite(total) or total <= 0:
        return np.empty((0, 3)), np.empty(0, dtype=np.int64)
    rng = np.random.default_rng(seed)
    indices = rng.choice(len(areas), size=count, p=areas / total)
    uv = rng.random((count, 2))
    root = np.sqrt(uv[:, 0])
    weights = np.column_stack((1.0 - root, root * (1.0 - uv[:, 1]), root * uv[:, 1]))
    return np.einsum("ni,nij->nj", weights, mesh.triangles[indices]), indices


def point_to_surface_distances(points: np.ndarray, mesh: Mesh, point_batch: int = 128, triangle_batch: int = 512) -> np.ndarray:
    """Exact nearest Euclidean triangle-surface distances with bounded working arrays.

    Orthogonal plane projections inside each triangle are considered alongside
    all three closest edge points.  Zero-area triangles do not define a surface.
    Runtime is O(points * faces), intentionally avoiding approximate vertex/KD
    distances; the low-dimensional first reconstruction is small enough for it.
    """
    points = np.asarray(points, dtype=np.float64).reshape((-1, 3))
    if not np.isfinite(points).all():
        raise ValueError("Distance points must be finite")
    if point_batch <= 0 or triangle_batch <= 0:
        raise ValueError("Distance batch sizes must be positive")
    triangles = mesh.triangles[triangle_areas(mesh) > 0]
    if not len(triangles):
        return np.full(len(points), np.nan)
    result = np.full(len(points), np.inf)
    for start in range(0, len(points), point_batch):
        p = points[start:start + point_batch, None, :]
        best_sq = np.full(len(p), np.inf)
        for first in range(0, len(triangles), triangle_batch):
            tri = triangles[first:first + triangle_batch]
            a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
            ab, ac = b - a, c - a
            ap = p - a[None, :, :]
            aa = np.einsum("ij,ij->i", ab, ab)
            bb = np.einsum("ij,ij->i", ab, ac)
            cc = np.einsum("ij,ij->i", ac, ac)
            dot_ab = np.einsum("ptj,tj->pt", ap, ab)
            dot_ac = np.einsum("ptj,tj->pt", ap, ac)
            normal = np.cross(ab, ac)
            # Cross-product magnitude avoids cancellation of aa*cc-bb**2 for
            # long, narrow blade triangles.
            normal_sq = np.einsum("ij,ij->i", normal, normal)
            denom = normal_sq
            u = (cc * dot_ab - bb * dot_ac) / denom
            v = (aa * dot_ac - bb * dot_ab) / denom
            plane_sq = np.einsum("ptj,tj->pt", ap, normal) ** 2 / normal_sq
            distances_sq = np.where((u >= 0) & (v >= 0) & (u + v <= 1), plane_sq, np.inf)
            for edge_start, edge_end in ((a, b), (b, c), (c, a)):
                edge = edge_end - edge_start
                edge_sq = np.einsum("ij,ij->i", edge, edge)
                offset = p - edge_start[None, :, :]
                fraction = np.clip(np.einsum("ptj,tj->pt", offset, edge) / edge_sq, 0.0, 1.0)
                delta = offset - fraction[:, :, None] * edge[None, :, :]
                candidate = np.einsum("ptj,ptj->pt", delta, delta)
                distances_sq = np.minimum(distances_sq, candidate)
            best_sq = np.minimum(best_sq, distances_sq.min(axis=1))
        result[start:start + len(p)] = np.sqrt(np.maximum(best_sq, 0.0))
    return result


def _clip_polygon_z(polygon: list[np.ndarray], bound: float, above: bool) -> list[np.ndarray]:
    result = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        cur_inside = current[2] >= bound if above else current[2] <= bound
        prev_inside = previous[2] >= bound if above else previous[2] <= bound
        if cur_inside != prev_inside:
            fraction = (bound - previous[2]) / (current[2] - previous[2])
            result.append(previous + fraction * (current - previous))
        if cur_inside:
            result.append(current)
    return result


def clip_mesh_z(mesh: Mesh, lower: float, upper: float) -> Mesh:
    """Clip source triangles to a fixed span band without creating artificial caps."""
    vertices, faces = [], []
    for triangle in mesh.triangles:
        polygon = _clip_polygon_z(list(triangle), lower, True)
        if polygon:
            polygon = _clip_polygon_z(polygon, upper, False)
        for index in range(1, len(polygon) - 1):
            first = len(vertices)
            vertices.extend([polygon[0], polygon[index], polygon[index + 1]])
            faces.append([first, first + 1, first + 2])
    return Mesh(np.asarray(vertices), np.asarray(faces))


def _distance_stats(source: Mesh, target: Mesh, count: int, seed: int) -> dict[str, Any]:
    source_area = float(triangle_areas(source).sum())
    target_area = float(triangle_areas(target).sum())
    reason = "SOURCE_HAS_NO_NONDEGENERATE_SURFACE" if source_area <= 0 else "TARGET_HAS_NO_NONDEGENERATE_SURFACE" if target_area <= 0 else None
    if reason:
        return {"mean_m": None, "p95_m": None, "standard_error_mean_m": None,
                "source_area_m2": source_area, "sample_count": 0, "null_reason": reason}
    points, _ = sample_surface(source, count, seed)
    distances = point_to_surface_distances(points, target)
    return {"mean_m": float(distances.mean()), "p95_m": float(np.quantile(distances, 0.95)),
            "standard_error_mean_m": float(distances.std(ddof=1) / np.sqrt(count)) if count > 1 else None,
            "source_area_m2": source_area, "sample_count": count, "null_reason": None}


def _section_chord_frame(points: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Minimum-area enclosing rectangle; long side is the geometric chord."""
    ordered = sorted(set(map(tuple, points.tolist())))
    cross = lambda a, b, c: (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
    lower: list[tuple[float, float]] = []
    upper: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = np.asarray(lower[:-1] + upper[:-1])
    if len(hull) < 2:
        return np.array([0.0, 1.0]), 0.0, 0.0
    best = None
    for index, point in enumerate(hull):
        edge = hull[(index + 1) % len(hull)] - point
        norm = np.linalg.norm(edge)
        if norm <= 0:
            continue
        direction = edge / norm
        normal = np.array([-direction[1], direction[0]])
        a, b = float(np.ptp(hull @ direction)), float(np.ptp(hull @ normal))
        if best is None or a*b < best[0] - 1e-12:
            best = (a*b, direction if a >= b else normal, max(a,b), min(a,b))
    assert best is not None
    return best[1], best[2], best[3]


def section_metrics(mesh: Mesh, z_m: float) -> dict[str, Any]:
    """Compare fixed sections with a twist-aware, geometry-only size convention.

    Chord/thickness are the long/short sides of the minimum-area enclosing xy
    rectangle.  They are geometric size estimates, with a twist-aware frame.
    It includes any self-intersecting components and never silently picks the
    largest component.  Segment topology is separately reported.
    """
    segments = []
    tolerance = 1e-8
    for triangle in mesh.triangles:
        intersections = []
        signed = triangle[:, 2] - z_m
        if np.all(np.abs(signed) <= tolerance):
            continue  # A coplanar cap is not the body section contour.
        for i, j in ((0, 1), (1, 2), (2, 0)):
            if abs(signed[i]) <= tolerance:
                intersections.append(triangle[i, :2])
            if signed[i] * signed[j] < 0:
                fraction = -signed[i] / (signed[j] - signed[i])
                intersections.append((triangle[i] + fraction * (triangle[j] - triangle[i]))[:2])
        unique = []
        for point in intersections:
            if not any(np.linalg.norm(point - old) <= tolerance for old in unique):
                unique.append(point)
        if len(unique) == 2 and np.linalg.norm(unique[1] - unique[0]) > tolerance:
            segments.append(unique)
    if not segments:
        return {"z_m": z_m, "chord_length_m": None, "body_thickness_m": None,
                "section_closed": False, "self_intersections": None, "null_reason": "NO_SECTION_INTERSECTION"}
    points = np.asarray(segments).reshape((-1, 2))
    direction, chord, thickness = _section_chord_frame(points)
    if chord <= tolerance:
        return {"z_m": z_m, "chord_length_m": None, "body_thickness_m": None,
                "section_closed": False, "self_intersections": None, "null_reason": "DEGENERATE_SECTION"}
    # The undirected chord angle is invariant to mesh/face ordering.
    if direction[1] < 0 or (abs(direction[1]) <= tolerance and direction[0] < 0):
        direction = -direction
    keys = [tuple(np.rint(point / tolerance).astype(np.int64)) for point in points]
    degrees: dict[tuple[int, int], int] = {}
    unique_segments = {}
    for index, segment in enumerate(segments):
        key = tuple(sorted((keys[2 * index], keys[2 * index + 1])))
        unique_segments[key] = segment
    for key in unique_segments:
        for point_key in key:
            degrees[point_key] = degrees.get(point_key, 0) + 1
    crossings = 0
    values = list(unique_segments.values())
    for first, (a, b) in enumerate(values):
        for c, d in values[first + 1:]:
            if any(np.linalg.norm(p - q) <= tolerance for p in (a, b) for q in (c, d)):
                continue
            cross = lambda u, v: float(u[0] * v[1] - u[1] * v[0])
            if cross(b - a, c - a) * cross(b - a, d - a) < 0 and cross(d - c, a - c) * cross(d - c, b - c) < 0:
                crossings += 1
    return {"z_m": z_m, "chord_length_m": chord, "body_thickness_m": thickness,
            "chord_angle_from_y_deg": float(np.degrees(np.arctan2(direction[0], direction[1]))),
            "section_closed": bool(degrees) and all(degree == 2 for degree in degrees.values()),
            "self_intersections": crossings, "segment_count": len(unique_segments), "null_reason": None}


def evaluate_mesh_pair(reconstruction: Mesh, truth: Mesh, *, span_bounds_m: Sequence[float] = (0.0, 61.5),
                       section_positions_m: Sequence[float] | None = None, sample_count: int = 8192,
                       regional_sample_count: int = 2048, seed: int = 20261002) -> dict[str, Any]:
    """Score without registration against full declared truth, including missing areas."""
    lower, upper = map(float, span_bounds_m)
    if not np.isfinite([lower, upper]).all() or upper <= lower:
        raise ValueError("Span bounds must be finite and increasing")
    if sample_count <= 0 or regional_sample_count <= 0:
        raise ValueError("Sample counts must be positive")
    if section_positions_m is None:
        section_positions_m = [lower + fraction * (upper - lower) for fraction in (0.15, 0.50, 0.85)]
    sections = list(map(float, section_positions_m))
    if any(not np.isfinite(z) or z <= lower or z >= upper for z in sections):
        raise ValueError("Fixed sections must be inside declared span bounds")
    global_stats = {"reconstruction_to_truth": _distance_stats(reconstruction, truth, sample_count, seed),
                    "truth_to_reconstruction": _distance_stats(truth, reconstruction, sample_count, seed)}
    regions = {}
    bounds = np.linspace(lower, upper, 4)
    for index, name in enumerate(("root", "middle", "tip")):
        rec_region = clip_mesh_z(reconstruction, float(bounds[index]), float(bounds[index + 1]))
        truth_region = clip_mesh_z(truth, float(bounds[index]), float(bounds[index + 1]))
        regions[name] = {"span_bounds_m": bounds[index:index + 2].tolist(),
                         "reconstruction_to_truth": _distance_stats(rec_region, truth, regional_sample_count, seed + index + 1),
                         "truth_to_reconstruction": _distance_stats(truth_region, reconstruction, regional_sample_count, seed + index + 1)}
    return {"global": global_stats, "regions": regions,
            "sections": [{"reconstruction": section_metrics(reconstruction, z), "truth": section_metrics(truth, z)} for z in sections],
            "surface": {"reconstruction_vertices": len(reconstruction.vertices), "reconstruction_faces": len(reconstruction.faces),
                        "truth_vertices": len(truth.vertices), "truth_faces": len(truth.faces)},
            "sampling": {"method": "triangle_area_probability_sqrt_barycentric", "seed": seed,
                         "distance_target": "continuous_triangle_surface", "statistics": "Monte Carlo source-area-weighted estimates",
                         "p95_method": "linear_sample_quantile", "units": "m"}}


def _load_json(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else json.loads(Path(value).read_text(encoding="utf-8"))


def _metadata_contract(model: Mapping[str, Any], truth: Mapping[str, Any]) -> list[str]:
    errors = []
    required = ("target", "t_ref_sim_time_s", "coordinate_frame", "units", "surface_scope")
    for name in required:
        if name not in model or name not in truth:
            errors.append(f"MISSING_METADATA:{name}")
        elif name == "t_ref_sim_time_s":
            try:
                if not np.isfinite(float(model[name])) or not np.isfinite(float(truth[name])) or abs(float(model[name]) - float(truth[name])) > 1e-8:
                    errors.append("REFERENCE_TIME_MISMATCH")
            except (TypeError, ValueError):
                errors.append("INVALID_REFERENCE_TIME")
        elif model[name] != truth[name]:
            errors.append(f"METADATA_MISMATCH:{name}")
    if model.get("target") != "T1/B1" or truth.get("target") != "T1/B1":
        errors.append("TARGET_MUST_BE_T1_B1")
    if model.get("units") != "m" or truth.get("units") != "m":
        errors.append("METRE_UNITS_REQUIRED")
    if model.get("coordinate_frame") != "blade_root_local" or truth.get("coordinate_frame") != "blade_root_local":
        errors.append("BLADE_ROOT_LOCAL_FRAME_REQUIRED")
    return errors


def _evidence_report(mesh: Mesh, evidence: Mapping[str, Any], truth: Mesh) -> dict[str, Any]:
    allowed = {"image_constrained", "appearance_only", "prior_only", "unknown", "missing"}
    regions = evidence.get("regions", [])
    if isinstance(regions, Mapping):
        regions = [{"name": name, **value} for name, value in regions.items()]
    face_labels = evidence.get("face_labels")
    report: dict[str, Any] = {"source": "reconstruction/surface_evidence.json", "regions": regions,
                             "closed_mesh_is_not_observation_evidence": True,
                             "missing_truth_area_m2": None,
                             "missing_area_reason": "Evidence does not label evaluation truth; nearest-boundary distances do not establish recovered missing area."}
    if face_labels is not None:
        if len(face_labels) != len(mesh.faces) or any(label not in allowed for label in face_labels):
            raise ValueError("face_labels must label each output face with an allowed evidence status")
        areas = triangle_areas(mesh)
        report["output_surface_area_by_evidence_m2"] = {label: float(areas[np.asarray(face_labels) == label].sum()) for label in sorted(allowed)}
    else:
        report["output_surface_area_by_evidence_m2"] = None
        report["area_reason"] = "No per-face evidence labels; regional descriptions are retained without inventing area coverage."
    missing_regions = []
    missing_intervals = []
    for region in regions:
        if region.get("status") != "missing":
            continue
        bounds = region.get("span_bounds_m")
        # An entire fixed span band has an evaluable truth-area denominator;
        # side/edge-only or appearance regions require a more specific domain.
        whole_band = region.get("surface_domain") == "whole_span_band"
        if bounds is not None and len(bounds) == 2 and whole_band:
            lower, upper = map(float, bounds)
            if not np.isfinite([lower, upper]).all() or upper <= lower:
                raise ValueError("Missing-region span bounds must be finite and increasing")
            area = float(triangle_areas(clip_mesh_z(truth, lower, upper)).sum())
            missing_intervals.append((lower, upper))
            missing_regions.append({"name": region.get("name"), "span_bounds_m": [lower, upper], "truth_surface_area_m2": area})
        else:
            missing_regions.append({"name": region.get("name"), "truth_surface_area_m2": None,
                                    "reason": "A declared missing region needs a fixed whole-span-band domain for an area calculation."})
    if missing_intervals and all(region["truth_surface_area_m2"] is not None for region in missing_regions):
        merged: list[list[float]] = []
        for lower, upper in sorted(missing_intervals):
            if merged and lower <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], upper)
            else:
                merged.append([lower, upper])
        report["missing_truth_area_m2"] = sum(float(triangle_areas(clip_mesh_z(truth, lower, upper)).sum()) for lower, upper in merged)
        report["missing_area_reason"] = "Union of missing whole-span bands declared by optimization evidence; no recovery inference from finite distances."
    report["missing_regions"] = missing_regions
    partial = any(region.get("status") in {"prior_only", "unknown", "missing"} for region in regions)
    partial = partial or (face_labels is not None and any(label in {"prior_only", "unknown", "missing"} for label in face_labels))
    report["coverage_conclusion"] = "PARTIAL_OR_PRIOR_DEPENDENT" if partial else "COVERAGE_NOT_PROVEN_BY_MESH_CLOSURE"
    return report


def _comparison(scores: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"relative_to_initial": {}, "relative_to_prior_only": {}, "regional_changes": []}
    final = scores["final"]
    improvements = []
    for key, baseline_name in (("relative_to_initial", "initial"), ("relative_to_prior_only", "prior_only")):
        baseline = scores[baseline_name]
        for direction in ("reconstruction_to_truth", "truth_to_reconstruction"):
            result[key][direction] = {}
            for metric in ("mean_m", "p95_m"):
                old, new = baseline["global"][direction][metric], final["global"][direction][metric]
                delta = None if old is None or new is None else float(new - old)
                result[key][direction][f"delta_{metric}"] = delta
                improvements.append(delta is not None and delta < -1e-8)
        for region in ("root", "middle", "tip"):
            changes = {"baseline": baseline_name, "region": region}
            for direction in ("reconstruction_to_truth", "truth_to_reconstruction"):
                old = baseline["regions"][region][direction]["mean_m"]
                new = final["regions"][region][direction]["mean_m"]
                changes[f"delta_{direction}_mean_m"] = None if old is None or new is None else float(new - old)
            result["regional_changes"].append(changes)
    section_changes = []
    section_degradation = False
    for baseline_name in ("initial", "prior_only"):
        for baseline_section, final_section in zip(scores[baseline_name]["sections"], final["sections"]):
            entry = {"baseline": baseline_name, "z_m": final_section["truth"]["z_m"]}
            for metric in ("chord_length_m", "body_thickness_m"):
                truth = final_section["truth"][metric]
                old, new = baseline_section["reconstruction"][metric], final_section["reconstruction"][metric]
                delta = None if truth is None or old is None or new is None else float(abs(new - truth) - abs(old - truth))
                entry[f"delta_abs_error_{metric}"] = delta
                if delta is None or delta > 1e-6:
                    section_degradation = True
            if final_section["reconstruction"].get("self_intersections") not in (0,):
                section_degradation = True
            section_changes.append(entry)
    result["section_changes"] = section_changes
    result["image_contribution"] = "ESTABLISHED_ON_THIS_CLIP" if all(improvements) and not section_degradation else "NOT_YET_ESTABLISHED"
    result["criterion"] = "Both source directions' mean/P95 improve over initial and same-configuration prior-only, with no fixed-section size-error degradation or detected section crossing. Sample estimates do not establish an engineering tolerance."
    result["conclusion_zh"] = "本片段及声明辅助输入下的图像几何贡献成立；分区退化与先验依赖仍单独列出。" if result["image_contribution"] == "ESTABLISHED_ON_THIS_CLIP" else "视频的额外几何贡献尚未证实；请查看无图像对照、双向距离、固定截面及分区变化。"
    return result


def _control_provenance(optimization: Mapping[str, Any]) -> dict[str, Any]:
    provenance = optimization.get("comparison_provenance", {})
    required = ("same_initial_parameters", "same_optimizer", "same_stage_schedule",
                "same_regularization", "same_budget", "only_image_weights_differ")
    errors = [f"NOT_CONFIRMED:{name}" for name in required if provenance.get(name) is not True]
    try:
        if float(provenance["prior_only_image_weight"]) != 0.0:
            errors.append("PRIOR_ONLY_HAS_IMAGE_WEIGHT")
        if not np.isfinite(float(provenance["final_image_weight"])) or float(provenance["final_image_weight"]) <= 0.0:
            errors.append("FINAL_HAS_NO_POSITIVE_IMAGE_WEIGHT")
    except (KeyError, TypeError, ValueError):
        errors.append("MISSING_OR_INVALID_IMAGE_WEIGHTS")
    return {"status": "DECLARED_SAME_CONFIGURATION" if not errors else "NOT_ESTABLISHED",
            "declaration": provenance, "errors": errors,
            "basis": "Frozen optimization audit declaration; evaluation does not tune either model."}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plot_comparison(scores: Mapping[str, Any], output: Path) -> dict[str, Any]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import pyplot as plt
    except ImportError:
        return {"status": "UNAVAILABLE", "reason": "matplotlib is not installed"}
    names = ("initial", "prior_only", "final")
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), constrained_layout=True)
    x = np.arange(3)
    for index, (direction, label) in enumerate((("reconstruction_to_truth", "Reconstruction to truth"), ("truth_to_reconstruction", "Truth to reconstruction"))):
        for offset, metric, title in ((-0.18, "mean_m", "Mean"), (0.18, "p95_m", "P95")):
            values = [scores[name]["global"][direction][metric] for name in names]
            axes[index].bar(x + offset, [np.nan if v is None else v for v in values], width=0.36, label=title)
        axes[index].set_xticks(x, names)
        axes[index].set_ylabel("Surface distance (m)")
        axes[index].set_title(label)
        axes[index].legend()
    z = [section["truth"]["z_m"] for section in scores["final"]["sections"]]
    for name in names:
        values = []
        for section in scores[name]["sections"]:
            rec, truth = section["reconstruction"]["chord_length_m"], section["truth"]["chord_length_m"]
            values.append(np.nan if rec is None or truth is None else rec - truth)
        axes[2].plot(z, values, marker="o", label=name)
    axes[2].axhline(0.0, color="black", linewidth=0.8)
    axes[2].set_xlabel("Fixed span section z (m)")
    axes[2].set_ylabel("Chord error (m)")
    axes[2].set_title("Fixed-section shape check")
    axes[2].legend()
    fig.savefig(output, dpi=150)
    plt.close(fig)
    return {"status": "WRITTEN", "path": output.name}


def evaluate_run(reconstruction_dir: str | Path, truth_ply: str | Path,
                 truth_metadata: Mapping[str, Any] | str | Path, *,
                 scoring_config: Mapping[str, Any] | str | Path | None = None,
                 output_dir: str | Path | None = None) -> dict[str, Any]:
    """Independently score all three frozen models; truth is only read here.

    The config freezes span bounds, fixed sections and sampling *before* scoring.
    ``model_state.json`` has the same five identity/coordinate fields as truth;
    its optional ``reference_state`` object can hold those fields instead.
    """
    reconstruction_dir = Path(reconstruction_dir)
    truth_ply = Path(truth_ply)
    output_dir = Path(output_dir) if output_dir is not None else truth_ply.parent.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    config = _load_json(scoring_config) if scoring_config is not None else {}
    model_state = _load_json(reconstruction_dir / "model_state.json")
    model_meta = model_state.get("reference_state", model_state)
    truth_meta = _load_json(truth_metadata)
    contract_errors = _metadata_contract(model_meta, truth_meta)
    paths = {"initial": reconstruction_dir / "initial_template.ply", "prior_only": reconstruction_dir / "prior_only.ply", "final": reconstruction_dir / "T1_B1.ply"}
    for name, path in paths.items():
        sidecar = path.with_suffix(".json")
        if sidecar.exists():
            contract_errors.extend(f"{name}:{error}" for error in _metadata_contract(_load_json(sidecar), truth_meta))
    span_bounds = list(config.get("span_bounds_m", [0.0, 61.5]))
    section_positions = config.get("section_positions_m", [float(span_bounds[0]) + fraction * (float(span_bounds[1]) - float(span_bounds[0])) for fraction in (0.15, 0.50, 0.85)])
    result: dict[str, Any] = {"schema": "nrel-single-blade-scores-v1", "units": "m",
                             "registration": "NONE", "scale_adjustment": "NONE",
                             "metadata": {"model": model_meta, "truth": truth_meta},
                             "scoring_config": {"span_bounds_m": span_bounds,
                                                "section_positions_m": section_positions,
                                                "sample_count": config.get("sample_count", 8192),
                                                "regional_sample_count": config.get("regional_sample_count", 2048),
                                                "seed": config.get("seed", 20261002)},
                             "section_convention": "xy minimum-area enclosing rectangle long side estimates chord and short side estimates body thickness; z fixed before scoring. A shape-only section cannot prove pressure/suction-side orientation.",
                             "truth_policy": "Evaluation only; no truth-based fitting-window, parameter or initialization choice.",
                             "metadata_errors": contract_errors}
    if contract_errors:
        result.update({"status": "NOT_COMPARABLE", "models": {}, "comparison": {"image_contribution": "NOT_YET_ESTABLISHED"},
                       "null_reason": ";".join(contract_errors)})
    else:
        truth = read_ply(truth_ply)
        meshes = {name: read_ply(path) for name, path in paths.items()}
        result["models"] = {name: evaluate_mesh_pair(mesh, truth, **result["scoring_config"]) for name, mesh in meshes.items()}
        evidence_path = reconstruction_dir / "surface_evidence.json"
        evidence = _load_json(evidence_path) if evidence_path.exists() else {}
        result["surface_evidence"] = _evidence_report(meshes["final"], evidence, truth)
        result["comparison"] = _comparison(result["models"])
        optimization_path = reconstruction_dir / "optimization.json"
        optimization = _load_json(optimization_path) if optimization_path.exists() else {}
        result["comparison_provenance"] = _control_provenance(optimization)
        if result["comparison_provenance"]["status"] != "DECLARED_SAME_CONFIGURATION":
            result["comparison"]["image_contribution"] = "NOT_YET_ESTABLISHED"
            result["comparison"]["conclusion_zh"] = "视频的额外几何贡献尚未证实：缺少同初值、同配置且仅关闭图像损失的无图像对照证据。"
        result["status"] = "SCORED" if all(value["global"]["truth_to_reconstruction"]["mean_m"] is not None for value in result["models"].values()) else "SCORED_WITH_NULL_DISTANCE"
        result["file_signatures"] = {**{name: {"file": path.name, "sha256": _sha256(path)} for name, path in paths.items()},
                                     "truth": {"file": str(truth_ply), "sha256": _sha256(truth_ply)}}
        result["comparison_plot"] = _plot_comparison(result["models"], output_dir / "comparison.png")
    (output_dir / "scores.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconstruction-dir", type=Path, required=True)
    parser.add_argument("--truth-ply", type=Path, required=True)
    parser.add_argument("--truth-metadata", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = evaluate_run(args.reconstruction_dir, args.truth_ply, args.truth_metadata,
                          scoring_config=args.config, output_dir=args.output_dir)
    print(json.dumps({"status": result["status"], "scores": str(args.output_dir / "scores.json"),
                      "image_contribution": result["comparison"]["image_contribution"]}, ensure_ascii=False))
    return 0 if result["status"] == "SCORED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
