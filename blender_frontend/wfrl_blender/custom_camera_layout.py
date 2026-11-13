"""Camera layout serialization, validation, comparison and transactional restore."""
from __future__ import annotations


def copy_text(scene, camera, slot):
    params = _core.parameters(camera)
    _core.validate_parameters(params)
    x, y, z = params.location
    return '\n'.join((
        f'相机：T1 / 相机 {_core._slot(slot)}',
        f'坐标约定：{_core.COORDINATE_VERSION}',
        '坐标原点：塔顶偏航参考点；+X 朝机舱尾部；+Y 面向叶轮时右侧；+Z 局部上方',
        f'模型标识：{camera.get("custom_model_signature") or _core.model_signature(scene)}',
        f'镜头中心（m）：X={x:.9f} Y={y:.9f} Z={z:.9f}',
        f'机舱局部朝向（deg）：水平角={params.yaw:.9f} 俯仰角={params.pitch:.9f} 画面旋转角={params.roll:.9f}',
        f'水平视场角（deg）：{params.fov:.9f}',
        f'垂直视场角（deg）：{params.vfov:.9f}',
        f'记录焦距（mm）：{params.focal_length_mm_record}',
        f'画幅比例：{_core.projection.aspect(params.fov, params.vfov):.9f}',
        f'输出宽高：{_core.projection.resolution(params.fov, params.vfov, params.output_long_edge_px)}',
        '画面旋转角：相机局部 +Z 轴旋转；同画面比较需相同模型、回放时刻与显示设置',
    ))


def layout_dict(scene):
    _core.migrate_scene(scene)
    cameras = []
    for slot in _core.SLOTS:
        cam = _core.get_camera(scene, slot)
        if cam is None:
            continue
        p = _core.parameters(cam)
        cameras.append({'slot_id': slot, 'installed': True, 'enabled': bool(cam.get('custom_enabled', True)),
            'label': str(cam.get('custom_label', f'C{slot}')), 'parameters': _core.asdict(p),
            'mount_mode': cam.get('custom_mount_mode', 'SURFACE'),
            'mount_validation_status': cam.get('custom_mount_validation', 'unverified'),
            'research_confirmed': bool(cam.get('custom_research_confirmed', False)),
            'nearest_distance_m': cam.get('custom_nearest_distance'),
            'mount_warning': cam.get('custom_mount_warning', ''),
            'anchor': _core.asdict(_core.anchor(cam)) if _core.anchor(cam) else None,
            'image_width_px': _core.projection.resolution(p.fov, p.vfov, p.output_long_edge_px)[0],
            'image_height_px': _core.projection.resolution(p.fov, p.vfov, p.output_long_edge_px)[1]})
    from . import stacked_camera_rig as rig
    rig_pose = rig.pose(scene)
    layout = {**({'rig_pose': rig_pose} if rig_pose is not None else {}),
        'schema_version': _core.SCHEMA_VERSION, 'coordinate_frame': _core.COORDINATE_VERSION,
        'orientation_convention': 'yaw +X toward +Y; pitch up; roll camera +Z; degrees',
        'model_signature': _core.model_signature(scene), 'resolution_policy': 'FOV_PRIORITY_LONG_EDGE',
        'cameras': cameras}
    _core.json.dumps(layout, allow_nan=False)
    return layout


