"""Isolated 15-candidate fixed-camera experiment; never writes product defaults.

Run with Blender --background --factory-startup --python-exit-code 1 --python
this-file. WFRL_TEST_OUTPUT must name a fresh evidence directory. Uses the
existing recorded MAPPO package. Technical renders highlight target Blade1 in
green so visible pixel area can be measured from actual depth-tested pixels.
They are framing evidence, not material, viewport-performance or hardware QA.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/blender"), str(ROOT / "blender_frontend"), str(ROOT)]
from framing_metrics import candidate_rank, qualified, transition_midpoints, visibility_summary, surface_cross_section_samples

MODULE = os.environ.get("WFRL_ADDON_MODULE", "wfrl_blender")
addon = importlib.import_module(MODULE)
core = importlib.import_module(MODULE + ".custom_cameras")
rig = importlib.import_module(MODULE + ".stacked_camera_rig")
farm = importlib.import_module(MODULE + ".farm_flex")
preview = importlib.import_module(MODULE + ".custom_camera_preview")
OUT = Path(os.environ.get("WFRL_TEST_OUTPUT", str(ROOT / "outputs/frontend-update-20260927/framing/run")))
WIDTH, HEIGHT = 320, 180
COARSE = [0., 10., 20., 45., 90., 135., 180., 225., 270., 315., 340., 350.]
EDGES = np.unique(np.round(np.concatenate((np.arange(0, 3.01, .1),
                      np.arange(3., 59.01, .5), np.arange(59., 61.501, .1))), 6))
START = time.perf_counter()


def write(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def original_id(obj):
    return obj.original if hasattr(obj, "original") else obj


def physical_collision(scene):
    """Reject box intersections with nacelle surfaces; preserve arm contact.

    The product contact validator separately checks arm interior sample points.
    A geometric contact check does not certify manufactured support strength.
    """
    from mathutils.bvhtree import BVHTree
    deps = bpy.context.evaluated_depsgraph_get()
    root_inverse = scene.objects[core.ROOT_NAME].matrix_world.inverted()
    surfaces = core._geometry(scene)
    overlaps = []
    for obj in scene.objects:
        if not obj.get("stacked_rig_physical") or obj.type != "MESH" or obj.name.endswith(".MountArm"):
            continue
        evaluated = obj.evaluated_get(deps)
        matrix = root_inverse @ evaluated.matrix_world
        vertices = [matrix @ vertex.co for vertex in evaluated.data.vertices]
        tree = BVHTree.FromPolygons(vertices, [tuple(poly.vertices) for poly in evaluated.data.polygons])
        for name, _, _, _, surface_tree in surfaces:
            count = len(tree.overlap(surface_tree))
            if count:
                overlaps.append({"rig_object": obj.name, "surface": name, "triangle_pairs": count})
    if overlaps:
        raise ValueError("physical box mesh intersects mounting surface: " + json.dumps(overlaps))
    return {"box_surface_triangle_intersections": overlaps,
            "support": "product validator checks true surface contact and support/body interior samples; intentional arm contact retained"}


def create_candidates(scene, baseline):
    point = np.asarray(baseline["rig_pose"]["surface_mount"]["point"])
    mount = baseline["rig_pose"]["surface_mount"]
    surface = next(g for g in core._geometry(scene) if g[0] == mount["surface_name"])
    normal = Vector(mount["normal"]).normalized()
    offsets = [("current", 0., 0.), ("x-minus", -.25, 0.), ("x-plus", .25, 0.),
               ("z-minus", 0., -.20), ("z-plus", 0., .20)]
    result = []
    for point_name, dx, dz in offsets:
        nominal = Vector(point + np.array([dx, 0., dz]))
        anchor_point, anchor_normal, _, _ = surface[-1].ray_cast(nominal + normal * 2, -normal, 4.)
        for delta in (-15., 0., 15.):
            name = f"{point_name}_{'minus15' if delta < 0 else 'plus15' if delta > 0 else 'zero'}"
            row = {"id": name, "surface_point_offset_xz_m": [dx, dz],
                   "requested_relative_aim_deg": delta, "actual_relative_aim_deg": None,
                   "installation_status": "REJECTED", "failed_attempts": []}
            if anchor_point is None:
                row["failed_attempts"].append({"reason": "no nacelle surface at requested nearby point"})
                result.append(row)
                continue
            # Angle reduction is explicit and independent of framing scores.
            for actual in ((delta, delta * 2 / 3, delta / 3) if delta else (0.,)):
                try:
                    if point_name == "current" and actual == 0:
                        layout = copy.deepcopy(baseline)
                    else:
                        anchor = core.SurfaceAnchor(tuple(anchor_point), tuple(anchor_normal), 0., surface[0])
                        pose = rig.surface_pose(scene, anchor, mount_type="SUPPORT",
                                                spin=mount["spin_deg"], aim=mount["aim_deg"] + actual)
                        layout = rig.transformed_layout(scene, baseline, pose)
                    core.restore_layout(scene, layout)
                    bpy.context.view_layer.update()
                    checks = physical_collision(scene)
                    for before, after in zip(baseline["cameras"], layout["cameras"]):
                        for key in ("fov", "vfov", "output_long_edge_px", "clip_near_m", "clip_far_m", "focal_length_mm_record"):
                            assert before["parameters"][key] == after["parameters"][key], (name, key)
                    row.update(installation_status="GEOMETRY_ACCEPTED", actual_relative_aim_deg=actual,
                               layout=layout, installation_checks=checks,
                               layout_sha256=core.layout_hash(layout))
                    write(f"layouts/{name}.json", layout)
                    break
                except (ValueError, RuntimeError) as exc:
                    row["failed_attempts"].append({"relative_aim_deg": actual, "reason": str(exc)})
            result.append(row)
    return result


def setup_render(scene, target):
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x, scene.render.resolution_y = WIDTH, HEIGHT
    scene.render.resolution_percentage = 100
    scene.render.use_border = False
    scene.render.use_crop_to_border = False
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.display.shading.light = "FLAT"
    scene.display.shading.color_type = "OBJECT"
    scene.display.shading.show_shadows = False
    scene.display.shading.show_cavity = False
    scene.display.shading.show_object_outline = False
    scene.display.shading.show_specular_highlight = False
    scene.display.shading.background_type = "WORLD"
    scene.world.color = (.025, .025, .025)
    scene.display.render_aa = "8"
    for obj in scene.objects:
        obj.color = (.36, .39, .43, 1.)
        if obj.get("stacked_rig_physical"):
            obj.color = (.06, .07, .08, 1.)
        # Render visibility and ray visibility exclude exactly the declared
        # nonphysical aids, never real nacelle/support/blades or fixtures.
        name = obj.name
        annotation = (name.startswith(("WFRL.Label.", "WFRL.Grid.", "WFRL.Inflow.", "WFRL.Deflection.",
                      "WFRL.Wake", "WFRL.Fixture.T1.LidarRay")) or ".TipTrail." in name
                      or ".ClearanceRadar.Beam" in name or name.endswith(".SensorFrustum"))
        if annotation:
            obj.hide_render = True
            obj.hide_set(True)
    target.color = (.015, .8, .035, 1.)


def render_camera(scene, camera, path):
    scene.camera = camera
    scene.render.filepath = str(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        rgba = np.empty(WIDTH * HEIGHT * 4, dtype=np.float32)
        image.pixels.foreach_get(rgba)
        rgb = rgba.reshape(HEIGHT, WIDTH, 4)[..., :3]
        # Same green target and neutral non-target colors in all renders.
        mask = ((rgb[..., 1] > 1.4 * rgb[..., 0]) & (rgb[..., 1] > 1.4 * rgb[..., 2])
                & (rgb[..., 1] > .15))
        return int(mask.sum()), float(mask.mean())
    finally:
        bpy.data.images.remove(image)


def mesh_samples(target, rest):
    target.data.calc_loop_triangles()
    triangles = np.asarray([list(t.vertices) for t in target.data.loop_triangles], dtype=int)
    vertex_spans = rest[triangles, 2] - 1.5
    # A centroid-only sample leaves artificial gaps between mesh rings.
    return surface_cross_section_samples(triangles, vertex_spans, EDGES)


def classify_blocker(obj, target):
    if obj is None:
        return "no_hit"
    name = original_id(obj).name
    if original_id(obj) == target:
        return "target_blade_self_occlusion"
    if name.startswith(rig.PREFIX):
        return "housing_or_support"
    if ".Nacelle" in name:
        return "nacelle"
    if ".Blade" in name:
        return "other_blade"
    return name


def evaluate(scene, candidate, angle, target, triangles, face_bins, face_spans, weights, times, progress, timebase, stage):
    requested_time = float(np.interp(angle, progress, times))
    frame_float = 1 + (requested_time - times[0]) * timebase
    # Cross the full-cycle endpoint, rather than silently round it short.
    frame = int(math.ceil(frame_float) if angle >= 360. else round(frame_float))
    scene.frame_set(frame)
    bpy.context.view_layer.update()
    deps = bpy.context.evaluated_depsgraph_get()
    evaluated = target.evaluated_get(deps)
    coords = np.empty(len(evaluated.data.vertices) * 3, dtype=np.float32)
    evaluated.data.vertices.foreach_get("co", coords)
    matrix = np.asarray(evaluated.matrix_world)
    world = coords.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
    centers = np.einsum("ijk,ij->ik", world[triangles], weights)
    normals = np.cross(world[triangles[:, 1]] - world[triangles[:, 0]], world[triangles[:, 2]] - world[triangles[:, 0]])
    observations, visible_sets = [], []
    for slot in core.SLOTS:
        camera = core.get_camera(scene, slot)
        p = core.parameters(camera)
        expected = candidate["layout"]["cameras"][slot-1]["parameters"]
        assert max(abs(a-b) for a, b in zip(p.location, expected["location"])) < 2e-5
        assert all(abs(getattr(p, key)-expected[key]) < 2e-5 for key in ("yaw", "pitch", "roll", "fov", "vfov")), "camera changed while evaluating fixed-camera phases"
        inverse = np.asarray(camera.evaluated_get(deps).matrix_world.inverted())
        origin = np.asarray(camera.evaluated_get(deps).matrix_world.translation)
        local = centers @ inverse[:3, :3].T + inverse[:3, 3]
        frontal = np.einsum("ij,ij->i", normals, origin - centers) > 0
        half = np.tan(np.radians([p.fov, p.vfov]) / 2)
        inside = ((local[:, 2] < -p.clip_near_m) &
                  (np.abs(local[:, :2]) <= -local[:, 2, None] * half).all(axis=1))
        visible, blockers = set(), {}
        root_tip = {"root_0_3m": {"sampled_front_faces": int((frontal & (face_spans <= 3)).sum()), "in_frame": 0, "visible": 0, "blockers": {}},
                    "tip_59_61_5m": {"sampled_front_faces": int((frontal & (face_spans >= 59)).sum()), "in_frame": 0, "visible": 0, "blockers": {}}}
        for index in np.flatnonzero(frontal & inside):
            delta = centers[index] - origin
            distance = float(np.linalg.norm(delta))
            hit, location, _, _, obj, _ = scene.ray_cast(deps, Vector(origin), Vector(delta / distance), distance=distance + .02)
            clear = bool(hit and original_id(obj) == target and np.linalg.norm(np.asarray(location) - centers[index]) < .01)
            blocker = None if clear else classify_blocker(obj if hit else None, target)
            if clear:
                visible.add(int(index))
            else:
                blockers[blocker] = blockers.get(blocker, 0) + 1
            for key, condition in (("root_0_3m", face_spans[index] <= 3), ("tip_59_61_5m", face_spans[index] >= 59)):
                if condition:
                    root_tip[key]["in_frame"] += 1
                    root_tip[key]["visible"] += int(clear)
                    if blocker:
                        d = root_tip[key]["blockers"]
                        d[blocker] = d.get(blocker, 0) + 1
        for counts in root_tip.values():
            counts["out_of_frustum"] = counts["sampled_front_faces"] - counts["in_frame"]
        image = Path("images") / candidate["id"] / f"frame-{frame:05d}-C{slot}.png"
        pixels, fraction = render_camera(scene, camera, OUT / image)
        observations.append({"camera": f"C{slot}", "target_blade": "Blade1", "image": str(image),
                             "sampled_front_faces": int(frontal.sum()), "out_of_frustum": int((frontal & ~inside).sum()),
                             "in_frustum": int((frontal & inside).sum()), "visible_surface_samples": len(visible),
                             "visible_surface_sample_ids": sorted(visible),
                             "blockers": blockers, "root_tip": root_tip,
                             "target_pixels": pixels, "frame_pixels": WIDTH * HEIGHT,
                             "target_pixel_fraction": fraction})
        visible_sets.append(visible)
    actual_time = float(scene["wfrl_clearance_time_s"])
    return {"candidate": candidate["id"], "stage": stage, "requested_turn_deg": angle,
            "turn_deg": float(np.interp(actual_time, times, progress)), "frame": frame, "time_s": actual_time,
            "target_blade": "Blade1", "cameras": observations,
            "visibility": visibility_summary(visible_sets, face_bins, EDGES)}


def main():
    if (OUT / "manifest.json").exists():
        raise RuntimeError("Use a fresh WFRL_TEST_OUTPUT directory; prior evidence will not be overwritten")
    OUT.mkdir(parents=True, exist_ok=True)
    addon.register()
    addon.load_demo_scene()
    scene = bpy.context.scene
    scene.frame_set(1)
    baseline = core.layout_dict(scene)
    write("baseline-layout.json", baseline)
    active = farm._ACTIVE
    times = np.asarray(active.times)
    angles = np.degrees(np.unwrap(np.radians(active.poses[:, 0, 1])))
    progress = (angles - angles[0]) * (1 if angles[1] >= angles[0] else -1)
    assert np.all(np.diff(progress) > 0) and progress[-1] >= 360
    timebase = float(scene["wfrl_clearance_timebase_fps"])
    target, rest, *_ = next(row for row in active.blades if row[0].name == "WFRL.Turbine.T1.Blade1")
    triangles, face_bins, face_spans, weights = mesh_samples(target, rest)
    missing_bins = sorted(set(range(len(EDGES)-1)) - set(face_bins.tolist()))
    assert not missing_bins, f"Mesh sampling has unknown bins {missing_bins}; cannot call them visibility gaps"
    write("surface-samples.json", {"target": target.name, "triangle_vertex_ids": triangles.tolist(),
                                   "span_bin_ids": face_bins.tolist(), "span_m": face_spans.tolist(),
                                   "barycentric_weights": weights.tolist()})
    manifest = {"status": "RUNNING", "blender": bpy.app.version_string, "module": MODULE,
                "addon_path": addon.__file__, "source_package": scene["wfrl_farm_flex_path"],
                "source_manifest_sha256": scene["wfrl_farm_manifest_sha256"],
                "source_status": active.manifest["status"], "target_blade": target.name,
                "baseline_layout_sha256": core.layout_hash(baseline),
                "measurement_source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (Path(__file__), ROOT / "scripts/blender/framing_metrics.py", Path(core.__file__),
                                 Path(rig.__file__), Path(farm.__file__)) if path.is_relative_to(ROOT)},
                "render": {"engine": "BLENDER_WORKBENCH", "technical_target_color": "green", "size_px": [WIDTH, HEIGHT],
                           "aa": "8", "light": "FLAT", "same_interior_and_exterior_geometry": True,
                           "meaning": "actual fixed-camera depth-tested technical frames; not production lighting or viewport performance"},
                "sampling": {"coarse_turn_deg": COARSE, "dense_step_deg": 5., "transition_refinement_deg": 2.5,
                             "span_edges_m": EDGES.tolist(), "surface_samples": len(triangles),
                             "missing_mesh_sample_bins": sorted(set(range(len(EDGES)-1)) - set(face_bins.tolist())),
                             "ray_hit_tolerance_m": .01, "root_m": [0, 3], "tip_m": [59, 61.5],
                             "recorded_cycle_time_s": [float(times[0]), float(np.interp(360., progress, times))],
                             "timeline_fps": timebase},
                "limits": ["sampled surface visibility only; visible longitudinal bins do not prove whole chord/surface coverage",
                           "fixed Blade1, fixed camera intrinsics, unchanged recorded physics",
                           "simulated support constraints and assumed housing dimensions do not certify physical installation",
                           "technical renders do not prove production-image detail quality or display smoothness",
                           "green-mask area excludes antialias fringe below a declared 1.4x dominance threshold"]}
    write("manifest.json", manifest)
    candidates = create_candidates(scene, baseline)
    write("candidates.json", candidates)
    setup_render(scene, target)
    if os.environ.get("WFRL_FRAMING_SMOKE") == "1":
        candidates = [next(c for c in candidates if c["id"] == "current_zero")]
        assert candidates[0]["installation_status"] == "GEOMETRY_ACCEPTED", "baseline failed installation constraints"
        coarse_angles = [0.]
    else:
        coarse_angles = COARSE
    results = []
    with preview.without_annotations(scene, bpy.context.view_layer):
        for candidate in candidates:
            if candidate["installation_status"] != "GEOMETRY_ACCEPTED":
                continue
            core.restore_layout(scene, candidate["layout"])
            setup_render(scene, target)
            rows = [evaluate(scene, candidate, angle, target, triangles, face_bins, face_spans,
                             weights, times, progress, timebase, "coarse") for angle in coarse_angles]
            result = {"id": candidate["id"], "qualified": qualified(rows), "rank": candidate_rank(rows), "samples": rows}
            results.append(result)
            write(f"metrics/{candidate['id']}-coarse.json", result)
            print("FRAMING_COARSE", candidate["id"], result["qualified"], result["rank"], flush=True)
    ordered = sorted((r for r in results if r["qualified"]), key=lambda r: tuple(r["rank"]), reverse=True)
    finalists = ordered[:2]
    if os.environ.get("WFRL_FRAMING_SMOKE") == "1":
        finalists = []
    dense = []
    with preview.without_annotations(scene, bpy.context.view_layer):
        for finalist in finalists:
            candidate = next(c for c in candidates if c["id"] == finalist["id"])
            core.restore_layout(scene, candidate["layout"])
            setup_render(scene, target)
            rows = [evaluate(scene, candidate, float(angle), target, triangles, face_bins, face_spans,
                             weights, times, progress, timebase, "dense") for angle in np.arange(0., 360.01, 5.)]
            extra = transition_midpoints(rows)
            rows.extend(evaluate(scene, candidate, angle, target, triangles, face_bins, face_spans,
                                 weights, times, progress, timebase, "transition") for angle in extra)
            rows.sort(key=lambda row: row["requested_turn_deg"])
            result = {"id": candidate["id"], "rank": candidate_rank(rows), "samples": rows,
                      "transition_extra_angles_deg": extra,
                      "joint_same_surface_overlap_angles_deg": [r["turn_deg"] for r in rows if qualified([r])],
                      "no_ray_visible_sample_angles_deg": [r["turn_deg"] for r in rows if not r["visibility"]["visible_length_m"]],
                      "zero_target_pixels_all_cameras_angles_deg": [r["turn_deg"] for r in rows if not any(c["target_pixels"] for c in r["cameras"])]}
            dense.append(result)
            write(f"metrics/{candidate['id']}-dense.json", result)
            print("FRAMING_DENSE", candidate["id"], len(rows), flush=True)
    core.restore_layout(scene, baseline)
    assert core.config_equal(core.layout_dict(scene), baseline)
    manifest.update(status="SMOKE_ONLY" if os.environ.get("WFRL_FRAMING_SMOKE") == "1" else "MEASURED",
                    elapsed_s=time.perf_counter() - START,
                    candidate_count=len(candidates), geometry_accepted=sum(c["installation_status"] == "GEOMETRY_ACCEPTED" for c in candidates),
                    coarse_rank=[{k: r[k] for k in ("id", "qualified", "rank")} for r in sorted(results, key=lambda r: tuple(r["rank"]), reverse=True)],
                    finalists=[r["id"] for r in finalists], dense_sample_counts={r["id"]: len(r["samples"]) for r in dense},
                    baseline_restored_in_isolated_process=True, product_default_or_user_file_written=False)
    write("manifest.json", manifest)
    print("FRAMING_DONE", manifest["status"], manifest["elapsed_s"], flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "error.txt").write_text(traceback.format_exc())
        raise
