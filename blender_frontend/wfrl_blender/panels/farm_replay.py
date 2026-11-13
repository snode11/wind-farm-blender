"""One sidebar entry for the dedicated three-turbine recorded-result player."""
import bpy
from .. import clearance_replay, farm_flex
from .clearance import number, playback_control, selected_installation_label, dual_method_label
from .ui_helpers import message, diagnostic, history_status


def unified_panel_active(scene):
    return (hasattr(bpy.types, 'WFRL_PT_FarmFlex') and farm_flex.is_active(scene))


def section(layout, key, title):
    header, body = layout.panel(key, default_closed=True)
    header.label(text=title)
    return body


class _BladeResponseLayout:
    """Use physical component labels with the shared response panel and saved enum IDs."""
    def __init__(self, layout):
        self.layout = layout

    def __getattr__(self, name):
        method = getattr(self.layout, name)
        if name in {'box', 'row', 'column'}:
            return lambda *args, **kwargs: type(self)(method(*args, **kwargs))
        if name == 'panel':
            return lambda *args, **kwargs: tuple(type(self)(part) if part is not None else None
                                               for part in method(*args, **kwargs))
        return method

    def prop(self, data, name, **kwargs):
        if name != 'wfrl_imbalance_metric':
            return self.layout.prop(data, name, **kwargs)
        from ..imbalance import METRICS
        column = self.layout.column(align=True)
        for key, (label, _unit, _factor) in METRICS.items():
            column.prop_enum(data, name, key, text=label)


def draw_blade_mechanics(layout, scene):
    from ..imbalance import ROOT_FORCES, latest_turn
    layout.label(text='弹性扭转：未提供数值', icon='INFO')
    details = section(layout, 'wfrl_blade_mechanics', '叶根力、坐标与扭转定义')
    if details is None:
        return
    for text in ('叶根载荷使用 BeamDyn B1/B2/B3RootF*r、RootM*r；INVALID 通道不作为有效零值。',
                 'r 系固定于运动叶根，等同 IEC 叶片 b 系，随叶片刚体运动和变桨转动；z 从根指向尖。',
                 'RootMzr 为叶根扭矩：绕 r 系展向 z 轴的力矩，不是材料应力或扭转应变。',
                 'TipRDxr/yr/zr 为无量纲 Wiener–Milenković 旋转参数，单个分量不能乘 180/π 当作扭角。',
                 'BeamDyn v3.5.3 的 TipRD 相对未变形截面定向，已扣除参考预扭及随叶根的刚体运动和变桨。',
                 '预弯与变形叶片的局部展向不等同 r 系 z 轴；叶尖旋转矩阵不能唯一分离沿叶片的弹性扭转。',
                 '当前数据未提供展向截面旋转或扭转应变；轴角投影和 Euler 角仅是定向分量，不作为弹性扭转角（°）。'):
        message(details, text)
    active = farm_flex.active_for(scene)
    data = getattr(active, 'imbalance', None)
    row = data['turbines'].get(scene.wfrl_imbalance_turbine) if data else None
    if row is None or not all(key in row['metrics'] for key in ROOT_FORCES):
        details.label(text='当前分析包未提供叶根力统计', icon='INFO')
        return
    value = clearance_replay.sample(scene)
    turn = latest_turn(row, value['time_s']) if value else None
    details.label(text='叶根力 · 同一三叶相位对齐统计')
    details.label(text='全片段汇总 · 包含当前位置之后的数据', icon='INFO')
    for key, (label, unit, factor) in ROOT_FORCES.items():
        metric = row['metrics'][key]
        card = details.box()
        card.label(text=label)
        card.label(text=f'三叶均值曲线差异：{metric["rms_spread"] * factor:.4f} {unit}')
        for blade, (mean, offset) in enumerate(zip(metric['blade_means'], metric['blade_offsets']), 1):
            card.label(text=f'B{blade} 均值 {mean * factor:+.4f} / 偏差 {offset * factor:+.4f} {unit}')
        card.label(text=(f'最近完整转差异：{turn["metrics"][key]["rms_spread"] * factor:.4f} {unit}'
                         if turn else '尚无完整三叶对齐转'))


