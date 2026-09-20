"""One sidebar entry for the dedicated three-turbine recorded-result player."""
import bpy
from .. import clearance_replay, farm_flex
from .clearance import number, playback_control


def unified_panel_active(scene):
    return (hasattr(bpy.types, 'WFRL_PT_FarmFlex') and farm_flex.is_active(scene))


def section(layout, key, title):
    header, body = layout.panel(key, default_closed=True)
    header.label(text=title)
    return body


def draw_radar(layout, scene, value):
    card = layout.box()
    from ..radar_feedback import alarm_state, icon_id, LABELS, PULSE_SECONDS
    state = alarm_state(value)
    header = card.row(align=True)
    header.label(text=f"雷达与净空 · {scene.get('wfrl_clearance_turbine', 'T1')} · B2")
    lamp = header.row(align=True)
    lamp.alignment = 'RIGHT'
    lamp.label(text='', icon_value=icon_id(state))
    card.label(text=LABELS[state], icon_value=icon_id(state))
    measurement = (value or {}).get('measurement') or {}
    status = card.column(align=True)
    if measurement:
        age = value['measurement_age_s']
        fps = scene.get('wfrl_clearance_timebase_fps', scene.render.fps / scene.render.fps_base)
        fresh = age <= 1 / fps + 1e-9
        status.label(text=('新测量' if fresh else '上次有效测量') + f" · 叶片 {measurement['blade_id']}", icon='TIME')
        status.label(text=f"时刻 {measurement['time_s']:.2f} s · {age:.2f} 秒前")
    else:
        status.label(text='等待下一次有效测量' if value else '回放数据未就绪', icon='INFO')
        status.label(text='测量时刻 — · 读数年龄 —')
    card.label(text='雷达到叶片距离：' + number(measurement.get('slant_range_m')) + ' m')
    clearance = card.column(align=True)
    clearance.label(text='叶尖—塔筒净空')
    clearance.label(text='仿真真值：' + number(measurement.get('truth_m')) + ' m')
    clearance.label(text='B2 估计：' + number(measurement.get('estimate_m')) + ' m')
    clearance.label(text='估计误差：' + number(measurement.get('error_m'), True) + ' m')
    reader = clearance_replay.reader_for(scene)
    if reader is not None:
        config = reader.package.manifest['replay']
        card.label(text=f"演示报警阈值 ≤ {config['threshold_m']:.2f} m · B2 估计")
    card.label(text='浅蓝：光束方向 · 亮橙：有效测量')
    details = section(card, 'wfrl_farm_measurement_details', '测量详情与统计')
    if details is not None:
        if reader is not None:
            details.label(text=f"报警解除 ≥ {config['threshold_m'] + config['hysteresis_m']:.2f} m")
        details.label(text='灯色随上次有效读数保留，过期转灰')
        details.label(text=f'光束亮起保留 {PULSE_SECONDS:.1f} 仿真秒，仅为提示')
        details.label(text='误差 = 估计 − 真值；正值为高估')
        if farm_flex.is_active(scene) and farm_flex._ACTIVE.tower_motion is not None:
            details.label(text='真值：叶尖表面至同高度塔壁' if farm_flex._ACTIVE.reference is not None else '真值：同高度的变形塔筒截面距离')
            details.label(text='固定标定估计未补偿塔架弯曲')
        if value:
            stats = value['statistics']
            details.label(text=f"仿真时间：{value['time_s']:.2f} s")
            details.label(text='截至当前回放位置')
            details.label(text='平均绝对误差：' + number(stats.get('mae_m')) + ' m')
            details.label(text='最大绝对误差：' + number(stats.get('max_abs_error_m')) + ' m')
            details.label(text=f"B2 有效样本：{stats['valid_samples']} / {stats['expected_samples']}")
            ratio = stats.get('valid_ratio')
            details.label(text='预期样本中有效：' + ('不适用' if ratio is None else f'{ratio:.1%}'))
            details.label(text='分母：评估网格内预期样本')
            details.label(text=f"经过测量区：{stats['passage_count']} 次")
            details.label(text=(f"整次漏测：{stats['missed_passage_count']} 次"
                               if scene.frame_current >= scene.frame_end else '整次漏测：片尾汇总'))
        else:
            details.label(text='暂无有效回放数据')


