"""Canonical material coordinates on the declared healthy Rotor template.

No scene mesh, repair mark, truth package, or object-ID map is read here.
q=(span_norm, perimeter_turns); UV=(perimeter_turns modulo 1, span_norm).
Span is physical normalized radius, not uniformly spaced section index.
"""
import numpy as np


def chart_binding(rotor, q):
    q = np.asarray(q, float)
    if q.shape != (2,) or not np.isfinite(q).all() or not 0 <= q[0] <= 1:
        raise ValueError('q must be finite [span_norm in 0..1, perimeter_turns]')
    S, N = len(rotor.tpl.r), rotor.cfg.n_ring
    si = float(np.interp(q[0], rotor.tpl.xi, np.arange(S)))
    i = min(int(np.floor(si)), S - 2)
    u = si - i
    cj = float((q[1] % 1.0) * N)
    j = int(np.floor(cj)) % N
    v = cj - np.floor(cj)
    a, b = i * N + j, i * N + (j + 1) % N
    c, d = (i + 1) * N + (j + 1) % N, (i + 1) * N + j
    # Same diagonal and indexing as BladeTemplate.faces().
    if v >= u:
        vertices, bary, half = [a, b, c], [1 - v, v - u, u], 0
    else:
        vertices, bary, half = [a, c, d], [1 - u, v, u - v], 1
    return {'triangle_id': 2 * (i * N + j) + half,
            'vertex_indices': vertices, 'barycentric_weights': bary,
            'q': q.tolist(), 'uv': [float(q[1] % 1), float(q[0])],
            'chart': 'physical-span-normalized/perimeter-turns.v1'}


def binding_to_chart(rotor, triangle_id, barycentric_weights):
    S, N = len(rotor.tpl.r), rotor.cfg.n_ring
    tri = int(triangle_id)
    if tri < 0 or tri >= 2 * (S - 1) * N:
        raise ValueError('tip caps have no lateral chart representation')
    w = np.asarray(barycentric_weights, float)
    if w.shape != (3,) or not np.isfinite(w).all() or abs(w.sum() - 1) > 1e-6 or w.min() < -1e-7:
        raise ValueError('invalid barycentric coordinates')
    i, j = divmod(tri // 2, N)
    if tri % 2 == 0:
        u, v = w[2], w[1] + w[2]
    else:
        u, v = w[1] + w[2], w[1]
    span = (1 - u) * rotor.tpl.xi[i] + u * rotor.tpl.xi[i + 1]
    return np.array([span, (j + v) / N])


def point_from_vertices(rotor, vertices, axis, blade_id, q):
    binding = chart_binding(rotor, q)
    b = int(blade_id)
    if b not in (0, 1, 2):
        raise ValueError('blade_id must be 0, 1, or 2')
    vv = vertices[b].reshape(-1, 3)[binding['vertex_indices']]
    w = np.asarray(binding['barycentric_weights'])
    point = w @ vv
    normal = np.cross(vv[1] - vv[0], vv[2] - vv[0])
    norm = np.linalg.norm(normal)
    if norm < 1e-12:
        raise ValueError('degenerate template triangle')
    normal /= norm
    section_ids = np.asarray(binding['vertex_indices']) // rotor.cfg.n_ring
    # Orient normal outward from the model axis; template winding is not
    # assumed to be outward. This uses only the declared forward model.
    axis_point = w @ axis[b, section_ids]
    if normal @ (point - axis_point) < 0:
        normal = -normal
    return point, normal, binding


def material_point(rotor, state, blade_id, q):
    vertices, axis = rotor.forward(state)
    return point_from_vertices(rotor, vertices, axis, blade_id, q)


def pixel_ray(cam, uv):
    uv = np.asarray(uv, float)
    if uv.shape != (2,) or not np.isfinite(uv).all():
        raise ValueError('pixel must be a finite original-image pixel center')
    ray_cv = np.linalg.solve(cam.K, np.r_[uv, 1.0])
    ray = cam.R.T @ ray_cv
    return cam.center, ray / np.linalg.norm(ray)


def ray_triangles(origin, direction, triangles, near=1e-6):
    """Two-sided ray hits; returns inf distances for misses, fixed shape."""
    tris = np.asarray(triangles, float)
    e1, e2 = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    h = np.cross(np.broadcast_to(direction, e2.shape), e2)
    det = np.einsum('ij,ij->i', e1, h)
    valid = np.abs(det) > 1e-12
    inv = np.divide(1.0, det, out=np.zeros_like(det), where=valid)
    s = origin - tris[:, 0]
    u = inv * np.einsum('ij,ij->i', s, h)
    cr = np.cross(s, e1)
    v = inv * (cr @ direction)
    distance = inv * np.einsum('ij,ij->i', e2, cr)
    valid &= (u >= -1e-8) & (v >= -1e-8) & (u + v <= 1 + 1e-8) & (distance > near)
    return np.where(valid, distance, np.inf), np.stack([1 - u - v, u, v], axis=1)


def intersect_pixel(rotor, state, cam, uv, blade_id=None):
    """Nearest model ray hit, never the source scene's dynamic surface."""
    vertices, _ = rotor.forward(state)
    faces = rotor.tpl.faces()
    origin, direction = pixel_ray(cam, uv)
    blades = range(3) if blade_id is None else [int(blade_id)]
    best = None
    for b in blades:
        triangles = vertices[b].reshape(-1, 3)[faces]
        distances, bary = ray_triangles(origin, direction, triangles)
        idx = int(np.argmin(distances))
        if not np.isfinite(distances[idx]):
            continue
        if best is None or distances[idx] < best['ray_distance']:
            try:
                q = binding_to_chart(rotor, idx, bary[idx])
            except ValueError:
                q = None
            best = {'blade_id': b, 'triangle_id': idx,
                    'barycentric_weights': bary[idx].tolist(),
                    'ray_distance': float(distances[idx]),
                    'position': (origin + distances[idx] * direction).tolist(),
                    'q': None if q is None else q.tolist(),
                    'binding': None if q is None else chart_binding(rotor, q)}
    return best


def point_visibility(rotor, vertices, axis, cam, blade_id, q, tolerance_m=0.005):
    """Diagnostic against all three *estimated* blade meshes.

    Occlusion never removes a confirmed RGB observation from optimization.
    The caller must preserve and report any contradiction.
    """
    point, normal, binding = point_from_vertices(rotor, vertices, axis, blade_id, q)
    view = cam.center - point
    distance = float(np.linalg.norm(view))
    direction = -view / max(distance, 1e-12)
    uv, z = cam.project(point[None])
    uv, z = uv[0], float(z[0])
    inside = bool(np.isfinite(uv).all() and 0 <= uv[0] <= cam.W - 1 and 0 <= uv[1] <= cam.H - 1)
    facing = float(normal @ (view / max(distance, 1e-12)))
    faces = rotor.tpl.faces()
    first = np.inf
    for b in range(3):
        hit, _ = ray_triangles(cam.center, direction, vertices[b].reshape(-1, 3)[faces])
        first = min(first, float(hit.min()))
    occluded = bool(np.isfinite(first) and first < distance - tolerance_m)
    return {'uv': uv.tolist(), 'depth_m': z, 'in_frame': inside,
            'facing_cosine': facing, 'back_facing': bool(facing < -1e-6),
            'occluded_by_estimated_blade': occluded,
            'nearest_model_hit_m': None if not np.isfinite(first) else first,
            'material_point_distance_m': distance, 'binding': binding}
