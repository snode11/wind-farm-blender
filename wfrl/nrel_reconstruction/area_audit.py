"""Evaluation-only area/domain audit of an already saved truth triangle mesh.

No fitting, registration, sampling, new rendering, or distance scoring occurs.
This module must not be imported by reconstruction/parameter-selection code.
"""
from __future__ import annotations

from .artifact_paths import relocated_path

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

from .evaluate import Mesh, clip_mesh_z, read_ply, triangle_areas


def clip_polygon_axis(polygon: np.ndarray, axis: int, bound: float,
                      above: bool, inclusive: bool = True) -> np.ndarray:
    """Clip a planar polygon without caps or tolerance-based vertex snapping.

    ``inclusive`` controls ownership of *coplanar faces*, not intersection lines
    (which have zero surface area). Newly intersected points lie exactly on bound.
    """
    points = np.asarray(polygon, dtype=float).reshape((-1, 3))
    output = []
    for index, current in enumerate(points):
        previous = points[index - 1]
        a = (current[axis] - bound) * (1 if above else -1)
        b = (previous[axis] - bound) * (1 if above else -1)
        inside_a = a >= 0 if inclusive else a > 0
        inside_b = b >= 0 if inclusive else b > 0
        if inside_a != inside_b:
            fraction = (bound - previous[axis]) / (current[axis] - previous[axis])
            intersection = previous + fraction * (current - previous)
            intersection[axis] = bound
            output.append(intersection)
        if inside_a:
            output.append(current)
    return np.asarray(output, dtype=float).reshape((-1, 3))


def polygon_area(polygon: np.ndarray) -> float:
    if len(polygon) < 3:
        return 0.0
    crosses = np.cross(polygon[1:-1] - polygon[0], polygon[2:] - polygon[0])
    return float(0.5 * np.linalg.norm(crosses, axis=1).sum())


def _bounds(points: np.ndarray) -> dict[str, Any] | None:
    if not len(points):
        return None
    return {"min_xyz_m": points.min(axis=0).tolist(), "max_xyz_m": points.max(axis=0).tolist()}


def _normal_class(triangle: np.ndarray) -> str:
    normal = np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])
    if not np.any(normal):
        return "degenerate"
    axis = int(np.argmax(np.abs(normal)))
    return "xyz"[axis] + ("_positive" if normal[axis] >= 0 else "_negative")