def draw_playback(layout, context, value, reader):
    """One shared clock and three transport actions, above every task page."""
    scene = context.scene
    duration = reader.end_s - reader.start_s if reader else 0
    playback = layout.column(align=True)
    text, icon, state = playback_control(scene, context.screen)
    playback.label(text=(f"仿真 {value['time_s']:.3f} s · {state}" if value else '回放数据未就绪'))
    row = playback.row(align=True)
    row.scale_y = 1.25
    row.operator('wfrl.clearance_playback', text=text, icon=icon)
    row.operator('wfrl.farm_transport', text='单步', icon='NEXT_KEYFRAME').action = 'STEP'
    row.operator('wfrl.farm_transport', text='复位', icon='LOOP_BACK').action = 'RESET'
    elapsed = max(0, value['time_s'] - reader.start_s) if value and reader else 0
    playback.prop(scene, 'wfrl_clearance_progress', text=f'{elapsed:.1f} / {duration:.0f} 秒', slider=True)


def draw_views(layout, context):
    scene = context.scene
    draw_turbine_views(layout, scene)
    row = layout.row(align=True)
    op = row.operator('wfrl.farm_flex_view', text='T1 侧前方')
    op.turbine = 'T1'; op.angle = 'FRONT'
    op = row.operator('wfrl.farm_flex_view', text='风场全景')
    op.turbine = 'all'; op.angle = 'DOWN'
    row = layout.row(align=True)
    row.operator('wfrl.clearance_view', text='测量区侧视').view = 'MEASUREMENT'
    row.operator('wfrl.clearance_view', text='雷达特写').view = 'RADAR'
    row = layout.row(align=True)
    for view in ('Top', 'Side'):
        row.operator('wfrl.select_camera', text=view).camera_name = 'WFRL.Camera.' + view
    layout.prop(scene, 'wfrl_flex_show_tip_trails', text='显示叶尖轨迹')
    layout.label(text='叶片 1 红 · 2 绿 · 3 蓝')
    gimbal = section(layout, 'wfrl_farm_gimbal', '云台控制')
    if gimbal is not None:
        from .gimbal import draw_gimbal_controls
        gimbal.label(text='当前机组：' + scene.get('wfrl_clearance_turbine', 'T1'))
        draw_gimbal_controls(gimbal, context, allow_turbine_selection=False)


def draw_turbine_views(layout, scene):
    """Keep the three Down buttons, plus one mounted-camera entry."""
    row = layout.row(align=True)
    for tid in farm_flex._ACTIVE.readers:
        op = row.operator('wfrl.farm_flex_view', text=tid + ' Down')
        op.turbine = tid
        op.angle = 'DOWN'
    row = layout.row(align=True)
    op = row.operator('wfrl.farm_flex_view', text='机舱相机', icon='CAMERA_DATA')
    op.turbine = scene.get('wfrl_clearance_turbine', 'T1')
    op.angle = 'NACELLE'
    row.label(text=op.turbine)
    if scene.wfrl_gimbal_kind == 'NACELLE':
        from . import gimbal
        row = layout.row(align=True)
        row.operator('wfrl.gimbal_mode', text='退出摇杆' if gimbal._ACTIVE else '云台摇杆', icon='ORIENTATION_GIMBAL')
        row.operator('wfrl.gimbal_preset', text='相机复位').preset = 'RESET'


