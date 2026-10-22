"""User-owned backend configuration. No machine-specific defaults."""
import bpy


def get_preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


class WFRLPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    project_dir: bpy.props.StringProperty(name='Project directory', subtype='DIR_PATH', default='')
    backend_python: bpy.props.StringProperty(name='Backend Python', subtype='FILE_PATH', default='')
    mpi_path: bpy.props.StringProperty(name='MPI executable', subtype='FILE_PATH', default='')
    fastfarm_path: bpy.props.StringProperty(name='FAST.Farm executable', subtype='FILE_PATH', default='')
    default_scene: bpy.props.StringProperty(name='Default scene', subtype='FILE_PATH', default='')
    port: bpy.props.IntProperty(name='Bridge port', default=8765, min=1024, max=65535)
    camera_video_file: bpy.props.StringProperty(name='相机视频', subtype='FILE_PATH', default='')
    video_ffmpeg: bpy.props.StringProperty(name='FFmpeg', subtype='FILE_PATH', default='')
    video_mediamtx: bpy.props.StringProperty(name='MediaMTX', subtype='FILE_PATH', default='')
    video_rtsp_port: bpy.props.IntProperty(name='RTSP 端口', default=8554, min=1024, max=65535)
    video_rtsp_lan: bpy.props.BoolProperty(name='允许局域网读取', default=False)

    def draw(self, context):
        layout = self.layout
        column = layout.column()
        # Runtime owns lifecycle policy; this hook never starts background work.
        from . import runtime
        editable = runtime.configuration_editable()
        column.enabled = editable
        for name in ('project_dir', 'backend_python', 'mpi_path', 'fastfarm_path', 'default_scene', 'port'):
            column.prop(self, name)
        if not editable:
            layout.label(text='Configuration locked while a session is active or unconfirmed.', icon='LOCKED')
        layout.label(text='Local Demo works without a backend Python, MPI or FAST.Farm.')
        layout.label(text='Use absolute executable paths. Check environment in the WFRL status panel.')
        video = layout.box()
        video.label(text='相机视频输出')
        for name in ('camera_video_file', 'video_ffmpeg', 'video_mediamtx', 'video_rtsp_port', 'video_rtsp_lan'):
            video.prop(self, name)


CLASSES = (WFRLPreferences,)
