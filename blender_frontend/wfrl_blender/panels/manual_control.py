"""A dedicated sidebar for independent rigid turbine motion."""
import bpy
import unicodedata

from .. import manual_control, manual_video


def _feedback_lines(text, width):
    """Wrap Chinese and object names within the sidebar width."""
    line, used = '', 0
    for char in text:
        size = 2 if unicodedata.east_asian_width(char) in {'W', 'F'} else 1
        if char == '\n' or (line and used + size > width):
            yield line
            line, used = '', 0
        if char != '\n':
            line += char
            used += size
    if line:
        yield line


def draw_feedback(layout, context):
    scene = context.scene
    if scene.wfrl_manual_error:
        box = layout.box()
        title = box.row()
        title.alert = True
        title.label(text=f'保护暂停 · {scene.wfrl_manual_error_turbine}', icon='ERROR')
        box.label(text='本场景全部机组保持暂停')
        scale = getattr(context.preferences.system, 'ui_scale', 1.0)
        width = max(16, int((context.region.width / max(scale, .5) - 50) / 7))
        for message in (scene.wfrl_manual_error.split('；', 1)[0], scene.wfrl_manual_recovery):
            for line in _feedback_lines(message, width):
                box.label(text=line)
        box.operator('wfrl.manual_recheck', icon='FILE_REFRESH')
        box.label(text='检查通过后，再手动运行或继续变桨')
        return
    states = {'USER': '已主动暂停', 'HIDDEN': '场景未在窗口显示，已暂停',
              'RESTORED': '载入或撤销恢复，已暂停', 'READY': '检查通过，保持暂停'}
    text = states.get(scene.wfrl_manual_status)
    if text and not any(entry.running or (manual_control.pitch_ramping(entry) and not entry.pitch_paused)
                        for entry in scene.wfrl_manual_turbines):
        layout.label(text=text, icon='INFO')


def draw_trial(layout, scene):
    trial = scene.wfrl_manual_trial
    entry = next(item for item in scene.wfrl_manual_turbines if item.turbine_id == 'T1')
    active = manual_control.trial_active(scene)
    box = layout.box()
    box.label(text='T1 恒速变桨试验')
    from ..turbine_geometry import model_label
    box.label(text='叶片模型：' + model_label(scene.get('wfrl_manual_blade_model', 'rootkink')))
    inputs = box.column(align=True)
    inputs.enabled = not active
    inputs.prop(entry, 'rpm', text='恒定 RPM')
    for index in range(1, 4):
        row = inputs.row(align=True)
        row.prop(trial, f'select_b{index}', text=f'B{index}')
        target = row.row(align=True)
        target.enabled = getattr(trial, f'select_b{index}')
        target.prop(trial, f'target_b{index}_deg', text='目标 °')
    inputs.prop(trial, 'duration_s')
    inputs.prop(trial, 'interpolation')
    inputs.operator('wfrl.manual_trial', text='确认并开始', icon='PLAY').action = 'CONFIRM'
    if active:
        _start, spec = manual_control.trial_configuration(scene)
        state = '变桨中' if trial.elapsed_s < spec.duration_s else '保持目标'
        box.label(text=f'{trial.elapsed_s:.2f} s · {state} · {spec.rpm:.2f} RPM')
        row = box.row(align=True)
        row.operator('wfrl.manual_trial', text='暂停' if entry.running else '继续',
                     icon='PAUSE' if entry.running else 'PLAY').action = 'PAUSE' if entry.running else 'START'
        row.operator('wfrl.manual_trial', text='复位试验', icon='LOOP_BACK').action = 'RESET'
        box.operator('wfrl.manual_trial', text='结束试验，保留当前姿态').action = 'END'
        box.label(text='修改起始姿态或 RPM 前先结束试验', icon='INFO')
    else:
        box.label(text='目标仅暂存；未选叶片保持实际桨距')
    pitches = manual_control.effective_pitches(entry)
    box.label(text='当前 ' + ' / '.join(f'B{i} {pitch:.1f}°' for i, pitch in enumerate(pitches, 1)))
    row = box.row(align=True)
    row.operator('wfrl.manual_down', text='T1 Down', icon='CAMERA_DATA')
    row.operator('wfrl.manual_focus', text='返回整机', icon='VIEWZOOM')


