"""RGB/FBU and projected mesh checks using permitted reconstruction files only.

Hard CPU rasterization makes these few high-resolution checks inexpensive. The
optimizer retains its soft renderer. Both use the same CV camera, root motion
and half-pixel resize. No scoring truth, replay or original capture is read.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .renderer import scale_intrinsics


def _permitted(path):
    path = Path(path).resolve()
    if any(part in {"evaluation-only", "internal-capture"} for part in path.parts):
        raise ValueError("Projection diagnostics may not read internal capture or scoring truth")
    return path


def read_mesh_ply(path):
    """Read the ASCII triangular PLY emitted by the independent optimizer."""
    path = _permitted(path)
    with path.open(encoding="utf-8") as handle:
        if handle.readline().strip() != "ply" or handle.readline().strip() != "format ascii 1.0":
            raise ValueError("Projection check expects optimizer ASCII PLY")
        vertices_count = faces_count = None
        for line in handle:
            fields = line.strip().split()
            if fields[:2] == ["element", "vertex"]:
                vertices_count = int(fields[2])
            elif fields[:2] == ["element", "face"]:
                faces_count = int(fields[2])
            elif fields == ["end_header"]:
                break
        if vertices_count is None or faces_count is None:
            raise ValueError("Missing mesh element sizes")
        vertices = np.array([[float(value) for value in handle.readline().split()[:3]]
                             for _ in range(vertices_count)], dtype=float)
        faces = []
        for _ in range(faces_count):
            fields = [int(value) for value in handle.readline().split()]
            if len(fields) != 4 or fields[0] != 3:
                raise ValueError("Only triangular optimizer meshes are supported")
            faces.append(fields[1:])
    return vertices, np.asarray(faces, dtype=int)


def _clip_triangle_near(triangle, near):
    polygon = []
    for index in range(3):
        start, end = triangle[index], triangle[(index + 1) % 3]
        in_start, in_end = start[2] > near, end[2] > near
        if in_start:
            polygon.append(start)
        if in_start != in_end:
            polygon.append(start + (near - start[2]) / (end[2] - start[2]) * (end - start))
    return [np.stack((polygon[0], polygon[index], polygon[index + 1]))
            for index in range(1, len(polygon) - 1)]


def hard_silhouette(vertices, faces, K, camera, root, image_size, near_m=0.01):
    """Hard triangle union at integer pixel centres; image_size=(W,H)."""
    width, height = map(int, image_size)
    transform = np.asarray(camera) @ np.asarray(root)
    camera_vertices = np.asarray(vertices) @ transform[:3, :3].T + transform[:3, 3]
    mask = np.zeros((height, width), dtype=bool)
    for face in faces:
        triangle = camera_vertices[face]
        if np.all(triangle[:, 2] <= near_m):
            continue
        clipped = [triangle] if np.all(triangle[:, 2] > near_m) else _clip_triangle_near(triangle, near_m)
        for triangle in clipped:
            homogeneous = triangle @ np.asarray(K).T
            points = homogeneous[:, :2] / homogeneous[:, 2:3]
            edge = np.roll(points, -1, axis=0) - points
            area = edge[0, 0] * (-edge[2, 1]) - edge[0, 1] * (-edge[2, 0])
            if abs(area) < 1e-10:
                continue
            minimum, maximum = points.min(axis=0), points.max(axis=0)
            xmin, ymin = max(0, int(np.ceil(minimum[0]))), max(0, int(np.ceil(minimum[1])))
            xmax, ymax = min(width - 1, int(np.floor(maximum[0]))), min(height - 1, int(np.floor(maximum[1])))
            if xmin > xmax or ymin > ymax:
                continue
            yy, xx = np.mgrid[ymin:ymax + 1, xmin:xmax + 1]
            covered = np.ones(xx.shape, dtype=bool)
            orientation = 1 if area > 0 else -1
            for index in range(3):
                signed = edge[index, 0] * (yy - points[index, 1]) - edge[index, 1] * (xx - points[index, 0])
                covered &= orientation * signed >= -1e-9
            mask[ymin:ymax + 1, xmin:xmax + 1] |= covered
    return mask


def _boundary(mask):
    interior = mask.copy()
    interior[1:] &= mask[:-1]
    interior[:-1] &= mask[1:]
    interior[:, 1:] &= mask[:, :-1]
    interior[:, :-1] &= mask[:, 1:]
    return mask & ~interior


def _statistics(mask, labels):
    foreground, background, certain = labels == 1, labels == 0, labels != 2
    intersection = mask & foreground
    union = (mask | foreground) & certain
    return {
        "foreground_coverage_fraction": float(mask[foreground].mean()) if foreground.any() else None,
        "background_spill_fraction": float(mask[background].mean()) if background.any() else None,
        "masked_iou": float(intersection.sum() / union.sum()) if union.any() else None,
        "foreground_pixels": int(foreground.sum()), "background_pixels": int(background.sum()),
        "uncertain_pixels": int((labels == 2).sum()), "projected_pixels_in_image": int(mask.sum()),
    }


def generate_projection_checks(observations_file, model_dir, output_dir,
                               image_size=(480, 270), frame_ids=None):
    """Write RGB/FBU, initial and final projections for selected video frames.

    observations_file is the RGB-derived list accepted by reconstruct(). Each
    item may include rgb_path; otherwise its *_FBU.png sibling *_rgb.jpg is used.
    Returns the written contact-sheet path and per-frame hard-mask statistics.
    """
    observations_file, model_dir = _permitted(observations_file), _permitted(model_dir)
    observations = json.loads(observations_file.read_text(encoding="utf-8"))
    if frame_ids is not None:
        observations = [item for item in observations if int(item["frame_id"]) in set(frame_ids)]
    if not observations:
        raise ValueError("No selected RGB observations")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    meshes = {name: read_mesh_ply(model_dir / f"{name}.ply") for name in ("initial_template", "T1_B1")}
    width, height = image_size
    heading = 30
    contact = Image.new("RGB", (3 * width, len(observations) * (height + heading)), (20, 22, 26))
    contact_draw = ImageDraw.Draw(contact)
    statistics = []
    for index, observation in enumerate(observations):
        label_path = _permitted(observation["labels_path"])
        rgb_path = _permitted(observation.get("rgb_path", str(label_path).replace("_FBU.png", "_rgb.jpg")))
        with Image.open(label_path) as image:
            source_size = image.size
            labels = np.array(image.resize(image_size, Image.Resampling.NEAREST), copy=True)
        with Image.open(rgb_path) as image:
            rgb_size = image.size
            if abs(rgb_size[0] / rgb_size[1] - source_size[0] / source_size[1]) > 1e-6:
                raise ValueError("RGB thumbnail and FBU have different aspect ratios")
            rgb = np.array(image.convert("RGB").resize(image_size, Image.Resampling.BILINEAR), copy=True)
        if not np.isin(labels, [0, 1, 2]).all():
            raise ValueError("Unexpected F/B/U labels")
        calibration = scale_intrinsics(np.asarray(observation["K"], dtype=float), source_size, image_size)
        views = []
        annotated = rgb.copy()
        foreground, unknown = labels == 1, labels == 2
        annotated[foreground] = (0.65 * annotated[foreground] + 0.35 * np.array([30, 230, 70])).astype(np.uint8)
        annotated[unknown] = (0.85 * annotated[unknown] + 0.15 * np.array([180, 70, 220])).astype(np.uint8)
        views.append(annotated)
        item_statistics = {"camera_id": observation["camera_id"], "frame_id": observation["frame_id"],
                           "split": observation.get("split", "fit"), "sim_time_s": observation.get("sim_time_s"),
                           "resolution": list(image_size), "labels_source_size": list(source_size),
                           "rgb_saved_size": list(rgb_size)}
        for name, color in [("initial_template", [255, 165, 30]), ("T1_B1", [20, 225, 255])]:
            vertices, faces = meshes[name]
            mask = hard_silhouette(vertices, faces, calibration, observation["T_camera_cv_from_world"],
                                   observation["T_world_from_blade_root"], image_size)
            view = annotated.copy()
            view[_boundary(mask)] = color
            views.append(view)
            item_statistics[name] = _statistics(mask, labels)
        row_top = index * (height + heading)
        label = f"{observation['camera_id']} #{observation['frame_id']} {observation.get('split','fit')} t={observation.get('sim_time_s')}"
        for column, (view, title) in enumerate(zip(views, ["RGB F=green/U=purple", "Initial=orange", "Video fit=cyan"])):
            contact.paste(Image.fromarray(view), (column * width, row_top + heading))
            contact_draw.text((column * width + 6, row_top + 6), f"{label} | {title}", fill="white")
        separate = Image.new("RGB", (3 * width, height + heading), (20, 22, 26))
        separate.paste(contact.crop((0, row_top, 3 * width, row_top + height + heading)), (0, 0))
        separate.save(output_dir / f"{observation['camera_id']}_{int(observation['frame_id']):06d}_projection.jpg", quality=92)
        statistics.append(item_statistics)
    contact_path = output_dir / "projection_checks.jpg"
    contact.save(contact_path, quality=92)
    report = {"contact_sheet": str(contact_path.resolve()), "rasterizer": "hard_cpu_triangle_union_at_pixel_centres",
              "inputs": "optimizer PLY + RGB-derived FBU + declared calibration/root motion; no truth",
              "resize": "half_pixel_intrinsic_resize", "statistics": statistics,
              "limits": "hard low-resolution silhouettes are inspection metrics, not geometry scores; U excluded; frame clipping unconstrained"}
    (output_dir / "projection_checks.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inspect RGB/FBU versus independent initial/final projected meshes")
    parser.add_argument("observations_file")
    parser.add_argument("model_dir")
    parser.add_argument("output_dir")
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=270)
    parser.add_argument("--frame-ids", nargs="+", type=int)
    arguments = parser.parse_args()
    report = generate_projection_checks(arguments.observations_file, arguments.model_dir, arguments.output_dir,
                                        (arguments.width, arguments.height), arguments.frame_ids)
    print(json.dumps({"contact_sheet": report["contact_sheet"], "checked_observations": len(report["statistics"])}))