def audit_partition(mesh: Mesh, span_bounds_m=(0.0, 61.5)) -> tuple[dict, list[dict], list[dict]]:
    """Exact planar clipping of each triangle into five disjoint span domains."""
    lower, upper = map(float, span_bounds_m)
    if not np.isfinite([lower, upper]).all() or upper <= lower:
        raise ValueError("Span bounds must be finite and increasing")
    cuts = np.linspace(lower, upper, 4)
    domains = [
        ("below_span", None, cuts[0], False, False),
        ("root", cuts[0], cuts[1], True, False),
        ("middle", cuts[1], cuts[2], True, False),
        ("tip", cuts[2], cuts[3], True, True),
        ("above_span", cuts[3], None, False, False),
    ]
    totals = {name: 0.0 for name, *_ in domains}
    coordinates = defaultdict(list)
    normal_areas = {name: Counter() for name in totals}
    source_faces = {name: [] for name in totals}
    rows, outside_pieces, per_face_errors = [], [], []
    original_areas = triangle_areas(mesh)
    for face_id, (triangle, original_area) in enumerate(zip(mesh.triangles, original_areas)):
        row = {"source_triangle_id": face_id, "original_area_m2": float(original_area),
               "original_normal_proxy": _normal_class(triangle)}
        for name, low, high, include_low, include_high in domains:
            polygon = triangle
            if low is not None:
                polygon = clip_polygon_axis(polygon, 2, low, True, include_low)
            if high is not None:
                polygon = clip_polygon_axis(polygon, 2, high, False, include_high)
            area = polygon_area(polygon)
            row[name + "_area_m2"] = area
            totals[name] += area
            if area > 0:
                coordinates[name].extend(polygon)
                source_faces[name].append(face_id)
                normal_areas[name][row["original_normal_proxy"]] += area
                if name in {"below_span", "above_span"}:
                    outside_pieces.append({"source_triangle_id": face_id, "domain": name,
                                           "area_m2": area, "original_normal_proxy": row["original_normal_proxy"],
                                           "original_vertices_xyz_m": triangle.tolist(),
                                           "clipped_polygon_xyz_m": polygon.tolist()})
        row["closure_residual_m2"] = float(original_area) - sum(row[name + "_area_m2"] for name in totals)
        per_face_errors.append(abs(row["closure_residual_m2"]))
        rows.append(row)
    total_area = float(original_areas.sum())
    regional_sum = sum(totals[name] for name in ("root", "middle", "tip"))
    outside_sum = totals["below_span"] + totals["above_span"]
    report = {
        "method": "Exact plane/triangle polygon intersections; fan triangle areas in float64; no Monte Carlo; no new caps",
        "coplanar_ownership": "below: z<lower; root: lower<=z<cut1; middle: cut1<=z<cut2; tip: cut2<=z<=upper; above: z>upper",
        "cuts_z_m": cuts.tolist(),
        "global_area_m2": total_area,
        "global_bbox": _bounds(mesh.vertices),
        "domains": {name: {"area_m2": totals[name], "positive_area_source_triangle_count": len(source_faces[name]),
                            "source_triangle_ids": source_faces[name],
                            "bbox": _bounds(np.asarray(coordinates[name]).reshape((-1, 3))),
                            "original_normal_proxy_area_m2": dict(normal_areas[name])} for name in totals},
        "three_region_area_m2": regional_sum,
        "outside_declared_span_area_m2": outside_sum,
        "outside_percent_of_global": 100 * outside_sum / total_area if total_area else None,
        "global_minus_regions_m2": total_area - regional_sum,
        "closure_residual_m2": total_area - sum(totals.values()),
        "max_triangle_closure_abs_m2": max(per_face_errors, default=0.0),
        "internal_coplanar_triangle_area_m2": {
            str(float(cut)): float(original_areas[np.all(mesh.triangles[:, :, 2] == cut, axis=1)].sum())
            for cut in cuts[1:-1]},
        "normal_proxy_definition": "Largest absolute component of original oriented triangle normal; ties x,y,z. Coordinates only: no pressure/suction or leading/trailing-edge semantics.",
    }
    # Independent compatibility check against the historical inclusive clipping.
    report["historical_inclusive_clip_area_m2"] = {
        name: float(triangle_areas(clip_mesh_z(mesh, float(cuts[i]), float(cuts[i + 1]))).sum())
        for i, name in enumerate(("root", "middle", "tip"))}
    return report, rows, outside_pieces