def draw_radar(layout, scene, value):
    modes = layout.row(align=True)
    modes.operator('wfrl.load_dual_beam', text='双束净空 / S1')
    modes.operator('wfrl.load_demo', text='原 B2 回放')
    layout.operator('wfrl.load_dual_beam', text='TLS 候选 / S1').builtin_method = 'TLS'
    if (value or {}).get('measurement_mode') == 'dual_beam':
        from .clearance import draw_dual_measurement
        draw_dual_measurement(layout, scene, value)
        stats = value['statistics']
        details = section(layout, 'wfrl_dual_beam_details', '双束统计与报警记录')
        if details is not None:
            details.label(text='累计统计截至当前仿真时刻')
            details.label(text=f"预期区双束有效：{stats['valid_samples']} / {stats['expected_samples']}")
            details.label(text=f"全部双束有效：{stats['all_valid_samples']}")
            details.label(text='平均绝对误差：' + number(stats['mae_m']) + ' m')
            details.label(text='绝对误差 P95：' + number(stats['p95_abs_error_m']) + ' m')
            details.label(text=f"S1 累计事件：{value['alarm']['event_count']}")
            reader = clearance_replay.reader_for(scene)
            details.label(text=f"报警显示保持 {reader.config['alarm_hold_s']:.1f} 仿真秒")
            details.label(text='保持时间不代表保护延迟')
            details.label(text='40 Hz 仿真采样；设备 DP 为 50 Hz')
            details.label(text='浅蓝为方向；亮橙为命中显示提示')
        return
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
        flex = farm_flex.ready_for(scene)
        if flex is not None and flex.tower_motion is not None:
            details.label(text='真值：叶尖表面至同高度塔壁' if flex.reference is not None else '真值：同高度的变形塔筒截面距离')
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


def draw_playback(layout, context, value, reader, *, show_time=None):
    """One shared clock and three transport actions, above every task page."""
    from .. import split_reconstruction
    scene = context.scene
    comparison = split_reconstruction.enabled(scene)
    playback = layout.column(align=True)
    text, icon, state = playback_control(scene, context.screen)
    if show_time is None:
        show_time = not comparison
    if show_time:
        playback.label(text=(f"仿真时刻 {value['time_s']:.3f} s · {state}" if value else '回放数据未就绪'))
    row = playback.row(align=True)
    row.scale_y = 1.25
    row.operator('wfrl.clearance_playback', text=text, icon=icon)
    row.operator('wfrl.farm_transport', text='单步', icon='NEXT_KEYFRAME').action = 'STEP'
    row.operator('wfrl.farm_transport', text='复位', icon='LOOP_BACK').action = 'RESET'
    if not comparison:
        duration = reader.end_s - reader.start_s if reader else 0
        elapsed = max(0, value['time_s'] - reader.start_s) if value and reader else 0
        playback.label(text=f'片段进度 {elapsed:.1f} / {duration:.1f} 秒')
        playback.prop(scene, 'wfrl_clearance_progress', text='', slider=True)


def draw_views(layout, context):
    scene = context.scene
    draw_turbine_views(layout, scene)
    row = layout.row(align=True)
    op = row.operator('wfrl.farm_flex_view', text='T1 侧前方')
    op.turbine = 'T1'
    op.angle = 'FRONT'
    op = row.operator('wfrl.farm_flex_view', text='风场全景')
    op.turbine = 'all'
    op.angle = 'DOWN'
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


def draw_split_views(layout, context):
    from .. import split_reconstruction, split_reconstruction_ui
    if split_reconstruction.available(context.scene):
        split_reconstruction_ui.draw_controls(layout, context)
        if split_reconstruction.enabled(context.scene):
            return
    layout.label(text='左右分屏 · T1', icon='CAMERA_DATA')
    row = layout.row(align=True)
    row.operator('wfrl.farm_split_view', text='Down + 侧前方').mode = 'DOWN'
    row.operator('wfrl.farm_split_view', text='机舱 + 侧前方').mode = 'NACELLE'
    layout.operator('wfrl.farm_split_view', text='恢复单屏', icon='FULLSCREEN_ENTER').mode = 'SINGLE'
    error = context.scene.get('wfrl_view_layout_error')
    if error:
        diagnostic(layout, '视图布局', error, error=True, context=context)


