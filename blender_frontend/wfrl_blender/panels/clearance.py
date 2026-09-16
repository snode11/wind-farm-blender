"""Result comparison and navigation within the shared Camera entry point."""
import bpy
from .. import clearance_replay, runtime


class WFRL_OT_ClearanceClip(bpy.types.Operator):
    bl_idname = 'wfrl.clearance_clip'
    bl_label = 'Load simulation result clip'
    bl_description = '加载已验证的离线结果包，从片段起点播放并切到测量区侧视'
    demo: bpy.props.EnumProperty(items=[('normal', '正常测量', ''), ('near_tower', '较小净空', '')])

    @classmethod
    def poll(cls, context):
        return runtime.configuration_editable()

    def execute(self, context):
        scene = context.scene
        path = getattr(scene, 'wfrl_clearance_' + self.demo + '_path')
        try:
            if not path.strip():
                raise ValueError('请在“数据配置”中选择所选片段的数据包目录')
            clearance_replay.load(scene, bpy.path.abspath(path), self.demo)
        except (ValueError, OSError, ImportError, KeyError, TypeError) as exc:
            from .. import _cancel_playback
            _cancel_playback()
            clearance_replay.clear(scene, '回放数据未就绪：' + str(exc))
            scene.wfrl_clearance_show_config = True
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        bpy.ops.wfrl.clearance_view(view='MEASUREMENT')
        if not bpy.app.background and context.screen and not context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


class WFRL_OT_ClearanceView(bpy.types.Operator):
    bl_idname = 'wfrl.clearance_view'
    bl_label = '演示视角'
    bl_description = '只切换镜头；保留当前数据模式、回放进度和暂停状态'
    view: bpy.props.EnumProperty(items=[('MEASUREMENT', '测量区侧视', ''), ('WORLD', '风场总览', ''),
                                        ('RADAR', '雷达特写', '')])

    @classmethod
    def poll(cls, context):
        return (context.scene.objects.get('WFRL.Camera.World') is not None
                or any(obj.name.startswith('WFRL.Turbine.') and obj.name.endswith('.YawRoot')
                       for obj in context.scene.objects))

    def execute(self, context):
        from . import gimbal
        if gimbal._ACTIVE:
            gimbal._ACTIVE.finish(context)
        if self.view in {'MEASUREMENT', 'RADAR'}:
            from ..cameras import ensure_clearance_camera, ensure_radar_closeup_camera
            scene = context.scene
            tid = (scene.get('wfrl_clearance_turbine', 'T1')
                   if clearance_replay.reader_for(scene) else scene.wfrl_gimbal_turbine)
            try:
                ensure_camera = ensure_radar_closeup_camera if self.view == 'RADAR' else ensure_clearance_camera
                camera = ensure_camera(scene, tid)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            name = camera.name
        else:
            name = 'WFRL.Camera.World'
        return bpy.ops.wfrl.select_camera(camera_name=name)


class WFRL_OT_ClearanceRestart(bpy.types.Operator):
    bl_idname = 'wfrl.clearance_restart'
    bl_label = '从头重播'
    bl_description = '使用已加载的结果，从起点重新播放，不重复累计统计'

    @classmethod
    def poll(cls, context):
        return clearance_replay.reader_for(context.scene) is not None

    def execute(self, context):
        from .. import _cancel_playback
        _cancel_playback()
        from .. import tip_tracking
        tip_tracking.reset(context.scene, 'restart')
        context.scene.frame_set(context.scene.frame_start)
        if not bpy.app.background and context.screen:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


class WFRL_OT_ClearancePlayback(bpy.types.Operator):
    bl_idname = 'wfrl.clearance_playback'
    bl_label = '播放雷达结果'
    bl_description = '播放或暂停当前片段；片段结束后从头播放'

    @classmethod
    def poll(cls, context):
        return clearance_replay.reader_for(context.scene) is not None and context.screen is not None

    def execute(self, context):
        if context.scene.frame_current >= context.scene.frame_end:
            return bpy.ops.wfrl.clearance_restart()
        return bpy.ops.screen.animation_play()


def playback_control(scene, screen):
    if scene.frame_current >= scene.frame_end:
        return '重新播放', 'REW', '本段已结束'
    if screen is not None and screen.is_animation_playing:
        return '暂停', 'PAUSE', '正在播放'
    return '播放', 'PLAY', '已暂停'


def progress_get(scene):
    span = scene.frame_end - scene.frame_start
    if clearance_replay.reader_for(scene) is None or span <= 0:
        return 0.0
    return min(100.0, max(0.0, 100 * (scene.frame_current - scene.frame_start) / span))


def progress_set(scene, value):
    if clearance_replay.reader_for(scene) is None:
        return
    ratio = min(100.0, max(0.0, value)) / 100
    from .. import tip_tracking
    tip_tracking.reset(scene, 'progress_seek')
    scene.frame_set(scene.frame_start + round(ratio * (scene.frame_end - scene.frame_start)))


def number(value, signed=False):
    return '--' if value is None else format(value, '+.2f' if signed else '.2f')


