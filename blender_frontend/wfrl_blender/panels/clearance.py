"""Compact result comparison within the existing Camera controls."""
import bpy
from .. import clearance_replay


class WFRL_OT_ClearanceClip(bpy.types.Operator):
    bl_idname = 'wfrl.clearance_clip'
    bl_label = 'Load simulation result clip'
    bl_description = '加载已验证的离线结果包，从片段起点同步回放'
    demo: bpy.props.EnumProperty(items=[('normal', '正常测量', ''), ('near_tower', '叶片靠近塔筒', '')])

    def execute(self, context):
        scene = context.scene
        scene['wfrl_clearance_demo'] = self.demo
        path = getattr(scene, 'wfrl_clearance_' + self.demo + '_path')
        try:
            if not path.strip():
                raise ValueError('未配置所选片段的数据包目录')
            clearance_replay.load(scene, bpy.path.abspath(path), self.demo)
        except (ValueError, OSError, ImportError, KeyError, TypeError) as exc:
            from .. import _cancel_playback
            _cancel_playback()
            clearance_replay.clear(scene, '回放数据未就绪：' + str(exc))
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if not bpy.app.background and context.screen and not context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
        return {'FINISHED'}


def number(value, signed=False):
    return '--' if value is None else format(value, '+.2f' if signed else '.2f')


def draw(layout, scene):
    box = layout.box()
    box.label(text='净空与误差对比')
    box.label(text='FAST.Farm 预计算结果回放')
    box.label(text='模型为运动示意，未显示弹性变形')
    if clearance_replay.reader_for(scene):
        box.label(text='净空计算已考虑后端叶片变形')
    box.label(text='手册简化估算 · 理想测距 · B2')
    row = box.row(align=True)
    for demo, title in (('normal', '正常测量'), ('near_tower', '叶片靠近塔筒')):
        row.operator('wfrl.clearance_clip', text=title, depress=scene.get('wfrl_clearance_demo') == demo).demo = demo
    if scene.get('wfrl_clearance_demo') == 'near_tower':
        box.label(text='后端较小净空工况，形变未显示')
    value = clearance_replay.sample(scene)
    if value is None:
        box.label(text=scene.get('wfrl_clearance_status', '回放数据未就绪'), icon='INFO')
    else:
        box.label(text=f"当前回放：t = {value['time_s']:.2f} s")
        measurement = value['measurement'] or {}
        box.label(text='仿真真实净空：' + number(measurement.get('truth_m')) + ' m')
        box.label(text='算法估计净空（B2）：' + number(measurement.get('estimate_m')) + ' m')
        box.label(text='估计偏差：' + number(measurement.get('error_m'), True) + ' m')
        box.label(text='正值表示高估净空')
        status = value['status']
        box.label(text={'waiting': '等待测量', 'above_threshold': '高于演示阈值', 'near_threshold': '接近演示阈值'}[status],
                  icon={'waiting': 'RADIOBUT_OFF', 'above_threshold': 'KEYTYPE_JITTER_VEC', 'near_threshold': 'KEYTYPE_KEYFRAME_VEC'}[status])
        if measurement:
            box.label(text=f"测量时刻：{measurement['time_s']:.2f} s · 叶片 {measurement['blade_id']}")
            box.label(text=f"最近有效测量：{value['measurement_age_s']:.1f} 仿真秒前")
        stats = value['statistics']
        box.label(text='本片段 · 截至当前回放位置')
        box.label(text='平均绝对误差：' + number(stats.get('mae_m')) + ' m')
        box.label(text='最大绝对误差：' + number(stats.get('max_abs_error_m')) + ' m')
        ratio = stats.get('valid_ratio')
        box.label(text='有效测量比例：' + ('不适用' if ratio is None else f'{ratio:.1%}'))
        row = box.row(align=True)
        row.operator('screen.animation_play', text='播放 / 暂停', icon='PLAY')
        box.prop(scene, 'frame_current', text='回放进度')
    box.label(text='测量光束示意 · 阈值仅用于演示')
    box.label(text='真实红外不可见；线端不代表测距')
    box.prop(scene, 'wfrl_clearance_normal_path')
    box.prop(scene, 'wfrl_clearance_near_tower_path')


CLASSES = (WFRL_OT_ClearanceClip,)


def register_properties():
    for demo, label in (('normal', '正常测量数据包'), ('near_tower', '较小净空数据包')):
        setattr(bpy.types.Scene, 'wfrl_clearance_' + demo + '_path', bpy.props.StringProperty(name=label, subtype='DIR_PATH'))


def unregister_properties():
    for demo in ('normal', 'near_tower'):
        name = 'wfrl_clearance_' + demo + '_path'
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
