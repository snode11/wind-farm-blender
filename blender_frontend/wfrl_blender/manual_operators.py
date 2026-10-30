"""Blender operators for manual turbine motion, trials and pose files."""
import os
from pathlib import Path
import tempfile
import time

import bpy
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import manual_control as control, manual_pose



class WFRL_OT_ManualRecheck(bpy.types.Operator):
    bl_idname = 'wfrl.manual_recheck'
    bl_label = '检查恢复（保持暂停）'
    bl_description = '检查所有机组的对象、原生控制和实际姿态；通过后清除保护提示，不启动运动'

    @classmethod
    def poll(cls, context):
        return control.is_manual(context.scene)

    def execute(self, context):
        valid, reason = control.recheck_feedback(context.scene)
        if not valid:
            self.report({'WARNING'}, reason)
            return {'CANCELLED'}
        self.report({'INFO'}, '检查通过，保持暂停；请手动运行或继续变桨')
        return {'FINISHED'}


class WFRL_OT_ManualPitchResume(bpy.types.Operator):
    bl_idname = 'wfrl.manual_pitch_resume'
    bl_label = '继续变桨'
    bl_description = '只继续所选风机的桨距过渡，不启动叶轮'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (control.is_manual(context.scene)
                and not (control.trial_active(context.scene) and context.scene.wfrl_manual_target in {'T1', 'ALL'})
                and any(control.pitch_ramping(entry) and entry.pitch_paused for entry in control.selected_entries(context.scene)))

    def execute(self, context):
        scene = context.scene
        turbine = ''
        try:
            valid, reason = control.validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            if scene.wfrl_manual_error:
                valid, reason = control.recheck_feedback(scene)
                if not valid:
                    raise ValueError(reason)
            now = time.monotonic()
            entries = control.selected_entries(scene)
            for entry in entries:
                turbine = entry.turbine_id
                control._validate_controlled_rotation(entry)
            for entry in entries:
                control._sync_pitch(scene, entry, now, running=True)
                control._apply_entry(entry)
            control._redraw(scene)
            control._wake_timer()
        except (ValueError, AttributeError, ReferenceError) as exc:
            control._protect_scene(scene, exc, turbine=turbine)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualTrial(bpy.types.Operator):
    bl_idname = 'wfrl.manual_trial'
    bl_label = 'T1 恒速变桨试验'
    bl_options = {'REGISTER', 'UNDO'}

    action: bpy.props.EnumProperty(items=(
        ('CONFIRM', '确认并开始', '整体校验，保存当前实际姿态并以恒定正 RPM 开始'),
        ('START', '继续', '从暂停的试验时间继续叶轮旋转和变桨'),
        ('PAUSE', '暂停', '同时冻结叶轮与三片桨距'),
        ('RESET', '复位试验', '暂停并恢复本次试验的起始姿态'),
        ('END', '结束试验', '暂停并保留当前姿态，允许调整下一组输入')))

    @classmethod
    def poll(cls, context):
        return control.is_manual(context.scene)

    def execute(self, context):
        try:
            if self.action == 'CONFIRM':
                control.confirm_trial(context.scene)
            else:
                control.trial_action(context.scene, self.action, undo_checkpoint=True)
        except (ValueError, KeyError, AttributeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_ManualDown(bpy.types.Operator):
    bl_idname = 'wfrl.manual_down'
    bl_label = 'T1 Down'
    bl_description = '从手动 T1 机舱固定下视同一叶轮；保留试验时间与运行状态'

    @classmethod
    def poll(cls, context):
        return control.is_manual(context.scene)

    def execute(self, context):
        from . import manual_scene
        try:
            manual_scene.down_view(context)
        except (ValueError, RuntimeError, AttributeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


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
        return control.is_manual(context.scene)

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

    action: bpy.props.EnumProperty(items=(('START', '运行', '按当前 RPM 运行，叶片变桨到 0°'),
                                         ('PAUSE', '暂停', '暂停运动并保留 RPM，叶片顺桨到 90°'),
                                         ('STOP', '停转', '将 RPM 设为 0 并暂停，叶片顺桨到 90°'),
                                         ('RESET', '复位', '恢复默认朝向、叶轮角度与 9 RPM，叶片立即顺桨 90°，保持暂停')))

    @classmethod
    def poll(cls, context):
        return control.is_manual(context.scene) and bool(control.selected_entries(context.scene))

    def execute(self, context):
        scene = context.scene
        valid, reason = control.validate_scene(scene)
        if not valid:
            control._protect_scene(scene, reason)
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}
        entries = control.selected_entries(scene)
        t1_trial = control.trial_active(scene) and any(entry.turbine_id == 'T1' for entry in entries)
        if t1_trial and self.action == 'STOP':
            self.report({'WARNING'}, '请先结束 T1 变桨试验，再修改 RPM')
            return {'CANCELLED'}
        if self.action == 'START' and not any(entry.rpm > 0 for entry in entries):
            self.report({'WARNING'}, '请先设置大于 0 的转速')
            return {'CANCELLED'}
        try:
            for entry in entries:
                if self.action == 'RESET':
                    control._validate_native_controls(getattr(entry, field) for field in control._POINTER_FIELDS)
                else:
                    control._validate_controlled_rotation(entry)
        except (ValueError, AttributeError, ReferenceError) as exc:
            control._protect_scene(scene, exc, turbine=entry.turbine_id)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if self.action == 'START' and scene.wfrl_manual_error:
            valid, reason = control.recheck_feedback(scene)
            if not valid:
                self.report({'ERROR'}, reason)
                return {'CANCELLED'}
        now = time.monotonic()
        if t1_trial and self.action == 'RESET':
            control._trial_undo_checkpoint(scene, now)
        for entry in entries:
            if t1_trial and entry.turbine_id == 'T1':
                control.trial_action(scene, self.action, now=now)
                continue
            clock = control._clock(scene, entry, now)
            control._write_azimuth(entry, clock.configure(now, running=False))
            control._updating = True
            try:
                if self.action == 'STOP':
                    entry.rpm = 0.0
                elif self.action == 'RESET':
                    entry.yaw_deg = entry.azimuth_deg = 0.0
                    entry.rpm = control.DEFAULT_RPM
                entry.running = (self.action == 'START' and entry.rpm > 0
                                 and control._scene_key(scene) in control.visible_scenes())
                if self.action != 'START' or entry.running:
                    control._command_pitch(entry, control.RUN_PITCH_DEG if entry.running else control.FEATHER_PITCH_DEG,
                                           now=now, settle=self.action == 'RESET')
            finally:
                control._updating = False
            clock.configure(now, rpm=entry.rpm, running=entry.running, azimuth_deg=entry.azimuth_deg)
            control._apply_entry(entry)
        if self.action == 'RESET' and scene.wfrl_manual_error:
            control.recheck_feedback(scene)
        elif self.action != 'START':
            control._pause_status(scene, 'USER')
        control.sync_batch(scene)
        control._redraw(scene)
        if self.action == 'START' or any(control.pitch_ramping(entry) for entry in entries):
            control._wake_timer()
        return {'FINISHED'}


class WFRL_OT_ManualFocus(bpy.types.Operator):
    bl_idname = 'wfrl.manual_focus'
    bl_label = '观察选中风机'
    bl_description = '调整当前视口，观察所选风机或全部风机'

    @classmethod
    def poll(cls, context):
        return control.is_manual(context.scene) and bool(control.selected_entries(context.scene))

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
        return control.is_manual(context.scene) and bool(control.selected_entries(context.scene))


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
            valid, reason = control.validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            now = time.monotonic()
            poses = control._selected_pose_changes(scene, manual_pose.preset_changes(self.preset), now=now)
            control._apply_poses(scene, poses, now=now)
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
            valid, reason = control.validate_scene(scene)
            if not valid:
                raise ValueError(reason)
            entries = control.selected_entries(scene)
            if self.field == 'azimuth_deg' and any(entry.running and entry.rpm > 0 for entry in entries):
                raise ValueError('请先暂停所选风机，再微调叶轮角度')
            if self.field.startswith('pitch_b') and any(not entry.individual_pitch for entry in entries):
                raise ValueError('请先为所选风机启用分别变桨')
            now = time.monotonic()
            snapshot = control.pose_snapshot(scene, now=now, actual_pitch=False)
            poses = {}
            for entry in entries:
                values = snapshot[entry.turbine_id]
                values[self.field] = manual_pose.adjusted_angle(self.field, values[self.field], self.delta)
                if self.field == 'pitch_deg':
                    for index in range(1, 4):
                        values[f'pitch_b{index}_deg'] = values['pitch_deg']
                poses[entry.turbine_id] = values
            control._apply_poses(scene, poses, pause=False, now=now, ramp=self.field in control._PITCH_FIELDS,
                         preserve_pitch=self.field not in control._PITCH_FIELDS)
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
            valid, reason = control.validate_scene(context.scene)
            if not valid:
                raise ValueError(reason)
            payload = manual_pose.encode_poses(control.pose_snapshot(context.scene))
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
            valid, reason = control.validate_scene(context.scene)
            if not valid:
                raise ValueError(reason)
            path = Path(bpy.path.abspath(self.filepath))
            with path.open('r', encoding='utf-8') as stream:
                contents = stream.read(1024 * 1024 + 1)
            if len(contents) > 1024 * 1024:
                raise ValueError('姿态文件过大')
            poses = manual_pose.decode_poses(contents)
            ids = [entry.turbine_id for entry in control.selected_entries(context.scene)]
            missing = set(ids) - set(poses)
            if missing:
                raise ValueError('姿态文件缺少所选风机：' + '、'.join(sorted(missing)))
            control._apply_poses(context.scene, {tid: poses[tid] for tid in ids})
        except (OSError, ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, f'无法载入姿态：{exc}')
            return {'CANCELLED'}
        self.report({'INFO'}, '所选风机姿态已载入，保持暂停')
        return {'FINISHED'}


CLASSES = (
    WFRL_OT_ManualRecheck,
    WFRL_OT_ManualPitchResume,
    WFRL_OT_ManualTrial,
    WFRL_OT_ManualDown,
    WFRL_OT_ManualEnter,
    WFRL_OT_ManualLeave,
    WFRL_OT_ManualAction,
    WFRL_OT_ManualFocus,
    WFRL_OT_ManualPosePreset,
    WFRL_OT_ManualPoseNudge,
    WFRL_OT_ManualPoseExport,
    WFRL_OT_ManualPoseImport,
)
