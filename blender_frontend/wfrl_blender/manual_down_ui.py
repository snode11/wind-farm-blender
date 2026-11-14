"""Manual Down operators, UI properties and callback registration."""
import os

import bpy
from bpy_extras.io_utils import ExportHelper

from . import cameras, manual_control, manual_dynamics
from . import manual_down_cameras as camera_rig
from . import manual_down_motion as motion
from . import manual_down_render as rendering
from . import manual_down_jobs as jobs
from .turbine_geometry import DEFAULT_MODEL, MODELS


_TURBINES = (('T1', 'T1', 'T1 风机'), ('T2', 'T2', 'T2 风机'), ('T3', 'T3', 'T3 风机'))


def _turbine_default(context):
    target = context.scene.wfrl_manual_target
    return target if target != 'ALL' else 'T1'


class WFRL_OT_ManualDownView(bpy.types.Operator):
    bl_idname = 'wfrl.manual_down_view'
    bl_label = '观察 Down 相机'
    bl_description = '在当前视口通过所选风机的 Down 相机观察，并恢复该相机的安装位置和预设方向'

    turbine: bpy.props.EnumProperty(items=_TURBINES)
    # SKIP_SAVE: a button that leaves ``side`` unset must mean CENTER, not the
    # side of the previous call.
    side: bpy.props.EnumProperty(
        name='侧向',
        items=(('CENTER', '中心', '主 Down 相机'),
               ('LEFT', '左侧', '左侧视相机'),
               ('RIGHT', '右侧', '右侧视相机')),
        default='CENTER', options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if context.scene is not None and jobs.is_exporting(context.scene):
            cls.poll_message_set('导出 Down 视频期间不能切换观察相机')
            return False
        return manual_control.is_manual(context.scene)

    def execute(self, context):
        area = getattr(context, 'area', None)
        if area is None or area.type != 'VIEW_3D':
            self.report({'ERROR'}, '请在要切换的三维视图中点击')
            return {'CANCELLED'}
        region = getattr(context, 'region', None)
        if region is None or region.type != 'WINDOW':
            region = next((item for item in area.regions if item.type == 'WINDOW'), None)
        space = area.spaces.active
        if (region is None or all(region != item for item in area.regions)
                or space is None or space.region_3d is None):
            self.report({'ERROR'}, '当前三维视图没有可用的窗口区域')
            return {'CANCELLED'}
        scene = context.scene
        scene.render.resolution_x, scene.render.resolution_y = rendering.VIDEO_SIZE
        scene.render.resolution_percentage = 100
        try:
            camera = camera_rig.ensure_down_camera(scene, self.turbine, reset=True)
            if self.side != 'CENTER':
                camera = camera_rig.ensure_side_cameras(scene, self.turbine)[self.side.capitalize()]
        except (ValueError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        space.use_local_camera = True
        space.camera = camera
        space.region_3d.view_perspective = 'CAMERA'
        cameras.fill_camera_view(area, scene)
        area.tag_redraw()
        return {'FINISHED'}


_ENGINE_ITEMS = (
    ('VIEWPORT_MATERIAL', '实时 · 视口材质',
     '视口材质预览（场景天空、光照、阴影与材质，单采样）；独立显卡且有 FFmpeg 时 1080p 约 0.02–0.04 s/帧，'
     '导出期间界面暂停响应'),
    ('VIEWPORT_SOLID', '最快 · 视口实体', '视口纯色实体着色，速度与视口材质相近；导出期间界面暂停响应'),
    ('SCENE', '高质量 · EEVEE 渲染', '最终渲染，含多采样抗锯齿；16 采样约 0.8 s/帧，可在渲染窗口取消'),
    ('WORKBENCH', '后台 · Workbench', '最终渲染路径的纯色模式，约 0.2 s/帧'))


class WFRL_OT_ManualDownVideo(bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_down_video'
    bl_label = '导出 Down 视频'
    bl_description = ('按当前转速与变桨设置渲染所选风机 Down 相机的 H.264 MP4，'
                      '同时写出逐帧叶轮角度与三片桨距的 JSON')
    filename_ext = '.mp4'

    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})
    turbine: bpy.props.EnumProperty(name='风机', items=_TURBINES)
    length_mode: bpy.props.EnumProperty(name='长度', items=(
        ('DURATION', '按时长', '按秒数确定帧数'), ('FRAMES', '按帧数', '直接指定帧数')))
    duration_s: bpy.props.FloatProperty(name='时长 s', default=10.0, min=.04, max=3600.0, precision=2)
    frame_count: bpy.props.IntProperty(name='帧数', default=250, min=1, max=90000)
    fps: bpy.props.IntProperty(name='帧率', default=25, min=1, max=120)
    resolution: bpy.props.EnumProperty(name='分辨率', items=(
        ('1080', '1920×1080', '与现场相机一致'), ('720', '1280×720', ''), ('540', '960×540', '')))
    engine: bpy.props.EnumProperty(name='渲染', items=_ENGINE_ITEMS)
    eevee_samples: bpy.props.IntProperty(name='EEVEE 采样', default=16, min=1, max=64)
    labels: bpy.props.BoolProperty(name='叶片编号叠加', default=True,
                                   description='在画面右上角显示当前画面中的叶片编号')
    from_start: bpy.props.BoolProperty(
        name='从变桨起点录制', default=True,
        description='开启时视频从本次变桨开始前的桨距录起；关闭时从当前实际桨距录起')
    dynamics: bpy.props.BoolProperty(
        name='叶片摆动与塔架运动', default=True,
        description='按转速与桨距叠加示意性的叶片挥舞弯曲和塔顶前后运动，各叶片净空随时间不同；不是结构求解')
    use_seed: bpy.props.BoolProperty(
        name='固定随机种子', default=False,
        description='相机抖动与叶片／塔架运动使用下方种子，相同设置重复导出得到相同的抖动与运动；关闭时每次随机')
    seed: bpy.props.IntProperty(name='种子', default=1, min=0, max=(1 << 30) - 1,
                                description='写入 JSON 的 camera_shake.seed 与 structural_motion.seed')

    @classmethod
    def poll(cls, context):
        return (manual_control.is_manual(context.scene)
                and not jobs.is_exporting(context.scene))

    def _frames(self):
        return self.frame_count if self.length_mode == 'FRAMES' else max(1, round(self.duration_s * self.fps))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'turbine')
        layout.prop(self, 'length_mode', expand=True)
        layout.prop(self, 'duration_s' if self.length_mode == 'DURATION' else 'frame_count')
        layout.prop(self, 'fps')
        frames = self._frames()
        layout.label(text=f'共 {frames} 帧 · {frames / self.fps:.2f} s')
        layout.prop(self, 'resolution')
        layout.prop(self, 'engine')
        if self.engine in rendering.VIEWPORT_ENGINES:
            renderer = rendering.gpu_renderer()
            layout.label(text='GPU ' + renderer, icon='ERROR' if rendering.is_integrated_gpu(renderer) else 'INFO')
            if rendering.is_integrated_gpu(renderer):
                layout.label(text='当前为集成显卡，导出较慢；在 Windows 图形设置中将 Blender 设为高性能')
            if rendering.ffmpeg_executable(context):
                layout.label(text='编码 FFmpeg · 快速路径', icon='INFO')
            else:
                layout.label(text='未找到 FFmpeg，使用 Blender 内置编码（较慢）', icon='ERROR')
                layout.label(text='可在扩展偏好设置“相机视频输出”中选择 ffmpeg.exe')
        if self.engine == 'SCENE':
            layout.prop(self, 'eevee_samples')
        layout.prop(self, 'labels')
        layout.prop(self, 'from_start')
        layout.prop(self, 'dynamics')
        row = layout.row(heading='固定随机种子')
        row.prop(self, 'use_seed', text='')
        sub = row.row()
        sub.enabled = self.use_seed
        sub.prop(self, 'seed', text='')

    def invoke(self, context, event):
        self.turbine = _turbine_default(context)
        self.labels = context.scene.wfrl_manual_blade_labels
        self.dynamics = context.scene.wfrl_manual_structural_motion
        self.filepath = bpy.path.abspath(f'//{self.turbine}_down.mp4') if bpy.data.filepath else \
            os.path.join(os.path.expanduser('~'), f'{self.turbine}_down.mp4')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            written = jobs.export_down_video(
                context.scene, self.turbine, self.filepath, frames=self._frames(), fps=self.fps,
                engine=self.engine, from_start=self.from_start, size=rendering.VIDEO_SIZES[self.resolution],
                samples=self.eevee_samples, labels=self.labels, dynamics=self.dynamics,
                seed=self.seed if self.use_seed else None, context=context)
        except (ValueError, RuntimeError, ReferenceError, AttributeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'已写出 {written}' if written else '正在渲染 Down 视频，完成后写出 MP4 与 JSON')
        return {'FINISHED'}


