"""Two video outputs inside the existing WFRL frontend."""
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from .. import video_output as service
from ..preferences import get_preferences


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
    layout.label(text='输出所选的相机视频')
    layout.label(text='不随当前视口改变')
    row = layout.column(align=True)
    row.scale_y = 1.3
    row.operator('wfrl.video_export', icon='EXPORT')
    if service.stream.active:
        row.operator('wfrl.video_stop', icon='PAUSE')
    else:
        row.operator('wfrl.video_stream', icon='PLAY')
    if service.export.active:
        layout.label(text=f'正在导出 {service.export.done_bytes / max(1, service.export.total_bytes):.0%}')
    else:
        layout.label(text=service.export.message, icon='ERROR' if service.export.error else 'FILE_MOVIE')
    layout.label(text=service.stream.message.splitlines()[0][:90], icon='ERROR' if service.stream.state == 'ERROR' else 'INFO')
    if service.stream.state == 'RUNNING':
        layout.label(text=service.stream.url)
        layout.operator('wfrl.video_copy_url', icon='COPYDOWN')
        if prefs.video_rtsp_lan:
            layout.label(text='远端读取：将 127.0.0.1 换成本机局域网 IP')
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