def draw_navigation(layout, scene):
    kind = scene.get('wfrl_scene_kind')
    if kind == 'clearance_replay':
        label = '雷达结果回放' if clearance_replay.reader_for(scene) else '雷达回放未就绪'
        source = 'FAST.Farm 离线结果'
    elif kind == 'live':
        label, source = '后端场景', '后端遥测 · ' + runtime.get_state().connection
    elif kind == 'demo':
        label, source = '旧场景需重新加载', '请加载 MAPPO 60 秒演示'
    else:
        label, source = '尚未加载场景', '无数据'
    layout.label(text='当前模式：' + label)
    layout.label(text='数据来源：' + source)
    row = layout.row(align=True)
    row.operator('wfrl.clearance_view', text='风场总览', icon='HOME').view = 'WORLD'
    row.operator('wfrl.clearance_view', text='测量区侧视', icon='VIEW_CAMERA').view = 'MEASUREMENT'
    layout.operator('wfrl.clearance_view', text='雷达特写', icon='ZOOM_IN').view = 'RADAR'
    if kind not in {'demo', 'live', 'clearance_replay'}:
        layout.operator('wfrl.load_demo', text='加载风场演示场景')


def fold(layout, scene, prop, title):
    opened = getattr(scene, prop)
    layout.prop(scene, prop, text=title, icon='TRIA_DOWN' if opened else 'TRIA_RIGHT', emboss=False)
    return opened


def draw_measurement(layout, scene, value):
    """Keep sample age beside all three values, using the fixed replay clock."""
    card = layout.box()
    measurement = value['measurement'] or {}
    if measurement:
        age = value['measurement_age_s']
        timebase = scene.get('wfrl_clearance_timebase_fps', scene.render.fps / scene.render.fps_base)
        # A sample acquired within this timeline step is new in this frame.
        # Pausing or changing playback FPS never reclassifies that same frame.
        fresh = age <= 1.0 / timebase + 1e-9
        card.label(text=('新测量' if fresh else '上次有效测量')
                   + f" · 叶片 {measurement['blade_id']}", icon='TIME')
        card.label(text=f'{age:.2f} 仿真秒前')
        card.label(text=f"测量时刻：{measurement['time_s']:.2f} s")
    else:
        card.label(text='等待下一次有效测量', icon='RADIOBUT_OFF')
        # Reserve the two sample metadata rows so playback buttons never jump
        # between fresh, held and expired states during an animation.
        card.label(text='')
        card.label(text='')
    numbers = card.column(align=True)
    numbers.scale_y = 1.2
    numbers.label(text='雷达 → 叶片（B2）：' + number(measurement.get('slant_range_m')) + ' m')
    numbers.label(text='仿真真值：' + number(measurement.get('truth_m')) + ' m')
    numbers.label(text='估计 B2：' + number(measurement.get('estimate_m')) + ' m')
    numbers.label(text='偏差：' + number(measurement.get('error_m'), True) + ' m')
    card.label(text='偏差 = 估计 − 真值')


def draw_flex_trails(layout, scene):
    """Expose the trajectory switch whenever a flexible preview is attached."""
    from .. import tip_tracking
    if tip_tracking.active(scene) is not None or scene.get('wfrl_flex_active', False):
        row = layout.row(align=True)
        row.prop(scene, 'wfrl_flex_show_tip_trails', text='显示叶尖轨迹', toggle=True)
        layout.label(text=f"B1 橙 · B2 蓝 · B3 红；保留最近 {int(scene.get('wfrl_tip_trail_max_points', 240))} 点")
        layout.label(text='形变网格叶尖位置 · 非相机测量')


