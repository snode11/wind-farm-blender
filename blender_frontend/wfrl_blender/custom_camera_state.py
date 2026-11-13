"""Camera lookup, schema migration, stored parameters and native pose validation."""
from __future__ import annotations


def is_available(scene):
    root = scene.objects.get(_core.ROOT_NAME)
    return root is not None and any(scene.objects.get(name) is not None and scene.objects.get(name).type == 'MESH' for name in _core.SURFACE_NAMES)


def _root(scene):
    if not _core.is_available(scene):
        raise ValueError('请先加载包含 T1 的演示场景')
    root = scene.objects[_core.ROOT_NAME]
    _validate_root_matrix(root.matrix_world)
    return root


def _validate_root_matrix(matrix):
    if not _core._finite(tuple(v for row in matrix for v in row)):
        raise ValueError('T1.YawRoot 姿态含非有限数值，无法使用米制相机参考')
    matrix = matrix.to_3x3()
    columns = list(matrix.col)
    if (any(abs(v.length - 1) > 1e-4 for v in columns)
            or any(abs(columns[i].dot(columns[j])) > 1e-4 for i in range(3) for j in range(i))
            or matrix.determinant() < 0):
        raise ValueError('T1.YawRoot 含非预期缩放、剪切或反射，无法使用米制相机参考')


def get_camera(scene, slot):
    slot = _core._slot(slot)
    return next((obj for obj in scene.objects if obj.type == 'CAMERA' and obj.get('wfrl_custom_camera') and obj.get('wfrl_custom_slot') == slot and not obj.get('wfrl_custom_draft')), None)


def migrate_camera(scene, camera):
    """Fill v2 fields once. Never recalculate saved VFOV from the scene later."""
    if camera.get('custom_mount_mode', 'SURFACE') == 'SURFACE':
        for key in ('custom_research_confirmed', 'custom_nearest_distance', 'custom_mount_warning'):
            if key in camera:
                del camera[key]
    if int(camera.get('custom_schema_version', 1)) >= _core.SCHEMA_VERSION:
        return camera
    h = float(camera.get('gimbal_fov', _core.DEFAULT_FOV))
    camera['custom_vfov'] = _core.math.degrees(2 * _core.math.atan(_core.math.tan(_core.math.radians(h / 2)) / _core.aspect_ratio(scene)))
    camera['custom_schema_version'] = _core.SCHEMA_VERSION
    camera['custom_clip_near'] = float(camera.data.clip_start)
    camera['custom_clip_far'] = float(camera.data.clip_end)
    camera['custom_enabled'] = True
    camera['custom_label'] = f"C{camera.get('wfrl_custom_slot', 1)}"
    camera['custom_mount_mode'] = 'SURFACE'
    camera['custom_mount_validation'] = 'surface_validated'
    camera['custom_long_edge'] = _core.projection.DEFAULT_LONG_EDGE
    return camera


def migrate_scene(scene):
    for slot in _core.SLOTS:
        camera = _core.get_camera(scene, slot)
        if camera is not None:
            _core.migrate_camera(scene, camera)


def parameters(camera):
    return _core.CameraParameters(tuple(camera.get('custom_location', camera.location)),
        float(camera.get('gimbal_yaw', 0)), float(camera.get('gimbal_pitch', 0)),
        float(camera.get('gimbal_roll', 0)), float(camera.get('gimbal_fov', _core.DEFAULT_FOV)),
        float(camera.get('custom_vfov', _core.projection.DEFAULT_VFOV)),
        camera.get('custom_focal_record'), int(camera.get('custom_long_edge', _core.projection.DEFAULT_LONG_EDGE)),
        float(camera.get('custom_clip_near', .005)), float(camera.get('custom_clip_far', 30000.)))


def anchor(camera):
    if 'custom_anchor' not in camera:
        return None
    return _core.SurfaceAnchor(tuple(camera['custom_anchor']), tuple(camera['custom_normal']), float(camera['custom_distance']), str(camera['custom_surface']))


