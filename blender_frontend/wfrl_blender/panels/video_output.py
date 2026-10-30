"""Two video outputs inside the existing WFRL frontend."""
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from .. import video_output as service
from ..preferences import get_preferences
from .ui_helpers import message, diagnostic, output_directory


def _source(context):
    preferences = get_preferences(context)
    return bpy.path.abspath(preferences.camera_video_file) if preferences and preferences.camera_video_file else ''


def _tick():
    service.stream.poll()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return .25 if service.stream.active or service.export.active else None


def _watch():
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=.1)


class WFRL_OT_VideoExport(bpy.types.Operator):
    bl_idname = 'wfrl.video_export'
    bl_label = '导出离线 MP4'
    bl_description = '将所选的已生成相机视频原样导出为 MP4，不重新渲染或录制窗口'
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(default='*.mp4', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return bool(_source(context)) and not service.export.active

    def invoke(self, context, event):
        self.filepath = str(Path(_source(context)).with_name('camera-video-export.mp4'))
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            service.export.start(_source(context), bpy.path.abspath(self.filepath))
            _watch()
        except (OSError, ValueError, RuntimeError) as exc:
            if not service.export.active:
                service.export.error = service.export.message = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_VideoStream(bpy.types.Operator):
    bl_idname = 'wfrl.video_stream'
    bl_label = '启动 RTSP'
    bl_description = '循环输出所选的相机 MP4；不改变 Blender 回放和当前视角'

    @classmethod
    def poll(cls, context):
        return bool(_source(context)) and not service.stream.active

    def execute(self, context):
        prefs = get_preferences(context)
        try:
            service.stream.start(_source(context),
                                 bpy.path.abspath(prefs.video_ffmpeg) if prefs.video_ffmpeg else '',
                                 bpy.path.abspath(prefs.video_mediamtx) if prefs.video_mediamtx else '',
                                 prefs.video_rtsp_port, prefs.video_rtsp_lan)
            _watch()
        except (OSError, ValueError, RuntimeError) as exc:
            if not service.stream.active:
                service.stream.state, service.stream.message = 'ERROR', str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_VideoStop(bpy.types.Operator):
    bl_idname = 'wfrl.video_stop'
    bl_label = '停止 RTSP'

    @classmethod
    def poll(cls, context):
        return service.stream.active

    def execute(self, context):
        service.stream.stop()
        return {'FINISHED'}


class WFRL_OT_VideoCopyURL(bpy.types.Operator):
    bl_idname = 'wfrl.video_copy_url'
    bl_label = '复制 RTSP 地址'
    bl_description = '复制本机读取地址；另一台电脑读取时将 127.0.0.1 换成本机局域网 IP'

    @classmethod
    def poll(cls, context):
        return service.stream.state == 'RUNNING'

    def execute(self, context):
        context.window_manager.clipboard = service.stream.url
        self.report({'INFO'}, '已复制本机 RTSP 地址')
        return {'FINISHED'}


def draw(layout, context):
    prefs = get_preferences(context)
    if prefs is None:
        layout.label(text='请先安装并启用 WFRL 扩展', icon='ERROR')
        return
    layout.label(text='相机视频输出', icon='CAMERA_DATA')
    source = layout.column()
    source.enabled = not service.stream.active and not service.export.active
    source.prop(prefs, 'camera_video_file', text='视频')
    source_path = _source(context)
    if source_path:
        message(layout, '源文件：' + Path(source_path).name, context=context)
    message(layout, '输出已有 MP4 文件；当前视口与回放进度不影响所选视频。', context=context)
    export = layout.box()
    export.label(text='离线 MP4 文件', icon='FILE_MOVIE')
    row = export.column(align=True)
    row.scale_y = 1.3
    row.operator('wfrl.video_export', text='导出所选 MP4…', icon='EXPORT')
    if service.export.active:
        factor = min(1., service.export.done_bytes / max(1, service.export.total_bytes))
        export.progress(factor=factor, type='BAR', text=f'复制进度 {factor:.0%}')
        export.label(text=f'{service.export.done_bytes / 1048576:.1f} / {service.export.total_bytes / 1048576:.1f} MiB')
    elif service.export.error:
        diagnostic(export, 'MP4 导出失败', service.export.error, error=True, context=context)
    elif service.export.output_path:
        export.label(text='MP4 导出完成', icon='CHECKMARK')
        message(export, '输出：' + service.export.output_path, context=context)
        output_directory(export, service.export.output_path)
    else:
        message(export, service.export.message, context=context)
    stream = layout.box()
    stream.label(text='RTSP 循环输出', icon='LINKED')
    row = stream.column(align=True)
    row.scale_y = 1.3
    if service.stream.active:
        row.operator('wfrl.video_stop', icon='PAUSE')
    else:
        row.operator('wfrl.video_stream', icon='PLAY')
    if service.stream.state == 'ERROR':
        diagnostic(stream, 'RTSP 输出失败', service.stream.message, error=True, context=context)
    else:
        message(stream, service.stream.message, context=context)
    if service.stream.state == 'RUNNING':
        message(stream, '本机地址：' + service.stream.url, context=context)
        stream.operator('wfrl.video_copy_url', icon='COPYDOWN')
        if prefs.video_rtsp_lan:
            message(stream, '远端读取：将 127.0.0.1 换成本机局域网 IP', context=context)
    header, paths = layout.panel('wfrl_video_output_paths', default_closed=True)
    header.label(text='源文件与导出位置')
    if paths is not None:
        message(paths, '当前源文件：' + (source_path or '未选择'), context=context)
        if service.export.source_path:
            message(paths, '最近一次导出源：' + service.export.source_path, context=context)
        if service.export.output_path:
            message(paths, '最近一次导出：' + service.export.output_path, context=context)
    header, settings = layout.panel('wfrl_video_output_settings', default_closed=True)
    header.label(text='视频输出设置')
    if settings is not None:
        settings.enabled = not service.stream.active
        settings.prop(prefs, 'video_ffmpeg', text='FFmpeg')
        settings.prop(prefs, 'video_mediamtx', text='MediaMTX')
        settings.prop(prefs, 'video_rtsp_port', text='RTSP 端口')
        settings.prop(prefs, 'video_rtsp_lan', text='允许局域网读取')
        settings.label(text='Windows 选择对应 .exe；本机路径仅保存在偏好设置')
        settings.label(text='局域网读取需在服务机允许入站 TCP 端口')
        settings.operator('wm.save_userpref', text='保存视频输出设置', icon='FILE_TICK')


class WFRL_PT_VideoOutput(bpy.types.Panel):
    bl_idname = 'WFRL_PT_video_output'
    bl_label = '相机视频输出'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MAPPO'

    @classmethod
    def poll(cls, context):
        from .. import farm_flex
        return not farm_flex.is_active(context.scene)

    def draw(self, context):
        draw(self.layout, context)


@persistent
def _before_load(_):
    service.shutdown()


def register():
    if _before_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_before_load)


def unregister():
    service.shutdown()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_before_load)


CLASSES = (WFRL_OT_VideoExport, WFRL_OT_VideoStream, WFRL_OT_VideoStop,
           WFRL_OT_VideoCopyURL, WFRL_PT_VideoOutput)