def draw(layout, scene):
    import sys
    farm = sys.modules.get(__package__.rsplit('.', 1)[0]+'.farm_flex')
    farm_active = farm is not None and farm.is_active(scene)
    box = layout.box()
    box.label(text='净空与误差对比')
    if farm_active:
        box.label(text=scene['wfrl_clearance_turbine']+' · 三机随机阵风 · MAPPO 偏航')
    else:
        row = box.row(align=True)
        for demo, title in (('normal', '正常测量'), ('near_tower', '较小净空')):
            row.operator('wfrl.clearance_clip', text=title, depress=(
                clearance_replay.reader_for(scene) is not None and scene.get('wfrl_clearance_demo') == demo)).demo = demo
    value = clearance_replay.sample(scene)
    if value is None:
        box.label(text='未加载有效雷达结果', icon='INFO')
        if not runtime.configuration_editable():
            box.label(text='先停止后端，再加载雷达片段')
        draw_flex_trails(box, scene)
    else:
        import sys
        module = sys.modules.get(__package__.rsplit('.', 1)[0]+'.blade_flex_preview')
        box.label(text=('仿真形变回放 · 实体叶片随数据弯曲'
                        if farm_active or (module is not None and module.is_active(scene))
                        else '后端含形变 · 画面为刚性示意'))
        draw_flex_trails(box, scene)
        if scene.get('wfrl_clearance_demo') == 'near_tower' and not farm_active:
            box.label(text='较小净空工况 · 以数值比较')
        draw_measurement(box, scene, value)
        text, icon, state = playback_control(scene, bpy.context.screen)
        row = box.row(align=True)
        row.operator('wfrl.clearance_playback', text=text, icon=icon)
        row.operator('wfrl.clearance_restart', text='从头重播', icon='REW')
        box.prop(scene, 'wfrl_clearance_progress', text='回放进度', slider=True)
        reader = clearance_replay.reader_for(scene)
        elapsed = max(0.0, value['time_s'] - reader.start_s)
        duration = reader.end_s - reader.start_s
        box.label(text=f'{state} · {elapsed:.2f} / {duration:.2f} s')
        box.label(text=f"仿真时间：{value['time_s']:.2f} s")
        if scene.frame_current >= scene.frame_end:
            summary = box.box()
            stats = value['statistics']
            summary.label(text='本段统计')
            summary.label(text=f"B2 有效样本：{stats['valid_samples']} / {stats['expected_samples']}")
            summary.label(text=f"经过 {stats['passage_count']} 次 · 整次漏测 {stats['missed_passage_count']} 次")
            summary.label(text='平均绝对误差：' + number(stats.get('mae_m')) + ' m')
    box.label(text='B2 手册简化估算 · 理想测距')
    box.label(text='光束为示意 · 不作安全判定')
    if fold(box, scene, 'wfrl_clearance_show_details', '测量详情与统计'):
        box.label(text='真实红外不可见；线端不代表命中点')
        if value is not None:
            stats = value['statistics']
            box.label(text='截至当前回放位置 · 正偏差为高估')
            box.label(text='平均绝对误差：' + number(stats.get('mae_m')) + ' m')
            box.label(text='最大绝对误差：' + number(stats.get('max_abs_error_m')) + ' m')
            ratio = stats.get('valid_ratio')
            box.label(text=f"B2 有效样本：{stats['valid_samples']} / {stats['expected_samples']}")
            box.label(text='预期样本中有效：' + ('不适用' if ratio is None else f'{ratio:.1%}'))
            box.label(text='分母：评估网格内预期样本')
            box.label(text=f"经过测量区：{stats['passage_count']} 次")
            # Cumulative counts may include a passage still in progress.
            # Only the completed clip can call every unmeasured passage a miss.
            box.label(text=('整次漏测：' + str(stats['missed_passage_count']) + ' 次'
                            if scene.frame_current >= scene.frame_end else '整次漏测：片尾汇总'))
            box.label(text=f'回放帧：{scene.frame_current} / {scene.frame_end}')
    if fold(box, scene, 'wfrl_clearance_show_config', '数据配置'):
        box.prop(scene, 'wfrl_clearance_normal_path')
        box.prop(scene, 'wfrl_clearance_near_tower_path')
        if value is None:
            box.label(text=scene.get('wfrl_clearance_status', '选择目录后点击上方工况按钮'), icon='INFO')
        else:
            box.label(text='当前为已加载数据；改路径后需重选工况')


CLASSES = (WFRL_OT_ClearanceClip, WFRL_OT_ClearanceView, WFRL_OT_ClearanceRestart,
           WFRL_OT_ClearancePlayback)


def register_properties():
    bpy.types.Scene.wfrl_clearance_progress = bpy.props.FloatProperty(
        name='回放进度', description='当前片段的播放百分比；仿真时间与读数同步更新',
        subtype='PERCENTAGE', min=0.0, max=100.0, precision=1,
        get=progress_get, set=progress_set, options={'SKIP_SAVE'})
    for demo, label in (('normal', '正常测量数据包'), ('near_tower', '较小净空数据包')):
        setattr(bpy.types.Scene, 'wfrl_clearance_' + demo + '_path', bpy.props.StringProperty(name=label, subtype='DIR_PATH'))
    for suffix in ('show_details', 'show_config', 'show_camera'):
        setattr(bpy.types.Scene, 'wfrl_clearance_' + suffix, bpy.props.BoolProperty(default=False))
    def _update_tip_trails(scene, context):
        from .. import tip_tracking
        if scene.wfrl_flex_show_tip_trails:
            turbine = scene.get('wfrl_clearance_turbine', 'T1')
            tip_tracking.enable(scene, turbine_id=turbine,
                                max_points=int(scene.get('wfrl_tip_trail_max_points', 240)))
        else:
            tip_tracking.disable(scene)
    bpy.types.Scene.wfrl_flex_show_tip_trails = bpy.props.BoolProperty(
        name='显示叶尖轨迹', description='随播放绘制形变后叶尖世界坐标，按当前片段限制缓存；拖动进度或重播时清空',
        default=False, update=_update_tip_trails)


def unregister_properties():
    for suffix in ('normal_path', 'near_tower_path', 'show_details', 'show_config', 'show_camera', 'progress'):
        name = 'wfrl_clearance_' + suffix
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    if hasattr(bpy.types.Scene, 'wfrl_flex_show_tip_trails'):
        delattr(bpy.types.Scene, 'wfrl_flex_show_tip_trails')