def draw_turbine_views(layout, scene):
    """Keep Down views and two directions on the same mounted camera."""
    row = layout.row(align=True)
    for tid in farm_flex.active_for(scene).readers:
        op = row.operator('wfrl.farm_flex_view', text=tid + ' Down')
        op.turbine = tid
        op.angle = 'DOWN'
    row = layout.row(align=True)
    op = row.operator('wfrl.farm_flex_view', text='机舱相机', icon='CAMERA_DATA')
    op.turbine = scene.get('wfrl_clearance_turbine', 'T1')
    op.angle = 'NACELLE'
    op = row.operator('wfrl.farm_flex_view', text='侧下视角', icon='VIEW_CAMERA')
    op.turbine = scene.get('wfrl_clearance_turbine', 'T1')
    op.angle = 'NACELLE_SIDE'
    if scene.wfrl_gimbal_kind == 'NACELLE':
        camera = scene.objects.get(f"WFRL.Camera.{scene.get('wfrl_clearance_turbine', 'T1')}.NacelleGimbal")
        if camera and camera.get('wfrl_nacelle_intrinsics'):
            layout.label(text='名义安装与模型相交；外壳待标定', icon='ERROR')
        from . import gimbal
        row = layout.row(align=True)
        row.operator('wfrl.gimbal_mode', text='退出摇杆' if gimbal._ACTIVE else '云台摇杆', icon='ORIENTATION_GIMBAL')
        row.operator('wfrl.gimbal_preset', text='相机复位').preset = 'RESET'


def draw_tools(layout, context):
    scene = context.scene
    flex = farm_flex.active_for(scene)
    views = section(layout, 'wfrl_farm_tools_views', '机组、视角与云台')
    if views is not None:
        draw_views(views, context)

    telemetry = section(layout, 'wfrl_farm_telemetry', '三机遥测、曲线与导出')
    if telemetry is not None:
        telemetry.prop(scene, 'wfrl_channel_telemetry', text='采样遥测与曲线')
        from .. import charts
        chart_history = charts.history_for(scene)
        for tid in flex.readers:
            card = telemetry.box()
            card.label(text=tid)
            for key in ('yaw', 'pitch', 'rotor_speed', 'power', 'torque', 'load'):
                points = chart_history.points(tid, key)
                point = points[-1] if points else None
                card.label(text=charts.LABELS[key] + ': ' + (f'{point.value:.2f} {point.unit}' if point and point.value is not None else '—'))
        power = chart_history.points(charts.FARM, 'power')
        telemetry.label(text='风场功率：' + (f'{power[-1].value:.2f} MW' if power and power[-1].value is not None else '—'))
        telemetry.label(text='变桨为三叶片平均；载荷为 B1 叶根弯矩')
        telemetry.label(text='奖励未记录；不填补缺失通道')
        telemetry.label(text='FAST.Farm / REVIEW_ONLY · 关闭采样时读数冻结')
        telemetry.prop(scene, 'wfrl_chart_visible', text='显示曲线')
        telemetry.prop(scene, 'wfrl_chart_channel', text='曲线通道')
        telemetry.prop(scene, 'wfrl_selected_turbine', text='曲线机组')
        telemetry.operator('wfrl.export_history', text='导出已采样历史', icon='EXPORT')
        history_status(telemetry, scene.get('wfrl_history_export_status', '每通道最多 600 点'), context=context)
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
        from .. import animation_render_ui
        animation_render_ui.draw(presentation, context)
    data = section(layout, 'wfrl_farm_data', '数据来源与重新加载')
    if data is not None:
        if flex.manifest.get('interface_only'):
            data.label(text='OpenFAST · 单机接口验证 · REVIEW_ONLY')
            data.label(text='8 m/s 恒定风 · 规定 9 rpm')
        else:
            data.label(text='FAST.Farm · 离线结果 · REVIEW_ONLY')
            data.label(text='三机随机阵风 · 九片叶片独立形变')
            data.label(text='MAPPO 控制偏航；基线负责转矩、变桨')
        data.label(text='轨迹为形变网格叶尖位置，非相机测量')
        data.label(text="红 / 绿 / 蓝：叶片 1 / 2 / 3")
        data.label(text="相邻两圈定格对比 2 秒后淡出")
        if flex.manifest.get('measurement_mode') == 'dual_beam':
            data.label(text=dual_method_label(clearance_replay.sample(scene)))
            data.label(text='S2/S3 同刻同叶片配对 · S1 独立报警')
        else:
            data.label(text='B2 手册简化估算 · 理想测距')
        data.label(text='光束为示意，不作安全判定')
        data.label(text='开发验收包 · 数值细化待验证')
        data.operator('wfrl.load_demo', text='重新加载当前结果', icon='FILE_REFRESH').package_path = scene['wfrl_farm_flex_path']
        data.operator('wfrl.load_dual_beam', text='打开外部双束数据包', icon='FILE_FOLDER').choose_file = True
        data.operator('wfrl.clearance_restart', text='从头重播', icon='REW')
        path = data.column()
        path.enabled = False
        path.prop(scene, '["wfrl_farm_flex_path"]', text='数据目录')
    diagnostics = section(layout, 'wfrl_farm_diagnostics', '版本、来源与播放诊断')
    if diagnostics is not None:
        from .. import frontend_diagnostics
        frontend_diagnostics.draw_panel(diagnostics, context)


