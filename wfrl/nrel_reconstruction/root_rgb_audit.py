"""Read-only native RGB review of the frozen root-band contour support.

No segmentation, optimization, truth geometry, or original mask edits. Native
silhouette edges are clipped in 3D to the diagnostic band before image clipping;
this distinguishes cropped exterior contours from in-frame surface material.
"""
from __future__ import annotations

from .artifact_paths import relocated_path

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial import cKDTree
import torch

from .contour import _hard_union_at_points
from .dataset import iter_rgb
from .optimize import _prepare
from .parameter_audit import contour_diagnostic, restore_model
from .surface_audit import (STATES, edge_adjacency, image_segment_interval,
                            inspect_points, project, read_mesh, section_samples, sha256)

ROOT_BAND = (6.15, 12.30)
SECTION_Z = 9.225


def clip_span(endpoints, lower=ROOT_BAND[0], upper=ROOT_BAND[1]):
    """Clip an actual 3D edge to inclusive local-z bounds, or return None."""
    points = np.asarray(endpoints, dtype=float)
    if points.shape != (2, 3) or not np.isfinite(points).all() or lower > upper:
        raise ValueError('Finite 2x3 endpoints and ordered span bounds required')
    dz = points[1, 2] - points[0, 2]
    if abs(dz) < 1e-12:
        return points.copy() if lower <= points[0, 2] <= upper else None
    a, b = sorted(((lower-points[0, 2])/dz, (upper-points[0, 2])/dz))
    a, b = max(0., a), min(1., b)
    return np.stack((points[0]+a*(points[1]-points[0]), points[0]+b*(points[1]-points[0]))) if b > a else None


def perspective_points(endpoints, camera_endpoints, image_fraction):
    s = np.asarray(image_fraction)
    t = s*camera_endpoints[0, 2]/((1-s)*camera_endpoints[1, 2]+s*camera_endpoints[0, 2])
    return endpoints[0]+t[:, None]*(endpoints[1]-endpoints[0])


def cut_segments(vertices, faces, z):
    result = []
    for face in faces:
        tri = vertices[face]
        if np.isclose(tri[:, 2], z, atol=1e-10).sum() == 2 and tri[:, 2].max() <= z+1e-10:
            continue
        hits = []
        for j in range(3):
            a, b = tri[j], tri[(j+1) % 3]
            if abs(a[2]-b[2]) < 1e-12:
                continue
            t = (z-a[2])/(b[2]-a[2])
            if -1e-12 <= t <= 1+1e-12:
                point = a+np.clip(t, 0, 1)*(b-a)
                if not any(np.linalg.norm(point-other) < 1e-9 for other in hits):
                    hits.append(point)
        if len(hits) == 2 and np.linalg.norm(hits[1]-hits[0]) > 1e-10:
            result.append(hits)
    return np.asarray(result).reshape(-1, 2, 3)


