"""Independent manual turbine state, rigid poses and main-thread motion."""
import math
import os
from pathlib import Path
import tempfile
import time
from dataclasses import replace

import bpy
from bpy.app.handlers import persistent
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .manual_motion import RotorClock
from . import manual_pose


DEFAULT_RPM = 9.0
TICK_SECONDS = 1.0 / 60.0
IDLE_SECONDS = 0.25
_POINTER_FIELDS = ('yaw_object', 'rotor_object', 'blade1_object', 'blade2_object', 'blade3_object')
_POSE_FIELDS = manual_pose.POSE_FIELDS
_CLOCKS = {}
_updating = False
_registered = False


def is_manual(scene):
    try:
        return scene is not None and scene.get('wfrl_scene_kind') == 'manual'
    except ReferenceError:
        return False


def _scene_key(scene):
    return scene.as_pointer()


def _entry_key(scene, entry):
    return (_scene_key(scene), entry.turbine_id)


def visible_scenes():
    """A scene can move only while a window displays it."""
    try:
        return {window.scene.as_pointer() for window in bpy.context.window_manager.windows}
    except (AttributeError, ReferenceError):
        return set()


def selected_entries(scene):
    if not is_manual(scene):
        return []
    target = scene.wfrl_manual_target
    return [entry for entry in scene.wfrl_manual_turbines
            if target == 'ALL' or entry.turbine_id == target]


def validate_scene(scene):
    """Reject incomplete controls and objects shared with another scene."""
    if not is_manual(scene):
        return False, '请先进入手动控制场景'
    try:
        entries = scene.wfrl_manual_turbines
        if not entries:
            return False, '手动控制场景缺少风机'
        owner = scene.get('wfrl_manual_owner')
        if not owner:
            return False, '手动控制场景缺少对象归属标识'
        seen_objects, seen_ids = set(), set()
        for entry in entries:
            if not entry.turbine_id or entry.turbine_id in seen_ids:
                return False, '风机标识缺失或重复'
            seen_ids.add(entry.turbine_id)
            if any(not math.isfinite(float(getattr(entry, field))) for field in _POSE_FIELDS):
                return False, '控制参数必须是有限数值'
            objects = [getattr(entry, field) for field in _POINTER_FIELDS]
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
                    return False, '控制对象不属于此手动场景'
                key = obj.as_pointer()
                if key in seen_objects:
                    return False, '风机控制对象不能重复使用'
                seen_objects.add(key)
                if any(owner != scene for owner in obj.users_scene):
                    return False, '控制对象与其他场景共享，请使用独立副本'
        return True, ''
    except (AttributeError, ReferenceError, TypeError):
        return False, '控制对象已失效，请重新创建场景'


def _clock(scene, entry, now):
    key = _entry_key(scene, entry)
    if key not in _CLOCKS:
        _CLOCKS[key] = RotorClock(entry.azimuth_deg, entry.rpm, False, now)
    return _CLOCKS[key]


def _write_azimuth(entry, value):
    global _updating
    previous = _updating
    _updating = True
    try:
        entry.azimuth_deg = value
    finally:
        _updating = previous


def _apply_rotor(entry):
    if entry.rotor_object.rotation_mode != 'XYZ':
        entry.rotor_object.rotation_mode = 'XYZ'
    entry.rotor_object.rotation_euler.x = math.radians(entry.azimuth_deg)


def _apply_entry(entry):
    entry.yaw_object.rotation_mode = 'XYZ'
    entry.yaw_object.rotation_euler.z = math.radians(entry.yaw_deg)
    _apply_rotor(entry)
    for index in range(1, 4):
        blade = getattr(entry, f'blade{index}_object')
        blade.rotation_mode = 'XYZ'
        pitch = getattr(entry, f'pitch_b{index}_deg') if entry.individual_pitch else entry.pitch_deg
        blade.rotation_euler.z = -math.radians(pitch)