def draw(layout, context, *, show_playback=True):
    scene = context.scene
    value = clearance_replay.sample(scene)
    reader = clearance_replay.reader_for(scene)
    layout.use_property_decorate = False
    flex = farm_flex.active_for(scene)
    if flex.model != 'nrel5mw':
        message(layout, '合成叶形；运动、载荷与雷达读数来自 NREL 基线', context=context)
    if scene.get('wfrl_blade_model_error'):
        diagnostic(layout, '叶片外形', scene['wfrl_blade_model_error'], error=True, context=context)
    if flex.manifest.get('measurement_mode') == 'dual_beam':
        message(layout, dual_method_label(value), context=context)
        installation_status = (value or {}).get('installation_status')
        message(layout, ('双束重建 · ' + selected_installation_label(getattr(reader, 'config', {}))
                         if installation_status == 'SIMULATION_SELECTED'
                         else '双束诊断 · 安装与性能待验收'), context=context)
    elif flex.manifest.get('acceptance_status', '').startswith('FAILED'):
        layout.label(text='测量覆盖未达演示目标', icon='ERROR')
    if show_playback:
        draw_playback(layout, context, value, reader)
    from .. import split_reconstruction
    if split_reconstruction.enabled(scene):
        draw_split_views(layout, context)
    else:
        # Blade shape stays visible so replay and X100 sampling can match the site blade.
        layout.prop(scene, 'wfrl_blade_model', text='叶片外形')
        views = section(layout, 'wfrl_farm_quick_views', '观察视角与分屏')
        if views is not None:
            draw_split_views(views, context)
        model = section(layout, 'wfrl_farm_model_details', '叶片外形与模型来源')
        if model is not None:
            if flex.reference is not None:
                from ..turbine_geometry import model_label
                model.label(text='单机预弯接口验证' if flex.manifest.get('interface_only')
                            else model_label(flex.model) + ' · BeamDyn')
            model.label(text='外形切换复用同一份保存运动，不重跑 FAST.Farm')
    tabs = layout.column(align=True)
    for pages in (('DEFLECTION', 'RADAR', 'IMBALANCE'), ('VIDEO', 'TOOLS')):
        row = tabs.row(align=True)
        row.scale_y = 1.25
        for page in pages:
            row.prop_enum(scene, 'wfrl_farm_panel_page', page)
    if scene.wfrl_farm_panel_page == 'DEFLECTION':
        from ..deflection import draw_panel
        draw_panel(layout, scene)
    elif scene.wfrl_farm_panel_page == 'RADAR':
        draw_turbine_views(layout, scene)
        draw_radar(layout, scene, value)
    elif scene.wfrl_farm_panel_page == 'IMBALANCE':
        from . import imbalance
        imbalance.draw(_BladeResponseLayout(layout), scene)
        draw_blade_mechanics(layout, scene)
    elif scene.wfrl_farm_panel_page == 'VIDEO':
        from . import video_output
        video_output.draw(layout, context)
    else:
        draw_tools(layout, context)


