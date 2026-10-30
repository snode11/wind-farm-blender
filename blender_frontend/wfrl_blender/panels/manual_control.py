"""A dedicated sidebar for independent rigid turbine motion."""
import bpy

from .. import manual_control


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
        valid, reason = manual_control.validate_scene(scene)
        if not valid:
            layout.label(text=reason, icon='ERROR')
            layout.operator('wfrl.manual_enter', text='重新创建手动场景', icon='FILE_REFRESH')
            return
        layout.prop(scene, 'wfrl_manual_target', expand=True)
        layout.operator('wfrl.manual_focus', icon='VIEWZOOM')
        entries = manual_control.selected_entries(scene)
        if not entries:
            layout.label(text='未找到所选风机', icon='ERROR')
            return
        target = scene.wfrl_manual_batch if scene.wfrl_manual_target == 'ALL' else entries[0]
        all_target = scene.wfrl_manual_target == 'ALL'
        mixed = set(manual_control.mixed_fields(scene)) if all_target else set()
        if all_target:
            layout.label(text='编辑仅同步当前字段', icon='INFO')
            if mixed:
                layout.label(text='* 表示多值，输入框显示 T1')

        presets = layout.row(align=True)
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
        angle = layout.column()
        angle.enabled = not any(entry.running and entry.rpm > 0 for entry in entries)
        draw_property(angle, 'azimuth_deg', slider=True)

        has_positive_rpm = any(entry.rpm > 0 for entry in entries)
        if not has_positive_rpm:
            layout.label(text='请先设置转速', icon='INFO')
        elif any(entry.rpm == 0 for entry in entries):
            layout.label(text='零 RPM 风机保持暂停', icon='INFO')
        row = layout.row(align=True)
        start = row.row(align=True)
        start.enabled = has_positive_rpm
        start.operator('wfrl.manual_action', text='运行', icon='PLAY').action = 'START'
        row.operator('wfrl.manual_action', text='暂停', icon='PAUSE').action = 'PAUSE'
        row = layout.row(align=True)
        row.operator('wfrl.manual_action', text='停转', icon='SNAP_FACE').action = 'STOP'
        row.operator('wfrl.manual_action', text='复位', icon='LOOP_BACK').action = 'RESET'
        if all_target:
            header, body = layout.panel('wfrl_manual_actual_values', default_closed=False)
            header.label(text='三机实际参数')
            if body:
                for entry in entries:
                    box = body.box()
                    state = '运行中' if entry.running and entry.rpm > 0 else '已暂停'
                    box.label(text=f'{entry.turbine_id}  ·  {state}  ·  {entry.rpm:.2f} RPM')
                    box.label(text=f'朝向 {entry.yaw_deg:.1f}°  ·  叶轮 {entry.azimuth_deg:.1f}°')
                    if entry.individual_pitch:
                        pitches = ' / '.join(f'{getattr(entry, f"pitch_b{i}_deg"):.1f}°' for i in range(1, 4))
                        box.label(text=f'独立桨距  {pitches}')
                    else:
                        box.label(text=f'统一桨距  {entry.pitch_deg:.1f}°')
        else:
            entry = entries[0]
            state = '运行中' if entry.running and entry.rpm > 0 else '已暂停'
            layout.label(text=f'{entry.turbine_id}  {state}  ·  {entry.rpm:.2f} RPM')
        header, body = layout.panel('wfrl_manual_pose_files', default_closed=False)
        header.label(text='姿态文件')
        if body:
            row = body.row(align=True)
            row.operator('wfrl.manual_pose_export', text='保存三机…', icon='EXPORT')
            row.operator('wfrl.manual_pose_import', text='载入所选…', icon='IMPORT')
            body.label(text='按机组编号载入，保持暂停')
        layout.separator()
        layout.label(text='刚性运动示意 · 不计算功率或载荷', icon='INFO')


CLASSES = (WFRL_PT_ManualControl,)
