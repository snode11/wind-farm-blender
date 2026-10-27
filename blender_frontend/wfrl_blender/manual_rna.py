"""RNA definitions and registration lifecycle for independent manual scenes."""
import bpy

from . import manual_control as control, manual_operators



def _properties(update_factory):
    return {
        'yaw_deg': bpy.props.FloatProperty(name='机舱朝向 °', default=0.0, min=-180.0, max=180.0,
                                           precision=1, update=update_factory('yaw_deg')),
        'pitch_deg': bpy.props.FloatProperty(name='集体桨距 °', default=0.0, min=-30.0, max=90.0,
                                             description='统一设置三片叶片桨距，覆盖各片独立桨距',
                                             precision=1, update=update_factory('pitch_deg')),
        'rpm': bpy.props.FloatProperty(name='转速 RPM', default=control.DEFAULT_RPM, min=0.0, max=30.0,
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
        **_properties(control._update),
        'turbine_id': bpy.props.StringProperty(),
        **{f'pitch_actual_b{i}_deg': bpy.props.FloatProperty(default=0.0) for i in range(1, 4)},
        **{f'pitch_start_b{i}_deg': bpy.props.FloatProperty(default=0.0) for i in range(1, 4)},
        'pitch_state_ready': bpy.props.BoolProperty(default=False),
        'pitch_paused': bpy.props.BoolProperty(default=True, options={'SKIP_SAVE'}),
        'running': bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'}, update=control._update('running')),
        **{field: bpy.props.PointerProperty(type=bpy.types.Object) for field in control._POINTER_FIELDS},
    }


class WFRL_ManualBatch(bpy.types.PropertyGroup):
    __annotations__ = _properties(control._batch_update)


class WFRL_ManualTrial(bpy.types.PropertyGroup):
    """Pending inputs and the saved configuration of one T1 pitch trial."""

    __annotations__ = {
        **{f'select_b{i}': bpy.props.BoolProperty(name=f'B{i}', default=i == 1)
           for i in range(1, 4)},
        **{f'target_b{i}_deg': bpy.props.FloatProperty(name=f'B{i} 目标 °', default=20.0,
                                                     min=-30.0, max=90.0, precision=1)
           for i in range(1, 4)},
        'duration_s': bpy.props.FloatProperty(name='变桨时长 s', default=8.0, min=.01,
                                             soft_max=60.0, precision=2),
        'interpolation': bpy.props.EnumProperty(name='变桨方式', default='SMOOTH', items=(
            ('SMOOTH', '平滑', '起止变桨速度为零的平滑过渡'),
            ('LINEAR', '匀速', '按桨距差除以时长的固定角速度变桨，到达目标后保持'))),
        'configuration': bpy.props.StringProperty(options={'HIDDEN'}),
        'engaged': bpy.props.BoolProperty(default=False, options={'HIDDEN'}),
        'elapsed_s': bpy.props.FloatProperty(name='试验时间 s', min=0.0, precision=3),
    }


CLASSES = (WFRL_ManualTurbine, WFRL_ManualBatch, WFRL_ManualTrial) + manual_operators.CLASSES

_SCENE_PROPERTIES = ('wfrl_manual_turbines', 'wfrl_manual_batch', 'wfrl_manual_target',
                     'wfrl_manual_return_scene', 'wfrl_manual_scene', 'wfrl_manual_step_deg',
                     'wfrl_manual_trial', 'wfrl_manual_pitch_rate_deg_s', 'wfrl_manual_display_detail',
                     'wfrl_manual_error', 'wfrl_manual_error_turbine',
                     'wfrl_manual_recovery', 'wfrl_manual_status')


def register():
    if control._registered:
        return
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_turbines = bpy.props.CollectionProperty(type=WFRL_ManualTurbine)
    bpy.types.Scene.wfrl_manual_batch = bpy.props.PointerProperty(type=WFRL_ManualBatch)
    bpy.types.Scene.wfrl_manual_trial = bpy.props.PointerProperty(type=WFRL_ManualTrial)
    for field in ('wfrl_manual_error', 'wfrl_manual_error_turbine', 'wfrl_manual_recovery'):
        setattr(bpy.types.Scene, field, bpy.props.StringProperty(options={'HIDDEN'}))
    bpy.types.Scene.wfrl_manual_status = bpy.props.EnumProperty(options={'HIDDEN'}, default='IDLE', items=(
        ('IDLE', '已暂停', ''), ('USER', '已主动暂停', ''),
        ('HIDDEN', '场景未在窗口显示，已暂停', ''),
        ('RESTORED', '载入或撤销恢复，已暂停', ''),
        ('ERROR', '保护暂停', ''), ('READY', '检查通过，保持暂停', '')))
    from .manual_geometry import DETAIL_ITEMS, detail_update
    bpy.types.Scene.wfrl_manual_display_detail = bpy.props.EnumProperty(
        name='显示精度', items=DETAIL_ITEMS, default='OVERVIEW', update=detail_update)
    bpy.types.Scene.wfrl_manual_pitch_rate_deg_s = bpy.props.FloatProperty(
        name='变桨速度 °/s', description='普通手动桨距按此速度到位；0 为立即到位，冻结试验使用自己的时间曲线',
        default=0.0, min=0.0, max=30.0, precision=1, update=control._pitch_rate_update)
    bpy.types.Scene.wfrl_manual_return_scene = bpy.props.PointerProperty(type=bpy.types.Scene)
    from .manual_scene_links import bind, resolve, restore
    bpy.types.Scene.wfrl_manual_scene = property(resolve, bind)
    bpy.types.Scene.wfrl_manual_step_deg = bpy.props.FloatProperty(
        name='微调步长 °', description='加减按钮按每台风机的当前值调整，不统一已有差异',
        default=1.0, min=.1, max=15.0, precision=1)
    bpy.types.Scene.wfrl_manual_target = bpy.props.EnumProperty(
        name='控制对象', items=(('T1', 'T1', '控制风机 T1'), ('T2', 'T2', '控制风机 T2'),
                              ('T3', 'T3', '控制风机 T3'), ('ALL', '全部', '只将编辑的参数应用到全部风机')),
        default='T1', update=control._selection_update)
    control._registered = True
    from . import manual_video
    manual_video.register()
    for name, function in control._HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)
    if hasattr(bpy.data, 'scenes'):
        restore()
        control.pause_all(reason='RESTORED', apply=False)
    if not bpy.app.timers.is_registered(control._timer):
        bpy.app.timers.register(control._timer, first_interval=control.TICK_SECONDS, persistent=True)


def unregister():
    from . import manual_scene
    from . import manual_video
    manual_video.unregister()
    control._registered = False
    if bpy.app.timers.is_registered(control._timer):
        bpy.app.timers.unregister(control._timer)
    control.pause_all()
    manual_scene.clear_views()
    for name, function in control._HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for name in _SCENE_PROPERTIES:
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
