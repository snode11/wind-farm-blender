"""Blender operators for manual Down views and video export."""
import os

import bpy
from bpy_extras.io_utils import ExportHelper

from . import cameras, manual_control
from . import manual_down as _down


def _turbine_default(context):
    target = context.scene.wfrl_manual_target
    return target if target != 'ALL' else 'T1'


class WFRL_OT_ManualDownView(bpy.types.Operator):
    bl_idname = 'wfrl.manual_down_view'
    bl_label = '观察 Down 相机'
    bl_description = '在当前视口通过所选风机的 Down 相机观察，并恢复该相机的安装位置和预设方向'

    turbine: bpy.props.EnumProperty(items=_down._TURBINES)

    @classmethod
    def poll(cls, context):
        return manual_control.is_manual(context.scene)

    def execute(self, context):
        scene = context.scene
        render = scene.render
        saved_render = (render.resolution_x, render.resolution_y, render.resolution_percentage)
        try:
            camera = _down.ensure_down_camera(scene, self.turbine, reset=True)
            render.resolution_x, render.resolution_y = _down.VIDEO_SIZE
            render.resolution_percentage = 100
            _down._ensure_labels(scene, camera)
            _down.update_labels(scene)
            for area in context.screen.areas if context.screen else ():
                if area.type != 'VIEW_3D':
                    continue
                space = area.spaces.active
                space.use_local_camera = True
                space.camera = camera
                space.region_3d.view_perspective = 'CAMERA'
                cameras.fill_camera_view(area, scene)
                area.tag_redraw()
            return {'FINISHED'}
        except Exception as exc:
            render.resolution_x, render.resolution_y, render.resolution_percentage = saved_render
            try:
                _down.update_labels(scene)
            except (ValueError, ReferenceError, AttributeError, KeyError):
                pass
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class WFRL_OT_ManualDownVideo(bpy.types.Operator, ExportHelper):
    bl_idname = 'wfrl.manual_down_video'
    bl_label = '导出 Down 视频'
    bl_description = ('按所选风机的转速与变桨设置渲染 Down 相机 H.264 MP4，其余风机冻结于实际姿态，'
                      '同时写出逐帧叶轮角度与三片桨距的 JSON')
    filename_ext = '.mp4'

    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})
    turbine: bpy.props.EnumProperty(name='风机', items=_down._TURBINES)
    length_mode: bpy.props.EnumProperty(name='长度', items=(
        ('DURATION', '按时长', '按秒数确定帧数'), ('FRAMES', '按帧数', '直接指定帧数')))
    duration_s: bpy.props.FloatProperty(name='时长 s', default=10.0, min=.04, max=3600.0, precision=2)
    frame_count: bpy.props.IntProperty(name='帧数', default=250, min=1, max=90000)
    fps: bpy.props.IntProperty(name='帧率', default=25, min=1, max=120)
    resolution: bpy.props.EnumProperty(name='分辨率', items=(
        ('1080', '1920×1080', '16:9 输出'), ('720', '1280×720', ''), ('540', '960×540', '')))
    engine: bpy.props.EnumProperty(name='渲染', items=_down._ENGINE_ITEMS)
    eevee_samples: bpy.props.IntProperty(name='EEVEE 采样', default=16, min=1, max=64)
    labels: bpy.props.BoolProperty(name='叶片编号叠加', default=True,
                                   description='在画面右上角显示当前画面中的叶片编号')
    from_start: bpy.props.BoolProperty(
        name='从变桨起点录制', default=True,
        description='开启时视频从本次变桨开始前的桨距录起；关闭时从当前实际桨距录起')

    @classmethod
    def poll(cls, context):
        return (manual_control.is_manual(context.scene)
                and not _down.active(context.scene)
                and not manual_control.trial_active(context.scene))

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
        if self.engine in _down.VIEWPORT_ENGINES:
            layout.label(text='需要可见窗口；导出期间界面暂停响应')
        layout.label(text='需要 FFmpeg 与 FFprobe，成片逐帧解码校验')
        if self.engine == 'SCENE':
            layout.prop(self, 'eevee_samples')
        layout.prop(self, 'labels')
        layout.prop(self, 'from_start')

    def invoke(self, context, event):
        self.turbine = _down._turbine_default(context)
        self.labels = context.scene.wfrl_manual_blade_labels
        self.filepath = bpy.path.abspath(f'//{self.turbine}_down.mp4') if bpy.data.filepath else \
            os.path.join(os.path.expanduser('~'), f'{self.turbine}_down.mp4')
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            written = _down.export_down_video(
                context.scene, self.turbine, self.filepath, frames=self._frames(), fps=self.fps,
                engine=self.engine, from_start=self.from_start, size=_down.VIDEO_SIZES[self.resolution],
                samples=self.eevee_samples, labels=self.labels, context=context, blocking=bpy.app.background)
        except (ValueError, OSError, RuntimeError, ReferenceError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f'已写出 {written}' if written else '正在渲染 Down 视频，完成后写出 MP4 与 JSON')
        return {'FINISHED'}