def draw_tools(layout, context):
    scene = context.scene
    views = section(layout, 'wfrl_farm_tools_views', '机组、视角与云台')
    if views is not None:
        draw_views(views, context)

    telemetry = section(layout, 'wfrl_farm_telemetry', '三机遥测、曲线与导出')
    if telemetry is not None:
        telemetry.prop(scene, 'wfrl_channel_telemetry', text='采样遥测与曲线')
        from .. import charts
        for tid in farm_flex._ACTIVE.readers:
            card = telemetry.box()
            card.label(text=tid)
            for key in ('yaw', 'pitch', 'rotor_speed', 'power', 'torque', 'load'):
                points = charts.history.points(tid, key)
                point = points[-1] if points else None
                card.label(text=charts.LABELS[key] + ': ' + (f'{point.value:.2f} {point.unit}' if point and point.value is not None else '—'))
        power = charts.history.points(charts.FARM, 'power')
        telemetry.label(text='风场功率：' + (f'{power[-1].value:.2f} MW' if power and power[-1].value is not None else '—'))
        telemetry.label(text='变桨为三叶片平均；载荷为 B1 叶根弯矩')
        telemetry.label(text='奖励未记录；不填补缺失通道')
        telemetry.label(text='FAST.Farm / REVIEW_ONLY · 关闭采样时读数冻结')
        telemetry.prop(scene, 'wfrl_chart_visible', text='显示曲线')
        telemetry.prop(scene, 'wfrl_chart_channel', text='曲线通道')
        telemetry.prop(scene, 'wfrl_selected_turbine', text='曲线机组')
        telemetry.operator('wfrl.export_history', text='导出已采样历史', icon='EXPORT')
        telemetry.label(text=scene.get('wfrl_history_export_status', '每通道最多 600 点'))
    presentation = section(layout, 'wfrl_farm_environment', '画面与环境')
    if presentation is not None:
        presentation.prop(scene, 'wfrl_show_atmosphere', text='环境显示')
        presentation.prop(scene, 'wfrl_atmosphere_preset', text='环境')
        presentation.prop(scene, 'wfrl_wake_quality', text='画面质量')
        presentation.prop(scene, 'wfrl_show_wake', text='显示尾流示意（非物理解算）')
        presentation.prop(scene, 'wfrl_wake_display', text='尾流样式')
        presentation.operator('wfrl.presentation_mode', text='演示 / 编辑界面')
    presentation = section(layout, 'wfrl_farm_capture', '截图、录制与渲染')
    if presentation is not None:
        presentation.prop(scene, 'wfrl_capture_directory', text='截图录制目录')
        presentation.operator('wfrl.capture_screenshot', text='保存截图')
        presentation.prop(scene, 'wfrl_capture_fps', text='录制帧率')
        presentation.prop(scene, 'wfrl_capture_duration', text='录制时长')
        presentation.operator('wfrl.capture_recording', text='录制窗口 PNG 序列')
        presentation.label(text=scene.get('wfrl_capture_status', '录制就绪'))
        row = presentation.row(align=True)
        row.operator('wfrl.render_still', text='渲染静帧')
        row.operator('wfrl.render_animation', text='渲染动画')
    data = section(layout, 'wfrl_farm_data', '数据来源与重新加载')
    if data is not None:
        if farm_flex._ACTIVE.manifest.get('interface_only'):
            data.label(text='OpenFAST · 单机接口验证 · REVIEW_ONLY')
            data.label(text='8 m/s 恒定风 · 规定 9 rpm')
        else:
            data.label(text='FAST.Farm · 离线结果 · REVIEW_ONLY')
            data.label(text='三机随机阵风 · 九片叶片独立形变')
            data.label(text='MAPPO 控制偏航；基线负责转矩、变桨')
        data.label(text='轨迹为形变网格叶尖位置，非相机测量')
        data.label(text="红 / 绿 / 蓝：叶片 1 / 2 / 3")
        data.label(text="相邻两圈定格对比 2 秒后淡出")
        data.label(text='B2 手册简化估算 · 理想测距')
        data.label(text='光束为示意，不作安全判定')
        data.label(text='开发验收包 · 数值细化待验证')
        data.operator('wfrl.load_demo', text='重新加载当前结果', icon='FILE_REFRESH').package_path = scene['wfrl_farm_flex_path']
        data.operator('wfrl.clearance_restart', text='从头重播', icon='REW')
        path = data.column(); path.enabled = False
        path.prop(scene, '["wfrl_farm_flex_path"]', text='数据目录')