def _redraw(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def apply_scene(scene):
    """Apply saved manual poses without changing scene, playback or backend state."""
    valid, reason = validate_scene(scene)
    if not valid:
        pause_scene(scene)
        return False
    try:
        for entry in scene.wfrl_manual_turbines:
            _apply_entry(entry)
        _redraw(scene)
        return True
    except (ReferenceError, AttributeError):
        pause_scene(scene)
        return False


def pause_scene(scene, *, now=None):
    """Pause motion, retaining each turbine's RPM and final visible phase."""
    global _updating
    if not is_manual(scene):
        return
    valid, _reason = validate_scene(scene)
    now = time.monotonic() if now is None else now
    previous = _updating
    _updating = True
    try:
        for entry in scene.wfrl_manual_turbines:
            clock = _CLOCKS.pop(_entry_key(scene, entry), None)
            changed = clock is not None or entry.running
            if clock is not None:
                entry.azimuth_deg = clock.configure(now, running=False)
            if entry.running:
                entry.running = False
            if valid and changed:
                _apply_entry(entry)
    except (ReferenceError, AttributeError):
        pass
    finally:
        _updating = previous


def _update_entry(entry, context, field):
    global _updating
    if _updating:
        return
    scene = entry.id_data
    valid, _reason = validate_scene(scene)
    if not valid:
        pause_scene(scene)
        return
    now = time.monotonic()
    clock = _clock(scene, entry, now)
    angle = entry.azimuth_deg if field == 'azimuth_deg' else None
    if field == 'individual_pitch' and entry.individual_pitch:
        _updating = True
        try:
            for index in range(1, 4):
                setattr(entry, f'pitch_b{index}_deg', entry.pitch_deg)
        finally:
            _updating = False
    if field == 'pitch_deg':
        _updating = True
        try:
            for index in range(1, 4):
                setattr(entry, f'pitch_b{index}_deg', entry.pitch_deg)
        finally:
            _updating = False
    running = entry.running and entry.rpm > 0 and _scene_key(scene) in visible_scenes()
    _write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=running, azimuth_deg=angle))
    if entry.running != running:
        _updating = True
        try:
            entry.running = running
        finally:
            _updating = False
    _apply_entry(entry)
    sync_batch(scene)
    _redraw(scene)
    if running and entry.rpm > 0:
        _wake_timer()


def _update(field):
    def changed(entry, context):
        _update_entry(entry, context, field)
    return changed


def _batch_update(field):
    def changed(batch, context):
        if _updating:
            return
        scene = batch.id_data
        if not is_manual(scene) or scene.wfrl_manual_target != 'ALL':
            return
        valid, _reason = validate_scene(scene)
        if not valid:
            pause_scene(scene)
            return
        value = getattr(batch, field)
        for entry in scene.wfrl_manual_turbines:
            if field == 'individual_pitch' and entry.individual_pitch == value:
                continue
            setattr(entry, field, value)
        sync_batch(scene)
    return changed


def sync_batch(scene, *, fields=_POSE_FIELDS):
    """Display the first turbine's inputs without modifying any turbine."""
    global _updating
    if not is_manual(scene) or not scene.wfrl_manual_turbines:
        return
    previous = _updating
    _updating = True
    try:
        first = scene.wfrl_manual_turbines[0]
        for field in fields:
            setattr(scene.wfrl_manual_batch, field, getattr(first, field))
    finally:
        _updating = previous


def _selection_update(scene, context):
    sync_batch(scene)


def mixed_fields(scene):
    entries = list(scene.wfrl_manual_turbines)
    if not entries:
        return []
    return [field for field in _POSE_FIELDS
            if any(abs(float(getattr(entry, field)) - float(getattr(entries[0], field))) > 1e-4
                   for entry in entries[1:])]


def pose_snapshot(scene, *, now=None):
    """Capture all turbine parameters at one wall-clock instant."""
    now = time.monotonic() if now is None else now
    poses = {}
    for entry in scene.wfrl_manual_turbines:
        values = {field: getattr(entry, field) for field in _POSE_FIELDS}
        clock = _CLOCKS.get(_entry_key(scene, entry))
        if entry.running and clock is not None:
            values['azimuth_deg'] = replace(clock).sample(now)
        poses[entry.turbine_id] = manual_pose.pose_values(values)
    return poses


