"""Frozen-model RGB evidence audit; no truth, fitting, or scene renderer.

The three barycentric points per triangle are an area quadrature, not a claim
that a whole triangle was measured. Visibility uses two-sided exact ray/triangle
intersections against every triangle of the same model. Foreground overlap is
only projection consistency. It is never a reconstructed-surface percentage.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree


BARYCENTRIC = np.array([[2/3, 1/6, 1/6], [1/6, 2/3, 1/6], [1/6, 1/6, 2/3]])
STATES = ('behind_camera', 'outside_image', 'self_occluded', 'visible_U', 'visible_F', 'visible_B')


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_mesh(path):
    """Read the frozen ASCII triangle PLY format; reject other formats."""
    with Path(path).open() as handle:
        if handle.readline().strip() != 'ply' or handle.readline().strip() != 'format ascii 1.0':
            raise ValueError('Frozen audit expects ASCII PLY 1.0')
        nv = nf = None
        while True:
            line = handle.readline().strip()
            if not line:
                raise ValueError('Truncated PLY header')
            if line.startswith('element vertex '): nv = int(line.split()[-1])
            if line.startswith('element face '): nf = int(line.split()[-1])
            if line == 'end_header': break
        vertices = np.array([[float(x) for x in handle.readline().split()[:3]] for _ in range(nv)])
        rows = [[int(x) for x in handle.readline().split()] for _ in range(nf)]
    if any(len(row) != 4 or row[0] != 3 for row in rows):
        raise ValueError('Only triangle PLY faces are supported')
    faces = np.array([row[1:] for row in rows])
    if not np.isfinite(vertices).all() or faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError('Invalid frozen mesh')
    return vertices, faces


def quadrature(vertices, faces):
    tri = vertices[faces]
    areas = np.linalg.norm(np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]), axis=1)/2
    points = np.einsum('sv,fvc->fsc', BARYCENTRIC, tri).reshape(-1, 3)
    return points, np.repeat(areas/3, 3), np.repeat(np.arange(len(faces)), 3)


def first_blockers(points_camera, triangles_camera, tolerance_m=1e-6, chunk_size=128):
    """Find triangles strictly before each target using Moller-Trumbore rays.

    Rays start at camera origin, are normalized, and are double-sided. Return
    -1 for no strictly closer hit. Endpoint hits within tolerance_m are ignored,
    including the target's own triangle and shared-edge endpoint intersections.
    Coplanar grazing rays have no certified intersection and are not occluders.
    """
    points = np.asarray(points_camera, dtype=float)
    triangles = np.asarray(triangles_camera, dtype=float)
    result = np.full(len(points), -1, dtype=int)
    if not len(points) or not len(triangles): return result
    a = triangles[:, 0]
    e1, e2 = triangles[:, 1]-a, triangles[:, 2]-a
    origin_delta = -a
    q = np.cross(origin_delta, e1)
    numerator = np.einsum('ij,ij->i', e2, q)
    det_eps = 1e-12*np.maximum(np.linalg.norm(e1, axis=1)*np.linalg.norm(e2, axis=1), 1e-12)
    for start in range(0, len(points), chunk_size):
        p = points[start:start+chunk_size]
        distance = np.linalg.norm(p, axis=1)
        directions = p/np.maximum(distance[:, None], 1e-30)
        h = np.cross(directions[:, None], e2[None])
        determinant = np.einsum('fi,rfi->rf', e1, h)
        good_det = np.abs(determinant) > det_eps[None]
        inverse = np.divide(1., determinant, out=np.zeros_like(determinant), where=good_det)
        u = np.einsum('fi,rfi->rf', origin_delta, h)*inverse
        v = np.einsum('ri,fi->rf', directions, q)*inverse
        t = numerator[None]*inverse
        hits = (good_det & (u >= -1e-10) & (v >= -1e-10) & (u+v <= 1+1e-10)
                & (t > tolerance_m) & (t < distance[:, None]-tolerance_m))
        candidate = np.where(hits, t, np.inf)
        nearest = candidate.argmin(axis=1)
        result[start:start+len(p)] = np.where(hits.any(axis=1), nearest, -1)
    return result


def project(points_camera, K):
    homogeneous = points_camera@np.asarray(K).T
    with np.errstate(divide='ignore', invalid='ignore'):
        return homogeneous[:, :2]/homogeneous[:, 2, None]


def pixel_samples(uv, labels):
    height, width = labels.shape
    inside = (np.isfinite(uv).all(axis=1) & (uv[:, 0] >= 0) & (uv[:, 0] <= width-1)
              & (uv[:, 1] >= 0) & (uv[:, 1] <= height-1))
    pixel = np.full(len(uv), -1, dtype=int)
    xy = np.floor(uv[inside]+.5).astype(int)
    pixel[inside] = labels[xy[:, 1], xy[:, 0]]
    return inside, pixel


def inspect_points(points, vertices, faces, observation, labels, valid, contour_tree):
    transform = np.asarray(observation['T_camera_cv_from_world'])@np.asarray(observation['T_world_from_blade_root'])
    camera_vertices = vertices@transform[:3, :3].T+transform[:3, 3]
    camera_points = points@transform[:3, :3].T+transform[:3, 3]
    uv = project(camera_points, observation['K'])
    inside, pixel = pixel_samples(uv, labels)
    in_front = camera_points[:, 2] > .01
    blockers = first_blockers(camera_points, camera_vertices[faces])
    visible = inside & in_front & (blockers < 0)
    state = np.full(len(points), 3, dtype=int)
    state[pixel == 1] = 4
    state[pixel == 0] = 5
    state[blockers >= 0] = 2
    state[~inside] = 1
    state[~in_front] = 0
    valid_domain = np.zeros(len(points), dtype=bool)
    xy = np.floor(uv[inside]+.5).astype(int)
    valid_domain[inside] = valid[xy[:, 1], xy[:, 0]]
    nearest = np.full(len(points), np.nan)
    finite = np.isfinite(uv).all(axis=1)
    if contour_tree is not None: nearest[finite] = contour_tree.query(uv[finite])[0]
    return dict(uv=uv, pixel=pixel, visible=visible, state=state, blockers=blockers,
                valid_domain=valid_domain, contour_distance_px=nearest, camera_vertices=camera_vertices)


def edge_adjacency(faces):
    adjacency = {}
    for face_id, face in enumerate(faces):
        for j in range(3):
            adjacency.setdefault(tuple(sorted((int(face[j]), int(face[(j+1)%3])))), []).append(face_id)
    return adjacency


def image_segment_interval(projected, width, height):
    """Return clipped image-line fractions in [0,1], or None if outside."""
    lower, upper = 0., 1.
    a, b = projected
    for axis, bound in ((0, width-1), (1, height-1)):
        delta = b[axis]-a[axis]
        if abs(delta) < 1e-12:
            if a[axis] < 0 or a[axis] > bound: return None
            continue
        left, right = sorted((-a[axis]/delta, (bound-a[axis])/delta))
        lower, upper = max(lower, left), min(upper, right)
    return (lower, upper) if upper >= lower else None


def contour_associations(vertices, faces, observation, labels, valid, contour_tree, adjacency):
    """Sample front/back silhouette edges; exact rays remove hidden candidates.

    Eligibility requires visible silhouette, valid reviewed domain, and observed
    outline points. Within-uncertainty agreement is separate: points outside the
    deadband still constrain the model through an active residual. Both incident
    face IDs are adjacency only, including a potentially hidden side.
    """
    transform = np.asarray(observation['T_camera_cv_from_world'])@np.asarray(observation['T_world_from_blade_root'])
    camera = vertices@transform[:3, :3].T+transform[:3, 3]
    tri = camera[faces]
    normals = np.cross(tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0])
    facing = np.einsum('fi,fi->f', normals, tri.mean(axis=1)) < 0
    rows, incident, matched_incident = [], np.zeros(len(faces), dtype=bool), np.zeros(len(faces), dtype=bool)
    for edge, neighbors in adjacency.items():
        if len(neighbors) != 2 or facing[neighbors[0]] == facing[neighbors[1]]: continue
        end = camera[list(edge)]
        # None of the frozen model's audited edges crosses the camera plane.
        # A crossing is excluded explicitly, never fabricated into a contour.
        if (end[:, 2] <= .01).any():
            rows.append(dict(edge=f'{edge[0]}:{edge[1]}', face_ids=':'.join(map(str, neighbors)),
                             reason='near_plane_crossing_or_behind', eligible=0, within_annotation_uncertainty=0))
            continue
        projected = project(end, observation['K'])
        interval = image_segment_interval(projected, labels.shape[1], labels.shape[0])
        if interval is None:
            rows.append(dict(edge=f'{edge[0]}:{edge[1]}', face_ids=':'.join(map(str, neighbors)),
                             reason='edge_outside_image', eligible=0, within_annotation_uncertainty=0))
            continue
        length = np.linalg.norm(projected[1]-projected[0])*(interval[1]-interval[0])
        count = max(2, min(4096, int(np.ceil(length))+1))
        # Uniform image-space edge fractions converted to perspective-correct
        # 3D interpolation; sampling thus has <=1px spacing unless capped.
        s = np.linspace(*interval, count)
        t = s*end[0, 2]/((1-s)*end[1, 2]+s*end[0, 2])
        points = vertices[edge[0]][None]*(1-t[:, None])+vertices[edge[1]][None]*t[:, None]
        info = inspect_points(points, vertices, faces, observation, labels, valid, contour_tree)
        for i in range(count):
            status = STATES[info['state'][i]]
            if not info['visible'][i]: reason = status
            elif not info['valid_domain'][i]: reason = 'excluded_reviewed_contour_domain'
            elif not np.isfinite(info['contour_distance_px'][i]): reason = 'no_trusted_contour'
            else: reason = 'eligible_for_contour_constraint'
            eligible = reason == 'eligible_for_contour_constraint'
            matched = eligible and info['contour_distance_px'][i] <= observation['contour_uncertainty_px']
            if eligible: incident[neighbors] = True
            if matched: matched_incident[neighbors] = True
            rows.append(dict(edge=f'{edge[0]}:{edge[1]}', face_ids=':'.join(map(str, neighbors)),
                             edge_sample=i, z_m=points[i, 2], u_px=info['uv'][i, 0], v_px=info['uv'][i, 1],
                             pixel='BFU'[info['pixel'][i]] if info['pixel'][i] >= 0 else 'outside',
                             blocker_face=int(info['blockers'][i]), contour_valid=int(info['valid_domain'][i]),
                             contour_distance_px=info['contour_distance_px'][i], eligible=int(eligible),
                             within_annotation_uncertainty=int(matched), active_residual=int(eligible and not matched), reason=reason,
                             clipped_to_image=int(interval != (0., 1.)), spacing_cap_reached=int(count == 4096)))
    return rows, incident, matched_incident


def section_samples(vertices, faces, z):
    """Three equally weighted mid-stratum points per actual cut line segment."""
    points, weights, face_ids = [], [], []
    for face_id, tri in enumerate(vertices[faces]):
        hits = []
        for j in range(3):
            a, b = tri[j], tri[(j+1)%3]
            if abs(a[2]-b[2]) < 1e-12: continue
            t = (z-a[2])/(b[2]-a[2])
            if -1e-12 <= t <= 1+1e-12:
                p = a+np.clip(t, 0, 1)*(b-a)
                if not any(np.linalg.norm(p-q) < 1e-9 for q in hits): hits.append(p)
        if len(hits) != 2: continue
        a, b = hits
        length = np.linalg.norm(b-a)
        if length < 1e-10: continue
        # At a mesh ring, each side contains the same section edge. Keep the
        # triangle on the +z side, avoiding duplicated line-length denominator.
        if np.isclose(tri[:, 2], z, atol=1e-10).sum() == 2 and tri[:, 2].max() <= z+1e-10: continue
        for t in (1/6, .5, 5/6):
            points.append(a+t*(b-a)); weights.append(length/3); face_ids.append(face_id)
    return np.array(points).reshape(-1, 3), np.array(weights), np.array(face_ids, dtype=int)


def face_tags(faces, ring_samples, span_samples):
    tags = []
    for face in faces:
        levels = face//ring_samples
        if np.all(levels == 0): tag = 'root_cap'
        elif np.all(levels == span_samples-1): tag = 'tip_cap'
        else:
            phi = (face % ring_samples)*2*np.pi/ring_samples
            tag = 'positive_template_thickness_half' if np.sin(phi).mean() > 0 else 'negative_template_thickness_half'
        tags.append(tag)
    return np.array(tags)


def area_summary(states, weights, contour_incident, face_ids, observation_indices, sample_mask=None):
    mask = np.ones(len(weights), dtype=bool) if sample_mask is None else sample_mask
    values = states[observation_indices][:, mask]
    w = weights[mask]
    total = float(w.sum())
    result = {'denominator_m2': total, 'sample_count': int(mask.sum())}
    for name, flag in [('visible_F_any', (values == 4).any(axis=0)), ('visible_B_any', (values == 5).any(axis=0)),
                       ('visible_U_any', (values == 3).any(axis=0)), ('visible_any', (values >= 3).any(axis=0)),
                       ('visible_nonF_withoutF', (values >= 3).any(axis=0) & ~(values == 4).any(axis=0)),
                       ('never_model_visible', ~(values >= 3).any(axis=0)),
                       ('F_and_B_different_views', (values == 4).any(axis=0) & (values == 5).any(axis=0))]:
        area = float(w[flag].sum())
        result[name] = {'area_estimate_m2': area, 'fraction': area/total if total else None}
    face_flag = contour_incident[observation_indices].any(axis=0)
    result['faces_incident_to_eligible_contour_count'] = int(np.unique(face_ids[mask][face_flag[face_ids[mask]]]).size)
    return result


def section_summary(states, weights, indices):
    selected = states[indices]
    total = float(weights.sum())
    result = {name: float(weights[(selected == j).any(axis=0)].sum())/total
              for j, name in enumerate(STATES)}
    visible, foreground = (selected >= 3).any(axis=0), (selected == 4).any(axis=0)
    result.update(visible_any=float(weights[visible].sum())/total,
                  never_model_visible=float(weights[~visible].sum())/total,
                  visible_nonF_withoutF=float(weights[visible & ~foreground].sum())/total)
    return result


def dump_csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with gzip.open(path, 'wt', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)


def run_audit(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    observation_path = root/'observations/observations.json'
    observations = json.loads(observation_path.read_text())
    expected = {(c, f) for c in ('C1', 'C2', 'C3') for f in (42, 43, 44, 45)}
    if len(observations) != 12 or {(o['camera_id'], o['frame_id']) for o in observations} != expected:
        raise ValueError('Audit requires exactly the frozen 12 reviewed observations')
    if any(o['split'] != ('fit' if o['frame_id'] in (42, 43) else 'heldout') for o in observations):
        raise ValueError('Frozen frame split changed')
    model_paths = {'initial_template': 'reconstruction/initial_template.ply',
                   'trusted_contour_26': 'contour-ablation/T1_B1.ply',
                   'formal_reconstruction': 'reconstruction/T1_B1.ply'}
    freeze = json.loads((root/'model_freeze.json').read_text())
    scoring = json.loads((root/'scoring_config.json').read_text())
    input_paths = [observation_path, root/'observations/annotations.json', root/'model_freeze.json', root/'scoring_config.json']
    models = {}
    for name, relative in model_paths.items():
        path = root/relative
        if sha256(path) != freeze['sha256'][relative]: raise ValueError(f'Model freeze mismatch: {relative}')
        metadata_path = path.with_suffix('.json')
        if sha256(metadata_path) != freeze['sha256'][str(metadata_path.relative_to(root))]:
            raise ValueError('Model metadata freeze mismatch')
        vertices, faces = read_mesh(path)
        models[name] = (vertices, faces, json.loads(metadata_path.read_text()))
        input_paths.extend([path, metadata_path])
    branch_history_path = root/'contour-ablation/optimization.json'
    if sha256(branch_history_path) != freeze['sha256']['contour-ablation/optimization.json']:
        raise ValueError('Trusted contour branch optimization record freeze mismatch')
    if len(json.loads(branch_history_path.read_text())['video_history']) != 26:
        raise ValueError('Expected the frozen 26-iteration trusted contour branch')
    input_paths.append(branch_history_path)
    vertices0, faces0, metadata0 = models['initial_template']
    if any(not np.array_equal(faces0, value[1]) or len(value[0]) != len(vertices0) for value in models.values()):
        raise ValueError('All frozen models must share exact face IDs and topology')
    p0, weights0, face_ids = quadrature(vertices0, faces0)
    tags = face_tags(faces0, metadata0['ring_samples'], metadata0['span_samples'])
    adjacency = edge_adjacency(faces0)
    if any(len(n) != 2 for n in adjacency.values()): raise ValueError('Audit requires closed manifold mesh')
    caches = []
    for obs in observations:
        paths = [Path(obs[key]).resolve() for key in ('labels_path', 'contour_valid_path', 'contour_points_path')]
        if any(path.parent != observation_path.parent for path in paths): raise ValueError('Observation input outside reviewed directory')
        input_paths.extend(paths)
        labels, valid = np.asarray(Image.open(paths[0])), np.asarray(Image.open(paths[1]))
        contour = np.load(paths[2], allow_pickle=False)
        if labels.shape != (1080, 1920) or valid.shape != labels.shape or not np.isin(labels, (0, 1, 2)).all():
            raise ValueError('Expected native reviewed 1920x1080 F/B/U labels')
        if contour.ndim != 2 or contour.shape[1] != 2 or not np.isfinite(contour).all(): raise ValueError('Invalid reviewed contour')
        caches.append((labels, valid > 0, cKDTree(contour) if len(contour) else None))
    # Hash every actual data input before and after the run. No traversal of
    # evaluation-only paths, replay surfaces, or any truth artifact occurs.
    signatures = {str(path): sha256(path) for path in input_paths}
    signatures[str(Path(__file__).resolve())] = sha256(__file__)
    groups = {'fit': [i for i, o in enumerate(observations) if o['split'] == 'fit'],
              'heldout_nonblind_diagnostic': [i for i, o in enumerate(observations) if o['split'] == 'heldout'],
              'all_reviewed_nonblind_diagnostic': list(range(len(observations)))}
    all_states, all_incident, all_matched, all_rows, edge_rows, section_rows, summaries, section_summaries = {}, {}, {}, [], [], [], {}, {}
    for name, (vertices, faces, metadata) in models.items():
        print(f'Auditing {name}', flush=True)
        points, weights, ids = quadrature(vertices, faces)
        states = np.zeros((len(observations), len(points)), dtype=int)
        incident = np.zeros((len(observations), len(faces)), dtype=bool)
        matched_incident = np.zeros_like(incident)
        sections = {z: section_samples(vertices, faces, z) for z in scoring['section_positions_m']}
        section_states = {z: np.zeros((len(observations), len(value[0])), dtype=int) for z, value in sections.items()}
        frame_summaries = []
        for obs_index, (obs, cache) in enumerate(zip(observations, caches)):
            labels, valid, tree = cache
            context = {'model': name, 'camera': obs['camera_id'], 'frame': obs['frame_id'], 'split': obs['split'],
                       'evidence_role': 'fit' if obs['split'] == 'fit' else 'nonblind_diagnostic'}
            info = inspect_points(points, vertices, faces, obs, labels, valid, tree)
            states[obs_index] = info['state']
            edges, incident[obs_index], matched_incident[obs_index] = contour_associations(vertices, faces, obs, labels, valid, tree, adjacency)
            edge_rows.extend([{**context, **row} for row in edges])
            frame_summaries.append({**context, 'area_by_exclusive_state_m2': {s: float(weights[info['state'] == j].sum()) for j, s in enumerate(STATES)},
                                    'eligible_contour_samples': sum(row['eligible'] for row in edges),
                                    'contour_samples_within_annotation_uncertainty': sum(row['within_annotation_uncertainty'] for row in edges),
                                    'contour_samples_with_active_residual': sum(row.get('active_residual', 0) for row in edges),
                                    'contour_exclusion_counts': dict(Counter(row['reason'] for row in edges)),
                                    'eligible_incident_faces': int(incident[obs_index].sum()),
                                    'within_uncertainty_incident_faces': int(matched_incident[obs_index].sum())})
            for i, face in enumerate(ids):
                all_rows.append({**context, 'face_id': int(face), 'sample_id': i%3, 'z_m': points[i, 2], 'side': tags[face],
                    'area_weight_model_m2': weights[i], 'area_weight_initial_m2': weights0[i],
                    'u_px': info['uv'][i, 0], 'v_px': info['uv'][i, 1],
                    'pixel': 'BFU'[info['pixel'][i]] if info['pixel'][i] >= 0 else 'outside',
                    'model_visible': int(info['visible'][i]), 'blocker_face': int(info['blockers'][i]), 'state': STATES[info['state'][i]],
                    'contour_domain_valid': int(info['valid_domain'][i]), 'nearest_trusted_contour_px': info['contour_distance_px'][i],
                    'face_incident_eligible_contour': int(incident[obs_index, face]),
                    'face_incident_within_uncertainty_contour': int(matched_incident[obs_index, face]),
                    'fixed_priors': '|'.join(metadata['fixed_priors'])})
            for z, (sp, sw, sf) in sections.items():
                sinfo = inspect_points(sp, vertices, faces, obs, labels, valid, tree)
                section_states[z][obs_index] = sinfo['state']
                for i, face in enumerate(sf):
                    section_rows.append({**context, 'section_z_m': z, 'face_id': int(face), 'sample_id': i, 'side': tags[face],
                                         'line_weight_m': sw[i], 'u_px': sinfo['uv'][i, 0], 'v_px': sinfo['uv'][i, 1],
                                         'state': STATES[sinfo['state'][i]], 'blocker_face': int(sinfo['blockers'][i]),
                                         'pixel': 'BFU'[sinfo['pixel'][i]] if sinfo['pixel'][i] >= 0 else 'outside',
                                         'contour_domain_valid': int(sinfo['valid_domain'][i]),
                                         'nearest_trusted_contour_px': sinfo['contour_distance_px'][i],
                                         'face_incident_eligible_contour': int(incident[obs_index, face]),
                                         'face_incident_within_uncertainty_contour': int(matched_incident[obs_index, face])})
        all_states[name], all_incident[name], all_matched[name] = states, incident, matched_incident
        summaries[name] = {'mesh_area_m2': float(weights.sum()), 'frames': frame_summaries,
                           'groups': {group: area_summary(states, weights, incident, ids, indices) for group, indices in groups.items()},
                           'root_local_band_6.15_12.30_m': {group: area_summary(states, weights, incident, ids, indices,
                                  (points[:, 2] >= 6.15) & (points[:, 2] <= 12.30)) for group, indices in groups.items()},
                           'side_groups': {side: {group: area_summary(states, weights, incident, ids, indices, tags[ids] == side)
                                                for group, indices in groups.items()} for side in np.unique(tags)},
                           'contour_incident_faces_by_group': {group: {'eligible_count': int(incident[indices].any(axis=0).sum()),
                                                                     'within_uncertainty_count': int(matched_incident[indices].any(axis=0).sum())}
                                                              for group, indices in groups.items()},
                           'fixed_priors': metadata['fixed_priors']}
        section_summaries[name] = {}
        for z, (sp, sw, sf) in sections.items():
            section_summaries[name][str(z)] = {'denominator_section_perimeter_m': float(sw.sum()), 'sample_count': len(sw),
                'state_aggregation': 'any_selected_view_not_exclusive; visible_F + visible_nonF_withoutF + never_model_visible form a partition',
                'groups': {group: section_summary(section_states[z], sw, indices) for group, indices in groups.items()},
                'by_camera': {camera: {group: section_summary(section_states[z], sw,
                                     [i for i in indices if observations[i]['camera_id'] == camera])
                                   for group, indices in groups.items()} for camera in ('C1', 'C2', 'C3')}}
    names = list(models)
    disagreement_rows, disagreements = [], {}
    stacked = np.stack([all_states[n] for n in names])
    disagreement = (stacked != stacked[0:1]).any(axis=0)
    incident_stack = np.stack([all_incident[n] for n in names])
    contour_disagreement = (incident_stack != incident_stack[0:1]).any(axis=0)[:, face_ids]
    matched_stack = np.stack([all_matched[n] for n in names])
    matched_disagreement = (matched_stack != matched_stack[0:1]).any(axis=0)[:, face_ids]
    for obs_index, obs in enumerate(observations):
        for i, face in enumerate(face_ids):
            disagreement_rows.append({'camera': obs['camera_id'], 'frame': obs['frame_id'], 'split': obs['split'],
                                     'face_id': int(face), 'sample_id': i%3, 'z_m': p0[i, 2], 'side': tags[face],
                                     'initial_area_weight_m2': weights0[i], 'uncertain_model_disagreement': int(disagreement[obs_index, i]),
                                     'uncertain_contour_eligibility_disagreement': int(contour_disagreement[obs_index, i]),
                                     'uncertain_contour_agreement_disagreement': int(matched_disagreement[obs_index, i]),
                                     **{n: STATES[all_states[n][obs_index, i]] for n in names}})
    for group, indices in groups.items():
        flag = disagreement[indices].any(axis=0)
        disagreements[group] = {'reference_denominator_m2': float(weights0.sum()),
                               'any_state_disagreement_area_estimate_m2': float(weights0[flag].sum()),
                               'any_state_disagreement_fraction': float(weights0[flag].sum()/weights0.sum()),
                               'any_contour_eligibility_disagreement_incident_faces': int(np.unique(face_ids[contour_disagreement[indices].any(axis=0)]).size),
                               'any_contour_agreement_disagreement_incident_faces': int(np.unique(face_ids[matched_disagreement[indices].any(axis=0)]).size)}
    if any(sha256(path) != signature for path, signature in signatures.items()): raise RuntimeError('Input changed while auditing')
    report = {'schema': 'nrel-frozen-surface-rgb-evidence.v1', 'status': 'NONBLIND_DIAGNOSTIC_ONLY',
              'truth_read': False, 'model_fitting': False, 'model_files_unchanged': True,
              'observations': [{'camera': o['camera_id'], 'frame': o['frame_id'], 'split': o['split']} for o in observations],
              'mesh_vertices': len(vertices0), 'mesh_faces': len(faces0), 'same_topology_verified': True,
              'quadrature': {'barycentric_points': BARYCENTRIC.tolist(), 'samples_per_face': 3,
                             'area_denominator': 'whole closed model including root/tip caps; each own model triangle area/3',
                             'cross_model_denominator': 'initial_template triangle area/3 for same face/sample IDs',
                             'root_band': 'samples with 6.15 <= z <= 12.30; band boundary area is sampled, not exactly clipped'},
              'visibility': {'method': 'exact two-sided Moller-Trumbore camera-ray intersections against all same-model triangles',
                             'endpoint_tolerance_m': 1e-6, 'near_plane_m': .01,
                             'other_scene_objects': 'not loaded; external occlusion only represented by reviewed pixel/contour domains'},
              'pixel_sampling': 'image-center domain 0<=u<=W-1, 0<=v<=H-1 (same boundary policy as prior contour backend); nearest pixel floor(u+0.5)',
              'contour_association': {'eligible': 'front/back silhouette edge, native-image sample, unoccluded ray, reviewed valid contour domain and existing trusted outline',
                                      'within_annotation_uncertainty': 'eligible AND nearest trusted contour <=3 native pixels; deadband agreement, not the only effective constraint',
                                      'active_residual': 'eligible AND nearest contour >3px; contributes a nonzero directional contour residual',
                                      'incident_face_scope': 'topological adjacency only; both sides can be incident, not whole supported face',
                                      'sampling': 'image-clipped edges at <=1 native pixel spacing; no fit-time 300-sample cap; excluded whole edges have one reason marker row'},
              'side_labels': 'template theta half by sign of untwisted thickness x; not certified pressure/suction labels',
              'fixed_prior_metadata_semantics': 'Historical fixed_priors strings are preserved verbatim. section_thickness means fixed thickness-to-chord ratio; absolute thickness changes together with chord. Twist, hidden-surface parameterization and span remain priors.',
              'section_method': 'exact triangle-plane intersections, 3 line quadrature samples/segment; perimeter denominator, not area; individual states aggregate ANY selected view and overlap (e.g. all samples outside C3 can coexist with F visible in C1). visible_any/never_model_visible are complementary',
              'interpretation': ['F overlap means only RGB foreground projection consistency, not reconstructed geometry.',
                                 'B overlap is image inconsistency conditional on a fixed model and reviewed mask.',
                                 'U includes real occlusion/crop and generic uncertainty; independent contour validity can retain valid outline in U.',
                                 'Any cross-model state disagreement is uncertain; agreeing models still share the same restrictive prior.',
                                 'Group any-view categories overlap and their fractions do not sum to one.',
                                 'State disagreement area uses initial-template triangle area/3 and any selected camera/frame where the three fixed models disagree; it is not an unreconstructed fraction.',
                                 'Heldout images were previously viewed; no blind generalization or accuracy claim.',
                                 'Finite three-point quadrature can miss thin regions and does not certify exact area.',
                                 'Thickness-to-chord ratio, twist, hidden-surface shape, span and rounded sections remain priors even where F agrees; absolute thickness changes with chord.'],
              'models': summaries, 'sections': section_summaries, 'model_disagreement': disagreements,
              'input_sha256': signatures, 'command': ' '.join(sys.argv),
              'other_mp4_frame_screening': 'not part of this audit; no reviewed mask was extended'}
    dump_csv(output/'surface_samples.csv.gz', all_rows)
    dump_csv(output/'contour_edge_samples.csv.gz', edge_rows)
    dump_csv(output/'section_samples.csv.gz', section_rows)
    dump_csv(output/'model_disagreement.csv.gz', disagreement_rows)
    (output/'surface_audit.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps({'output': str(output), 'surface_rows': len(all_rows), 'contour_rows': len(edge_rows),
                      'section_rows': len(section_rows), 'disagreement': disagreements}, indent=2), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run_audit(args.root, args.output)


if __name__ == '__main__': main()
