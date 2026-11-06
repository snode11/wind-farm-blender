"""Evaluated mount surfaces, placement validation and model fingerprints."""
from __future__ import annotations


def _surface_geometry(scene, depsgraph=None, *, with_normals=False):
    """Evaluated shell vertices/faces in the actual nacelle reference frame."""
    import bpy
    root = _core._root(scene)
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    root_eval = root.evaluated_get(depsgraph)
    result = []
    for name in _core.surface_names(scene):
        obj = scene.objects.get(name)
        if obj is None or obj.type != 'MESH':
            continue
        evaluated = obj.evaluated_get(depsgraph)
        if obj.parent == root and not obj.constraints and obj.parent_type == 'OBJECT':
            # Avoid world-space float cancellation as the turbine moves.
            transform = evaluated.matrix_parent_inverse @ evaluated.matrix_basis
        else:
            transform = root_eval.matrix_world.inverted() @ evaluated.matrix_world
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            vertices = [transform @ v.co for v in mesh.vertices]
            faces = [tuple(t.vertices) for t in mesh.loop_triangles]
            normals = None
            if with_normals:
                normal_matrix = transform.to_3x3().inverted().transposed()
                normals = [(normal_matrix @ t.normal).normalized() for t in mesh.loop_triangles]
            result.append((name, vertices, faces, normals))
        finally:
            evaluated.to_mesh_clear()
    return result


def _geometry(scene, depsgraph=None):
    """Collision acceleration for the evaluated mount surfaces."""
    from mathutils.bvhtree import BVHTree
    return [(name, vertices, faces, normals,
             BVHTree.FromPolygons(vertices, faces, all_triangles=True))
            for name, vertices, faces, normals in
            _core._surface_geometry(scene, depsgraph, with_normals=True)]


def _inside(point, tree, normals):
    from mathutils import Vector
    # For an outward-oriented closed shell, an inside ray first exits the shell.
    # Three non-axis-aligned probes avoid an accidental edge/tangent hit.
    votes = []
    for xyz in ((1, .371, .217), (.191, 1, .431), (.313, .229, 1)):
        direction = Vector(xyz).normalized()
        hit, _normal, face, _distance = tree.ray_cast(point, direction)
        if hit is None:
            votes.append(False)
        elif abs(normals[face].dot(direction)) > 1e-7:
            votes.append(normals[face].dot(direction) > 0)
    return sum(votes) > len(votes) / 2


def validate_position(scene, location, depsgraph=None):
    """Return outward face/edge mounting anchors; never rewrite XYZ.

    Edge positions may project onto several noncoplanar faces. Keep those
    candidates for an explicit user choice instead of choosing silently.
    """
    from mathutils import Vector
    from mathutils.geometry import closest_point_on_tri
    if len(location) != 3 or not _core._finite(location):
        raise ValueError('镜头中心 XYZ 必须为有限数值')
    point = Vector(location)
    candidates = []
    geometry = _core._geometry(scene, depsgraph)
    for name, vertices, faces, normals, tree in geometry:
        if _core._inside(point, tree, normals):
            raise ValueError('镜头中心位于机舱外壳内部；请输入外侧坐标')
        nearest, _normal, _face, separation = tree.find_nearest(point)
        if nearest is None:
            continue
        if separation < _core.MIN_DISTANCE - 1e-6:
            raise ValueError('镜头距外壳不足 0.02 m')
        if separation > _core.MAX_DISTANCE + 1e-6:
            continue
        for face, normal in zip(faces, normals):
            a, b, c = (vertices[index] for index in face)
            distance = (point - a).dot(normal)
            if not _core.MIN_DISTANCE - 1e-6 <= distance <= _core.MAX_DISTANCE + 1e-6:
                continue
            projected = point - normal * distance
            if (closest_point_on_tri(projected, a, b, c) - projected).length > 2e-5:
                continue
            if any((Vector(old.point) - projected).length < 2e-5 and Vector(old.normal).dot(normal) > .99999 for old in candidates):
                continue
            candidates.append(_core.SurfaceAnchor(tuple(projected), tuple(normal), min(_core.MAX_DISTANCE, max(_core.MIN_DISTANCE, distance)), name))
        # At a convex edge the outward normal is a cone, not one face normal.
        # Preserve valid diagonal offsets by using the nearest edge point and
        # its outward radial normal; never snap the user's optical centre.
        radial = point - nearest
        if radial.length > 1e-8 and radial.dot(normals[_face]) > 0:
            radial.normalize()
            if not any((Vector(old.point) - nearest).length < 2e-5 and Vector(old.normal).dot(radial) > .99999 for old in candidates):
                candidates.append(_core.SurfaceAnchor(tuple(nearest), tuple(radial), min(_core.MAX_DISTANCE, max(_core.MIN_DISTANCE, separation)), name))
    if not candidates:
        raise ValueError('镜头须位于可安装外壳外侧 0.02–0.50 m 内，且法线落点在壳体表面')
    return sorted(candidates, key=lambda a: (a.distance, a.point))


