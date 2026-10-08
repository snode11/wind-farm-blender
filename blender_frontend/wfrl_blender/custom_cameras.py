"""Three user-owned T1 observation cameras, expressed in YawRoot local metres.

No module import needs Blender. Camera construction and evaluated surface queries
import bpy lazily. This module never saves files, preferences, or replay state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from . import camera_projection as projection
import hashlib
import json
import math

ROOT_NAME = 'WFRL.Turbine.T1.YawRoot'
SURFACE_NAMES = ('WFRL.Turbine.T1.Nacelle',)
COORDINATE_VERSION = 'T1.YawRoot.local / v1'
MIN_DISTANCE = .02
MAX_DISTANCE = .50
DEFAULT_DISTANCE = .10
DEFAULT_FOV = 75.0
MAX_CUSTOM_CAMERAS = 3
SLOTS = tuple(range(1, MAX_CUSTOM_CAMERAS + 1))
SCHEMA_VERSION = 2
_RETIRED = []


@dataclass(frozen=True)
class CameraParameters:
    location: tuple[float, float, float]
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    fov: float = DEFAULT_FOV
    vfov: float = projection.DEFAULT_VFOV
    focal_length_mm_record: float | None = None
    output_long_edge_px: int = projection.DEFAULT_LONG_EDGE
    clip_near_m: float = .005
    clip_far_m: float = 30000.


@dataclass(frozen=True)
class SurfaceAnchor:
    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    distance: float
    surface_name: str = SURFACE_NAMES[0]


def _finite(values):
    try:
        return all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False


def validate_parameters(params):
    if len(params.location) != 3 or not _finite((*params.location, params.yaw, params.pitch, params.roll, params.fov)):
        raise ValueError('镜头中心和角度必须为有限数值（不接受 NaN 或无穷）')
    if not 0 <= params.yaw < 360:
        raise ValueError('水平角须在 [0, 360)° 范围内')
    if not -90 <= params.pitch <= 90:
        raise ValueError('俯仰角须在 −90–90° 范围内')
    projection.validate(params.fov, params.vfov, params.clip_near_m, params.clip_far_m)
    if params.clip_near_m > MIN_DISTANCE / 2:
        raise ValueError('近裁剪不能超过 0.01 m，以免隐藏紧邻镜头的实体遮挡')
    projection.resolution(params.fov, params.vfov, params.output_long_edge_px)
    if params.focal_length_mm_record is not None:
        projection.finite(params.focal_length_mm_record)
        if params.focal_length_mm_record <= 0:
            raise ValueError('记录焦距必须为正，或留空')
    return params


def validate_distance(distance):
    if not _finite((distance,)) or not MIN_DISTANCE <= distance <= MAX_DISTANCE:
        raise ValueError('镜头离壳距离须在 0.02–0.50 m 范围内')
    return float(distance)


def screen_direction_delta(yaw, pitch, roll, dx, dy):
    """Move optical aim right/up in screen degrees, including arbitrary roll.

    Construct the same camera axes as aim_gimbal, rather than dividing by
    cos(pitch), which becomes singular at the poles. Stored roll is unchanged.
    """
    if not _finite((yaw, pitch, roll, dx, dy)):
        raise ValueError('方向控制必须为有限数值')
    if dx == 0 and dy == 0:
        return yaw, pitch
    y, p, r = map(math.radians, (yaw, pitch, roll))
    cy, sy, cp, sp, cr, sr = math.cos(y), math.sin(y), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    forward = (cp * cy, cp * sy, sp)
    right = (sy, -cy, 0)
    up = (-sp * cy, -sp * sy, cp)
    tx, ty = math.tan(math.radians(dx)), math.tan(math.radians(dy))
    vector = tuple(forward[i] + tx * (cr * right[i] + sr * up[i]) + ty * (-sr * right[i] + cr * up[i]) for i in range(3))
    horizontal = math.hypot(vector[0], vector[1])
    new_yaw = yaw if horizontal < 1e-12 else math.degrees(math.atan2(vector[1], vector[0])) % 360
    return new_yaw, math.degrees(math.atan2(vector[2], horizontal))


def _slot(slot):
    if isinstance(slot, bool) or slot not in SLOTS:
        raise ValueError('自定义相机仅提供 C1–C3')
    return int(slot)


def camera_name(slot):
    return f'WFRL.Camera.T1.Custom.{_slot(slot)}'


def is_available(scene):
    root = scene.objects.get(ROOT_NAME)
    return root is not None and any(scene.objects.get(name) is not None and scene.objects.get(name).type == 'MESH' for name in SURFACE_NAMES)


def _root(scene):
    if not is_available(scene):
        raise ValueError('请先加载包含 T1 的演示场景')
    root = scene.objects[ROOT_NAME]
    if not _finite(tuple(v for row in root.matrix_world for v in row)):
        raise ValueError('T1.YawRoot 姿态含非有限数值，无法使用米制相机参考')
    matrix = root.matrix_world.to_3x3()
    columns = list(matrix.col)
    if (any(abs(v.length - 1) > 1e-4 for v in columns)
            or any(abs(columns[i].dot(columns[j])) > 1e-4 for i in range(3) for j in range(i))
            or matrix.determinant() < 0):
        raise ValueError('T1.YawRoot 含非预期缩放、剪切或反射，无法使用米制相机参考')
    return root


def get_camera(scene, slot):
    slot = _slot(slot)
    return next((obj for obj in scene.objects if obj.type == 'CAMERA' and obj.get('wfrl_custom_camera') and obj.get('wfrl_custom_slot') == slot and not obj.get('wfrl_custom_draft')), None)


def migrate_camera(scene, camera):
    """Fill v2 fields once. Never recalculate saved VFOV from the scene later."""
    if camera.get('custom_mount_mode', 'SURFACE') == 'SURFACE':
        for key in ('custom_research_confirmed', 'custom_nearest_distance', 'custom_mount_warning'):
            if key in camera:
                del camera[key]
    if int(camera.get('custom_schema_version', 1)) >= SCHEMA_VERSION:
        return camera
    h = float(camera.get('gimbal_fov', DEFAULT_FOV))
    camera['custom_vfov'] = math.degrees(2 * math.atan(math.tan(math.radians(h / 2)) / aspect_ratio(scene)))
    camera['custom_schema_version'] = SCHEMA_VERSION
    camera['custom_clip_near'] = float(camera.data.clip_start)
    camera['custom_clip_far'] = float(camera.data.clip_end)
    camera['custom_enabled'] = True
    camera['custom_label'] = f"C{camera.get('wfrl_custom_slot', 1)}"
    camera['custom_mount_mode'] = 'SURFACE'
    camera['custom_mount_validation'] = 'surface_validated'
    camera['custom_long_edge'] = projection.DEFAULT_LONG_EDGE
    return camera


def migrate_scene(scene):
    for slot in SLOTS:
        camera = get_camera(scene, slot)
        if camera is not None:
            migrate_camera(scene, camera)


def parameters(camera):
    return CameraParameters(tuple(camera.get('custom_location', camera.location)),
        float(camera.get('gimbal_yaw', 0)), float(camera.get('gimbal_pitch', 0)),
        float(camera.get('gimbal_roll', 0)), float(camera.get('gimbal_fov', DEFAULT_FOV)),
        float(camera.get('custom_vfov', projection.DEFAULT_VFOV)),
        camera.get('custom_focal_record'), int(camera.get('custom_long_edge', projection.DEFAULT_LONG_EDGE)),
        float(camera.get('custom_clip_near', .005)), float(camera.get('custom_clip_far', 30000.)))


def anchor(camera):
    if 'custom_anchor' not in camera:
        return None
    return SurfaceAnchor(tuple(camera['custom_anchor']), tuple(camera['custom_normal']), float(camera['custom_distance']), str(camera['custom_surface']))


def apply_parameters(camera, params, anchor=None):
    from mathutils import Matrix
    validate_parameters(params)
    if anchor is not None:
        validate_distance(anchor.distance)
        if len(anchor.point) != 3 or len(anchor.normal) != 3 or not _finite((*anchor.point, *anchor.normal)):
            raise ValueError('安装锚点必须为有限三维数值')
        if not anchor.surface_name or abs(sum(v * v for v in anchor.normal) - 1) > 1e-4:
            raise ValueError('安装锚点须包含合法外壳与单位外法线')
        if max(abs(params.location[i] - anchor.point[i] - anchor.normal[i] * anchor.distance) for i in range(3)) > 5e-5:
            raise ValueError('镜头中心与安装锚点、法线和离壳距离不一致')
        camera['custom_anchor'] = tuple(anchor.point)
        camera['custom_normal'] = tuple(anchor.normal)
        camera['custom_distance'] = anchor.distance
        camera['custom_surface'] = anchor.surface_name
        camera['custom_mount_mode'] = 'SURFACE'
        camera['custom_mount_validation'] = 'surface_validated'
        # A copied research camera must not carry its old offset acknowledgement.
        for key in ('custom_research_confirmed', 'custom_nearest_distance', 'custom_mount_warning'):
            if key in camera:
                del camera[key]
    camera.location = params.location
    camera['custom_location'] = tuple(float(value) for value in params.location)
    camera['gimbal_roll'] = params.roll
    camera.rotation_euler = Matrix(projection.rotation(params.yaw, params.pitch, params.roll)).to_euler('XYZ')
    camera['gimbal_yaw'], camera['gimbal_pitch'], camera['gimbal_fov'] = params.yaw, params.pitch, params.fov
    camera['custom_vfov'] = params.vfov
    camera['custom_long_edge'] = params.output_long_edge_px
    camera['custom_schema_version'] = SCHEMA_VERSION
    if params.focal_length_mm_record is None:
        if 'custom_focal_record' in camera:
            del camera['custom_focal_record']
    else:
        camera['custom_focal_record'] = params.focal_length_mm_record
    camera.data.type, camera.data.sensor_fit = 'PERSP', 'HORIZONTAL'
    camera.data.sensor_width = 36
    camera.data.lens = 36 / (2 * math.tan(math.radians(params.fov / 2)))
    camera.data.shift_x = camera.data.shift_y = 0
    camera.data.clip_start, camera.data.clip_end = params.clip_near_m, params.clip_far_m
    camera['custom_clip_near'], camera['custom_clip_far'] = params.clip_near_m, params.clip_far_m
    return camera


def begin_draft(scene, slot):
    import bpy
    slot = _slot(slot)
    root = _root(scene)
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消当前相机调整')
    original = get_camera(scene, slot)
    if original is not None:
        migrate_camera(scene, original)
    name = camera_name(slot) + '.Draft'
    if original is not None:
        camera = original.copy()
        camera.data = original.data.copy()
        camera.name = name
    else:
        camera = bpy.data.objects.new(name, bpy.data.cameras.new(name + '.Data'))
    (root.users_collection[0] if root.users_collection else scene.collection).objects.link(camera)
    camera.parent = root
    camera.matrix_parent_inverse.identity()
    camera['wfrl_custom_camera'] = True
    camera['wfrl_custom_slot'] = slot
    camera['wfrl_custom_draft'] = True
    camera['custom_coordinate_version'] = COORDINATE_VERSION
    camera.data.type = 'PERSP'
    if original is None:
        camera.data.clip_start, camera.data.clip_end = .005, 30000
    camera.data.display_size = .15
    camera.data.show_passepartout = True
    camera.data.passepartout_alpha = .6
    camera.hide_render = True
    camera.lock_location = (True, True, True)
    camera.lock_rotation = (True, True, True)
    camera.lock_scale = (True, True, True)
    if original is None:
        camera['custom_enabled'] = True
        camera['custom_label'] = f'C{slot}'
        camera['custom_mount_mode'] = 'SURFACE'
        camera['custom_mount_validation'] = 'unverified'
        apply_parameters(camera, CameraParameters((0, 0, 0)))
    return camera


def _remove(camera):
    import bpy
    try:
        data = camera.data
        bpy.data.objects.remove(camera, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.cameras.remove(data)
    except ReferenceError:
        pass


def cancel_draft(camera):
    try:
        if camera is not None and camera.get('wfrl_custom_draft'):
            _remove(camera)
    except ReferenceError:
        pass


def commit_draft(scene, slot, camera):
    slot = _slot(slot)
    if not camera.get('wfrl_custom_draft') or camera.get('wfrl_custom_slot') != slot or camera.parent != _root(scene):
        raise ValueError('当前草稿或 T1 安装参考已失效')
    validate_parameters(parameters(camera))
    from . import stacked_camera_rig
    stacked_camera_rig.validate_pose(scene, {}, [({'slot_id': slot}, parameters(camera), None)])
    if camera.get('custom_mount_mode') == 'RESEARCH':
        validate_research_position(scene, parameters(camera).location,
                                   confirmed=bool(camera.get('custom_research_confirmed')))
    else:
        candidates = validate_position(scene, parameters(camera).location)
        stored = anchor(camera)
        if stored is None or not any(a.surface_name == stored.surface_name
                and sum((a.point[i] - stored.point[i]) ** 2 for i in range(3)) < 1e-7
                and sum(a.normal[i] * stored.normal[i] for i in range(3)) > .9999
                and abs(a.distance - stored.distance) < 5e-5 for a in candidates):
            raise ValueError('所选安装锚点已失效，请重新选择表面')
        camera['custom_mount_validation'] = 'surface_validated'
    from . import custom_camera_history as history
    before = history.before_change(scene)
    camera['custom_model_signature'] = model_signature(scene)
    _publish(scene, {slot: camera})
    history.committed(scene, before, f'确认 C{slot} 修改')
    return camera


def clear_slot(scene, slot):
    slot = _slot(slot)
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消当前相机调整')
    if get_camera(scene, slot) is not None:
        from . import custom_camera_history as history
        before = history.before_change(scene)
        _publish(scene, {slot: None})
        history.committed(scene, before, f'清除 C{slot}')


def set_enabled(scene, slot, enabled):
    from . import custom_camera_history as history
    camera = get_camera(scene, slot)
    if camera is None or bool(camera.get('custom_enabled', True)) == bool(enabled):
        return
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先确认或取消当前相机调整')
    before = history.before_change(scene)
    camera['custom_enabled'] = bool(enabled)
    history.committed(scene, before, f'C{slot} ' + ('参与' if enabled else '不参与') + '三路预览与采集')
    _refresh_references(scene)


def _geometry(scene, depsgraph=None):
    """Evaluated shell triangles in the actual nacelle reference frame."""
    import bpy
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    root = _root(scene)
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    root_eval = root.evaluated_get(depsgraph)
    result = []
    for name in surface_names(scene):
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
            normal_matrix = transform.to_3x3().inverted().transposed()
            normals = [(normal_matrix @ t.normal).normalized() for t in mesh.loop_triangles]
            tree = BVHTree.FromPolygons(vertices, faces, all_triangles=True)
            result.append((name, vertices, faces, normals, tree))
        finally:
            evaluated.to_mesh_clear()
    return result


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
    if len(location) != 3 or not _finite(location):
        raise ValueError('镜头中心 XYZ 必须为有限数值')
    point = Vector(location)
    candidates = []
    geometry = _geometry(scene, depsgraph)
    for name, vertices, faces, normals, tree in geometry:
        if _inside(point, tree, normals):
            raise ValueError('镜头中心位于机舱外壳内部；请输入外侧坐标')
        nearest, _normal, _face, separation = tree.find_nearest(point)
        if nearest is None:
            continue
        if separation < MIN_DISTANCE - 1e-6:
            raise ValueError('镜头距外壳不足 0.02 m')
        if separation > MAX_DISTANCE + 1e-6:
            continue
        for face, normal in zip(faces, normals):
            a, b, c = (vertices[index] for index in face)
            distance = (point - a).dot(normal)
            if not MIN_DISTANCE - 1e-6 <= distance <= MAX_DISTANCE + 1e-6:
                continue
            projected = point - normal * distance
            if (closest_point_on_tri(projected, a, b, c) - projected).length > 2e-5:
                continue
            if any((Vector(old.point) - projected).length < 2e-5 and Vector(old.normal).dot(normal) > .99999 for old in candidates):
                continue
            candidates.append(SurfaceAnchor(tuple(projected), tuple(normal), min(MAX_DISTANCE, max(MIN_DISTANCE, distance)), name))
        # At a convex edge the outward normal is a cone, not one face normal.
        # Preserve valid diagonal offsets by using the nearest edge point and
        # its outward radial normal; never snap the user's optical centre.
        radial = point - nearest
        if radial.length > 1e-8 and radial.dot(normals[_face]) > 0:
            radial.normalize()
            if not any((Vector(old.point) - nearest).length < 2e-5 and Vector(old.normal).dot(radial) > .99999 for old in candidates):
                candidates.append(SurfaceAnchor(tuple(nearest), tuple(radial), min(MAX_DISTANCE, max(MIN_DISTANCE, separation)), name))
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
    if not hit or obj is None or obj.original.name not in surface_names(scene):
        return None
    root = _root(scene).evaluated_get(depsgraph)
    point = root.matrix_world.inverted() @ location
    # world normal -> local normal uses transpose of local-to-world linear map.
    local_normal = (root.matrix_world.to_3x3().transposed() @ normal).normalized()
    return SurfaceAnchor(tuple(point), tuple(local_normal), DEFAULT_DISTANCE, obj.original.name)


def place_on_surface(camera, surface, distance=DEFAULT_DISTANCE):
    distance = validate_distance(distance)
    surface = replace(surface, distance=distance)
    location = tuple(surface.point[i] + surface.normal[i] * distance for i in range(3))
    nx, ny, nz = surface.normal
    yaw = math.degrees(math.atan2(ny, nx)) % 360
    pitch = math.degrees(math.atan2(nz, math.hypot(nx, ny)))
    camera['custom_mount_mode'] = 'SURFACE'
    return apply_parameters(camera, replace(parameters(camera), location=location, yaw=yaw, pitch=pitch, roll=0), surface)


def set_distance(camera, distance):
    surface = anchor(camera)
    if surface is None:
        raise ValueError('请先点击机舱外壳安装')
    distance = validate_distance(distance)
    location = tuple(surface.point[i] + surface.normal[i] * distance for i in range(3))
    return apply_parameters(camera, replace(parameters(camera), location=location), replace(surface, distance=distance))


def model_signature(scene, depsgraph=None):
    geometry = _geometry(scene, depsgraph)
    payload = [(name, [tuple(round(float(v), 4) for v in vertex) for vertex in vertices], faces) for name, vertices, faces, _normals, _tree in geometry]
    return 'sha256:' + hashlib.sha256(json.dumps(payload, separators=(',', ':')).encode()).hexdigest()[:20]


def aspect_ratio(scene):
    render = scene.render
    return render.resolution_x * render.pixel_aspect_x / (render.resolution_y * render.pixel_aspect_y)


def copy_text(scene, camera, slot):
    params = parameters(camera)
    validate_parameters(params)
    x, y, z = params.location
    return '\n'.join((
        f'相机：T1 / 相机 {_slot(slot)}',
        f'坐标约定：{COORDINATE_VERSION}',
        '坐标原点：塔顶偏航参考点；+X 朝机舱尾部；+Y 面向叶轮时右侧；+Z 局部上方',
        f'模型标识：{camera.get("custom_model_signature") or model_signature(scene)}',
        f'镜头中心（m）：X={x:.9f} Y={y:.9f} Z={z:.9f}',
        f'机舱局部朝向（deg）：水平角={params.yaw:.9f} 俯仰角={params.pitch:.9f} 画面旋转角={params.roll:.9f}',
        f'水平视场角（deg）：{params.fov:.9f}',
        f'垂直视场角（deg）：{params.vfov:.9f}',
        f'记录焦距（mm）：{params.focal_length_mm_record}',
        f'画幅比例：{projection.aspect(params.fov, params.vfov):.9f}',
        f'输出宽高：{projection.resolution(params.fov, params.vfov, params.output_long_edge_px)}',
        '画面旋转角：相机局部 +Z 轴旋转；同画面比较需相同模型、回放时刻与显示设置',
    ))


def surface_names(scene):
    """Only named baseline shell or explicitly marked meshes under T1 YawRoot."""
    root = scene.objects.get(ROOT_NAME)
    names = []
    for obj in scene.objects:
        if obj.type != 'MESH':
            continue
        if obj.name in SURFACE_NAMES:
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
    if len(location) != 3 or not _finite(location):
        raise ValueError('镜头中心 XYZ 必须为有限数值')
    point, nearest_distance, verified = Vector(location), math.inf, True
    for _name, _vertices, faces, normals, tree in _geometry(scene):
        closed = _closed_mesh(faces)
        verified = verified and closed
        if closed and _inside(point, tree, normals):
            raise ValueError('镜头中心位于实体机舱内部')
        near, _normal, _face, distance = tree.find_nearest(point)
        if near is not None:
            nearest_distance = min(nearest_distance, distance)
    if nearest_distance < MIN_DISTANCE - 1e-6:
        raise ValueError('镜头距外壳不足 0.02 m，不通过裁剪隐藏壳体')
    if not confirmed and (not verified or nearest_distance > MAX_DISTANCE):
        raise ValueError('研发位置未验证或超过 0.50 m；请显式确认支架偏置 / 未验证安装')
    return {'status': 'research_outside' if verified else 'unverified_open_surface',
            'nearest_distance_m': nearest_distance, 'warning':
            '研发坐标：支架与安装未认证' if verified else '表面不闭合：安装位置未验证'}


def apply_research(scene, camera, params, confirmed=False):
    validate_parameters(params)
    result = validate_research_position(scene, params.location, confirmed)
    apply_parameters(camera, params)
    for name in ('custom_anchor', 'custom_normal', 'custom_distance', 'custom_surface'):
        if name in camera:
            del camera[name]
    camera['custom_mount_mode'] = 'RESEARCH'
    camera['custom_mount_validation'] = result['status']
    camera['custom_research_confirmed'] = confirmed
    camera['custom_mount_warning'] = result['warning']
    camera['custom_nearest_distance'] = result['nearest_distance_m']
    return camera


def enabled_cameras(scene):
    migrate_scene(scene)
    return [cam for slot in SLOTS if (cam := get_camera(scene, slot)) is not None and cam.get('custom_enabled', True)]


def layout_dict(scene):
    migrate_scene(scene)
    cameras = []
    for slot in SLOTS:
        cam = get_camera(scene, slot)
        if cam is None:
            continue
        p = parameters(cam)
        cameras.append({'slot_id': slot, 'installed': True, 'enabled': bool(cam.get('custom_enabled', True)),
            'label': str(cam.get('custom_label', f'C{slot}')), 'parameters': asdict(p),
            'mount_mode': cam.get('custom_mount_mode', 'SURFACE'),
            'mount_validation_status': cam.get('custom_mount_validation', 'unverified'),
            'research_confirmed': bool(cam.get('custom_research_confirmed', False)),
            'nearest_distance_m': cam.get('custom_nearest_distance'),
            'mount_warning': cam.get('custom_mount_warning', ''),
            'anchor': asdict(anchor(cam)) if anchor(cam) else None,
            'image_width_px': projection.resolution(p.fov, p.vfov, p.output_long_edge_px)[0],
            'image_height_px': projection.resolution(p.fov, p.vfov, p.output_long_edge_px)[1]})
    from . import stacked_camera_rig as rig
    rig_pose = rig.pose(scene)
    return {**({'rig_pose': rig_pose} if rig_pose is not None else {}),
        'schema_version': SCHEMA_VERSION, 'coordinate_frame': COORDINATE_VERSION,
        'orientation_convention': 'yaw +X toward +Y; pitch up; roll camera +Z; degrees',
        'model_signature': model_signature(scene), 'resolution_policy': 'FOV_PRIORITY_LONG_EDGE',
        'cameras': cameras}


def layout_hash(layout):
    return hashlib.sha256(json.dumps(layout, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_layout(scene, payload):
    if not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('不支持的布局 schema，须为 v2')
    if payload.get('coordinate_frame') != COORDINATE_VERSION:
        raise ValueError('布局坐标版本不匹配，未进行位置换算')
    if payload.get('model_signature') != model_signature(scene):
        raise ValueError('布局模型签名不匹配，未进行位置换算')
    records = payload.get('cameras')
    if not isinstance(records, list) or len(records) > MAX_CUSTOM_CAMERAS:
        raise ValueError('布局最多包含三个槽位；旧四路布局已停用，请使用三相机布局')
    seen, validated = set(), []
    for record in records:
        slot = _slot(record['slot_id'])
        if slot in seen:
            raise ValueError('布局槽位重复')
        seen.add(slot)
        params = CameraParameters(**{**record['parameters'], 'location': tuple(record['parameters']['location'])})
        validate_parameters(params)
        if type(record.get('enabled')) is not bool or not isinstance(record.get('label'), str):
            raise ValueError('启用状态 / 标签无效')
        if record.get('installed') is not True or type(record.get('research_confirmed')) is not bool:
            raise ValueError('安装状态 / 偏置确认字段无效')
        if not isinstance(record.get('mount_warning', ''), str):
            raise ValueError('安装警告字段无效')
        nearest = record.get('nearest_distance_m')
        if nearest is not None and (not _finite((nearest,)) or nearest < 0):
            raise ValueError('安装距离字段无效')
        if record.get('mount_mode') == 'RESEARCH':
            result = validate_research_position(scene, params.location, record.get('research_confirmed') is True)
            if record.get('mount_validation_status') != result['status']:
                raise ValueError('研发安装校验状态与当前模型不一致')
            surface = None
        elif record.get('mount_mode') == 'SURFACE':
            if record.get('mount_validation_status') != 'surface_validated':
                raise ValueError('表面安装校验状态无效')
            surface = SurfaceAnchor(**record['anchor'])
            candidates = validate_position(scene, params.location)
            if not any(a.surface_name == surface.surface_name and abs(a.distance - surface.distance) < 5e-5
                and sum((a.point[i] - surface.point[i]) ** 2 for i in range(3)) < 1e-7
                and sum(a.normal[i] * surface.normal[i] for i in range(3)) > .9999 for a in candidates):
                raise ValueError('布局安装锚点与当前表面不匹配')
        else:
            raise ValueError('未知安装模式')
        validated.append((record, params, surface))
    from . import stacked_camera_rig
    stacked_camera_rig.validate_pose(scene, payload, validated)
    return validated


def model_identity(scene):
    """Raw mount mesh content catches in-place edits, independent of playback pose."""
    from array import array
    digest = hashlib.sha256()
    for name in surface_names(scene):
        obj = scene.objects[name]
        coords = array('f', [0.]) * (len(obj.data.vertices) * 3)
        obj.data.vertices.foreach_get('co', coords)
        digest.update(name.encode())
        digest.update(coords.tobytes())
        digest.update(repr([tuple(face.vertices) for face in obj.data.polygons]).encode())
        # Mount transforms below YawRoot, never the animated root/world pose.
        parent = obj
        while parent and parent.name != ROOT_NAME:
            digest.update(repr(tuple(v for row in parent.matrix_basis for v in row)).encode())
            parent = parent.parent
    return digest.hexdigest()


def live_layout_hash(scene, layout=None):
    """Also detect native transform/lens edits bypassing our stored parameters."""
    layout = layout if layout is not None else layout_dict(scene)
    actual = []
    for slot in SLOTS:
        cam = get_camera(scene, slot)
        if cam is not None:
            actual.append((slot, tuple(round(v, 7) for row in cam.matrix_basis for v in row),
                cam.parent.name if cam.parent else None, tuple(round(v, 7) for row in cam.matrix_parent_inverse for v in row), cam.data.type,
                round(cam.data.lens, 7), round(cam.data.clip_start, 7), round(cam.data.clip_end, 7),
                cam.data.sensor_fit, round(cam.data.sensor_width, 7),
                round(cam.data.shift_x, 7), round(cam.data.shift_y, 7)))
    return layout_hash({'layout': layout, 'actual': actual})


def validate_native_state(scene, cameras=None, depsgraph=None):
    """Reject native edits whose rendered pose disagrees with saved local XYZ.

    Cameras are fixed in YawRoot coordinates. Comparing the evaluated local
    transform excludes the root's legitimate replay motion, but includes camera
    constraints, drivers, delta transforms and native edits. Never repair a pose
    here: capture must not silently overwrite an externally edited camera.
    """
    import bpy
    from mathutils import Matrix
    root = _root(scene)
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    if not _finite(tuple(v for row in root.evaluated_get(depsgraph).matrix_world for v in row)):
        raise ValueError('T1.YawRoot 实际姿态含非有限数值，无法使用米制相机参考')
    cameras = cameras if cameras is not None else [get_camera(scene, slot) for slot in SLOTS]
    for cam in cameras:
        if cam is None:
            continue
        slot = int(cam['wfrl_custom_slot'])
        message = f'C{slot} 原生相机状态与保存配置不一致；请撤销原生修改，或清除该相机后通过编辑器重新安装再采集'
        if cam.parent != root or cam.parent_type != 'OBJECT':
            raise ValueError(message + '（安装参考已变化）')
        p = validate_parameters(parameters(cam))
        expected = Matrix(projection.rotation(p.yaw, p.pitch, p.roll)).to_4x4()
        expected.translation = p.location
        evaluated = cam.evaluated_get(depsgraph)
        # For the canonical object parent, evaluated basis retains local metre
        # precision even when the world/root has a large translated coordinate.
        local = evaluated.matrix_parent_inverse @ evaluated.matrix_basis
        if not _finite(tuple(v for matrix in (local, evaluated.matrix_world, cam.matrix_parent_inverse)
                             for row in matrix for v in row)):
            raise ValueError(message + '（实际姿态含非有限数值）')
        if (any(abs(local[i][j] - expected[i][j]) > 5e-5 for i in range(4) for j in range(4))
                or any(abs(cam.matrix_parent_inverse[i][j] - (1 if i == j else 0)) > 1e-6
                       for i in range(4) for j in range(4))):
            raise ValueError(message + '（局部姿态已变化）')
        # Constraints do not modify matrix_basis; check their actual evaluated
        # result in root coordinates as well. Avoid unnecessary world subtraction
        # on the normal, unconstrained replay path.
        if any(not constraint.mute and constraint.influence != 0 for constraint in cam.constraints):
            relative = root.evaluated_get(depsgraph).matrix_world.inverted() @ evaluated.matrix_world
            if not _finite(tuple(v for row in relative for v in row)):
                raise ValueError(message + '（约束产生了非有限姿态）')
            if any(abs(relative[i][j] - expected[i][j]) > 5e-5 for i in range(4) for j in range(4)):
                raise ValueError(message + '（约束改变了实际姿态）')
        lens = 36 / (2 * math.tan(math.radians(p.fov / 2)))
        data = evaluated.data
        if not _finite((data.sensor_width, data.lens, data.shift_x, data.shift_y,
                        data.clip_start, data.clip_end)):
            raise ValueError(message + '（镜头或裁剪设置含非有限数值）')
        if (data.type != 'PERSP' or data.sensor_fit != 'HORIZONTAL'
                or abs(data.sensor_width - 36) > 1e-5 or abs(data.lens - lens) > max(1e-5, abs(lens) * 1e-6)
                or abs(data.shift_x) > 1e-6 or abs(data.shift_y) > 1e-6
                or abs(data.clip_start - p.clip_near_m) > max(1e-7, p.clip_near_m * 1e-6)
                or abs(data.clip_end - p.clip_far_m) > max(1e-5, p.clip_far_m * 1e-6)):
            raise ValueError(message + '（镜头或裁剪设置已变化）')
    return True


def config_equal(a, b):
    # JSON represents tuples as lists; numeric 75 and 75.0 are equivalent.
    return json.loads(json.dumps(a, allow_nan=False)) == json.loads(json.dumps(b, allow_nan=False))


def _summary_value(key, value):
    if key == 'enabled':
        return '参与' if value else '不参与'
    if key == 'research_confirmed':
        return '已确认' if value else '未确认'
    if key == 'mount_mode':
        return {'SURFACE': '表面安装', 'RESEARCH': '研发坐标'}.get(value, str(value))
    if key == 'anchor' and value:
        point = ', '.join(f'{v:.4f}' for v in value['point'])
        normal = ', '.join(f'{v:.3f}' for v in value['normal'])
        return f'{value["surface_name"]}；点 ({point})；法线 ({normal})；距离 {value["distance"]:.4f} m'
    if isinstance(value, (tuple, list)):
        return '(' + ', '.join(f'{v:.4f}' for v in value) + ')'
    if isinstance(value, float):
        return f'{value:.4f}'
    return str(value)


def layout_summary(current, incoming):
    """Three fixed slots, full replacement; pure function shared by dialog/tests."""
    old = {r['slot_id']: r for r in current['cameras']}
    new = {r['slot_id']: r for r in incoming['cameras']}
    rows = []
    fields = {'label': '备注名', 'enabled': '参与状态', 'mount_mode': '安装模式',
              'anchor': '锚点', 'research_confirmed': '偏置确认'}
    params = {'location': 'XYZ', 'yaw': 'Yaw', 'pitch': 'Pitch', 'roll': 'Roll',
              'fov': 'HFOV', 'vfov': 'VFOV', 'output_long_edge_px': '输出长边',
              'focal_length_mm_record': '记录焦距', 'clip_near_m': '近裁剪', 'clip_far_m': '远裁剪'}
    for slot in SLOTS:
        a, b = old.get(slot), new.get(slot)
        action = ('保持为空' if a is None else '将清空') if b is None else (
            '将创建' if a is None else '配置不变' if config_equal(a, b) else '将替换')
        changes = []
        if a and b and action == '将替换':
            for key, label in fields.items():
                if not config_equal(a.get(key), b.get(key)):
                    changes.append(f'{label}：{_summary_value(key,a.get(key))} → {_summary_value(key,b.get(key))}')
            for key, label in params.items():
                if not config_equal(a['parameters'].get(key), b['parameters'].get(key)):
                    changes.append(f'{label}：{_summary_value(key,a["parameters"].get(key))} → {_summary_value(key,b["parameters"].get(key))}')
        rows.append({'slot': slot, 'action': action, 'label': b['label'] if b else '',
                     'enabled': b['enabled'] if b else None, 'changes': changes})
    return rows


def _publication_checkpoint(stage, slot=None):
    """Fault-injection seam; no irreversible object deletion before commit."""


def _refresh_references(scene):
    if scene.get('wfrl_stacked_camera_rig'):
        from . import stacked_camera_rig
        stacked_camera_rig.sync_modules(scene)
    from . import custom_camera_preview as preview
    from .panels import custom_cameras as panel
    preview.shutdown()
    preview.changed()
    op = panel._ACTIVE
    if op and op.scene == scene and op.stage in panel._VIEWING:
        op.select_slot(op.slot)
        if op.stage == 'WATCH' and op.camera is None:
            # Keep publication reversible until its commit checkpoint.
            # An empty slot can remain selected in the external layout.
            op.stage = 'LAYOUT'
        op.area.tag_redraw()


def cleanup_retired():
    for obj in tuple(_RETIRED):
        try:
            if not obj.users_collection and not obj.get('wfrl_custom_camera'):
                _remove(obj)
            _RETIRED.remove(obj)
        except ReferenceError:
            _RETIRED.remove(obj)
        except (RuntimeError, ValueError):
            pass


def _publish(scene, replacements):
    """Publish/detach reversibly, then garbage-collect obsolete objects.

    Originals stay alive and linked until replacement objects and references are
    ready. A failure at any pre-commit stage restores object identity and links.
    Deletion is post-commit garbage collection, never part of layout visibility.
    """
    import bpy
    cleanup_retired()
    from .panels import custom_cameras as panel
    controller = panel._ACTIVE
    controller_stage = controller.stage if controller and controller.scene == scene else None
    originals = {slot: get_camera(scene, slot) for slot in replacements}
    objects = [obj for obj in (*originals.values(), *replacements.values()) if obj is not None]
    saved = [(obj, obj.name, obj.data.name, bool(obj.get('wfrl_custom_camera')),
              bool(obj.get('wfrl_custom_draft')), tuple(obj.users_collection)) for obj in objects]
    refs = [(scene, 'camera', scene.camera)]
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D' and area.spaces.active.use_local_camera:
                refs.append((area.spaces.active, 'camera', area.spaces.active.camera))
    try:
        for slot, original in originals.items():
            if original is not None:
                original['wfrl_custom_camera'] = False
                original.name += '.Retired'
                original.data.name += '.Retired'
            _publication_checkpoint('retire', slot)
        for slot, cam in replacements.items():
            if cam is not None:
                cam.name = camera_name(slot)
                cam.data.name = cam.name + '.Data'
                cam['wfrl_custom_camera'], cam['wfrl_custom_draft'] = True, False
            _publication_checkpoint('publish', slot)
        for owner, attr, old in refs:
            for slot, original in originals.items():
                if original is not None and old == original:
                    setattr(owner, attr, replacements[slot])
        _publication_checkpoint('references')
        for slot, original in originals.items():
            if original is not None:
                for collection in tuple(original.users_collection):
                    collection.objects.unlink(original)
            _publication_checkpoint('detach', slot)
        _refresh_references(scene)
        _publication_checkpoint('commit')
    except Exception:
        # First remove naming collisions, then restore exact original names.
        for obj, *_rest in saved:
            obj.name += '.Rollback'
            obj.data.name += '.Rollback'
        for obj, name, data_name, flag, draft, collections in saved:
            obj.name, obj.data.name = name, data_name
            obj['wfrl_custom_camera'], obj['wfrl_custom_draft'] = flag, draft
            for collection in collections:
                if obj.name not in collection.objects:
                    collection.objects.link(obj)
        for owner, attr, old in refs:
            setattr(owner, attr, old)
        try:
            if controller_stage is not None and not controller.closed:
                controller.stage = controller_stage
            _refresh_references(scene)
        except (RuntimeError, ReferenceError):
            pass
        raise
    # Unlinked, unflagged leftovers cannot appear in a slot even if Blender
    # refuses garbage collection. Retry on the next transaction.
    for original in originals.values():
        if original is not None:
            try:
                _remove(original)
            except (RuntimeError, ValueError):
                _RETIRED.append(original)


def restore_layout(scene, payload):
    """Reusable validated restore boundary; no UI import or history side effect."""
    validated = validate_layout(scene, payload)
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先结束当前草稿')
    from . import stacked_camera_rig as rig
    old_rig_pose = rig.pose(scene)
    staged = []
    try:
        for record, params, surface in validated:
            cam = begin_draft(scene, record['slot_id'])
            staged.append(cam)
            cam['wfrl_custom_draft'] = False
            cam['wfrl_custom_camera'] = False
            if surface:
                apply_parameters(cam, params, surface)
            else:
                apply_research(scene, cam, params, record['research_confirmed'])
            cam['custom_enabled'], cam['custom_label'] = record['enabled'], record['label']
            cam['custom_model_signature'] = payload['model_signature']
            # Restore optional validation metadata after recomputing constraints.
            cam['custom_mount_validation'] = record['mount_validation_status']
            if record.get('nearest_distance_m') is not None:
                cam['custom_nearest_distance'] = record['nearest_distance_m']
            if record.get('mount_warning'):
                cam['custom_mount_warning'] = record['mount_warning']
            _publication_checkpoint('stage', record['slot_id'])
        by_slot = {cam['wfrl_custom_slot']: cam for cam in staged}
        rig.apply_pose(scene, payload.get('rig_pose'))
        _publish(scene, {slot: by_slot.get(slot) for slot in SLOTS})
    except Exception:
        rig.apply_pose(scene, old_rig_pose)
        for cam in staged:
            _remove(cam)
        raise
    return staged


def import_layout(scene, payload, overwrite=False):
    validate_layout(scene, payload)
    if any(get_camera(scene, slot) is not None for slot in SLOTS) and not overwrite:
        raise ValueError('已有相机；请明确确认替换完整布局')
    from . import custom_camera_history as history
    before = history.before_change(scene)
    if config_equal(before[1], payload):
        return [get_camera(scene, slot) for slot in SLOTS if get_camera(scene, slot) is not None]
    result = restore_layout(scene, payload)
    history.committed(scene, before, '导入完整三槽布局')
    return result