def _apply_poses(scene, poses, *, pause=True, now=None):
    """Validate every target before applying poses and synchronizing its clock."""
    global _updating
    valid, reason = validate_scene(scene)
    if not valid:
        raise ValueError(reason)
    entries = {entry.turbine_id: entry for entry in scene.wfrl_manual_turbines}
    if not poses or not set(poses) <= set(entries):
        raise ValueError('姿态文件中的风机与当前场景不匹配')
    checked = {tid: manual_pose.pose_values(values) for tid, values in poses.items()}
    now = time.monotonic() if now is None else now
    previous = _updating
    _updating = True
    try:
        for tid, values in checked.items():
            entry = entries[tid]
            running = not pause and entry.running and values['rpm'] > 0
            for field, value in values.items():
                setattr(entry, field, value)
            entry.running = running
            key = _entry_key(scene, entry)
            _CLOCKS.pop(key, None)
            if running:
                _CLOCKS[key] = RotorClock(entry.azimuth_deg, entry.rpm, True, now)
            _apply_entry(entry)
    finally:
        _updating = previous
    sync_batch(scene)
    _redraw(scene)


def _selected_pose_changes(scene, changes, *, now):
    poses = pose_snapshot(scene, now=now)
    result = {}
    for entry in selected_entries(scene):
        result[entry.turbine_id] = {**poses[entry.turbine_id], **changes}
    return result


def tick(now=None):
    """Advance visible manual scenes; invalid or hidden scenes remain paused."""
    now = time.monotonic() if now is None else now
    visible = visible_scenes()
    active_keys = set()
    moving = False
    for scene in bpy.data.scenes:
        if not is_manual(scene):
            continue
        valid, _reason = validate_scene(scene)
        if not valid or _scene_key(scene) not in visible:
            pause_scene(scene, now=now)
            continue
        changed = False
        for entry in scene.wfrl_manual_turbines:
            key = _entry_key(scene, entry)
            active_keys.add(key)
            if not entry.running:
                _CLOCKS.pop(key, None)
                continue
            clock = _clock(scene, entry, now)
            _write_azimuth(entry, clock.configure(now, rpm=entry.rpm, running=True))
            if entry.rpm > 0:
                _apply_rotor(entry)
                changed = True
                moving = True
        if changed:
            sync_batch(scene, fields=('azimuth_deg',))
            _redraw(scene)
    for key in tuple(_CLOCKS):
        if key not in active_keys:
            _CLOCKS.pop(key, None)
    return TICK_SECONDS if moving else IDLE_SECONDS


def _timer():
    if not _registered:
        return None
    try:
        return tick()
    except (AttributeError, ReferenceError, RuntimeError):
        pause_all()
        return IDLE_SECONDS


def _wake_timer():
    if not _registered:
        return
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def pause_all():
    for scene in getattr(bpy.data, 'scenes', ()):
        pause_scene(scene)
    _CLOCKS.clear()


@persistent
def _before_restore(_unused):
    pause_all()


@persistent
def _before_load(_unused):
    from . import manual_scene
    pause_all()
    manual_scene.clear_views()


@persistent
def _after_restore(_unused):
    pause_all()
    for scene in bpy.data.scenes:
        if is_manual(scene):
            apply_scene(scene)
            sync_batch(scene)
    if _registered and not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def _properties(update_factory):
    return {
        'yaw_deg': bpy.props.FloatProperty(name='机舱朝向 °', default=0.0, min=-180.0, max=180.0,
                                           precision=1, update=update_factory('yaw_deg')),
        'pitch_deg': bpy.props.FloatProperty(name='集体桨距 °', default=0.0, min=-30.0, max=90.0,
                                             description='统一设置三片叶片桨距，覆盖各片独立桨距',
                                             precision=1, update=update_factory('pitch_deg')),
        'rpm': bpy.props.FloatProperty(name='转速 RPM', default=DEFAULT_RPM, min=0.0, max=30.0,
                                       precision=2, update=update_factory('rpm')),
        'azimuth_deg': bpy.props.FloatProperty(name='叶轮角度 °', default=0.0, min=0.0, max=360.0,
                                               precision=1, update=update_factory('azimuth_deg')),
        'individual_pitch': bpy.props.BoolProperty(name='分别调整三片叶片', default=False,
                                                   update=update_factory('individual_pitch')),
        **{f'pitch_b{i}_deg': bpy.props.FloatProperty(name=f'叶片 {i} 桨距 °', default=0.0,
                                                      min=-30.0, max=90.0, precision=1,
                                                      update=update_factory(f'pitch_b{i}_deg'))
           for i in range(1, 4)},
    }