def raycast_surface(scene, origin_world, direction_world, depsgraph=None):
    """Only accept the first visible scene hit when it is an allowed shell."""
    import bpy
    from mathutils import Vector
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    direction = Vector(direction_world).normalized()
    hit, location, normal, _index, obj, _matrix = scene.ray_cast(depsgraph, Vector(origin_world), direction)
    if not hit or obj is None or obj.original.name not in _core.surface_names(scene):
        return None
    root = _core._root(scene).evaluated_get(depsgraph)
    point = root.matrix_world.inverted() @ location
    # world normal -> local normal uses transpose of local-to-world linear map.
    local_normal = (root.matrix_world.to_3x3().transposed() @ normal).normalized()
    return _core.SurfaceAnchor(tuple(point), tuple(local_normal), _core.DEFAULT_DISTANCE, obj.original.name)


def model_signature(scene, depsgraph=None):
    geometry = _core._surface_geometry(scene, depsgraph)
    payload = [(name, [tuple(round(float(v), 4) for v in vertex) for vertex in vertices], faces) for name, vertices, faces, _normals in geometry]
    return 'sha256:' + _core.hashlib.sha256(_core.json.dumps(payload, separators=(',', ':')).encode()).hexdigest()[:20]


def surface_names(scene):
    """Only named baseline shell or explicitly marked meshes under T1 YawRoot."""
    root = scene.objects.get(_core.ROOT_NAME)
    names = []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if obj.name in _core.SURFACE_NAMES:
            names.append(obj.name)
        elif obj.get('wfrl_camera_mount_surface'):
            parent = obj.parent
            while parent and parent != root:
                parent = parent.parent
            if parent == root:
                names.append(obj.name)
    return tuple(sorted(names))


def _closed_mesh(faces):
    edges = {}
    for face in faces:
        for a, b in zip(face, (*face[1:], face[0])):
            key = tuple(sorted((a, b)))
            edges[key] = edges.get(key, 0) + 1
    return bool(edges) and all(v == 2 for v in edges.values())


def validate_research_position(scene, location, confirmed=False):
    from mathutils import Vector
    if len(location) != 3 or not _core._finite(location):
        raise ValueError('镜头中心 XYZ 必须为有限数值')
    point, nearest_distance, verified = Vector(location), _core.math.inf, True
    for _name, _vertices, faces, normals, tree in _core._geometry(scene):
        closed = _core._closed_mesh(faces)
        verified = verified and closed
        if closed and _core._inside(point, tree, normals):
            raise ValueError('镜头中心位于实体机舱内部')
        near, _normal, _face, distance = tree.find_nearest(point)
        if near is not None:
            nearest_distance = min(nearest_distance, distance)
    if nearest_distance < _core.MIN_DISTANCE - 1e-6:
        raise ValueError('镜头距外壳不足 0.02 m，不通过裁剪隐藏壳体')
    if not confirmed and (not verified or nearest_distance > _core.MAX_DISTANCE):
        raise ValueError('研发位置未验证或超过 0.50 m；请显式确认支架偏置 / 未验证安装')
    return {'status': 'research_outside' if verified else 'unverified_open_surface',
            'nearest_distance_m': nearest_distance, 'warning':
            '研发坐标：支架与安装未认证' if verified else '表面不闭合：安装位置未验证'}


def model_identity(scene):
    """Raw mount mesh content catches in-place edits, independent of playback pose."""
    from array import array
    digest = _core.hashlib.sha256()
    for name in _core.surface_names(scene):
        obj = scene.objects[name]
        coords = array('f', [0.]) * (len(obj.data.vertices) * 3)
        obj.data.vertices.foreach_get('co', coords)
        digest.update(name.encode())
        digest.update(coords.tobytes())
        digest.update(repr([tuple(face.vertices) for face in obj.data.polygons]).encode())
        # Mount transforms below YawRoot, never the animated root/world pose.
        parent = obj
        while parent and parent.name != _core.ROOT_NAME:
            digest.update(repr(tuple(v for row in parent.matrix_basis for v in row)).encode())
            parent = parent.parent
    return digest.hexdigest()


# Shared state and replaceable helpers are resolved through the public namespace.
from . import custom_cameras as _core