def band_edges(vertices, faces, observation, labels, valid, observed):
    """Stage-by-stage exact-band projection, masks, ray and union-probe audit."""
    transform = np.asarray(observation['T_camera_cv_from_world']) @ np.asarray(observation['T_world_from_blade_root'])
    camera = vertices @ transform[:3, :3].T + transform[:3, 3]
    triangles = camera[faces]
    facing = np.einsum('fi,fi->f', np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0]), triangles.mean(axis=1)) < 0
    if (triangles[:, :, 2] <= .01).any():
        raise ValueError('This native cross-check requires the frozen all-in-front mesh')
    projected_triangles = project(triangles.reshape(-1, 3), observation['K']).reshape(-1, 3, 2)
    tree = cKDTree(observed) if len(observed) else None
    edges, rows = [], []
    width, height = labels.shape[1], labels.shape[0]
    for edge, neighbors in edge_adjacency(faces).items():
        if len(neighbors) != 2 or facing[neighbors[0]] == facing[neighbors[1]]:
            continue
        endpoints = clip_span(vertices[list(edge)])
        if endpoints is None:
            continue
        cp = endpoints @ transform[:3, :3].T + transform[:3, 3]
        uv = project(cp, observation['K'])
        interval = image_segment_interval(uv, width, height)
        row = {'edge': ':'.join(map(str, edge)), 'face_ids': ':'.join(map(str, neighbors)),
               'z0_m': float(endpoints[0, 2]), 'z1_m': float(endpoints[1, 2]),
               'u0_px': float(uv[0, 0]), 'v0_px': float(uv[0, 1]),
               'u1_px': float(uv[1, 0]), 'v1_px': float(uv[1, 1]),
               'projected_full_band_length_px': float(np.linalg.norm(uv[1]-uv[0])),
               'intersects_image': interval is not None, 'in_image_length_px': 0.}
        if interval is None:
            row['stage'] = 'outside_image_before_mask_or_visibility'
            edges.append(row)
            continue
        delta = uv[1]-uv[0]
        length = float(np.linalg.norm(delta))
        inside_length = length*(interval[1]-interval[0])
        count = max(2, math.ceil(inside_length)+1)
        s = np.linspace(*interval, count)
        points = perspective_points(endpoints, cp, s)
        info = inspect_points(points, vertices, faces, observation, labels, valid, tree)
        # Same three probe radii as the old backend, evaluated independently at
        # the native <=1px audit samples in float64. Not the fit sampling set.
        normal = np.array([-delta[1], delta[0]])/length
        probes = np.concatenate([info['uv']+radius*sign*normal for radius in (.35, .75, 1.5) for sign in (1, -1)])
        occupancy = _hard_union_at_points(torch.tensor(probes), torch.tensor(projected_triangles)).numpy().reshape(6, count)
        boundary = (occupancy[0] ^ occupancy[1]) | (occupancy[2] ^ occupancy[3]) | (occupancy[4] ^ occupancy[5])
        weights = np.full(count, inside_length/(count-1))
        weights[[0, -1]] *= .5
        for i, point in enumerate(points):
            reason = STATES[info['state'][i]] if not info['visible'][i] else ('eligible' if info['valid_domain'][i] else 'excluded_contour_domain')
            rows.append({'edge': row['edge'], 'sample': i, 'z_m': float(point[2]),
                         'u_px': float(info['uv'][i, 0]), 'v_px': float(info['uv'][i, 1]),
                         'projected_line_weight_px': float(weights[i]),
                         'FBU': 'BFU'[info['pixel'][i]] if info['pixel'][i] >= 0 else 'outside',
                         'ray_visible': bool(info['visible'][i]), 'blocker_face': int(info['blockers'][i]),
                         'independent_contour_valid': bool(info['valid_domain'][i]),
                         'old_union_probe_boundary': bool(boundary[i]),
                         'distance_to_saved_outline_px': float(info['contour_distance_px'][i]),
                         'reason': reason})
        row.update(stage='native_samples_audited', in_image_length_px=inside_length,
                   root_in_image_samples=count, root_ray_visible_samples=int(info['visible'].sum()),
                   root_ray_visible_domain_valid_samples=int((info['visible'] & info['valid_domain']).sum()))
        edges.append(row)
    return edges, rows


def summarize_native(edges, rows):
    def length(predicate):
        return sum(r['projected_line_weight_px'] for r in rows if predicate(r))
    return {'root_silhouette_edges': len(edges), 'root_edges_intersect_image': sum(e['intersects_image'] for e in edges),
            'root_projected_length_px': sum(e['projected_full_band_length_px'] for e in edges),
            'root_inside_image_length_px': sum(e['in_image_length_px'] for e in edges),
            'native_root_samples': len(rows), 'reason_counts': dict(Counter(r['reason'] for r in rows)),
            'native_ray_visible_length_px': length(lambda r: r['ray_visible']),
            'native_ray_visible_excluded_length_px': length(lambda r: r['ray_visible'] and not r['independent_contour_valid']),
            'native_ray_visible_eligible_length_px': length(lambda r: r['reason'] == 'eligible'),
            'native_ray_visible_domain_valid_but_probe_rejected_samples': sum(r['ray_visible'] and r['independent_contour_valid'] and not r['old_union_probe_boundary'] for r in rows),
            'native_ray_visible_U_and_independent_valid_samples': sum(r['ray_visible'] and r['FBU'] == 'U' and r['independent_contour_valid'] for r in rows)}


