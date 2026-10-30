"""Split-screen live field camera beside a turbine's simulated Down view.

Starting splits the calling 3D view in two: the left half shows the turbine's
Down camera of the manual scene, the right half becomes an Image Editor that
draws the newest decoded stream frame. The stream area's header carries a
close button; the panel's stop button does the same. One session exists per
Blender process; stopping, closing the stream area, loading a file or
disabling the extension ends FFmpeg and frees the texture.
"""
import bpy

from . import live_stream

_SESSION = {}
_HANDLE = None
_TICK_S = 0.02


def session():
    return _SESSION or None


def _areas(window):
    try:
        return list(window.screen.areas)
    except (ReferenceError, AttributeError):
        return []


def _find(window, pointer):
    return next((area for area in _areas(window) if area.as_pointer() == pointer), None)


def _draw():
    import gpu
    from gpu_extras.batch import batch_for_shader
    from gpu_extras.presets import draw_texture_2d
    import blf
    if not _SESSION or bpy.context.area is None or bpy.context.area.as_pointer() != _SESSION.get('area'):
        return
    region = bpy.context.region
    stream = _SESSION['stream']
    width, height = stream.size
    sequence, frame = stream.latest()
    if frame is not None and sequence != _SESSION['shown']:
        _SESSION['texture'] = _texture(frame, width, height)
        _SESSION['shown'] = sequence
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRI_FAN', {'pos': ((0, 0), (region.width, 0),
                                                         (region.width, region.height), (0, region.height))})
    shader.uniform_float('color', (0, 0, 0, 1))
    batch.draw(shader)
    if _SESSION.get('texture') is not None:
        scale = min(region.width / width, region.height / height)
        w, h = width * scale, height * scale
        draw_texture_2d(_SESSION['texture'], ((region.width - w) / 2, (region.height - h) / 2), w, h)
    font = 0
    blf.size(font, 14)
    blf.color(font, 1, 1, 1, .9)
    label = {'LIVE': f'LIVE  {stream.fps:.1f} fps', 'CONNECTING': 'CONNECTING',
             'RECONNECTING': 'RECONNECTING', 'ERROR': 'ERROR'}.get(stream.state, stream.state)
    blf.position(font, 12, region.height - 24, 0)
    blf.draw(font, f'{label}   {_SESSION["label"]}')
    if stream.state != 'LIVE' and stream.errors:
        blf.size(font, 12)
        blf.position(font, 12, region.height - 44, 0)
        blf.draw(font, stream.errors[-1][:160])


def _texture(frame, width, height):
    """Upload one float RGBA frame; GPUTexture takes initial data only as a FLOAT buffer."""
    import gpu
    data = gpu.types.Buffer('FLOAT', frame.size, frame)
    return gpu.types.GPUTexture((width, height), format='RGBA8', data=data)


def _tick():
    if not _SESSION:
        return None
    area = _find(_SESSION['window'], _SESSION['area'])
    if area is None or area.type != 'IMAGE_EDITOR':
        stop(close_area=False)
        return None
    if not _SESSION.get('arranged'):
        _arrange(area)
    if _SESSION['stream'].sequence != _SESSION['shown'] or _SESSION['stream'].state != 'LIVE':
        area.tag_redraw()
    return _TICK_S


def _arrange(area):
    """After the split has a real size, put the stream on the right half."""
    window = _SESSION['window']
    other = _find(window, _SESSION['partner'])
    if other is None or area.width <= 1 or other.width <= 1:
        return
    if area.x < other.x:
        # The split placed the new area on the left: swap roles so the
        # stream sits on the right beside the simulated view.
        area.type, other.type = 'VIEW_3D', 'IMAGE_EDITOR'
        _copy_view(_SESSION['view'], area.spaces.active)
        _SESSION['area'], _SESSION['partner'] = other.as_pointer(), area.as_pointer()
        area, other = other, area
    _prepare_stream_area(area)
    _SESSION['arranged'] = True


def _copy_view(saved, space):
    space.use_local_camera = saved['local']
    space.camera = saved['camera']
    space.region_3d.view_perspective = saved['perspective']
    space.show_region_ui = True


def _prepare_stream_area(area):
    space = area.spaces.active
    space.image = None
    space.show_region_header = True
    space.show_region_toolbar = False
    space.show_region_ui = False
    area.tag_redraw()


def start(context, url, *, turbine='T1', size='1280x720'):
    """Split the calling 3D view and play ``url`` on the right half."""
    global _HANDLE
    from .manual_down import ffmpeg_executable
    from . import manual_control
    url = live_stream.check_url(url)
    ffmpeg = ffmpeg_executable(context)
    if not ffmpeg:
        raise ValueError('播放现场视频需要 FFmpeg；请在扩展偏好设置“相机视频输出”中选择 ffmpeg.exe')
    area = context.area
    if area is None or area.type != 'VIEW_3D' or context.window is None:
        raise ValueError('请在三维视图中点击')
    stop()
    if manual_control.is_manual(context.scene):
        result = bpy.ops.wfrl.manual_down_view(turbine=turbine, side='CENTER')
        if 'FINISHED' not in result:
            raise ValueError(f'无法切换到 {turbine} Down 相机')
    space = area.spaces.active
    view = dict(local=space.use_local_camera, camera=space.camera,
                perspective=space.region_3d.view_perspective)
    window, screen = context.window, context.window.screen
    before = {item.as_pointer() for item in screen.areas}
    with context.temp_override(window=window, screen=screen, area=area):
        result = bpy.ops.screen.area_split(direction='VERTICAL', factor=.5)
    added = [item for item in screen.areas if item.as_pointer() not in before]
    if 'FINISHED' not in result or len(added) != 1:
        raise ValueError('无法分割当前视图')
    stream_area = added[0]
    stream_area.type = 'IMAGE_EDITOR'
    stream = live_stream.LiveStream(ffmpeg, url, live_stream.SIZES[size])
    _SESSION.update(stream=stream, window=window, area=stream_area.as_pointer(),
                    partner=area.as_pointer(), view=view, shown=0, texture=None, arranged=False,
                    turbine=turbine, label=f'{turbine} field  {live_stream.display_url(url)}')
    if _HANDLE is None:
        _HANDLE = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=_TICK_S)