def build_review_cameras(scene):
    from ..cameras import ensure_nacelle_gimbal
    for tid in farm_flex.active_for(scene).readers:
        ensure_nacelle_gimbal(scene, tid)
    from mathutils import Vector
    tip = bpy.data.objects.new('WFRL.Camera.T1.TipComparison', bpy.data.cameras.new('T1TipComparison'))
    bpy.data.collections['WFRL_Scene'].objects.link(tip)
    tip.data.type = 'ORTHO'
    tip.data.ortho_scale = 28
    tip.data.clip_start = .1
    tip.data.clip_end = 3000
    tip.data.show_passepartout = False
    camera=bpy.data.objects.new('WFRL.Camera.FarmFlexOverview',bpy.data.cameras.new('FarmFlexOverview'))
    bpy.data.collections['WFRL_Scene'].objects.link(camera)
    camera.location=(-450,-1250,520)
    camera.rotation_euler=(Vector((504,0,65))-camera.location).to_track_quat('-Z','Y').to_euler()
    camera.data.type='ORTHO'
    camera.data.ortho_scale=1500
    camera.data.clip_start=.1
    camera.data.clip_end=30000
    camera.data.show_passepartout=False
    
    
    # A fixed front-quarter view frames the full rotor and its tip paths.
    front_camera=bpy.data.objects.new('WFRL.Camera.T1.FrontQuarter',bpy.data.cameras.new('T1FrontQuarter'))
    bpy.data.collections['WFRL_Scene'].objects.link(front_camera)
    front_camera.location=(-245,-140,125)
    front_camera.rotation_euler=(Vector((-5,0,90))-front_camera.location).to_track_quat('-Z','Y').to_euler()
    front_camera.data.type='ORTHO'
    front_camera.data.ortho_scale=270
    front_camera.data.clip_start=.1
    front_camera.data.clip_end=3000
    front_camera.data.show_passepartout=False
    
    

def _saved_value(value):
    if hasattr(value, 'bl_rna'):
        return value
    if hasattr(value, 'to_list'):
        return value.to_list()
    return value.copy() if hasattr(value, 'copy') else value