def layout_hash(layout):
    return _core.hashlib.sha256(_core.json.dumps(layout, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_layout(scene, payload):
    if not isinstance(payload, dict) or payload.get('schema_version') != _core.SCHEMA_VERSION:
        raise ValueError('不支持的布局 schema，须为 v2')
    if payload.get('coordinate_frame') != _core.COORDINATE_VERSION:
        raise ValueError('布局坐标版本不匹配，未进行位置换算')
    _core.json.dumps(payload, allow_nan=False)
    if payload.get('model_signature') != _core.model_signature(scene):
        raise ValueError('布局模型签名不匹配，未进行位置换算')
    records = payload.get('cameras')
    if not isinstance(records, list) or len(records) > _core.MAX_CUSTOM_CAMERAS:
        raise ValueError('布局最多包含三个槽位；旧四路布局已停用，请使用三相机布局')
    seen, validated = set(), []
    for record in records:
        slot = _core._slot(record['slot_id'])
        if slot in seen:
            raise ValueError('布局槽位重复')
        seen.add(slot)
        params = _core.CameraParameters(**{**record['parameters'], 'location': tuple(record['parameters']['location'])})
        _core.validate_parameters(params)
        if type(record.get('enabled')) is not bool or not isinstance(record.get('label'), str):
            raise ValueError('启用状态 / 标签无效')
        if record.get('installed') is not True or type(record.get('research_confirmed')) is not bool:
            raise ValueError('安装状态 / 偏置确认字段无效')
        if not isinstance(record.get('mount_warning', ''), str):
            raise ValueError('安装警告字段无效')
        nearest = record.get('nearest_distance_m')
        if nearest is not None and (not _core._finite((nearest,)) or nearest < 0):
            raise ValueError('安装距离字段无效')
        if record.get('mount_mode') == 'RESEARCH':
            result = _core.validate_research_position(scene, params.location, record.get('research_confirmed') is True)
            if record.get('mount_validation_status') != result['status']:
                raise ValueError('研发安装校验状态与当前模型不一致')
            surface = None
        elif record.get('mount_mode') == 'SURFACE':
            if record.get('mount_validation_status') != 'surface_validated':
                raise ValueError('表面安装校验状态无效')
            surface = _core.SurfaceAnchor(**record['anchor'])
            _core.validate_distance(surface.distance)
            if len(surface.point) != 3 or len(surface.normal) != 3 or not _core._finite((*surface.point, *surface.normal)):
                raise ValueError('安装锚点必须为有限三维数值')
            if not surface.surface_name or abs(sum(v * v for v in surface.normal) - 1) > 1e-4:
                raise ValueError('安装锚点须包含合法外壳与单位外法线')
            if max(abs(params.location[i] - surface.point[i] - surface.normal[i] * surface.distance) for i in range(3)) > 5e-5:
                raise ValueError('镜头中心与安装锚点、法线和离壳距离不一致')
            candidates = _core.validate_position(scene, params.location)
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


def live_layout_hash(scene, layout=None):
    """Also detect native transform/lens edits bypassing our stored parameters."""
    layout = layout if layout is not None else _core.layout_dict(scene)
    actual = []
    for slot in _core.SLOTS:
        cam = _core.get_camera(scene, slot)
        if cam is not None:
            actual.append((slot, tuple(round(v, 7) for row in cam.matrix_basis for v in row),
                cam.parent.name if cam.parent else None, tuple(round(v, 7) for row in cam.matrix_parent_inverse for v in row), cam.data.type,
                round(cam.data.lens, 7), round(cam.data.clip_start, 7), round(cam.data.clip_end, 7),
                cam.data.sensor_fit, round(cam.data.sensor_width, 7),
                round(cam.data.shift_x, 7), round(cam.data.shift_y, 7)))
    return _core.layout_hash({'layout': layout, 'actual': actual})


def config_equal(a, b):
    # JSON represents tuples as lists; numeric 75 and 75.0 are equivalent.
    return _core.json.loads(_core.json.dumps(a, allow_nan=False)) == _core.json.loads(_core.json.dumps(b, allow_nan=False))


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
    for slot in _core.SLOTS:
        a, b = old.get(slot), new.get(slot)
        action = ('保持为空' if a is None else '将清空') if b is None else (
            '将创建' if a is None else '配置不变' if _core.config_equal(a, b) else '将替换')
        changes = []
        if a and b and action == '将替换':
            for key, label in fields.items():
                if not _core.config_equal(a.get(key), b.get(key)):
                    changes.append(f'{label}：{_core._summary_value(key,a.get(key))} → {_core._summary_value(key,b.get(key))}')
            for key, label in params.items():
                if not _core.config_equal(a['parameters'].get(key), b['parameters'].get(key)):
                    changes.append(f'{label}：{_core._summary_value(key,a["parameters"].get(key))} → {_core._summary_value(key,b["parameters"].get(key))}')
        rows.append({'slot': slot, 'action': action, 'label': b['label'] if b else '',
                     'enabled': b['enabled'] if b else None, 'changes': changes})
    return rows


def restore_layout(scene, payload):
    """Reusable validated restore boundary; no UI import or history side effect."""
    validated = _core.validate_layout(scene, payload)
    if any(obj.get('wfrl_custom_draft') for obj in scene.objects):
        raise ValueError('请先结束当前草稿')
    from . import stacked_camera_rig as rig
    old_rig_pose = rig.pose(scene)
    staged = []
    try:
        for record, params, surface in validated:
            cam = _core.begin_draft(scene, record['slot_id'])
            staged.append(cam)
            cam['wfrl_custom_draft'] = False
            cam['wfrl_custom_camera'] = False
            if surface:
                _core.apply_parameters(cam, params, surface)
            else:
                _core.apply_research(scene, cam, params, record['research_confirmed'])
            cam['custom_enabled'], cam['custom_label'] = record['enabled'], record['label']
            cam['custom_model_signature'] = payload['model_signature']
            # Restore optional validation metadata after recomputing constraints.
            cam['custom_mount_validation'] = record['mount_validation_status']
            if record.get('nearest_distance_m') is not None:
                cam['custom_nearest_distance'] = record['nearest_distance_m']
            if record.get('mount_warning'):
                cam['custom_mount_warning'] = record['mount_warning']
            _core._publication_checkpoint('stage', record['slot_id'])
        by_slot = {cam['wfrl_custom_slot']: cam for cam in staged}
        rig.apply_pose(scene, payload.get('rig_pose'))
        _core._publish(scene, {slot: by_slot.get(slot) for slot in _core.SLOTS})
    except Exception as failure:
        errors = []
        try:
            rig.apply_pose(scene, old_rig_pose)
        except Exception as exc:
            errors.append(str(exc))
        for cam in staged:
            try:
                data = cam.data
            except ReferenceError:
                continue
            try:
                _core._remove(cam)
            except (RuntimeError, ValueError) as exc:
                # Unpublished cameras remain owned by this failed transaction.
                try:
                    cam['wfrl_custom_draft'] = True
                except ReferenceError:
                    pass
                _core._remember_retired(cam, data)
                errors.append(str(exc))
        if errors:
            raise RuntimeError(str(failure) + '；布局回滚清理失败：' + '；'.join(errors)) from failure
        raise
    return staged


def import_layout(scene, payload, overwrite=False):
    _core.validate_layout(scene, payload)
    if any(_core.get_camera(scene, slot) is not None for slot in _core.SLOTS) and not overwrite:
        raise ValueError('已有相机；请明确确认替换完整布局')
    from . import custom_camera_history as history
    before = history.before_change(scene)
    if _core.config_equal(before[1], payload):
        return [_core.get_camera(scene, slot) for slot in _core.SLOTS if _core.get_camera(scene, slot) is not None]
    result = _core.restore_layout(scene, payload)
    history.committed(scene, before, '导入完整三槽布局')
    return result


# Shared state and replaceable helpers are resolved through the public namespace.
from . import custom_cameras as _core