class WFRL_OT_ManualTripleDownVideo(bpy.types.Operator, ExportHelper):
    """Export the main Down camera plus Left and Right side cameras as three synchronised MP4s."""
    bl_idname = 'wfrl.manual_triple_down_video'
    bl_label = '导出三路 Down 视频'
    bl_description = ('按当前转速与变桨设置渲染所选风机的中心、左、右三路 Down 相机 H.264 MP4，'
                      '三路视频帧对帧同步；相邻相机重叠 12°，合计有效视场 153°')
    filename_ext = '.mp4'

    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})
    turbine: bpy.props.EnumProperty(name='风机', items=_TURBINES)
    length_mode: bpy.props.EnumProperty(name='长度', items=(
        ('DURATION', '按时长', '按秒数确定帧数'), ('FRAMES', '按帧数', '直接指定帧数')))
    duration_s: bpy.props.FloatProperty(name='时长 s', default=10.0, min=.04, max=3600.0, precision=2)
    frame_count: bpy.props.IntProperty(name='帧数', default=250, min=1, max=90000)
    fps: bpy.props.IntProperty(name='帧率', default=25, min=1, max=120)
    resolution: bpy.props.EnumProperty(name='分辨率', items=(
        ('1080', '1920×1080', '与现场相机一致'), ('720', '1280×720', ''), ('540', '960×540', '')))
    engine: bpy.props.EnumProperty(name='渲染', items=(
        ('VIEWPORT_MATERIAL', '实时 · 视口材质', '视口材质预览，单采样'),
        ('VIEWPORT_SOLID', '最快 · 视口实体', '视口纯色实体着色')))
    labels: bpy.props.BoolProperty(name='叶片编号叠加', default=True,
                                    description='在每路画面右上角显示当前画面中的叶片编号')
    from_start: bpy.props.BoolProperty(
        name='从变桨起点录制', default=True,
        description='开启时视频从本次变桨开始前的桨距录起；关闭时从当前实际桨距录起')
    dynamics: bpy.props.BoolProperty(
        name='叶片摆动与塔架运动', default=True,
        description='按转速与桨距叠加示意性的叶片挥舞弯曲和塔顶前后运动，各叶片净空随时间不同；不是结构求解')
    use_seed: bpy.props.BoolProperty(
        name='固定随机种子', default=False,
        description='相机抖动与叶片／塔架运动使用下方种子，相同设置重复导出得到相同的抖动与运动；关闭时每次随机')
    seed: bpy.props.IntProperty(name='种子', default=1, min=0, max=(1 << 30) - 1,
                                description='写入 JSON 的 camera_shake.seed 与 structural_motion.seed')

    @classmethod
    def poll(cls, context):
        return (manual_control.is_manual(context.scene)
                and not jobs.is_exporting(context.scene))

    def _frames(self):
        return self.frame_count if self.length_mode == 'FRAMES' else max(1, round(self.duration_s * self.fps))

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, 'turbine')
        layout.prop(self, 'length_mode', expand=True)
        layout.prop(self, 'duration_s' if self.length_mode == 'DURATION' else 'frame_count')
        layout.prop(self, 'fps')
        frames = self._frames()
        layout.label(text=f'共 {frames} 帧 · {frames / self.fps:.2f} s · 三路共 {3 * frames} 帧')
        layout.prop(self, 'resolution')
        layout.prop(self, 'engine')
        renderer = rendering.gpu_renderer()
        layout.label(text='GPU ' + renderer, icon='ERROR' if rendering.is_integrated_gpu(renderer) else 'INFO')
        if rendering.is_integrated_gpu(renderer):
            layout.label(text='当前为集成显卡，导出较慢；在 Windows 图形设置中将 Blender 设为高性能')
        if rendering.ffmpeg_executable(context):
            layout.label(text='编码 FFmpeg · 三路并行快速路径', icon='INFO')
        else:
            layout.label(text='三路导出需要 FFmpeg，当前未找到', icon='ERROR')
            layout.label(text='请在扩展偏好设置“相机视频输出”中选择 ffmpeg.exe')
        layout.label(text='保存路径选择中心路；左路、右路文件名加 _left、_right 后缀')
        layout.prop(self, 'labels')
        layout.prop(self, 'from_start')
        layout.prop(self, 'dynamics')
        row = layout.row(heading='固定随机种子')
        row.prop(self, 'use_seed', text='')
        sub = row.row()
        sub.enabled = self.use_seed
        sub.prop(self, 'seed', text='')

    def invoke(self, context, event):
        self.turbine = _turbine_default(context)
        self.labels = context.scene.wfrl_manual_blade_labels
        self.dynamics = context.scene.wfrl_manual_structural_motion
        self.filepath = bpy.path.abspath(f'//{self.turbine}_down.mp4') if bpy.data.filepath else \
            os.path.join(os.path.expanduser('~'), f'{self.turbine}_down.mp4')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            center = jobs._mp4_target(self.filepath)
            stem = center.stem
            for tail in ('_down', '_center'):
                if stem.lower().endswith(tail):
                    stem = stem[: -len(tail)]
                    break
            left = center.with_name(f'{stem}_left.mp4')
            right = center.with_name(f'{stem}_right.mp4')
            written = jobs.export_triple_down_video(
                context.scene, self.turbine, [center, left, right], frames=self._frames(), fps=self.fps,
                engine=self.engine, from_start=self.from_start, size=rendering.VIDEO_SIZES[self.resolution],
                labels=self.labels, dynamics=self.dynamics, seed=self.seed if self.use_seed else None,
                context=context)
        except (ValueError, RuntimeError, ReferenceError, AttributeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'已写出三路视频：{os.path.basename(written[0])} / {os.path.basename(written[1])} / {os.path.basename(written[2])}')
        return {'FINISHED'}