class WFRL_PT_ManualControl(bpy.types.Panel):
    bl_label = '风机控制'
    bl_idname = 'WFRL_PT_manual_control'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = '风机控制'
    bl_order = -300

    def draw(self, context):
        layout, scene = self.layout, context.scene
        layout.use_property_split = True
        layout.use_property_decorate = False
        if not manual_control.is_manual(scene):
            layout.label(text='自由调整风机姿态与转速')
            layout.operator('wfrl.manual_enter', text='创建 / 进入手动场景', icon='OUTLINER_OB_EMPTY')
            layout.label(text='独立场景 · 刚性运动示意', icon='INFO')
            return

        layout.operator('wfrl.manual_leave', text='返回原场景', icon='BACK')
        draw_feedback(layout, context)
        valid, reason = manual_control.validate_scene(scene)
        if not valid:
            layout.label(text=reason, icon='ERROR')
            layout.operator('wfrl.manual_enter', text='重新创建手动场景', icon='FILE_REFRESH')
            return
        layout.prop(scene, 'wfrl_manual_target', expand=True)
        layout.prop(scene, 'wfrl_manual_display_detail')
        layout.label(text='固定精度 · Down / 视频使用近景')
        layout.label(text='静帧渲染请选近景')
        if scene.get('wfrl_manual_geometry_error'):
            layout.label(text=scene['wfrl_manual_geometry_error'], icon='ERROR')
        layout.operator('wfrl.manual_focus', icon='VIEWZOOM')
        entries = manual_control.selected_entries(scene)
        if not entries:
            layout.label(text='未找到所选风机', icon='ERROR')
            return
        target = scene.wfrl_manual_batch if scene.wfrl_manual_target == 'ALL' else entries[0]
        all_target = scene.wfrl_manual_target == 'ALL'
        trial_locked = manual_control.trial_active(scene) and any(entry.turbine_id == 'T1' for entry in entries)
        if scene.wfrl_manual_target in {'T1', 'ALL'}:
            draw_trial(layout, scene)
            manual_video.draw(layout, scene)
        mixed = set(manual_control.mixed_fields(scene)) if all_target else set()
        if all_target:
            layout.label(text='编辑仅同步当前字段', icon='INFO')
            if mixed:
                layout.label(text='* 表示多值，输入框显示 T1')

        presets = layout.row(align=True)
        presets.enabled = not trial_locked
        for preset, label in (('BLADE_DOWN', '叶片1朝下'), ('BLADE_SIDE', '叶片1侧向'), ('FEATHER', '顺桨90°')):
            presets.operator('wfrl.manual_pose_preset', text=label).preset = preset
        layout.prop(scene, 'wfrl_manual_step_deg')

        def draw_property(parent, field, *, text=None, slider=False):
            row = parent.row(align=True)
            label = text or target.bl_rna.properties[field].name
            row.prop(target, field, text=label + (' *' if field in mixed else ''), slider=slider)
            if field.endswith('_deg'):
                for sign, icon in ((-1, 'REMOVE'), (1, 'ADD')):
                    operator = row.operator('wfrl.manual_pose_nudge', text='', icon=icon)
                    operator.field, operator.delta = field, sign * scene.wfrl_manual_step_deg

        pose = layout.column(align=True)
        pose.enabled = not trial_locked
        draw_property(pose, 'yaw_deg', slider=True)
        draw_property(pose, 'pitch_deg', text='三片统一桨距 °', slider=True)
        if any(entry.individual_pitch for entry in entries):
            pose.label(text='修改此项会覆盖独立桨距', icon='INFO')
        draw_property(pose, 'rpm', slider=True)
        draw_property(pose, 'individual_pitch')
        if all(entry.individual_pitch for entry in entries):
            for index in range(1, 4):
                draw_property(pose, f'pitch_b{index}_deg', slider=True)
        elif any(entry.individual_pitch for entry in entries):
            pose.label(text='桨距模式不同，可选择单台编辑', icon='INFO')
        pose.prop(scene, 'wfrl_manual_pitch_rate_deg_s')
        for entry in entries:
            if manual_control.pitch_ramping(entry):
                actual = ' / '.join(f'{value:.1f}°' for value in manual_control.effective_pitches(entry))
                pose.label(text=f'{entry.turbine_id} 当前桨距 {actual}')
        if any(manual_control.pitch_ramping(entry) and entry.pitch_paused for entry in entries):
            pose.operator('wfrl.manual_pitch_resume', icon='PLAY')
        angle = layout.column()
        angle.enabled = not trial_locked and not any(entry.running and entry.rpm > 0 for entry in entries)
        draw_property(angle, 'azimuth_deg', slider=True)

        has_positive_rpm = any(entry.rpm > 0 for entry in entries)
        if not has_positive_rpm:
            layout.label(text='请先设置转速', icon='INFO')
        elif any(entry.rpm == 0 for entry in entries):
            layout.label(text='零 RPM 风机保持暂停', icon='INFO')
        row = layout.row(align=True)
        row.enabled = not (trial_locked and scene.wfrl_manual_target == 'T1')
        start = row.row(align=True)
        start.enabled = has_positive_rpm
        start.operator('wfrl.manual_action', text='运行', icon='PLAY').action = 'START'
        row.operator('wfrl.manual_action', text='暂停', icon='PAUSE').action = 'PAUSE'
        row = layout.row(align=True)
        stop = row.row(align=True)
        stop.enabled = not trial_locked
        stop.operator('wfrl.manual_action', text='停转', icon='SNAP_FACE').action = 'STOP'
        reset = row.row(align=True)
        reset.enabled = not (trial_locked and scene.wfrl_manual_target == 'T1')
        reset.operator('wfrl.manual_action', text='复位所选' if trial_locked else '复位', icon='LOOP_BACK').action = 'RESET'
        if all_target:
            header, body = layout.panel('wfrl_manual_actual_values', default_closed=False)
            header.label(text='三机实际参数')
            if body:
                for entry in entries:
                    box = body.box()
                    state = '运行中' if entry.running and entry.rpm > 0 else '已暂停'
                    box.label(text=f'{entry.turbine_id}  ·  {state}  ·  {entry.rpm:.2f} RPM')
                    box.label(text=f'朝向 {entry.yaw_deg:.1f}°  ·  叶轮 {entry.azimuth_deg:.1f}°')
                    pitches = ' / '.join(f'{value:.1f}°' for value in manual_control.effective_pitches(entry))
                    box.label(text=f'实际桨距  {pitches}')
        else:
            entry = entries[0]
            state = '运行中' if entry.running and entry.rpm > 0 else '已暂停'
            layout.label(text=f'{entry.turbine_id}  {state}  ·  {entry.rpm:.2f} RPM')
        header, body = layout.panel('wfrl_manual_down', default_closed=False)
        header.label(text='Down 相机与叶片外形')
        if body:
            shape = body.column()
            shape.enabled = not manual_control.trial_active(scene)
            shape.prop(scene, 'wfrl_manual_blade_shape')
            body.prop(scene, 'wfrl_manual_blade_labels')
            row = body.row(align=True)
            for turbine in ('T1', 'T2', 'T3'):
                row.operator('wfrl.manual_down_view', text=f'{turbine} Down').turbine = turbine
            export = body.column()
            export.enabled = not manual_control.trial_active(scene)
            export.operator('wfrl.manual_down_video', text='导出所选 Down MP4…', icon='RENDER_ANIMATION')
            body.label(text='冻结试验使用上方的 T1 后台视频导出')
        header, body = layout.panel('wfrl_manual_pose_files', default_closed=False)
        header.label(text='姿态文件')
        if body:
            row = body.row(align=True)
            row.operator('wfrl.manual_pose_export', text='保存三机…', icon='EXPORT')
            load = row.row(align=True)
            load.enabled = not trial_locked
            load.operator('wfrl.manual_pose_import', text='载入所选…', icon='IMPORT')
            body.label(text='按机组编号载入，保持暂停')
        layout.separator()
        layout.label(text='刚性运动示意 · 不计算功率或载荷', icon='INFO')
        layout.label(text='参数范围仅供预览，不是实体运行限值')


CLASSES = (WFRL_PT_ManualControl,)