class WFRL_ManualTurbine(bpy.types.PropertyGroup):
    __annotations__ = {
        **_properties(_update),
        'turbine_id': bpy.props.StringProperty(),
        'running': bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'}, update=_update('running')),
        **{field: bpy.props.PointerProperty(type=bpy.types.Object) for field in _POINTER_FIELDS},
    }


class WFRL_ManualBatch(bpy.types.PropertyGroup):
    __annotations__ = _properties(_batch_update)


class WFRL_OT_ManualEnter(bpy.types.Operator):
    bl_idname = 'wfrl.manual_enter'
    bl_label = '进入手动控制场景'
    bl_description = '创建或进入独立的风机手动控制场景'

    @classmethod
    def poll(cls, context):
        from . import manual_scene
        if not manual_scene.can_enter(context):
            cls.poll_message_set(manual_scene.entry_error(context))
            return False
        return True

    def execute(self, context):
        from . import manual_scene
        existing_scenes = {scene.as_pointer() for scene in bpy.data.scenes}
        try:
            scene = manual_scene.enter(context)
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if (scene.as_pointer() not in existing_scenes
                and context.preferences.edit.use_global_undo):
            bpy.ops.ed.undo_push(message='创建风机手动控制场景')
        return {'FINISHED'}


class WFRL_OT_ManualLeave(bpy.types.Operator):
    bl_idname = 'wfrl.manual_leave'
    bl_label = '返回原场景'
    bl_description = '暂停手动风机并返回进入前的场景'

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene)

    def execute(self, context):
        from . import manual_scene
        try:
            manual_scene.leave(context)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualAction(bpy.types.Operator):
    bl_idname = 'wfrl.manual_action'
    bl_label = '风机手动控制'
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=(('START', '运行', '按当前 RPM 运行'),
                                         ('PAUSE', '暂停', '暂停运动并保留 RPM'),
                                         ('STOP', '停转', '将 RPM 设为 0 并暂停'),
                                         ('RESET', '复位', '恢复默认姿态与 9 RPM，保持暂停')))

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene) and bool(selected_entries(context.scene))

    def execute(self, context):
        global _updating
        scene = context.scene
        valid, reason = validate_scene(scene)
        if not valid:
            pause_scene(scene)
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        entries = selected_entries(scene)
        if self.action == 'START' and not any(entry.rpm > 0 for entry in entries):
            self.report({'WARNING'}, '请先设置大于 0 的转速')
            return {'CANCELLED'}
        now = time.monotonic()
        for entry in entries:
            clock = _clock(scene, entry, now)
            _write_azimuth(entry, clock.configure(now, running=False))
            _updating = True
            try:
                if self.action == 'STOP':
                    entry.rpm = 0.0
                elif self.action == 'RESET':
                    entry.yaw_deg = entry.pitch_deg = entry.azimuth_deg = 0.0
                    entry.rpm = DEFAULT_RPM
                    entry.individual_pitch = False
                    for index in range(1, 4):
                        setattr(entry, f'pitch_b{index}_deg', 0.0)
                entry.running = (self.action == 'START' and entry.rpm > 0
                                 and _scene_key(scene) in visible_scenes())
            finally:
                _updating = False
            clock.configure(now, rpm=entry.rpm, running=entry.running, azimuth_deg=entry.azimuth_deg)
            _apply_entry(entry)
        sync_batch(scene)
        _redraw(scene)
        if self.action == 'START':
            _wake_timer()
        return {'FINISHED'}


