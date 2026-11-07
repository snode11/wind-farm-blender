"""Scene ownership, native-control validation and protection feedback."""
from . import manual_control as _control


def _pause_status(scene, reason):
    if not scene.wfrl_manual_error:
        scene.wfrl_manual_status = reason


def _record_error(scene, reason, turbine=''):
    """Latch a scene-local protection reason until explicit validation succeeds."""
    reason = str(reason) or '控制对象无法读取'
    if not turbine and reason.startswith(('T1 ', 'T2 ', 'T3 ')):
        turbine = reason[:2]
    if '约束' in reason or '驱动' in reason or '动作' in reason:
        recovery = '在独立副本中移除冲突约束、动作或驱动'
    elif '旋转' in reason:
        recovery = '撤销原生旋转，或用手动面板重设姿态'
    elif '共享' in reason or '归属' in reason:
        recovery = '解除跨场景共享，或重新创建独立手动场景'
    else:
        recovery = '撤销对象修改；对象缺失时重新创建手动场景'
    values = (reason, turbine or '全部机组', recovery, 'ERROR')
    fields = ('wfrl_manual_error', 'wfrl_manual_error_turbine',
              'wfrl_manual_recovery', 'wfrl_manual_status')
    changed = any(getattr(scene, field) != value for field, value in zip(fields, values))
    for field, value in zip(fields, values):
        setattr(scene, field, value)
    return changed


def _protect_scene(scene, reason, *, turbine='', now=None):
    if not _control.is_manual(scene):
        return
    changed = _control._record_error(scene, reason, turbine)
    _control.pause_scene(scene, now=now, apply=False)
    if changed:
        _control._redraw(scene)


def recheck_feedback(scene):
    """Validate every controlled pose without writing or restarting motion."""
    if not _control.is_manual(scene):
        return False, '请先进入手动控制场景'
    _control.pause_scene(scene, apply=False)
    valid, reason = _control.validate_scene(scene)
    turbine = ''
    if valid:
        try:
            for entry in scene.wfrl_manual_turbines:
                turbine = entry.turbine_id
                _control._validate_controlled_rotation(entry)
        except (ValueError, AttributeError, ReferenceError, RuntimeError) as exc:
            valid, reason = False, str(exc)
    if not valid:
        _control._protect_scene(scene, reason, turbine=turbine)
        return False, reason
    scene.wfrl_manual_error = ''
    scene.wfrl_manual_error_turbine = ''
    scene.wfrl_manual_recovery = ''
    scene.wfrl_manual_status = 'READY'
    _control._redraw(scene)
    return True, ''


def _validate_native_controls(objects):
    """Reject native evaluation that can override rigid poses or fixed optics."""
    objects = set(objects)
    for obj in tuple(objects):
        ancestor = obj.parent
        while ancestor is not None:
            objects.add(ancestor)
            ancestor = ancestor.parent
    for obj in objects:
        if obj.constraints or obj.animation_data:
            raise ValueError(f'{obj.name} 含约束、动作或驱动，无法保证手动参数与实际姿态一致；'
                             '请在独立副本中处理后再操作')
        if obj.type == 'CAMERA' and obj.data.animation_data:
            raise ValueError(f'{obj.data.name} 镜头含动作或驱动，无法保证固定相机参数')


def _validate_controlled_rotation(entry):
    """Require controlled axes to match the last pose written to the objects."""
    try:
        _control._validate_native_controls(getattr(entry, field) for field in _control._POINTER_FIELDS)
    except ValueError as exc:
        raise ValueError(f'{entry.turbine_id} {exc}') from exc
    axes = [(entry.yaw_object, 2, _control.math.radians(entry.yaw_deg), '机舱'),
            (entry.rotor_object, 0, _control.math.radians(entry.azimuth_deg), '叶轮')]
    axes.extend((getattr(entry, f'blade{index}_object'), 2, -_control.math.radians(pitch), f'B{index}')
                for index, pitch in enumerate(_control.effective_pitches(entry), 1))
    for obj, axis, expected, label in axes:
        actual = obj.rotation_euler[axis]
        if (obj.rotation_mode != 'XYZ' or not _control.math.isfinite(actual)
                or abs(_control.math.remainder(actual - expected, _control.math.tau)) > 1e-5):
            raise ValueError(f'{entry.turbine_id} {label} 的原生旋转与手动控制参数不一致；'
                             '请撤销原生变换或通过手动面板重新设置姿态后再确认')


