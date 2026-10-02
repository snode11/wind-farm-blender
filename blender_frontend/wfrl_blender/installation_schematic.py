"""Cached installation drawing built from the current scene's real meshes.

The cache is expressed in the nacelle's local coordinates, so animation never
refreshes it. Only installation/configuration changes invalidate the snapshot.
This is a static geometric illustration, not a fourth live camera or a render.
"""
import math
from . import custom_cameras as core


def fingerprint(scene):
    from . import stacked_camera_rig as rig
    surfaces = set(core.surface_names(scene))
    cameras = tuple((slot, cam.as_pointer(), repr(core.parameters(cam)), bool(cam.get('custom_enabled', True)))
                    for slot in core.SLOTS if (cam := core.get_camera(scene, slot)))
    # depsgraph revisions change on every animation frame. They must not turn
    # this static drawing into another live view.
    objects = tuple((obj.as_pointer(), obj.data.as_pointer(), bool(obj.hide_render),
                     tuple(round(v, 8) for row in obj.matrix_basis for v in row))
                    for obj in scene.objects if obj.type == 'MESH'
                    and (obj.name in surfaces or obj.get('stacked_rig_physical')))
    return (scene.as_pointer(), repr(rig.pose(scene)), cameras, objects)


def capture(scene):
    from mathutils import Vector
    from . import stacked_camera_rig as rig
    root = scene.objects.get(core.ROOT_NAME)
    if root is None:
        raise ValueError('当前场景没有安装参考坐标')
    pose = rig.pose(scene) or {}
    normal = Vector(pose.get('surface_mount', {}).get('normal', (0, -1, 0)))
    direction = (normal.normalized() + Vector((.45, 0, .32))).normalized()
    right = Vector((0, 0, 1)).cross(direction).normalized()
    up = direction.cross(right).normalized()
    inv = root.matrix_world.inverted()
    surfaces = set(core.surface_names(scene))
    objects = [obj for obj in scene.objects if obj.type == 'MESH' and not obj.hide_render
               and (obj.name in surfaces or obj.get('stacked_rig_physical'))]
    if not objects:
        raise ValueError('当前场景没有机舱或安装实体')
    triangles, all_points, detail_points, labels = [], [], [], []
    def project(point):
        return (point.dot(right), point.dot(up), point.dot(direction))
    for obj in objects:
        matrix = inv @ obj.matrix_world
        vertices = [matrix @ v.co for v in obj.data.vertices]
        projected = [project(v) for v in vertices]
        all_points.extend(projected)
        is_rig = bool(obj.get('stacked_rig_physical'))
        if is_rig:
            detail_points.extend(projected)
        support = 'MountArm' in obj.name
        base = (.97, .55, .16) if support else ((.23, .71, .86) if is_rig else (.39, .46, .54))
        obj.data.calc_loop_triangles()
        for tri in obj.data.loop_triangles:
            indices = tri.vertices
            points = tuple(projected[i] for i in indices)
            normal = (vertices[indices[1]] - vertices[indices[0]]).cross(vertices[indices[2]] - vertices[indices[0]])
            light = .55 + .45 * abs(normal.normalized().dot(direction)) if normal.length else .7
            triangles.append((sum(p[2] for p in points) / 3, points, tuple(c * light for c in base) + (1.,)))
        if support and projected:
            labels.append(('支架', tuple(sum(p[i] for p in projected) / len(projected) for i in range(3))))
    for slot in core.SLOTS:
        camera = core.get_camera(scene, slot)
        if camera:
            point = project((inv @ camera.matrix_world).translation)
            labels.append((f'C{slot}', point))
            if not detail_points:
                detail_points.append(point)
    if not detail_points:
        detail_points = all_points
    triangles.sort(key=lambda row: row[0])
    def bounds(points, padding):
        lo_x, hi_x = min(p[0] for p in points), max(p[0] for p in points)
        lo_y, hi_y = min(p[1] for p in points), max(p[1] for p in points)
        cx, cy = (lo_x + hi_x) / 2, (lo_y + hi_y) / 2
        width, height = max(.15, hi_x - lo_x) * padding, max(.15, hi_y - lo_y) * padding
        return (cx - width / 2, cy - height / 2, width, height)
    return {'triangles': triangles, 'labels': labels,
            'overview': bounds(all_points, 1.2), 'detail': bounds(detail_points, 1.65),
            'objects': tuple(obj.name for obj in objects), 'rig_pose': pose,
            'coordinate_frame': core.ROOT_NAME, 'kind': 'STATIC_ACTUAL_MESH_PROJECTION'}


def fit_transform(bounds, rectangle):
    """Uniformly fit the complete bounds; return scale and pixel translation."""
    bx, by, bw, bh = bounds
    x, y, width, height = rectangle
    if min(bw, bh, width, height) <= 0 or not all(math.isfinite(v) for v in (*bounds, *rectangle)):
        raise ValueError('示意图尺寸必须为有限正数')
    scale = min(width / bw, height / bh)
    return scale, x + (width - bw * scale) / 2 - bx * scale, y + (height - bh * scale) / 2 - by * scale


def clipped_polygon(points, rectangle):
    """Clip the drawing inset without GPU scissor state or camera cropping."""
    x, y, width, height = rectangle
    points = list(points)
    for axis, edge, sign in ((0, x, 1), (0, x+width, -1), (1, y, 1), (1, y+height, -1)):
        incoming, points = points, []
        if not incoming:break
        previous = incoming[-1]
        for current in incoming:
            before, after = sign*(previous[axis]-edge) >= 0, sign*(current[axis]-edge) >= 0
            if before != after:
                factor = (edge-previous[axis])/(current[axis]-previous[axis])
                points.append(tuple(previous[i]+factor*(current[i]-previous[i]) for i in (0, 1)))
            if after:points.append(current)
            previous = current
    return points