def audit_topology(mesh: Mesh) -> dict[str, Any]:
    """Index topology, including edge incidence, orientation and vertex links.

    Geometry is not welded and surface/surface intersections are not tested.
    A closed manifold here is an index-topology finding, not observation evidence.
    """
    edge_faces = defaultdict(list)
    links = defaultdict(list)
    for face_id, face in enumerate(mesh.faces):
        for a, b in zip(face, np.roll(face, -1)):
            edge_faces[tuple(sorted((int(a), int(b))))].append((face_id, int(a), int(b)))
        for i, v in enumerate(face):
            links[int(v)].append((int(face[(i + 1) % 3]), int(face[(i + 2) % 3])))
    boundary = [edge for edge, entries in edge_faces.items() if len(entries) == 1]
    nonmanifold = [edge for edge, entries in edge_faces.items() if len(entries) > 2 or edge[0] == edge[1]]
    orientation = [edge for edge, entries in edge_faces.items()
                   if len(entries) == 2 and entries[0][1:] == entries[1][1:]]
    invalid_links = []
    for vertex, edges in links.items():
        adjacent = defaultdict(list)
        for a, b in edges:
            adjacent[a].append(b)
            adjacent[b].append(a)
        pending = set(adjacent)
        components = 0
        while pending:
            stack = [pending.pop()]
            components += 1
            while stack:
                for other in adjacent[stack.pop()]:
                    if other in pending:
                        pending.remove(other)
                        stack.append(other)
        degrees = [len(value) for value in adjacent.values()]
        boundary_vertex = any(vertex in edge for edge in boundary)
        valid_degrees = (degrees.count(1) == 2 and all(d in {1, 2} for d in degrees)) if boundary_vertex else all(d == 2 for d in degrees)
        if components != 1 or not valid_degrees:
            invalid_links.append(vertex)
    parent = list(range(len(mesh.faces)))
    def root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value
    for entries in edge_faces.values():
        for entry in entries[1:]:
            parent[root(entry[0])] = root(entries[0][0])
    face_counts = Counter(tuple(sorted(map(int, face))) for face in mesh.faces)
    areas = triangle_areas(mesh)
    boundary_details = [{"vertex_ids": list(edge), "vertices_xyz_m": mesh.vertices[list(edge)].tolist(),
                         "length_m": float(np.linalg.norm(mesh.vertices[edge[0]] - mesh.vertices[edge[1]]))} for edge in boundary]
    return {
        "vertex_count": len(mesh.vertices), "triangle_count": len(mesh.faces),
        "unique_undirected_edge_count": len(edge_faces),
        "euler_characteristic_used_vertices": len(links) - len(edge_faces) + len(mesh.faces),
        "unused_vertex_count": len(mesh.vertices) - len(links),
        "exact_duplicate_coordinate_count": len(mesh.vertices) - len(np.unique(mesh.vertices, axis=0)),
        "duplicate_face_count": sum(count - 1 for count in face_counts.values()),
        "zero_area_triangle_count": int(np.count_nonzero(areas == 0)),
        "boundary_edge_count": len(boundary),
        "boundary_edge_length_m": sum(item["length_m"] for item in boundary_details),
        "boundary_edges": boundary_details,
        "nonmanifold_edge_count": len(nonmanifold), "nonmanifold_edges": [list(edge) for edge in nonmanifold],
        "invalid_vertex_link_count": len(invalid_links), "invalid_vertex_link_ids": invalid_links,
        "inconsistent_oriented_edge_count": len(orientation),
        "edge_connected_component_count": len({root(i) for i in range(len(mesh.faces))}),
        "closed_oriented_manifold_index_topology": bool(len(mesh.faces)) and not (boundary or nonmanifold or invalid_links or orientation),
        "welding": "NONE; original PLY indices",
        "limitations": "No triangle/triangle intersection test. No aerodynamic/material edge labels. Topological closure does not prove camera visibility or recovered surface area.",
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_metadata(manifest: dict, mesh_hash: str, scores: dict[str, dict]) -> dict:
    errors = []
    if manifest.get("file_sha256") != mesh_hash:
        errors.append("truth_manifest.file_sha256 mismatch")
    if manifest.get("algorithm_input") is not False:
        errors.append("truth_manifest.algorithm_input is not false")
    fields = ("target", "t_ref_sim_time_s", "coordinate_frame", "units", "surface_scope")
    score_checks = {}
    for name, score in scores.items():
        model, recorded_truth = (score.get("metadata", {}).get(key, {}) for key in ("model", "truth"))
        matched = {field: model.get(field) == manifest.get(field) and field in model and field in manifest for field in fields}
        recorded_equal = recorded_truth == manifest
        signature_equal = score.get("file_signatures", {}).get("truth", {}).get("sha256") == mesh_hash
        score_checks[name] = {"common_fields_equal": matched, "recorded_truth_manifest_equal": recorded_equal,
                              "truth_signature_equal": signature_equal, "historical_metadata_errors": score.get("metadata_errors")}
        if not all(matched.values()) or not recorded_equal or not signature_equal or score.get("metadata_errors"):
            errors.append(name + ": metadata/signature mismatch")
    return {"status": "MATCHED_SAVED_RECORDS" if not errors else "MISMATCH", "errors": errors,
            "common_truth_identity": {key: manifest.get(key) for key in fields}, "scores": score_checks,
            "scope": "Saved-record consistency and current PLY hash only; does not rerender or revalidate the historical simulation state."}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_audit(comparison_path: Path, output_dir: Path) -> dict:
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise FileExistsError(f"Refusing to overwrite nonempty diagnostic output: {output_dir}")
    comparison_path = relocated_path(comparison_path)
    comparison = _read_json(comparison_path)
    score_paths = {"first": relocated_path(comparison["comparison_source"]["first_scores"])}
    score_paths.update({name: relocated_path(path) for name, path in comparison["comparison_source"]["second_scores"].items()})
    scores = {name: _read_json(path) for name, path in score_paths.items()}
    truth_path = relocated_path(scores["reconstruction"]["file_signatures"]["truth"]["file"])
    manifest_path = truth_path.with_name("truth_manifest.json")
    manifest = _read_json(manifest_path)
    paths = {"comparison": comparison_path, "truth_mesh": truth_path, "truth_manifest": manifest_path,
             **{"score_" + name: path for name, path in score_paths.items()},
             "audit_source": Path(__file__).resolve(), "historical_evaluator_source": Path(__file__).with_name("evaluate.py").resolve()}
    signatures_before = {name: {"path": str(path), "sha256": sha256(path)} for name, path in paths.items()}
    mesh = read_ply(truth_path)
    metadata = audit_metadata(manifest, signatures_before["truth_mesh"]["sha256"], scores)
    if metadata["errors"]:
        raise ValueError("Saved truth provenance mismatch: " + "; ".join(metadata["errors"]))
    partition, rows, outside = audit_partition(mesh, comparison["scoring_config"]["span_bounds_m"])
    topology = audit_topology(mesh)
    historical = {"first": comparison["first"], "initial": comparison["initial"], **comparison["methods"]}
    historical_checks = {}
    for name, model in historical.items():
        global_area = model["global"]["truth_to_reconstruction"]["source_area_m2"]
        regions = {key: value["truth_to_reconstruction"]["source_area_m2"] for key, value in model["regions"].items()}
        historical_checks[name] = {
            "global_area_m2": global_area, "regional_areas_m2": regions,
            "global_difference_from_current_m2": global_area - partition["global_area_m2"],
            "region_differences_from_current_inclusive_clip_m2": {
                key: value - partition["historical_inclusive_clip_area_m2"][key] for key, value in regions.items()},
        }
    signatures_after = {name: sha256(path) for name, path in paths.items()}
    unchanged = all(signatures_after[name] == value["sha256"] for name, value in signatures_before.items())
    area_tolerance = 1e-9 * max(1.0, partition["global_area_m2"])
    area_closed = abs(partition["closure_residual_m2"]) <= area_tolerance and partition["max_triangle_closure_abs_m2"] <= area_tolerance
    historical_match = all(
        abs(check["global_difference_from_current_m2"]) <= area_tolerance
        and all(abs(delta) <= area_tolerance for delta in check["region_differences_from_current_inclusive_clip_m2"].values())
        for check in historical_checks.values())
    report = {
        "schema": "nrel-independent-truth-area-domain-audit.v1", "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "DIAGNOSTIC_AREA_CLOSED" if area_closed and unchanged and historical_match else "DIAGNOSTIC_CHECK_FAILED",
        "truth_policy": "Evaluation-only diagnostic; prohibited as reconstruction input, fitting/parameter/threshold selection, or frame selection.",
        "operations": {"registration": "NONE", "scale_adjustment": "NONE", "optimization": "NONE", "new_scoring": "NONE", "new_capture": "NONE"},
        "metadata_audit": metadata, "truth_manifest": manifest, "input_signatures": signatures_before,
        "inputs_unchanged_during_audit": unchanged, "partition": partition, "topology": topology,
        "historical_area_checks": historical_checks,
        "historical_areas_match_current_saved_mesh": historical_match,
        "historical_scoring_config_unchanged": comparison["scoring_config"],
        "historical_criterion_unchanged": comparison["criterion"],
        "historical_interpretation_unchanged": comparison["interpretation"],
        "historical_final_joint_geometry_criterion": comparison["methods"]["reconstruction"]["joint_geometry_criterion_vs_initial_and_first"],
        "semantic_surface_domains": {"pressure_side_area_m2": None, "suction_side_area_m2": None,
                                      "leading_edge_area_m2": None, "trailing_edge_area_m2": None,
                                      "reason": "Saved PLY supplies no material/physical semantic labels. Normal proxies and index boundaries are geometric diagnostics only."},
        "closure_check_tolerance_m2": area_tolerance,
        "limits": ["Area is the saved piecewise-planar surface area, not exact continuous CAD/physical blade area.",
                   "Strict z domains use saved floating-point coordinates; even micron-scale negative z can exclude part of a nearly horizontal root surface.",
                   "No coordinate correction, snapping tolerance, or rescoring is applied to historical results.",
                   "No material pressure/suction/leading/trailing semantics or image visibility is inferred from geometry.",
                   "Global and per-region Monte Carlo distance statistics use different source domains/samples; regional means/P95 cannot be arithmetically substituted for global scores."],
        "runtime": {"python": sys.version, "numpy": np.__version__},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "per_triangle_area_accounting.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["source_triangle_id"])
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "outside_span_pieces.json").write_text(json.dumps({"evaluation_only": True, "pieces": outside}, indent=2) + "\n")
    (output_dir / "area_audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    p = partition
    text = ["# 独立评分域面积对账（仅评估诊断）", "", "只复用已保存真值网格；未优化、未采集、未重评分、未改变历史门槛。", "",
            "| 域 | 原三角形严格裁剪面积（m²） | 有正面积贡献的原三角形数 |", "|---|---:|---:|"]
    for name, value in p["domains"].items():
        text.append(f"| {name} | {value['area_m2']:.12f} | {value['positive_area_source_triangle_count']} |")
    text += ["", f"全网格面积 **{p['global_area_m2']:.12f} m²**；三段合计 **{p['three_region_area_m2']:.12f} m²**。",
             f"差额 **{p['global_minus_regions_m2']:.12f} m²** = z<0 与 z>61.5 两域；总面积闭合残差 {p['closure_residual_m2']:.3g} m²。", "",
             f"保存网格 z 范围：**{p['global_bbox']['min_xyz_m'][2]:.12g} 至 {p['global_bbox']['max_xyz_m'][2]:.12g} m**。越界面积占全网格 **{p['outside_percent_of_global']:.9f}%**。", "",
             "低于 z=0 的片段非常薄，但原表面近似平行于裁剪平面，严格切割仍可分走可观的根部表面积。这里记录坐标事实，不把它当成几何缺失，也不猜测导出误差或设计意图。",
             "z<0 与 z>61.5 的每个裁剪多边形、原三角形编号和坐标见 outside_span_pieces.json；所有原面的面积归属和闭合残差见 per_triangle_area_accounting.csv。", "",
             f"原网格：{topology['vertex_count']} 个顶点，{topology['triangle_count']} 个三角形；边界边 {topology['boundary_edge_count']}、非流形边 {topology['nonmanifold_edge_count']}、异常顶点 link {topology['invalid_vertex_link_count']}、方向不一致边 {topology['inconsistent_oriented_edge_count']}。该检查未执行三角形相互穿透检测。", "",
             "PLY 不提供压力面/吸力面或前后缘材料语义；这些语义域面积保持 null。报告中的 x/y/z 法向主分量类别只是原局部坐标几何代理。拓扑闭合不证明观测覆盖。", "",
             f"历史全部域面积重新计算一致：{historical_match}；原始输入哈希保持不变：{unchanged}。同态元数据仅核对保存记录的一致性，未重新验证历史场景。", "",
             f"历史联合结论保留：**{report['historical_final_joint_geometry_criterion']}**。"]
    (output_dir / "README.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    if not area_closed or not unchanged or not historical_match:
        raise ValueError("Area closure, historical consistency or immutable-input audit failed; inspect saved diagnostic report")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_audit(args.comparison, args.output)
    print(json.dumps({"status": report["status"], "global_area_m2": report["partition"]["global_area_m2"],
                      "outside_area_m2": report["partition"]["outside_declared_span_area_m2"], "report": str(args.output / "area_audit.json")}))


if __name__ == "__main__":
    main()
