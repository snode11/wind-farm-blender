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
    view: bpy.props.EnumProperty(items=[('MEASUREMENT', '测量区侧视', ''), ('WORLD', '风场总览', '')])

    @classmethod
    def poll(cls, context):
        return (context.scene.objects.get('WFRL.Camera.World') is not None
                or any(obj.name.startswith('WFRL.Turbine.') and obj.name.endswith('.YawRoot')
                       for obj in context.scene.objects))

    def execute(self, context):
        from . import gimbal
        if gimbal._ACTIVE:
            gimbal._ACTIVE.finish(context)
        if self.view == 'MEASUREMENT':
            from ..cameras import ensure_clearance_camera
            scene = context.scene
            tid = (scene.get('wfrl_clearance_turbine', 'T1')
                   if clearance_replay.reader_for(scene) else scene.wfrl_gimbal_turbine)
            try:
                camera = ensure_clearance_camera(scene, tid)
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
        context.scene.frame_set(context.scene.frame_start)
        if not bpy.app.background and context.screen:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


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
        label, source = '风场演示', 'SYNTH 合成演示'
    else:
        label, source = '尚未加载场景', '无数据'
    layout.label(text='当前模式：' + label)
    layout.label(text='数据来源：' + source)
    row = layout.row(align=True)
    row.operator('wfrl.clearance_view', text='风场总览', icon='HOME').view = 'WORLD'
    row.operator('wfrl.clearance_view', text='测量区侧视', icon='VIEW_CAMERA').view = 'MEASUREMENT'
    if kind not in {'demo', 'live', 'clearance_replay'}:
        layout.operator('wfrl.load_demo', text='加载风场演示场景')


def fold(layout, scene, prop, title):
    opened = getattr(scene, prop)
    layout.prop(scene, prop, text=title, icon='TRIA_DOWN' if opened else 'TRIA_RIGHT', emboss=False)
    return opened


def draw(layout, scene):
    box = layout.box()
    box.label(text='净空与误差对比')
    row = box.row(align=True)
    for demo, title in (('normal', '正常测量'), ('near_tower', '较小净空')):
        row.operator('wfrl.clearance_clip', text=title, depress=(
            clearance_replay.reader_for(scene) is not None and scene.get('wfrl_clearance_demo') == demo)).demo = demo
    value = clearance_replay.sample(scene)
    if value is None:
        box.label(text='未加载有效雷达结果', icon='INFO')
        if not runtime.configuration_editable():
            box.label(text='先停止后端，再加载雷达片段')
    else:
        box.label(text='后端含形变 · 画面为刚性示意')
        if scene.get('wfrl_clearance_demo') == 'near_tower':
            box.label(text='较小净空工况 · 以数值比较')
        measurement = value['measurement'] or {}
        numbers = box.column(align=True)
        numbers.scale_y = 1.15
        numbers.label(text='仿真真值：' + number(measurement.get('truth_m')) + ' m')
        numbers.label(text='B2 估计：' + number(measurement.get('estimate_m')) + ' m')
        numbers.label(text='偏差：' + number(measurement.get('error_m'), True) + ' m（估计 − 真值）')
        status = value['status']
        box.label(text={'waiting': '等待有效测量', 'above_threshold': '高于演示阈值', 'near_threshold': '接近演示阈值'}[status],
                  icon={'waiting': 'RADIOBUT_OFF', 'above_threshold': 'KEYTYPE_JITTER_VEC', 'near_threshold': 'KEYTYPE_KEYFRAME_VEC'}[status])
        row = box.row(align=True)
        row.operator('screen.animation_play', text='播放 / 暂停', icon='PLAY')
        row.operator('wfrl.clearance_restart', text='从头重播', icon='REW')
        box.prop(scene, 'frame_current', text='回放进度')
        box.label(text=f"仿真时间：{value['time_s']:.2f} s")
    box.label(text='B2 手册简化估算 · 理想测距')
    box.label(text='光束为示意 · 阈值不作安全判定')
    if fold(box, scene, 'wfrl_clearance_show_details', '测量详情与统计'):
        box.label(text='真实红外不可见；线端不代表命中点')
        if value is not None:
            measurement = value['measurement'] or {}
            if measurement:
                box.label(text=f"测量时刻：{measurement['time_s']:.2f} s · 叶片 {measurement['blade_id']}")
                box.label(text=f"读数保留：{value['measurement_age_s']:.1f} 仿真秒")
            stats = value['statistics']
            box.label(text='截至当前回放位置 · 正偏差为高估')
            box.label(text='平均绝对误差：' + number(stats.get('mae_m')) + ' m')
            box.label(text='最大绝对误差：' + number(stats.get('max_abs_error_m')) + ' m')
            ratio = stats.get('valid_ratio')
            box.label(text='有效测量比例：' + ('不适用' if ratio is None else f'{ratio:.1%}'))
    if fold(box, scene, 'wfrl_clearance_show_config', '数据配置'):
        box.prop(scene, 'wfrl_clearance_normal_path')
        box.prop(scene, 'wfrl_clearance_near_tower_path')
        if value is None:
            box.label(text=scene.get('wfrl_clearance_status', '选择目录后点击上方工况按钮'), icon='INFO')
        else:
            box.label(text='当前为已加载数据；改路径后需重选工况')


CLASSES = (WFRL_OT_ClearanceClip, WFRL_OT_ClearanceView, WFRL_OT_ClearanceRestart)


def register_properties():
    for demo, label in (('normal', '正常测量数据包'), ('near_tower', '较小净空数据包')):
        setattr(bpy.types.Scene, 'wfrl_clearance_' + demo + '_path', bpy.props.StringProperty(name=label, subtype='DIR_PATH'))
    for suffix in ('show_details', 'show_config', 'show_camera'):
        setattr(bpy.types.Scene, 'wfrl_clearance_' + suffix, bpy.props.BoolProperty(default=False))


def unregister_properties():
    for suffix in ('normal_path', 'near_tower_path', 'show_details', 'show_config', 'show_camera'):
        name = 'wfrl_clearance_' + suffix
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