def other_scene_objects(scene, *, objects=None):
    """Find objects linked into foreign scenes, optionally restricted to controls."""
    others = tuple(other for other in _control.bpy.data.scenes if other != scene)
    if objects is None:
        return {obj.as_pointer() for other in others for obj in other.objects}
    objects = tuple(objects)
    if not objects or not others:
        return set()
    foreign = {collection for other in others
               for collection in (other.collection, *other.collection.children_recursive)}
    foreign.update(others)
    users = _control.bpy.data.user_map(subset=set(objects), value_types={'COLLECTION', 'SCENE'})
    return {obj.as_pointer() for obj in objects
            if users.get(obj, set()) & foreign
            and any(other.objects.get(obj.name) == obj for other in others)}


def validate_scene(scene):
    """Reject incomplete controls and objects shared with another scene."""
    if not _control.is_manual(scene):
        return False, '请先进入手动控制场景'
    turbine = ''
    try:
        entries = scene.wfrl_manual_turbines
        if not entries:
            return False, '手动控制场景缺少风机'
        owner = scene.get('wfrl_manual_owner')
        if not owner:
            return False, '手动控制场景缺少对象归属标识'
        seen_objects, seen_ids = set(), set()
        controlled = []
        if not _control.math.isfinite(scene.wfrl_manual_pitch_rate_deg_s):
            return False, '变桨速度必须是有限数值'
        for entry in entries:
            turbine = entry.turbine_id
            if not entry.turbine_id or entry.turbine_id in seen_ids:
                return False, '风机标识缺失或重复'
            seen_ids.add(entry.turbine_id)
            if any(not _control.math.isfinite(float(getattr(entry, field))) for field in _control._POSE_FIELDS):
                return False, f'{turbine} 控制参数必须是有限数值'
            if entry.pitch_state_ready and any(not _control.math.isfinite(value) for value in _control.effective_pitches(entry)):
                return False, f'{turbine} 实际桨距必须是有限数值'
            objects = [getattr(entry, field) for field in _control._POINTER_FIELDS]
            if any(obj is None for obj in objects):
                return False, f'{entry.turbine_id} 控制对象缺失，请重新创建场景'
            yaw, rotor, *blades = objects
            root = yaw.parent
            if (root is None or root.get('wfrl_turbine_id') != entry.turbine_id
                    or rotor.parent != yaw
                    or any(blade.parent is None or blade.parent.parent != rotor for blade in blades)
                    or any(blade.type != 'MESH' for blade in blades)):
                return False, f'{entry.turbine_id} 控制层级不完整，请重新创建场景'
            objects.extend([root] + [blade.parent for blade in blades])
            for obj in objects:
                if obj is None or scene.objects.get(obj.name) != obj:
                    return False, f'{entry.turbine_id} 控制对象缺失，请重新创建场景'
                if obj.get('wfrl_manual_owner') != owner:
                    return False, f'{turbine} 控制对象不属于此手动场景'
                key = obj.as_pointer()
                if key in seen_objects:
                    return False, f'{turbine} 风机控制对象不能重复使用'
                seen_objects.add(key)
                controlled.append((turbine, obj))
        shared = _control.other_scene_objects(scene, objects=(obj for _tid, obj in controlled))
        for turbine, obj in controlled:
            if obj.as_pointer() in shared:
                return False, f'{turbine} 控制对象与其他场景共享，请使用独立副本'
        if _control.trial_active(scene):
            turbine = 'T1'
            _control.trial_configuration(scene)
            elapsed = scene.wfrl_manual_trial.elapsed_s
            if 'T1' not in seen_ids or not _control.math.isfinite(elapsed) or elapsed < 0:
                return False, 'T1 变桨试验时间或对象无效'
            try:
                _control._validate_native_controls(getattr(_control._t1(scene), field) for field in _control._POINTER_FIELDS)
            except ValueError as exc:
                return False, f'T1 {exc}'
        return True, ''
    except (AttributeError, ReferenceError, TypeError, ValueError, KeyError):
        return False, f'{turbine} 控制对象已失效，请重新创建场景'.strip()


def guard_pose_value(entry, value, previous, _is_set):
    """Keep the stored input when native evaluation overrides the manual pose."""
    if _control._updating or not _control.is_manual(entry.id_data):
        return value
    try:
        _control._validate_native_controls(getattr(entry, name) for name in _control._POINTER_FIELDS)
    except (ValueError, AttributeError, ReferenceError) as exc:
        _control._protect_scene(entry.id_data, exc, turbine=entry.turbine_id)
        return previous
    return value


def validate_pose_export(scene):
    """Reject snapshots that disagree with any native controlled pose."""
    for entry in scene.wfrl_manual_turbines:
        _control._validate_controlled_rotation(entry)