CLASSES = (WFRL_OT_ManualDownView, WFRL_OT_ManualDownVideo, WFRL_OT_ManualTripleDownVideo)
_BLADE_ITEMS = tuple((key, label, '手动场景叶片外形', index)
                     for index, (key, (_, label)) in enumerate(MODELS.items()))


def register():
    for name, function in jobs._LIFECYCLE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    bpy.types.Scene.wfrl_manual_blade_model = bpy.props.EnumProperty(
        name='叶片外形', items=_BLADE_ITEMS, default=DEFAULT_MODEL, update=camera_rig._blade_model_update)
    bpy.types.Scene.wfrl_manual_blade_labels = bpy.props.BoolProperty(
        name='叶片编号', default=True, update=camera_rig._labels_update,
        description='在 Down 画面右上角显示当前画面中的叶片编号 B1/B2/B3')
    if camera_rig._labels_listener not in manual_control.POSE_LISTENERS:
        manual_control.POSE_LISTENERS.append(camera_rig._labels_listener)
    manual_dynamics.register()
    bpy.types.Scene.wfrl_manual_camera_shake = bpy.props.BoolProperty(
        name='画面抖动', default=True, update=motion._shake_toggle,
        description='运转时 Down 相机随转速做示意性的机舱振动小角度抖动（与导出视频同一模型）')
    if motion.live_shake_update not in manual_control.MOTION_HOOKS:
        manual_control.MOTION_HOOKS.append(motion.live_shake_update)  # after the blade bend: tips are current
    if motion.live_shake_stop not in manual_control.STOP_HOOKS:
        manual_control.STOP_HOOKS.append(motion.live_shake_stop)
    for name, function in motion._SHAKE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)


def unregister():
    jobs.stop_exports()
    for name, function in jobs._LIFECYCLE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    motion.stop_live_shakes()
    for name, function in motion._SHAKE_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for hooks, function in ((manual_control.MOTION_HOOKS, motion.live_shake_update),
                            (manual_control.STOP_HOOKS, motion.live_shake_stop)):
        if function in hooks:
            hooks.remove(function)
    if hasattr(bpy.types.Scene, 'wfrl_manual_camera_shake'):
        del bpy.types.Scene.wfrl_manual_camera_shake
    manual_dynamics.unregister()
    if camera_rig._labels_listener in manual_control.POSE_LISTENERS:
        manual_control.POSE_LISTENERS.remove(camera_rig._labels_listener)
    for name in ('wfrl_manual_blade_model', 'wfrl_manual_blade_labels'):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