def draw(layout, context):
    scene = context.scene
    value = clearance_replay.sample(scene)
    reader = clearance_replay.reader_for(scene)
    layout.use_property_decorate = False
    if farm_flex._ACTIVE.reference is not None:
        layout.label(text='单机预弯接口验证' if farm_flex._ACTIVE.manifest.get('interface_only') else 'NREL 5MW 预弯改型 · BeamDyn')
    if farm_flex._ACTIVE.manifest.get('acceptance_status', '').startswith('FAILED'):
        layout.label(text='测量覆盖未达演示目标', icon='ERROR')
    draw_playback(layout, context, value, reader)
    tabs = layout.row(align=True)
    tabs.scale_y = 1.25
    tabs.prop(scene, 'wfrl_farm_panel_page', expand=True)
    if scene.wfrl_farm_panel_page == 'DEFLECTION':
        from ..deflection import draw_panel
        draw_panel(layout, scene)
    elif scene.wfrl_farm_panel_page == 'RADAR':
        draw_turbine_views(layout, scene)
        draw_radar(layout, scene, value)
    else:
        draw_tools(layout, context)


def build_review_cameras(scene):
    from ..cameras import ensure_nacelle_gimbal
    for tid in farm_flex._ACTIVE.readers:
        ensure_nacelle_gimbal(scene, tid)
    from mathutils import Vector
    tip = bpy.data.objects.new('WFRL.Camera.T1.TipComparison', bpy.data.cameras.new('T1TipComparison'))
    bpy.data.collections['WFRL_Scene'].objects.link(tip)
    tip.data.type = 'ORTHO'; tip.data.ortho_scale = 28
    tip.data.clip_start = .1; tip.data.clip_end = 3000
    tip.data.show_passepartout = False
    camera=bpy.data.objects.new('WFRL.Camera.FarmFlexOverview',bpy.data.cameras.new('FarmFlexOverview'))
    bpy.data.collections['WFRL_Scene'].objects.link(camera)
    camera.location=(-450,-1250,520)
    camera.rotation_euler=(Vector((504,0,65))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type='ORTHO';camera.data.ortho_scale=1500
    camera.data.clip_start=.1;camera.data.clip_end=30000
    camera.data.show_passepartout=False
    
    
    # A fixed front-quarter view frames the full rotor and its tip paths.
    front_camera=bpy.data.objects.new('WFRL.Camera.T1.FrontQuarter',bpy.data.cameras.new('T1FrontQuarter'))
    bpy.data.collections['WFRL_Scene'].objects.link(front_camera)
    front_camera.location=(-245,-140,125)
    front_camera.rotation_euler=(Vector((-5,0,90))-front_camera.location).to_track_quat('-Z','Y').to_euler()
    front_camera.data.type='ORTHO';front_camera.data.ortho_scale=270
    front_camera.data.clip_start=.1;front_camera.data.clip_end=3000
    front_camera.data.show_passepartout=False
    
    

class WFRL_OT_FarmFlexView(bpy.types.Operator):
    bl_idname='wfrl.farm_flex_view'
    bl_label='查看机组'
    turbine:bpy.props.StringProperty(default='T1')
    angle:bpy.props.EnumProperty(items=[('DOWN','Down 演示视角',''),('NACELLE','机舱云台',''),('FRONT','侧前方','')],default='DOWN')
    @classmethod
    def poll(cls, context):
        return farm_flex.is_active(context.scene)

    def execute(self,context):
        scene=context.scene
        preview=farm_flex._ACTIVE
        from .. import tip_tracking
        from ..cameras import ensure_gimbal, ensure_nacelle_gimbal, down_gimbal, fill_camera_view
        front_camera=scene.objects['WFRL.Camera.T1.FrontQuarter']
        from . import gimbal
        if gimbal._ACTIVE:
            gimbal._ACTIVE.finish(context)
        if self.turbine != 'all' and self.turbine not in preview.readers:
            self.report({'WARNING'}, '该数据包未包含此机组')
            return {'CANCELLED'}
        preview.visible_turbines=set(range(len(preview.readers))) if self.turbine=='all' else {int(self.turbine[1:])-1}
        preview.cache.clear()
        # Hidden turbines need no mesh uploads. Switching to overview samples
        # all nine at the current time before revealing them.
        for index,tid in enumerate(('T1','T2','T3')):
            for obj in scene.objects:
                if obj.name.startswith(f'WFRL.Turbine.{tid}.') and '.ClearanceRadar.' not in obj.name and '.TipTrail.' not in obj.name:
                    obj.hide_set(index not in preview.visible_turbines)
                    obj.hide_render=index not in preview.visible_turbines
        if self.turbine=='all':
            camera=scene.objects['WFRL.Camera.FarmFlexOverview']
        else:
            scene['wfrl_clearance_turbine']=self.turbine
            scene.wfrl_gimbal_kind = 'NACELLE' if self.angle == 'NACELLE' else 'DEMO'
            scene.wfrl_gimbal_turbine=self.turbine
            clearance_replay._READERS[scene.as_pointer()]=preview.readers[self.turbine]
            trail=tip_tracking.active(scene)
            if trail is not None and trail.turbine_id != self.turbine:
                tip_tracking.disable(scene,'turbine_change')
            if self.turbine=='T1' and self.angle=='FRONT':
                camera=front_camera
            elif self.angle=='NACELLE':
                camera=ensure_nacelle_gimbal(scene,self.turbine)
            else:
                camera=ensure_gimbal(scene,self.turbine);down_gimbal(camera)
        scene.camera=camera;scene['wfrl_camera']=camera.name
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type=='VIEW_3D':
                    area.spaces.active.use_local_camera=False
                    area.spaces.active.camera=camera
                    area.spaces.active.region_3d.view_perspective='CAMERA'
                    fill_camera_view(area,scene)
        preview.update(scene)
        if self.turbine != 'all' and scene.wfrl_flex_show_tip_trails:
            tip_tracking.enable(scene,self.turbine)
        trail=tip_tracking.active(scene)
        if trail is not None:
            for obj in trail.objects.values():
                # Wider display tubes keep the complete rotor's paths readable.
                obj.data.bevel_depth=.24 if camera==front_camera else .08
        scene['wfrl_farm_view']='all' if self.turbine=='all' else self.turbine+'_'+self.angle
        return {'FINISHED'}


class WFRL_PT_FarmFlex(bpy.types.Panel):
    bl_label='三机 MAPPO 回放'
    bl_idname='WFRL_PT_FarmFlex'
    bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='MAPPO';bl_order=-300

    @classmethod
    def poll(cls, context):
        return True

    def draw(self,context):
        if farm_flex.is_active(context.scene):
            draw(self.layout,context)
        else:
            self.layout.operator('wfrl.load_demo', icon='FILE_REFRESH')
            self.layout.label(text=context.scene.get('wfrl_clearance_status', '完整离线演示 · 无需后端'), icon='INFO')



class WFRL_OT_FarmTransport(bpy.types.Operator):
    bl_idname = 'wfrl.farm_transport'
    bl_label = 'MAPPO 播放控制'
    action: bpy.props.EnumProperty(items=[('STEP','单步',''),('STOP','停止',''),('RESET','复位','')])

    @classmethod
    def poll(cls, context):
        return farm_flex.is_active(context.scene)

    def execute(self, context):
        from .. import _cancel_playback, tip_tracking
        _cancel_playback()
        scene = context.scene
        if self.action == 'STEP':
            scene.frame_set(min(scene.frame_end, scene.frame_current + 1))
        elif self.action == 'RESET':
            tip_tracking.reset(scene, 'reset')
            scene.frame_set(scene.frame_start)
        # STOP holds the actual recorded pose; no invented feathering phase.
        return {'FINISHED'}


class WFRL_OT_DeflectionView(bpy.types.Operator):
    bl_idname = 'wfrl.deflection_view'
    bl_label = 'T1 叶尖对照特写'

    @classmethod
    def poll(cls, context):
        return farm_flex.is_active(context.scene) and farm_flex._ACTIVE.comparison is not None

    def execute(self, context):
        from ..cameras import fill_camera_view
        bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT')
        scene = context.scene
        scene.camera = scene.objects['WFRL.Camera.T1.TipComparison']
        scene['wfrl_camera'] = scene.camera.name
        scene.wfrl_deflection_visible = True
        farm_flex._ACTIVE.update(scene)
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.use_local_camera = False
                    area.spaces.active.camera = scene.camera
                    area.spaces.active.region_3d.view_perspective = 'CAMERA'
                    fill_camera_view(area, scene)
        return {'FINISHED'}


CLASSES = (WFRL_OT_FarmFlexView, WFRL_OT_FarmTransport, WFRL_OT_DeflectionView, WFRL_PT_FarmFlex)
