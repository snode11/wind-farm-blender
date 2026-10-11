"""Gated RGB feature reprojection for the existing global rotor state.

Only a declared RGB observation sidecar is read.  A track is bound once by its
anchor pixel ray on the *reconstructed healthy* mesh, after the contour fit at
that anchor.  No material coordinates, source mesh, state, or evaluation frame
can be supplied to this module.  Binding remains a candidate, not a geometry
acceptance result.  Missing/rejected observations remain in every denominator.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np


SCHEMA = 'blade-texture-geometry.v1'
VISIBILITY_PROTOCOL = 'estimated_surface.v1'
LEGACY_VISIBILITY = 'positive_depth_only'
GATES = {'localization_sigma_px_max': 1.0, 'forward_backward_error_px_max': 1.0,
         'ncc_min': .8, 'contrast_gray_min': 12.0, 'identity_margin_min': 2.0,
         'effective_sigma_px_floor': .25}
ALLOWED_INPUTS = {'declared_rgb', 'derived_mask', 'camera_calibration', 'frame_t'}
FORBIDDEN_KEYS = {'truth', 'source_state', 'source_geometry', 'source_texture',
                  'object_id', 'dynamic_mesh', 'evaluation', 'state', 'geometry'}


def _forbidden_keys(value):
    if isinstance(value, dict):
        if FORBIDDEN_KEYS.intersection(value):
            raise ValueError('forbidden answer field in texture observations')
        for child in value.values():
            _forbidden_keys(child)
    elif isinstance(value, list):
        for child in value:
            _forbidden_keys(child)


def _pixel(value, nullable=False):
    if nullable and value is None:
        return None
    uv = np.asarray(value, float)
    if uv.shape != (2,) or not np.isfinite(uv).all():
        raise ValueError('texture observation requires a finite original pixel pair')
    return uv


def quality_reason(observation):
    """Recompute numeric gates; an asserted accept flag cannot bypass them."""
    if observation.get('uv') is None:
        return 'RGB_NOT_OBSERVED'
    if observation.get('accept') is False:
        return 'TRACKER_REJECTED:' + str(observation.get('status', 'UNSPECIFIED'))
    q = observation.get('quality', {})
    for name, threshold, direction in (
            ('localization_sigma_px', GATES['localization_sigma_px_max'], 'max'),
            ('forward_backward_error_px', GATES['forward_backward_error_px_max'], 'max'),
            ('ncc', GATES['ncc_min'], 'min'),
            ('contrast_gray', GATES['contrast_gray_min'], 'min'),
            ('identity_margin', GATES['identity_margin_min'], 'min')):
        try:
            if isinstance(q[name], bool):
                return 'INVALID_QUALITY:' + name
            value = float(q[name])
        except (KeyError, TypeError, ValueError):
            return 'MISSING_QUALITY:' + name
        if not np.isfinite(value) or (name != 'ncc' and value < 0):
            return 'INVALID_QUALITY:' + name
        if name == 'ncc' and not -1 <= value <= 1:
            return 'INVALID_QUALITY:ncc'
        if (direction == 'max' and value > threshold) or (direction == 'min' and value < threshold):
            return 'FAILED_QUALITY:' + name
    return None


def support_reason(uv, mask, known):
    """Check declared foreground/known support at an original subpixel center."""
    H, W = mask.shape
    u, v = uv
    if not 0 <= u <= W - 1 or not 0 <= v <= H - 1:
        return 'OUTSIDE_DECLARED_IMAGE'
    xs = [int(np.floor(u)), int(np.ceil(u))]
    ys = [int(np.floor(v)), int(np.ceil(v))]
    if known is not None and not np.asarray(known)[np.ix_(ys, xs)].all():
        return 'UNKNOWN_CONTOUR_DOMAIN'
    # An RGB edge at an antialias pixel may straddle the derived binary mask.
    # Require at least one of its four actual neighboring foreground centers;
    # do not dilate, fill, or infer foreground across an unknown domain.
    if not np.asarray(mask)[np.ix_(ys, xs)].any():
        return 'OUTSIDE_DERIVED_FOREGROUND'
    return None


def ray_binding(cam, vertices, faces, uv):
    """Exact subpixel ray/triangle intersection on the estimated healthy mesh."""
    triangles = np.asarray(vertices).reshape(3, -1, 3)[:, faces].reshape(-1, 3, 3)
    d_cv = np.linalg.solve(cam.K, np.r_[uv, 1.0])
    # Blender's saved R is accepted within the camera contract's 1e-6
    # orthogonality tolerance. R.T is only an approximate inverse at that
    # precision; using it here while project() uses R produced ~1e-3 pixel
    # loop errors and rejected every actual anchor at the unchanged 1e-5 gate.
    # Solve the declared transform exactly; no model/source information enters.
    center = np.linalg.solve(cam.R, -cam.t)
    direction = np.linalg.solve(cam.R, d_cv)
    direction /= np.linalg.norm(direction)
    a = triangles[:, 0]
    e1, e2 = triangles[:, 1] - a, triangles[:, 2] - a
    h = np.cross(np.broadcast_to(direction, e2.shape), e2)
    determinant = np.einsum('ij,ij->i', e1, h)
    ok = np.abs(determinant) > 1e-12
    inv = np.divide(1., determinant, out=np.zeros_like(determinant), where=ok)
    s = center - a
    u = np.einsum('ij,ij->i', s, h) * inv
    q = np.cross(s, e1)
    v = (q @ direction) * inv
    distance = np.einsum('ij,ij->i', e2, q) * inv
    ok &= (u >= -1e-10) & (v >= -1e-10) & (u + v <= 1 + 1e-10) & (distance > .05)
    if not ok.any():
        return None
    index = int(np.argmin(np.where(ok, distance, np.inf)))
    bary = np.array([1 - u[index] - v[index], u[index], v[index]])
    point = bary @ triangles[index]
    projected, depth = cam.project(point[None])
    if depth[0] <= .05 or np.linalg.norm(projected[0] - uv) > 1e-5:
        return None
    return {'blade_0based': index // len(faces), 'face': index % len(faces),
            'barycentric': bary.tolist(), 'anchor_uv': uv.tolist(),
            'binding_method': 'exact inverse declared camera transform, subpixel RGB ray -> closest visible estimated healthy triangle',
            'status': 'CANDIDATE_FIXED_TRIANGLE_BINDING'}


def binding_visibility(cam, vertices, faces, binding):
    """Check the fixed material point against the closest estimated surface.

    The point is evaluated on its original blade/face/barycentric coordinates;
    it is never rebound to the triangle currently visible at its pixel. Rays
    test both sides of *all* three blades, so back surfaces remain occluders.
    Only a coincident face sharing the point's topological edge/vertex may
    substitute for the bound face. Coincident other blades are ambiguous.

    Structured blade lofts use section centers to orient each face outward,
    including the tip cap. Flat (3, vertex, 3) fixtures must declare outward
    face winding. Tolerances below cover floating point intersection error in
    model meters; they are not image/acceptance or mask-support thresholds.
    """
    result = {'visible': False, 'reason': 'INVALID_FIXED_BINDING',
              'protocol': VISIBILITY_PROTOCOL, 'predicted_uv': None, 'depth': None,
              'target_distance': None, 'nearest_distance': None,
              'distance_gap': None, 'tolerance_m': None,
              'nearest_blade_0based': None, 'nearest_face': None,
              'front_facing_cosine': None}
    V, F = np.asarray(vertices, float), np.asarray(faces, int)
    if (V.ndim not in (3, 4) or V.shape[0] != 3 or V.shape[-1] != 3 or
            not np.isfinite(V).all() or F.ndim != 2 or F.shape[1] != 3 or not len(F)):
        return result
    flat = V.reshape(3, -1, 3)
    if (F < 0).any() or (F >= flat.shape[1]).any():
        return result
    try:
        blade, face = binding['blade_0based'], binding['face']
        bary = np.asarray(binding['barycentric'], float)
        if (isinstance(blade, bool) or isinstance(face, bool) or
                not isinstance(blade, (int, np.integer)) or not isinstance(face, (int, np.integer)) or
                not 0 <= blade < 3 or not 0 <= face < len(F) or bary.shape != (3,) or
                not np.isfinite(bary).all() or (bary < -1e-10).any() or
                not np.isclose(bary.sum(), 1., atol=1e-10, rtol=0)):
            return result
    except (KeyError, TypeError, ValueError):
        return result
    triangles = flat[:, F].reshape(-1, 3, 3)
    target_index = blade * len(F) + face
    point = bary @ triangles[target_index]
    uv, depth = cam.project(point[None])
    result['depth'] = float(depth[0]) if np.isfinite(depth[0]) else None
    if depth[0] <= .05 or not np.isfinite(uv).all():
        result['reason'] = 'INVALID_DEPTH'
        return result
    result['predicted_uv'] = uv[0].tolist()
    try:
        # Match project() exactly even for permitted rounded camera R values.
        center = np.linalg.solve(cam.R, -cam.t)
    except np.linalg.LinAlgError:
        result['reason'] = 'INVALID_CAMERA_TRANSFORM'
        return result
    direction = point - center
    target_distance = float(np.linalg.norm(direction))
    if not np.isfinite(target_distance) or target_distance <= 0:
        result['reason'] = 'INVALID_CAMERA_RAY'
        return result
    direction /= target_distance
    extent = float(np.max(np.ptp(flat.reshape(-1, 3), axis=0)))
    tolerance = max(1e-8, target_distance * 1e-9, extent * 1e-10)
    result.update(target_distance=target_distance, tolerance_m=tolerance)

    a = triangles[:, 0]
    e1, e2 = triangles[:, 1] - a, triangles[:, 2] - a
    normals = np.cross(e1, e2)
    normal_length = np.linalg.norm(normals, axis=1)
    if normal_length[target_index] <= 1e-12:
        result['reason'] = 'DEGENERATE_BOUND_FACE'
        return result
    if V.ndim == 4:
        sections, ring = V.shape[1:3]
        centers = V.mean(axis=2)
        section_indices = F // ring
        outward = (V - centers[:, :, None, :]).reshape(3, -1, 3)[:, F].mean(axis=2)
        cap = (section_indices == sections - 1).all(axis=1)
        if sections > 1:
            outward[:, cap] = (centers[:, -1] - centers[:, -2])[:, None, :]
            root_cap = (section_indices == 0).all(axis=1)
            outward[:, root_cap] = (centers[:, 0] - centers[:, 1])[:, None, :]
        outward = outward.reshape(-1, 3)
        normals *= np.where(np.einsum('ij,ij->i', normals, outward) < 0, -1., 1.)[:, None]
    facing = -normals @ direction / np.maximum(normal_length, 1e-300)
    result['front_facing_cosine'] = float(facing[target_index])

    h = np.cross(np.broadcast_to(direction, e2.shape), e2)
    determinant = np.einsum('ij,ij->i', e1, h)
    valid = np.abs(determinant) > 1e-12 * np.maximum(normal_length, 1.)
    inv = np.divide(1., determinant, out=np.zeros_like(determinant), where=valid)
    s = center - a
    u = np.einsum('ij,ij->i', s, h) * inv
    q = np.cross(s, e1)
    v = (q @ direction) * inv
    distance = np.einsum('ij,ij->i', e2, q) * inv
    valid &= (u >= -1e-10) & (v >= -1e-10) & (u + v <= 1. + 1e-10) & (distance > 0)
    if not valid.any():
        result['reason'] = 'NO_ESTIMATED_SURFACE_INTERSECTION'
        return result
    nearest = int(np.argmin(np.where(valid, distance, np.inf)))
    result.update(nearest_distance=float(distance[nearest]),
                  distance_gap=float(target_distance - distance[nearest]),
                  nearest_blade_0based=nearest // len(F), nearest_face=nearest % len(F))
    nearer = valid & (distance < target_distance - tolerance)
    if nearer.any():
        result['reason'] = ('OCCLUDED_BY_OTHER_BLADE' if nearest // len(F) != blade
                            else 'SELF_OCCLUDED')
        return result
    coincident = valid & (np.abs(distance - target_distance) <= tolerance)
    if np.any(coincident & (np.arange(len(triangles)) // len(F) != blade)):
        result['reason'] = 'AMBIGUOUS_OTHER_BLADE_SURFACE'
        return result
    # At a shared edge/vertex the ray may numerically select a neighboring
    # triangle. Interior points require the original face, never a nearby one.
    support = F[face][bary > 1e-9]
    adjacent = (support[:, None, None] == F[None]).any(axis=2).all(axis=0) if len(support) else np.zeros(len(F), bool)
    compatible = np.zeros(len(triangles), bool)
    compatible[blade * len(F):(blade + 1) * len(F)] = adjacent
    visible = coincident & compatible & (facing > 1e-10)
    if not visible.any():
        result['reason'] = ('BACK_FACING_BOUND_SURFACE' if facing[target_index] <= 1e-10
                            else 'FIXED_FACE_NOT_FRONTMOST')
        return result
    result['visible'] = True
    result['reason'] = ('VISIBLE_FIXED_FACE' if visible[target_index] else 'VISIBLE_ADJACENT_EDGE_OR_VERTEX')
    return result


def image_information(J, scales, names, active=None):
    """Image-only local Jacobian rank, including unavailable state directions."""
    scales = np.asarray(scales, float)
    active = np.ones(len(names), bool) if active is None else np.asarray(active, bool)
    J = np.asarray(J, float).reshape(-1, int(active.sum()))
    singular, vh = np.empty(0), np.empty((0, int(active.sum())))
    if J.size:
        _, singular, vh = np.linalg.svd(J * scales[active][None], full_matrices=False)
    threshold = max(float(singular[0]) * 1e-7, 1e-9) if len(singular) else 1e-9
    rank = int((singular > threshold).sum())
    identified = np.zeros(len(names), bool)
    null_energy = np.ones(len(names))
    if rank:
        null_energy[active] = np.clip(1. - (vh[:rank] ** 2).sum(0), 0., 1.)
        identified[active] = null_energy[active] < 1e-8
    return {'rank': rank, 'row_count': len(J), 'active_state_names': list(np.asarray(names)[active]),
            'singular_values_scaled': singular.tolist(), 'rank_threshold': threshold,
            'identified': dict(zip(names, identified.tolist())),
            'nullspace_energy': dict(zip(names, null_energy.tolist())),
            'scope': 'local image Jacobian; no priors; does not establish geometric accuracy'}


class FrameFeatures:
    """A fixed-size frame objective; quality/identity selection never changes inside solve."""
    def __init__(self, entries, decisions, cams_full, cams_fit, faces, weight,
                 visibility=LEGACY_VISIBILITY):
        self.entries, self.decisions = entries, decisions
        self.cams_full, self.cams_fit, self.faces = cams_full, cams_fit, faces
        self.weight = float(weight)
        self.count = len(entries)
        if visibility not in (LEGACY_VISIBILITY, VISIBILITY_PROTOCOL):
            raise ValueError('unknown fixed binding visibility protocol')
        self.visibility = visibility

    def residuals(self, vertices):
        values = []
        for entry in self.entries:
            binding, slot = entry['binding'], entry['camera_index']
            tri = vertices[binding['blade_0based']].reshape(-1, 3)[self.faces[binding['face']]]
            point = np.asarray(binding['barycentric']) @ tri
            cam, full = self.cams_fit[slot], self.cams_full[slot]
            uv, depth = cam.project(point[None])
            scale = np.array([cam.W / full.W, cam.H / full.H])
            target = (entry['uv'] + .5) * scale - .5
            delta = uv[0] - target if depth[0] > .05 and np.isfinite(uv).all() else np.full(2, 30.)
            if self.visibility == VISIBILITY_PROTOCOL:
                visible = binding_visibility(full, vertices, self.faces, binding)['visible']
                if not visible:
                    # Keep both residual rows and their declared weight. A
                    # hidden fixed material point is not zero image error.
                    delta = np.full(2, 30.) * scale
            sigma = max(float(entry['quality']['localization_sigma_px']), GATES['effective_sigma_px_floor'])
            # Sigma is measured in original pixels. Transform it by the same
            # center-aware scale as delta, preserving the uncertainty-normalized
            # feature objective under --fit-scale (raw scores remain original).
            values.append(delta * self.weight / (sigma * scale))
        return np.concatenate(values) if values else np.empty(0)

    def summary(self, vertices):
        errors, visible_errors, rows = [], [], []
        visibility_counts = Counter()
        for entry in self.entries:
            binding, slot = entry['binding'], entry['camera_index']
            tri = vertices[binding['blade_0based']].reshape(-1, 3)[self.faces[binding['face']]]
            point = np.asarray(binding['barycentric']) @ tri
            uv, depth = self.cams_full[slot].project(point[None])
            finite = bool(depth[0] > .05 and np.isfinite(uv).all())
            error = float(np.linalg.norm(uv[0] - entry['uv'])) if finite else None
            if finite:
                errors.append(error)
            visibility = (binding_visibility(self.cams_full[slot], vertices, self.faces, binding)
                          if self.visibility == VISIBILITY_PROTOCOL else None)
            if visibility is not None:
                visibility_counts[visibility['reason']] += 1
                if visibility['visible'] and finite:
                    visible_errors.append(error)
            rows.append({'id': entry['id'], 'camera': self.cams_full[slot].name,
                         'observed_uv': entry['uv'].tolist(), 'predicted_uv': uv[0].tolist() if finite else None,
                         'original_pixel_error': error,
                         'projection_status': visibility['reason'] if visibility is not None else
                            ('POSITIVE_DEPTH_ONLY' if finite else 'INVALID_DEPTH'),
                         'binding_visibility': visibility})
        r = self.residuals(vertices)
        transformed = r * np.sqrt(2. / (np.sqrt(1. + (r / 2.) ** 2) + 1.))
        counts = Counter(d['reason'] for d in self.decisions)
        return {'enabled': True, 'status': 'ACTIVE_CANDIDATE' if self.count else 'NO_ACCEPTED_TARGETS',
                'planned_observations': len(self.decisions), 'used_observations': self.count,
                'missing_or_rejected_observations': len(self.decisions) - self.count,
                'decision_counts': dict(counts), 'decisions': self.decisions,
                'original_pixel_error_count': len(errors),
                'original_pixel_error_rms': float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
                'original_pixel_error_p95': float(np.percentile(errors, 95)) if errors else None,
                'binding_visibility_protocol': self.visibility,
                'visibility_counts': dict(visibility_counts),
                'visible_observations': len(visible_errors) if self.visibility == VISIBILITY_PROTOCOL else None,
                'occluded_or_invalid_observations': self.count - len(visible_errors) if self.visibility == VISIBILITY_PROTOCOL else None,
                'visible_original_pixel_error_count': len(visible_errors),
                'visible_original_pixel_error_rms': float(np.sqrt(np.mean(np.square(visible_errors)))) if visible_errors else None,
                'visible_original_pixel_error_p95': float(np.percentile(visible_errors, 95)) if visible_errors else None,
                'weighted_robust_cost': float(.5 * transformed @ transformed),
                'reprojection': rows, 'acceptance': 'NOT_EVALUATED',
                'visibility_contract': ('closest estimated surface of all three blades; fixed blade/face/barycentric; '
                    'front face or coincident topological edge/vertex; occluded rows retain 30 original px penalty'
                    if self.visibility == VISIBILITY_PROTOCOL else
                    'RGB support and positive depth only; geometric self-occlusion is not accepted here'),
                'residual_scale_contract': 'original-pixel sigma scaled with image delta; sigma floor 0.25 px'}


class TextureGeometry:
    """Validated fit-only observations, immutable bindings, and complete audit state."""
    def __init__(self, path, inputs, rotor, weight=1.0):
        path = Path(path).resolve()
        if any(p.lower() == 'evaluation' or p.lower().startswith('truth') or
               'object_id' in p.lower() or p.lower().startswith('source-surface') for p in path.parts):
            raise ValueError('forbidden texture observation sidecar path')
        if inputs.bundle is None:
            raise ValueError('texture geometry requires an explicit declared input bundle')
        if not np.isfinite(weight) or weight < 0:
            raise ValueError('texture weight must be finite and nonnegative')
        content = path.read_bytes()
        data = json.loads(content)
        _forbidden_keys(data)
        if data.get('schema') != SCHEMA or data.get('pixel_coordinates') != 'original_image_pixel_centers':
            raise ValueError('texture observation schema/pixel convention mismatch')
        self.visibility = data.get('binding_visibility', LEGACY_VISIBILITY)
        if self.visibility not in (LEGACY_VISIBILITY, VISIBILITY_PROTOCOL):
            raise ValueError('unknown fixed binding visibility protocol')
        provenance = data.get('provenance', {})
        declared = set(provenance.get('allowed_inputs', []))
        if (provenance.get('role') != 'solver_rgb_observations' or not provenance.get('method') or
                'declared_rgb' not in declared or not declared.issubset(ALLOWED_INPUTS)):
            raise ValueError('texture observation provenance requires permitted RGB-only inputs')
        contexts = {}
        hashes = {a['path']: a['sha256'] for a in inputs.access_log if a.get('role') == 'RGB'}
        for record in inputs.bundle.manifest['frames']:
            if record['frame'] not in inputs.bundle.frame_ids:
                continue
            for cam in record['cameras']:
                contexts[(record['frame'], cam['name'])] = (float(record['t']), hashes[cam['rgb']])
        self.frames, self.tracks, self.bindings, self.binding_decisions = {}, [], {}, []
        self.rotor, self.faces, self.weight = rotor, rotor.tpl.faces(), float(weight)
        self.access_log = [{'path': str(path), 'role': 'solver_rgb_observations',
                            'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}]
        self.provenance = provenance
        identifiers = set()
        for track in data.get('tracks', []):
            identifier = track.get('id')
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise ValueError('texture track IDs must be unique nonempty strings')
            identifiers.add(identifier)
            anchor = track['anchor']; _pixel(anchor['uv'])
            observations = track.get('observations', [])
            keys = set()
            for item in [anchor] + observations:
                frame, camera = item.get('frame'), item.get('camera')
                if isinstance(frame, bool) or not isinstance(frame, int) or (frame, camera) not in contexts:
                    raise ValueError('texture observation references an undeclared fit frame/camera')
                expected_t, expected_hash = contexts[(frame, camera)]
                if not np.isclose(float(item['t']), expected_t, rtol=0, atol=1e-7):
                    raise ValueError('texture observation timestamp identity mismatch')
                if item.get('rgb_sha256') != expected_hash:
                    raise ValueError('texture observation RGB identity mismatch')
                if float(item['t']) < float(anchor['t']):
                    raise ValueError('texture target precedes its immutable anchor')
                _pixel(item.get('uv'), nullable=item is not anchor)
                if item is not anchor:
                    key = (frame, camera)
                    if key in keys:
                        raise ValueError('duplicate texture track observation frame/camera')
                    keys.add(key)
                    self.frames.setdefault(frame, []).append((track, item))
            self.tracks.append(track)

    def _support(self, record, item):
        slot = next(i for i, c in enumerate(record.cameras) if c.name == item['camera'])
        known = None if record.known_contours is None else record.known_contours[slot]
        return slot, support_reason(_pixel(item['uv']), record.masks[slot], known)

    def prepare(self, record, cams_fit):
        entries, decisions = [], []
        for track, item in self.frames.get(record.frame, []):
            reason = quality_reason(item)
            anchor = track['anchor']
            if reason is None:
                slot, reason = self._support(record, item)
            if reason is None and record.t == anchor['t'] and item['camera'] == anchor['camera']:
                reason = 'ANCHOR_SELF_OBSERVATION'
            if reason is None and track['id'] not in self.bindings:
                reason = 'NO_FIXED_ANCHOR_BINDING'
            if reason is None and self.weight == 0:
                reason = 'WEIGHT_ZERO_ABLATION'
            if reason is None:
                entries.append({'id': track['id'], 'uv': _pixel(item['uv']),
                                'quality': item['quality'], 'camera_index': slot,
                                'binding': self.bindings[track['id']]})
            decisions.append({'id': track['id'], 'camera': item['camera'],
                              'reason': reason or 'USED', 'tracker_status': item.get('status')})
        return FrameFeatures(entries, decisions, record.cameras, cams_fit, self.faces, self.weight,
                             visibility=self.visibility)

    def bind_anchors(self, record, state, geometry_valid=True):
        vertices = None
        for track in self.tracks:
            anchor = track['anchor']
            if anchor['frame'] != record.frame:
                continue
            identifier = track['id']
            if identifier in self.bindings or any(d['id'] == identifier for d in self.binding_decisions):
                raise ValueError('texture track anchor binding cannot be reset')
            anchor_obs = next((o for o in track['observations'] if o['frame'] == anchor['frame']
                               and o['camera'] == anchor['camera']), None)
            reason = 'MISSING_ANCHOR_QUALITY' if anchor_obs is None else quality_reason(anchor_obs)
            if not geometry_valid:
                reason = 'ANCHOR_GEOMETRY_UNAVAILABLE'
            if anchor_obs is not None and anchor_obs['uv'] is not None and not np.allclose(anchor_obs['uv'], anchor['uv'], atol=1e-7, rtol=0):
                reason = 'ANCHOR_PIXEL_IDENTITY_MISMATCH'
            if reason is None:
                slot, reason = self._support(record, anchor)
            binding = None
            if reason is None:
                if vertices is None:
                    vertices = self.rotor.forward(state)[0]
                binding = ray_binding(record.cameras[slot], vertices, self.faces, _pixel(anchor['uv']))
                if binding is None:
                    reason = 'NO_VISIBLE_ESTIMATED_ANCHOR_TRIANGLE'
                elif self.visibility == VISIBILITY_PROTOCOL:
                    visibility = binding_visibility(record.cameras[slot], vertices, self.faces, binding)
                    if not visibility['visible']:
                        reason = 'ANCHOR_ESTIMATED_SURFACE:' + visibility['reason']
                        binding = None
            if binding is not None:
                self.bindings[identifier] = binding
            self.binding_decisions.append({'id': identifier, 'anchor': anchor,
                'reason': reason or 'BOUND_ONCE', 'binding': binding})

    def audit(self):
        return {'schema': SCHEMA, 'enabled': True, 'weight': self.weight, 'gates': GATES,
                'binding_visibility_protocol': self.visibility,
                'provenance': self.provenance, 'access_log': self.access_log,
                'planned_tracks': len(self.tracks), 'bound_tracks': len(self.bindings),
                'planned_observations': sum(len(t['observations']) for t in self.tracks),
                'binding_decisions': self.binding_decisions,
                'binding_contract': 'once after anchor contour fit; immutable healthy triangle/barycentric; no rebind',
                'scope': 'existing global pose/deformation state only; no local defect depth or classification'}