def stop(*, close_area=True):
    """End the stream; optionally close its area so the simulated view fills the space again."""
    global _HANDLE
    if not _SESSION:
        return
    session_data = dict(_SESSION)
    _SESSION.clear()
    session_data['stream'].stop()
    if _HANDLE is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_HANDLE, 'WINDOW')
        _HANDLE = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if not close_area:
        return
    window = session_data['window']
    area = _find(window, session_data['area'])
    if area is None:
        return
    if not _close_area(window, area, _find(window, session_data['partner'])):
        area.type = 'VIEW_3D'


def _close_area(window, area, partner):
    """Merge the stream area back so the simulated view fills the space again."""
    region = next((item for item in area.regions if item.type == 'WINDOW'), None)
    before = len(_areas(window))
    try:
        with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
            bpy.ops.screen.area_close()
    except (RuntimeError, TypeError):
        pass
    if len(_areas(window)) < before or partner is None:
        return len(_areas(window)) < before
    # Join by dragging from the stream area into its partner.
    source = (area.x + area.width // 2, area.y + area.height // 2)
    target = (partner.x + partner.width // 2, partner.y + partner.height // 2)
    try:
        with bpy.context.temp_override(window=window, screen=window.screen, area=partner):
            bpy.ops.screen.area_join(source_xy=source, target_xy=target)
    except (RuntimeError, TypeError):
        pass
    return len(_areas(window)) < before


def _draw_header(self, context):
    if _SESSION and context.area is not None and context.area.as_pointer() == _SESSION.get('area'):
        self.layout.separator_spacer()
        self.layout.operator('wfrl.live_stream_stop', text='关闭现场视频', icon='X')


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    stop(close_area=False)


_STATE_TEXT = {'CONNECTING': '正在连接', 'LIVE': '正在播放', 'RECONNECTING': '重连中',
               'ERROR': '错误', 'STOPPED': '已停止'}


def status():
    """(icon, text) lines for the panel."""
    if not _SESSION:
        return []
    stream = _SESSION['stream']
    lines = [('PLAY' if stream.state == 'LIVE' else 'TIME',
              f'{_SESSION["turbine"]} 现场 · {_STATE_TEXT.get(stream.state, stream.state)}'
              + (f' · {stream.fps:.1f} fps' if stream.state == 'LIVE' else ''))]
    if stream.state != 'LIVE':
        lines.append(('ERROR' if stream.state == 'ERROR' else 'INFO', stream.message[:120]))
    return lines


class WFRL_OT_LiveStreamStart(bpy.types.Operator):
    bl_idname = 'wfrl.live_stream_start'
    bl_label = '分屏播放现场视频'
    bl_description = '左侧显示所选风机 Down 仿真相机，右侧播放现场相机视频流（需要 FFmpeg）'

    turbine: bpy.props.EnumProperty(items=(('T1', 'T1', ''), ('T2', 'T2', ''), ('T3', 'T3', '')),
                                    options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def execute(self, context):
        wm = context.window_manager
        try:
            start(context, wm.wfrl_live_url, turbine=self.turbine, size=wm.wfrl_live_size)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_LiveStreamStop(bpy.types.Operator):
    bl_idname = 'wfrl.live_stream_stop'
    bl_label = '停止现场视频'
    bl_description = '停止拉流并关闭右侧现场画面，左侧仿真视图恢复为整幅'

    @classmethod
    def poll(cls, context):
        return bool(_SESSION)

    def execute(self, context):
        stop()
        return {'FINISHED'}


CLASSES = (WFRL_OT_LiveStreamStart, WFRL_OT_LiveStreamStop)


def register():
    for cls in CLASSES:
        if not cls.is_registered:
            bpy.utils.register_class(cls)
    # Window-manager properties are not written to .blend files, so a stream
    # URL that carries an access token stays out of saved scenes.
    bpy.types.WindowManager.wfrl_live_url = bpy.props.StringProperty(
        name='流地址', description='现场相机视频流地址，例如 rtmp://主机:1935/rtp/设备ID_通道ID')
    bpy.types.WindowManager.wfrl_live_size = bpy.props.EnumProperty(
        name='解码尺寸', items=(('1280x720', '1280×720', '较低延迟与占用'),
                              ('1920x1080', '1920×1080', '与 Down 仿真画幅一致'),
                              ('960x540', '960×540', '弱网络或集成显卡')))
    if _on_load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load_pre)
    bpy.types.IMAGE_HT_header.remove(_draw_header)
    bpy.types.IMAGE_HT_header.append(_draw_header)


def unregister():
    stop(close_area=False)
    bpy.types.IMAGE_HT_header.remove(_draw_header)
    if _on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load_pre)
    for name in ('wfrl_live_url', 'wfrl_live_size'):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