def font(size=23):
    for path in ('/System/Library/Fonts/Supplemental/Arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def native_overlay(rgb, obs, annotation, vertices, faces, edges, rows, observed, valid):
    image = Image.fromarray(rgb).convert('RGBA')
    wash = Image.new('RGBA', image.size, (0, 0, 0, 0)); draw = ImageDraw.Draw(wash)
    width, height = image.size
    for x0, y0, x1, y1 in annotation['excluded_boxes_normalized']:
        box = (int(x0*width), int(y0*height), int(x1*width)-1, int(y1*height)-1)
        draw.rectangle(box, fill=(245, 160, 35, 30), outline=(255, 174, 35, 235), width=3)
    # Saved contour-valid domain is rendered separately; the generic FBU U ring
    # is not shaded as excluded. This distinction is essential to the audit.
    ys, xs = np.nonzero(valid[1:, :] != valid[:-1, :])
    for x, y in zip(xs, ys): draw.point((int(x), int(y)+1), fill=(255, 160, 20, 255))
    ys, xs = np.nonzero(valid[:, 1:] != valid[:, :-1])
    for x, y in zip(xs, ys): draw.point((int(x)+1, int(y)), fill=(255, 160, 20, 255))
    image = Image.alpha_composite(image, wash).convert('RGB'); draw = ImageDraw.Draw(image)
    for x, y in observed:
        draw.ellipse((x-1, y-1, x+1, y+1), fill=(10, 210, 235))
    for row in rows:
        x, y = row['u_px'], row['v_px']
        color = (0, 215, 70) if row['reason'] == 'eligible' else ((190, 0, 240) if row['ray_visible'] else (125, 125, 125))
        draw.ellipse((x-1.4, y-1.4, x+1.4, y+1.4), fill=color)
    transform = np.asarray(obs['T_camera_cv_from_world']) @ np.asarray(obs['T_world_from_blade_root'])
    for z, color in ((6.15, (150, 215, 70)), (SECTION_Z, (70, 65, 235)), (12.3, (150, 215, 70))):
        segments = cut_segments(vertices, faces, z)
        camera = segments @ transform[:3, :3].T + transform[:3, 3]
        for segment in project(camera.reshape(-1, 3), obs['K']).reshape(-1, 2, 2):
            interval = image_segment_interval(segment, width, height)
            if interval is None: continue
            a, b = segment[0]+interval[0]*(segment[1]-segment[0]), segment[0]+interval[1]*(segment[1]-segment[0])
            # Dashes distinguish the entire cut section from visible silhouette.
            count = max(1, math.ceil(np.linalg.norm(b-a)/10))
            for j in range(0, count, 2):
                p, q = a+(b-a)*j/count, a+(b-a)*min(j+1, count)/count
                draw.line((tuple(p), tuple(q)), fill=color, width=2)
    # Keep the original 1920x1080 pixel field unobstructed. The caption occupies
    # an appended margin; all reported/crop coordinates remain native RGB.
    panel = Image.new('RGB', (width, height+125), (12, 20, 27)); panel.paste(image, (0, 0))
    draw = ImageDraw.Draw(panel)
    draw.text((25, height+12), f"{obs['camera_id']} #{obs['frame_id']} | native RGB region 1920 x 1080 | root z = 6.15-12.30 m | " + ('FIT' if obs['split'] == 'fit' else 'NONBLIND'), font=font(25), fill='white')
    draw.text((25, height+49), 'Cyan: saved RGB outline | bright green: eligible model root | violet: excluded model root | amber: saved review domain', font=font(23), fill='white')
    draw.text((25, height+84), 'Dashed cuts: green z=6.15 / 12.30; blue z=9.225 (whole cut, including hidden side). Red tip paint is original RGB.', font=font(23), fill='white')
    return panel


def padded_crop_evidence(rgb, obs, vertices, faces, edges, rows, overlay):
    """Show off-image root edges in a projection diagram; no generated RGB."""
    extents = np.array([[e['u0_px'], e['v0_px']] for e in edges]+[[e['u1_px'], e['v1_px']] for e in edges]+[[0, 0], [1919, 1079]])
    low = np.minimum(extents.min(0), [0, 0])-60
    high = np.maximum(extents.max(0), [1919, 1079])+60
    scale = min(1700/(high[0]-low[0]), 1080/(high[1]-low[1]))
    size = np.ceil((high-low)*scale).astype(int)
    canvas = Image.new('RGB', (int(size[0]), int(size[1])+90), (40, 46, 52)); draw = ImageDraw.Draw(canvas)
    origin = np.round(-low*scale).astype(int)+[0, 90]
    small = Image.fromarray(rgb).resize((round(1920*scale), round(1080*scale)), Image.Resampling.LANCZOS)
    canvas.paste(small, tuple(origin))
    draw.rectangle((int(origin[0]), int(origin[1]), int(origin[0]+small.width-1), int(origin[1]+small.height-1)), outline=(255, 200, 50), width=3)
    for edge in edges:
        a = (np.array([edge['u0_px'], edge['v0_px']])-low)*scale+[0, 90]
        b = (np.array([edge['u1_px'], edge['v1_px']])-low)*scale+[0, 90]
        draw.line((tuple(a), tuple(b)), fill=(250, 60, 165), width=3)
        draw.text(tuple((a+b)/2), f"{min(edge['z0_m'], edge['z1_m']):.2f}-{max(edge['z0_m'], edge['z1_m']):.2f}", fill='white', font=font(16))
    draw.text((20, 12), f"{obs['camera_id']} #{obs['frame_id']} | projected root-band silhouette / crop diagnosis", fill='white', font=font(26))
    draw.text((20, 48), 'Amber rectangle: actual image extent. Gray: NO RGB. Magenta: model edges (z metres).', fill='white', font=font(23))
    return canvas


def write_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def run(source, bundle, output):
    source, bundle, output = relocated_path(source), relocated_path(bundle), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output/'root_rgb_audit.json').exists():
        raise ValueError('Use a new output directory; existing audit report is immutable')
    if any(part in {'evaluation-only', 'internal-capture'} for path in (source, bundle, output) for part in path.parts):
        raise ValueError('No truth or internal-capture inputs permitted')
    observation_path, annotation_path = source/'observations/observations.json', source/'observations/annotations.json'
    observations = [o for o in json.loads(observation_path.read_text()) if o['camera_id'] in ('C1', 'C2') and o['frame_id'] in (42, 43, 44, 45)]
    if len(observations) != 8:
        raise ValueError('Requires exactly the eight fixed reviewed C1/C2 observations')
    annotations = {(a['camera_id'], a['frame_id']): a for a in json.loads(annotation_path.read_text())['annotations']}
    mesh_path, meta_path = source/'reconstruction/T1_B1.ply', source/'reconstruction/T1_B1.json'
    vertices, faces = read_mesh(mesh_path); model = restore_model(json.loads(meta_path.read_text()))
    if np.max(np.abs(vertices-model().numpy())) > 1e-7 or not np.array_equal(faces, model.faces.numpy()):
        raise ValueError('Frozen native model restoration mismatch')
    freeze_path = source/'model_freeze.json'; freeze = json.loads(freeze_path.read_text())['sha256']
    for path in (mesh_path, meta_path):
        if freeze[str(path.relative_to(source))] != sha256(path):
            raise ValueError('Frozen formal model signature mismatch')
    input_paths = [observation_path, annotation_path, mesh_path, meta_path, freeze_path]
    for obs in observations:
        for key in ('labels_path', 'contour_points_path', 'contour_valid_path'):
            path = relocated_path(obs[key])
            obs[key] = str(path)
            if path.parent != observation_path.parent:
                raise ValueError('Observation input outside permitted reviewed directory')
            input_paths.append(path)
    input_paths += [bundle/f'{camera}.mp4' for camera in ('C1', 'C2')]
    signatures = {str(path): sha256(path) for path in input_paths}
    rgb_frames, rgb_signatures = {}, []
    for camera in ('C1', 'C2'):
        for frame, rgb in enumerate(iter_rgb(bundle/f'{camera}.mp4', 1920, 1080)):
            if frame not in (42, 43, 44, 45): continue
            annotation = annotations[(camera, frame)]
            digest = hashlib.sha256(rgb.tobytes()).hexdigest()
            if digest != annotation['rgb_sha256'] or signatures[str(bundle/f'{camera}.mp4')] != annotation['source_video_sha256']:
                raise ValueError('RGB or MP4 signature differs from historical annotation')
            rgb_frames[(camera, frame)] = rgb
            path = output/f'{camera}_{frame:06d}_native_rgb.png'; Image.fromarray(rgb).save(path)
            rgb_signatures.append({'camera': camera, 'frame': frame, 'rgb_sha256': digest, 'matches_annotation': True, 'png_path': path.name, 'png_sha256': sha256(path)})
    all_edges, all_samples, summaries, backend = [], [], [], []
    torch.set_num_threads(1)
    for obs in observations:
        camera, frame = obs['camera_id'], obs['frame_id']; key = f'{camera}_{frame:06d}'
        print(f'Auditing native RGB {key}', flush=True)
        annotation, rgb = annotations[(camera, frame)], rgb_frames[(camera, frame)]
        labels = np.asarray(Image.open(obs['labels_path'])); valid = np.asarray(Image.open(obs['contour_valid_path'])) > 0
        observed = np.load(obs['contour_points_path'], allow_pickle=False)
        if labels.shape != (1080, 1920) or valid.shape != labels.shape:
            raise ValueError('Expected native 1920x1080 reviewed observations')
        edges, rows = band_edges(vertices, faces, obs, labels, valid, observed)
        context = {'camera': camera, 'frame': frame, 'role': 'fit' if obs['split'] == 'fit' else 'nonblind_diagnostic'}
        all_edges.extend([{**context, **row} for row in edges]); all_samples.extend([{**context, **row} for row in rows])
        summary = {**context, **summarize_native(edges, rows)}
        xy = np.floor(observed+.5).astype(int)
        summary['saved_observed_outline_count'] = len(observed)
        summary['saved_observed_outline_FBU_counts'] = dict(Counter('BFU'[x] for x in labels[xy[:, 1], xy[:, 0]]))
        summary['saved_observed_outline_in_independent_domain'] = int(valid[xy[:, 1], xy[:, 0]].sum())
        sp, sw, _ = section_samples(vertices, faces, SECTION_Z)
        info = inspect_points(sp, vertices, faces, obs, labels, valid, cKDTree(observed))
        summary['section_9.225m'] = {'samples': len(sp), 'in_image_samples': int(np.isin(info['state'], [2, 3, 4, 5]).sum()), 'visible_samples': int(info['visible'].sum()), 'state_counts': dict(Counter(STATES[x] for x in info['state'])), 'line_perimeter_m': float(sw.sum()), 'in_image_perimeter_fraction_estimate': float(sw[np.isin(info['state'], [2, 3, 4, 5])].sum()/sw.sum())}
        summaries.append(summary)
        for width in (640, 1920):
            size = (width, width*9//16)
            prepared = _prepare([obs], size, 'cpu', require_contours=True)[0]
            for dense in (False, True):
                result = contour_diagnostic(model, prepared, size, dense=dense)
                root = next(r for r in result['regions'] if r['region'] == 'root_band')
                backend.append({**context, 'width': width, 'sampling': 'extended_global_budget' if dense else 'historical_default', 'root_c2o_count': root['c2o']['count'], 'root_o2c_count': root['o2c']['count'], 'root_o2c_mean_native_px': root['o2c']['mean_native_px'], **result['counts']})
        overlay = native_overlay(rgb, obs, annotation, vertices, faces, edges, rows, observed, valid)
        overlay.save(output/f'{key}_native_overlay.png')
        padded_crop_evidence(rgb, obs, vertices, faces, edges, rows, overlay).save(output/f'{key}_crop_projection.jpg', quality=94)
        # Identical local crop windows for RGB and overlay preserve native pixel
        # scale. Windows describe review areas; they are not new annotations.
        crops = {'upper_boundary': (300, 110, 1250, 350), 'lower_boundary': (300, 770, 1250, 1010),
                 'root_upper_boundary': (1050, 110, 1700, 470),
                 'root_lower_boundary': (1050, 650, 1700, 1000)} if camera == 'C1' else {'entry_boundary': (0, 0, 850, 340), 'lower_entry': (0, 740, 850, 1080)}
        for name, box in crops.items():
            w, h = box[2]-box[0], box[3]-box[1]
            canvas = Image.new('RGB', (w, h*2+80), (14, 23, 31)); draw = ImageDraw.Draw(canvas)
            canvas.paste(Image.fromarray(rgb).crop(box), (0, 40)); canvas.paste(overlay.crop(box), (0, h+80))
            draw.text((10, 8), f'{key} {name} | original RGB | x={box[0]}:{box[2]}, y={box[1]}:{box[3]}', font=font(20), fill='white')
            draw.text((10, h+48), 'Same native pixels + saved domain / outline + model projection', font=font(20), fill='white')
            canvas.save(output/f'{key}_{name}_crop.png')
    unchanged = all(sha256(path) == digest for path, digest in signatures.items())
    if not unchanged: raise RuntimeError('An input changed during read-only RGB audit')
    report = {'schema': 'nrel-root-native-rgb-audit.v1', 'status': 'NONBLIND_DIAGNOSTIC_ONLY',
              'truth_read': False, 'fitting': False, 'segmentation_rerun': False, 'original_masks_unchanged': True,
              'root_band_m': ROOT_BAND, 'section_z_m': SECTION_Z, 'native_image_size': [1920, 1080],
              'method': 'Clip actual formal-v2 silhouette edges to root band in 3D, project, image-clip, sample <=1 native px, exact same-model camera rays, separate reviewed contour validity; no truth or other-object geometry.',
              'backend_crosscheck': 'Historical function runs unchanged at 640 and native with default/extended global sampling. Probe crosscheck in native sample CSV evaluates old union-probe radii on independent float64 <=1px samples; it is not the old fit sampling set.',
              'line_length_semantics': 'Image-clipped band edge length is analytic; subcategory projected length uses endpoint-trapezoid sample weights. Counts/lengths are contour support, never area or recovered geometry.',
              'section_semantics': 'Dashed curves display the complete fixed-z cut, including model-hidden portions. Section in-image counts/perimeter use the retained three-point-per-triangle-segment quadrature; surface visibility is not exterior-contour visibility.',
              'FBU_vs_contour_valid': 'Generic FBU uncertainty ring around an RGB edge is U. Saved independent contour-valid mask permits those RGB boundary points unless near explicit reviewed exclusion/crop domain. U alone does not remove trusted contour constraints.',
              'rgb_frames': rgb_signatures, 'frames': summaries, 'historical_backend': backend,
              'input_sha256': signatures, 'inputs_unchanged': unchanged,
              'source_sha256': {str(Path(__file__).resolve()): sha256(__file__)}}
    write_csv(output/'native_root_edges.csv', all_edges); write_csv(output/'native_root_samples.csv', all_samples)
    write_csv(output/'historical_backend_root.csv', backend)
    (output/'root_rgb_audit.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'frames': len(summaries), 'root_edges': len(all_edges), 'native_root_samples': len(all_samples), 'unchanged': unchanged}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True, type=Path)
    parser.add_argument('--bundle-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(); run(args.source_dir, args.bundle_dir, args.output_dir)


if __name__ == '__main__':
    main()