class WFRL_OT_ManualFocus(bpy.types.Operator):
    bl_idname = 'wfrl.manual_focus'
    bl_label = '观察选中风机'
    bl_description = '调整当前视口，观察所选风机或全部风机'

    @classmethod
    def poll(cls, context):
        return is_manual(context.scene) and bool(selected_entries(context.scene))

    def execute(self, context):
        from . import manual_scene
        try:
            manual_scene.focus(context, context.scene.wfrl_manual_target)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class _ManualPoseTarget:
    @classmethod
    def poll(cls, context):
        return is_manual(context.scene) and bool(selected_entries(context.scene))


class WFRL_OT_ManualPosePreset(_ManualPoseTarget, bpy.types.Operator):
    bl_idname = 'wfrl.manual_pose_preset'
    bl_label = '应用常用姿态'
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(items=(
        ('BLADE_DOWN', '叶片1朝下', '暂停所选风机，将叶片1转到叶轮盘下方，保留桨距、朝向与转速'),
        ('BLADE_SIDE', '叶片1侧向', '暂停所选风机，将叶片1转到叶轮盘侧方，保留桨距、朝向与转速'),
        ('FEATHER', '顺桨 90°', '暂停所选风机，关闭分别变桨并将三片桨距设为90°，保留朝向、叶轮角度与转速')))

    def execute(self, context):
        scene = context.scene
        try:
            valid, reason = validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            now = time.monotonic()
            poses = _selected_pose_changes(scene, manual_pose.preset_changes(self.preset), now=now)
            _apply_poses(scene, poses, now=now)
        except (ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualPoseNudge(_ManualPoseTarget, bpy.types.Operator):
    bl_idname = 'wfrl.manual_pose_nudge'
    bl_label = '微调角度'
    bl_description = '按各台风机当前值增减角度；角度限于预览范围，叶轮角度循环'
    bl_options = {'REGISTER', 'UNDO'}

    field: bpy.props.EnumProperty(items=tuple((name, label, '') for name, label in (
        ('yaw_deg', '机舱朝向'), ('pitch_deg', '统一桨距'), ('azimuth_deg', '叶轮角度'),
        ('pitch_b1_deg', '叶片1桨距'), ('pitch_b2_deg', '叶片2桨距'), ('pitch_b3_deg', '叶片3桨距'))))
    delta: bpy.props.FloatProperty(name='增量 °', default=1.0)

    def execute(self, context):
        scene = context.scene
        try:
            valid, reason = validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            entries = selected_entries(scene)
            if self.field == 'azimuth_deg' and any(entry.running and entry.rpm > 0 for entry in entries):
                raise ValueError('请先暂停所选风机，再微调叶轮角度')
            if self.field.startswith('pitch_b') and any(not entry.individual_pitch for entry in entries):
                raise ValueError('请先为所选风机启用分别变桨')
            now = time.monotonic()
            snapshot = pose_snapshot(scene, now=now)
            poses = {}
            for entry in entries:
                values = snapshot[entry.turbine_id]
                values[self.field] = manual_pose.adjusted_angle(self.field, values[self.field], self.delta)
                if self.field == 'pitch_deg':
                    for index in range(1, 4):
                        values[f'pitch_b{index}_deg'] = values['pitch_deg']
                poses[entry.turbine_id] = values
            _apply_poses(scene, poses, pause=False, now=now)
        except (ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualPoseExport(_ManualPoseTarget, bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_pose_export'
    bl_label = '保存三机姿态'
    bl_description = '将三台风机的姿态、桨距模式和转速保存为JSON，不保存运行状态'
    filename_ext = '.json'

    filter_glob: bpy.props.StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        temporary = None
        try:
            valid, reason = validate_scene(context.scene)
            if not valid:
                raise ValueError(reason)
            payload = manual_pose.encode_poses(pose_snapshot(context.scene))
            path = Path(bpy.path.abspath(self.filepath))
            if not path.name or path.is_dir():
                raise ValueError('请选择JSON文件位置')
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix='.' + path.name, suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            os.replace(temporary, path)
        except (OSError, ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, f'无法保存姿态：{exc}')
            return {'CANCELLED'}
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        self.report({'INFO'}, '三机姿态已保存')
        return {'FINISHED'}


class WFRL_OT_ManualPoseImport(_ManualPoseTarget, bpy.types.Operator, ImportHelper):
    bl_idname = 'wfrl.manual_pose_import'
    bl_label = '载入所选姿态'
    bl_description = '按T1/T2/T3编号恢复所选风机，保持暂停；未选风机保持原状态'
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.json'

    filter_glob: bpy.props.StringProperty(default='*.json', options={'HIDDEN'})

    def execute(self, context):
        try:
            valid, reason = validate_scene(context.scene)
            if not valid:
                raise ValueError(reason)
            path = Path(bpy.path.abspath(self.filepath))
            with path.open('r', encoding='utf-8') as stream:
                contents = stream.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                raise ValueError('姿态文件过大')
            poses = manual_pose.decode_poses(contents)
            ids = [entry.turbine_id for entry in selected_entries(context.scene)]
            missing = set(ids) - set(poses)
            if missing:
                raise ValueError('姿态文件缺少所选风机：' + '、'.join(sorted(missing)))
            _apply_poses(context.scene, {tid: poses[tid] for tid in ids})
        except (OSError, ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, f'无法载入姿态：{exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, '所选风机姿态已载入，保持暂停')
        return {'FINISHED'}


CLASSES = (WFRL_ManualTurbine, WFRL_ManualBatch, WFRL_OT_ManualEnter,
           WFRL_OT_ManualLeave, WFRL_OT_ManualAction, WFRL_OT_ManualFocus,
           WFRL_OT_ManualPosePreset, WFRL_OT_ManualPoseNudge,
           WFRL_OT_ManualPoseExport, WFRL_OT_ManualPoseImport)
_HANDLERS = (('load_pre', _before_load), ('load_post', _after_restore),
             ('undo_pre', _before_restore), ('undo_post', _after_restore),
             ('redo_pre', _before_restore), ('redo_post', _after_restore))
_SCENE_PROPERTIES = ('wfrl_manual_turbines', 'wfrl_manual_batch', 'wfrl_manual_target',
                     'wfrl_manual_return_scene', 'wfrl_manual_scene', 'wfrl_manual_step_deg')


def register():
    global _registered
    if _registered:
        return
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_turbines = bpy.props.CollectionProperty(type=WFRL_ManualTurbine)
    bpy.types.Scene.wfrl_manual_batch = bpy.props.PointerProperty(type=WFRL_ManualBatch)
    bpy.types.Scene.wfrl_manual_return_scene = bpy.props.PointerProperty(type=bpy.types.Scene)
    bpy.types.Scene.wfrl_manual_scene = bpy.props.PointerProperty(type=bpy.types.Scene)
    bpy.types.Scene.wfrl_manual_step_deg = bpy.props.FloatProperty(
        name='微调步长 °', description='加减按钮按每台风机的当前值调整，不统一已有差异',
        default=1.0, min=.1, max=15.0, precision=1)
    bpy.types.Scene.wfrl_manual_target = bpy.props.EnumProperty(
        name='控制对象', items=(('T1', 'T1', '控制风机 T1'), ('T2', 'T2', '控制风机 T2'),
                              ('T3', 'T3', '控制风机 T3'), ('ALL', '全部', '只将编辑的参数应用到全部风机')),
        default='T1', update=_selection_update)
    _registered = True
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)
    if hasattr(bpy.data, 'scenes'):
        pause_all()
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=TICK_SECONDS, persistent=True)


def unregister():
    global _registered
    from . import manual_scene
    _registered = False
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    pause_all()
    manual_scene.clear_views()
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for name in _SCENE_PROPERTIES:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