class _ViewSwitchState:
    """Restore the panel's camera, output, visibility and viewport writes on failure."""
    def __init__(self, scene):
        from ..cameras import demo_view_areas
        self.scene = scene
        self.attributes, self.camera_properties, self.created = [], [], []
        self.remember(scene, ('camera',))
        self.remember(scene.render, ('resolution_x', 'resolution_y', 'pixel_aspect_x', 'pixel_aspect_y'))
        names = ['wfrl_camera', 'wfrl_clearance_turbine', 'wfrl_farm_view',
                 'wfrl_nacelle_previous_output', 'wfrl_view_layout', 'wfrl_view_layout_error']
        for name in ('wfrl_gimbal_kind', 'wfrl_gimbal_turbine', 'wfrl_deflection_visible'):
            if hasattr(scene, 'is_property_set'):
                names.append(name)
            else:
                self.remember(scene, (name,))
        self.properties = {name: (name in scene, _saved_value(scene.get(name))) for name in names}
        for area in demo_view_areas(scene):
            space = area.spaces.active
            self.remember(space, ('use_local_camera', 'camera', 'show_region_ui'))
            if space.region_3d is not None:
                self.remember(space.region_3d, ('view_perspective', 'view_camera_zoom'))
                self.remember(space.region_3d, ('view_camera_offset',), copy=True)
        self.preview = farm_flex.active_for(scene)
        self.visibility = []
        if self.preview is not None:
            self.visible = set(self.preview.visible_turbines)
            self.cache = self.preview.cache.copy()
            self.reader = clearance_replay._READERS.get(scene.as_pointer())
            self.visibility = [(obj, obj.hide_get(), obj.hide_render) for obj in scene.objects
                               if obj.name.startswith('WFRL.Turbine.')]

    def remember(self, owner, names, *, copy=False):
        for name in names:
            if hasattr(owner, name):
                value = getattr(owner, name)
                self.attributes.append((owner, name, _saved_value(value) if copy else value))

    def remember_camera(self, camera):
        if camera is None:
            return
        self.remember(camera, ('parent',))
        self.remember(camera, ('location', 'rotation_euler', 'matrix_parent_inverse'), copy=True)
        self.remember(camera.data, ('type', 'lens', 'sensor_fit', 'sensor_width', 'sensor_height',
                                   'shift_x', 'shift_y', 'clip_start', 'clip_end', 'display_size',
                                   'show_passepartout', 'passepartout_alpha'))
        self.camera_properties.append((camera, {key: _saved_value(camera[key]) for key in camera.keys()}))

    def restore(self):
        errors = []

        def attempt(action):
            try:
                action()
            except Exception as exc:
                errors.append(str(exc))

        for name, (present, value) in self.properties.items():
            if present:
                attempt(lambda name=name, value=value: self.scene.__setitem__(name, value))
            elif name in self.scene:
                attempt(lambda name=name: self.scene.__delitem__(name))
        for owner, name, value in self.attributes:
            attempt(lambda owner=owner, name=name, value=value: setattr(owner, name, value))
        for camera, properties in self.camera_properties:
            for key in set(camera.keys()) - properties.keys():
                attempt(lambda key=key: camera.__delitem__(key))
            for key, value in properties.items():
                attempt(lambda key=key, value=value: camera.__setitem__(key, value))
        for obj, hidden, render in self.visibility:
            attempt(lambda obj=obj, hidden=hidden: obj.hide_set(hidden))
            attempt(lambda obj=obj, render=render: setattr(obj, 'hide_render', render))
        if self.preview is not None:
            self.preview.visible_turbines = self.visible
            self.preview.cache.clear()
            self.preview.cache.update(self.cache)
            # A failed source frame remains invalid; view rollback cannot certify it.
            if not getattr(self.preview, 'frame_error', ''):
                key = self.scene.as_pointer()
                if self.reader is None:
                    clearance_replay._READERS.pop(key, None)
                else:
                    clearance_replay._READERS[key] = self.reader
        for camera in self.created:
            data = camera.data
            attempt(lambda camera=camera: bpy.data.objects.remove(camera, do_unlink=True))
            if data.users == 0:
                attempt(lambda data=data: bpy.data.cameras.remove(data))
        if errors:
            raise RuntimeError('视图恢复失败：' + '; '.join(errors))


def _view_failed(operator, state, error):
    if state is not None:
        try:
            state.restore()
        except Exception as exc:
            error = f'{error}；{exc}'
    operator.report({'ERROR'}, str(error))
    return {'CANCELLED'}


def _required_camera(scene, name):
    camera = scene.objects.get(name)
    if camera is None or camera.type != 'CAMERA':
        raise ValueError('场景相机未就绪：' + name)
    return camera