def apply_parameters(camera, params, anchor=None):
    from mathutils import Matrix
    _core.validate_parameters(params)
    if anchor is not None:
        _core.validate_distance(anchor.distance)
        if len(anchor.point) != 3 or len(anchor.normal) != 3 or not _core._finite((*anchor.point, *anchor.normal)):
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
    camera.rotation_euler = Matrix(_core.projection.rotation(params.yaw, params.pitch, params.roll)).to_euler('XYZ')
    camera['gimbal_yaw'], camera['gimbal_pitch'], camera['gimbal_fov'] = params.yaw, params.pitch, params.fov
    camera['custom_vfov'] = params.vfov
    camera['custom_long_edge'] = params.output_long_edge_px
    camera['custom_schema_version'] = _core.SCHEMA_VERSION
    if params.focal_length_mm_record is None:
        if 'custom_focal_record' in camera:
            del camera['custom_focal_record']
    else:
        camera['custom_focal_record'] = params.focal_length_mm_record
    camera.data.type, camera.data.sensor_fit = 'PERSP', 'HORIZONTAL'
    camera.data.sensor_width = 36
    camera.data.lens = 36 / (2 * _core.math.tan(_core.math.radians(params.fov / 2)))
    camera.data.shift_x = camera.data.shift_y = 0
    camera.data.clip_start, camera.data.clip_end = params.clip_near_m, params.clip_far_m
    camera['custom_clip_near'], camera['custom_clip_far'] = params.clip_near_m, params.clip_far_m
    return camera


def aspect_ratio(scene):
    render = scene.render
    return render.resolution_x * render.pixel_aspect_x / (render.resolution_y * render.pixel_aspect_y)


def apply_research(scene, camera, params, confirmed=False):
    _core.validate_parameters(params)
    result = _core.validate_research_position(scene, params.location, confirmed)
    _core.apply_parameters(camera, params)
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
    _core.migrate_scene(scene)
    return [cam for slot in _core.SLOTS if (cam := _core.get_camera(scene, slot)) is not None and cam.get('custom_enabled', True)]


def validate_native_state(scene, cameras=None, depsgraph=None):
    """Reject native edits whose rendered pose disagrees with saved local XYZ.

    Cameras are fixed in YawRoot coordinates. Comparing the evaluated local
    transform excludes the root's legitimate replay motion, but includes camera
    constraints, drivers, delta transforms and native edits. Never repair a pose
    here: capture must not silently overwrite an externally edited camera.
    """
    import bpy
    from mathutils import Matrix
    root = _core._root(scene)
    depsgraph = depsgraph or bpy.context.evaluated_depsgraph_get()
    _validate_root_matrix(root.evaluated_get(depsgraph).matrix_world)
    cameras = cameras if cameras is not None else [_core.get_camera(scene, slot) for slot in _core.SLOTS]
    for cam in cameras:
        if cam is None:
            continue
        slot = int(cam['wfrl_custom_slot'])
        message = f'C{slot} 原生相机状态与保存配置不一致；请撤销原生修改，或清除该相机后通过编辑器重新安装再采集'
        if cam.parent != root or cam.parent_type != 'OBJECT':
            raise ValueError(message + '（安装参考已变化）')
        p = _core.validate_parameters(_core.parameters(cam))
        expected = Matrix(_core.projection.rotation(p.yaw, p.pitch, p.roll)).to_4x4()
        expected.translation = p.location
        evaluated = cam.evaluated_get(depsgraph)
        # For the canonical object parent, evaluated basis retains local metre
        # precision even when the world/root has a large translated coordinate.
        local = evaluated.matrix_parent_inverse @ evaluated.matrix_basis
        if not _core._finite(tuple(v for matrix in (local, evaluated.matrix_world, cam.matrix_parent_inverse)
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
            if not _core._finite(tuple(v for row in relative for v in row)):
                raise ValueError(message + '（约束产生了非有限姿态）')
            if any(abs(relative[i][j] - expected[i][j]) > 5e-5 for i in range(4) for j in range(4)):
                raise ValueError(message + '（约束改变了实际姿态）')
        lens = 36 / (2 * _core.math.tan(_core.math.radians(p.fov / 2)))
        data = evaluated.data
        if not _core._finite((data.sensor_width, data.lens, data.shift_x, data.shift_y,
                        data.clip_start, data.clip_end)):
            raise ValueError(message + '（镜头或裁剪设置含非有限数值）')
        if (data.type != 'PERSP' or data.sensor_fit != 'HORIZONTAL'
                or abs(data.sensor_width - 36) > 1e-5 or abs(data.lens - lens) > max(1e-5, abs(lens) * 1e-6)
                or abs(data.shift_x) > 1e-6 or abs(data.shift_y) > 1e-6
                or abs(data.clip_start - p.clip_near_m) > max(1e-7, p.clip_near_m * 1e-6)
                or abs(data.clip_end - p.clip_far_m) > max(1e-5, p.clip_far_m * 1e-6)):
            raise ValueError(message + '（镜头或裁剪设置已变化）')
    return True


# Shared state and replaceable helpers are resolved through the public namespace.
from . import custom_cameras as _core