class WFRL_OT_FarmFlexView(bpy.types.Operator):
    bl_idname='wfrl.farm_flex_view'
    bl_label='查看机组'
    turbine:bpy.props.StringProperty(default='T1')
    angle:bpy.props.EnumProperty(items=[('DOWN','Down 视角','从对应机组机舱底壳偏叶片后缘一侧的支架朝叶轮侧下俯视经过的叶片，随机舱与塔顶姿态运动'),('NACELLE','机舱云台',''),('NACELLE_SIDE','侧下视角','原位转向叶轮侧下方，使用同一台机舱相机'),('FRONT','侧前方','')],default='DOWN')
    @classmethod
    def poll(cls, context):
        return farm_flex.is_active(context.scene)

    def execute(self,context):
        scene=context.scene
        preview=farm_flex.active_for(scene)
        from .. import tip_tracking
        from ..cameras import ensure_gimbal, ensure_nacelle_gimbal, down_gimbal, fill_camera_view
        from ..cameras import nacelle_side_view, restore_nacelle_view
        state = None
        try:
            if preview is None or not self.poll(context):
                raise ValueError('回放数据未就绪')
            if self.turbine != 'all' and self.turbine not in preview.readers:
                raise ValueError('该数据包未包含此机组')
            front_camera = scene.objects.get('WFRL.Camera.T1.FrontQuarter')
            state = _ViewSwitchState(scene)
            if self.turbine == 'all':
                camera = _required_camera(scene, 'WFRL.Camera.FarmFlexOverview')
                state.remember_camera(camera)
            elif self.turbine == 'T1' and self.angle == 'FRONT':
                camera = _required_camera(scene, 'WFRL.Camera.T1.FrontQuarter')
                state.remember_camera(camera)
            else:
                nacelle = self.angle in {'NACELLE', 'NACELLE_SIDE'}
                name = f'WFRL.Camera.{self.turbine}.' + ('NacelleGimbal' if nacelle else 'Gimbal')
                existing = scene.objects.get(name)
                state.remember_camera(existing)
                camera = (ensure_nacelle_gimbal if nacelle else ensure_gimbal)(scene, self.turbine)
                if existing is None:
                    state.created.append(camera)
                if self.angle == 'NACELLE_SIDE':
                    nacelle_side_view(camera)
                elif nacelle:
                    restore_nacelle_view(camera)
                else:
                    down_gimbal(camera)
            from . import gimbal
            if gimbal._ACTIVE:
                gimbal._ACTIVE.finish(context)
                if gimbal._ACTIVE is not None:
                    raise RuntimeError('云台资源仍在清理，请稍后重试切换视角')
            from .custom_cameras import dismiss_for_view_switch
            dismiss_for_view_switch(scene)
            preview.visible_turbines=set(range(len(preview.readers))) if self.turbine=='all' else {int(self.turbine[1:])-1}
            preview.cache.clear()
            # Hidden blade meshes need no uploads; T1 measurements and shared support stay current.
            for index,tid in enumerate(('T1','T2','T3')):
                for obj in scene.objects:
                    if obj.name.startswith(f'WFRL.Turbine.{tid}.') and '.ClearanceRadar.' not in obj.name and '.TipTrail.' not in obj.name:
                        obj.hide_set(index not in preview.visible_turbines)
                        obj.hide_render=index not in preview.visible_turbines
            if self.turbine != 'all':
                scene['wfrl_clearance_turbine']=self.turbine
                scene.wfrl_gimbal_kind = 'NACELLE' if self.angle in {'NACELLE', 'NACELLE_SIDE'} else 'DEMO'
                scene.wfrl_gimbal_turbine=self.turbine
                clearance_replay._READERS[scene.as_pointer()]=preview.readers[self.turbine]
            scene.camera=camera
            scene['wfrl_camera']=camera.name
            from ..nacelle_camera import select_output
            select_output(scene, camera)
            from ..cameras import demo_view_areas
            for area in demo_view_areas(scene):
                area.spaces.active.use_local_camera=False
                area.spaces.active.camera=camera
                area.spaces.active.region_3d.view_perspective='CAMERA'
                fill_camera_view(area,scene)
            preview.update(scene)
            if self.turbine != 'all':
                trail=tip_tracking.active(scene)
                if trail is not None and trail.turbine_id != self.turbine:
                    tip_tracking.disable(scene,'turbine_change')
                if scene.wfrl_flex_show_tip_trails:
                    tip_tracking.enable(scene,self.turbine)
            trail=tip_tracking.active(scene)
            if trail is not None:
                for obj in trail.objects.values():
                    # Wider display tubes keep the complete rotor's paths readable.
                    obj.data.bevel_depth=.24 if camera==front_camera else .08
            scene['wfrl_farm_view']='all' if self.turbine=='all' else self.turbine+'_'+self.angle
        except Exception as exc:
            return _view_failed(self, state, exc)
        return {'FINISHED'}


class WFRL_OT_FarmSplitView(bpy.types.Operator):
    bl_idname = 'wfrl.farm_split_view'
    bl_label = 'T1 左右分屏'
    bl_description = '左侧 Down 或机舱，右侧 T1 侧前方；两侧同步播放，保留当前进度'
    mode: bpy.props.EnumProperty(items=[('DOWN', 'Down + 侧前方', ''),
                                      ('NACELLE', '机舱 + 侧前方', ''),
                                      ('SINGLE', '恢复单屏', '')], default='DOWN')

    @classmethod
    def poll(cls, context):
        from .. import cameras, split_reconstruction
        return (not bpy.app.background and context.window is not None
                and farm_flex.is_active(context.scene) and cameras._LAYOUT_JOB is None
                and not split_reconstruction.enabled(context.scene))

    def execute(self, context):
        from .. import cameras
        scene = context.scene
        state = None
        try:
            if not self.poll(context):
                raise ValueError('分屏需要就绪的回放窗口，且没有正在进行的布局任务')
            state = _ViewSwitchState(scene)
            if self.mode == 'SINGLE':
                if scene.camera is None:
                    raise ValueError('请先选择场景相机')
                cameras.set_view_layout(context, 'SINGLE', camera_names=(scene.camera.name,))
            else:
                _required_camera(scene, 'WFRL.Camera.T1.FrontQuarter')
                result = bpy.ops.wfrl.farm_flex_view(turbine='T1', angle=self.mode)
                if result != {'FINISHED'}:
                    raise RuntimeError('风场视角切换已取消')
                cameras.set_view_layout(context, 'DUAL', camera_names=(
                    scene.camera.name, 'WFRL.Camera.T1.FrontQuarter'))
        except Exception as exc:
            return _view_failed(self, state, exc)
        return {'FINISHED'}


class WFRL_PT_FarmFlex(bpy.types.Panel):
    bl_label='三机 MAPPO 回放'
    bl_idname='WFRL_PT_FarmFlex'
    bl_space_type='VIEW_3D'
    bl_region_type='UI'
    bl_category='MAPPO'
    bl_order=-300

    @classmethod
    def poll(cls, context):
        return context.scene.get("wfrl_scene_kind") != "manual"

    def draw(self,context):
        from .home import draw_load, draw_status
        busy = draw_status(self.layout, context)
        if farm_flex.is_active(context.scene):
            controls = self.layout.column()
            controls.enabled = not busy
            controls.operator('wfrl.split_recon_open_example', text='打开几何重建示例…', icon='FILE_BLEND')
            draw(controls,context)
        else:
            draw_load(self.layout, context, busy=busy)



class WFRL_OT_FarmTransport(bpy.types.Operator):
    bl_idname = 'wfrl.farm_transport'
    bl_label = 'MAPPO 播放控制'
    action: bpy.props.EnumProperty(items=[('STEP','单步',''),('STOP','停止',''),('RESET','复位','')])

    @classmethod
    def poll(cls, context):
        return farm_flex.is_active(context.scene)

    def execute(self, context):
        from .. import tip_tracking, playback
        scene = context.scene
        playback.cancel(scene)
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
        flex = farm_flex.ready_for(context.scene)
        return flex is not None and flex.comparison is not None

    def execute(self, context):
        from ..cameras import fill_camera_view
        scene = context.scene
        state = None
        try:
            if not self.poll(context):
                raise ValueError('叶尖对照数据未就绪')
            camera = _required_camera(scene, 'WFRL.Camera.T1.TipComparison')
            state = _ViewSwitchState(scene)
            if bpy.ops.wfrl.farm_flex_view(turbine='T1', angle='FRONT') != {'FINISHED'}:
                raise RuntimeError('风场视角切换已取消')
            scene.camera = camera
            scene['wfrl_camera'] = camera.name
            scene.wfrl_deflection_visible = True
            farm_flex.active_for(scene).update(scene)
            from ..cameras import demo_view_areas
            for area in demo_view_areas(scene):
                area.spaces.active.use_local_camera = False
                area.spaces.active.camera = scene.camera
                area.spaces.active.region_3d.view_perspective = 'CAMERA'
                fill_camera_view(area, scene)
        except Exception as exc:
            return _view_failed(self, state, exc)
        return {'FINISHED'}


class WFRL_OT_CopyFrontendDiagnostics(bpy.types.Operator):
    bl_idname = 'wfrl.copy_frontend_diagnostics'
    bl_label = '复制完整诊断信息'

    def execute(self, context):
        import json
        from .. import frontend_diagnostics
        context.window_manager.clipboard = json.dumps(frontend_diagnostics.snapshot(context),
            indent=2, ensure_ascii=False)
        self.report({'INFO'}, '诊断信息已复制')
        return {'FINISHED'}


CLASSES = (WFRL_OT_FarmFlexView, WFRL_OT_FarmSplitView, WFRL_OT_FarmTransport, WFRL_OT_DeflectionView, WFRL_PT_FarmFlex, WFRL_OT_CopyFrontendDiagnostics)
