"""Split-screen live field camera beside a turbine's simulated Down view.

Starting splits the calling 3D view in two: the left half shows the turbine's
Down camera of the manual scene, the right half becomes an Image Editor that
draws the newest decoded stream frame. The stream area's header carries a
close button; the panel's stop button does the same. One session exists per
Blender process. Leaving the originating layout stops the stream and defers
merging its area until the layout is visible again. The screen and its private
generated image identify the owned split after a file is reopened.
"""
from uuid import uuid4

import bpy

from . import live_stream

_SESSION = {}
_CLOSING = {}
_KEPT_EDITORS = {}
_HANDLE = None
_LAYOUT_KEY = 'wfrl_live_split'
_TICK_S = 0.02
_RESTORE_S = 0.2


def session():
    """Playing or still-stopping session. Drawing code calls this, so it only reads state;
    the timer drops a finished stopping record and redraws the views that showed it."""
    if _SESSION:
        return _SESSION
    return _CLOSING if _CLOSING and _CLOSING['stream'].active else None


def _closing():
    if _CLOSING and not _CLOSING['stream'].active:
        _CLOSING.clear()
    return _CLOSING


def _areas(window):
    try:
        return list(window.screen.areas)
    except (ReferenceError, AttributeError):
        return []


def _find(window, pointer):
    return next((area for area in _areas(window) if area.as_pointer() == pointer), None)


def _origin(window):
    return window.as_pointer(), window.workspace.session_uid, window.screen.session_uid


def _window(origin):
    wm = bpy.context.window_manager
    if wm is None:
        return None
    return next((window for window in wm.windows if window.as_pointer() == origin[0]), None)


def _image_editor(area):
    if area is not None and area.ui_type == 'IMAGE_EDITOR':
        space = area.spaces.active
        if space.ui_mode == 'VIEW':
            return space
    return None


def _blank_marker(image):
    return (image is not None and image.source == 'GENERATED' and tuple(image.size) == (1, 1)
            and not image.is_dirty)


def _owns_editor(area, layout):
    if area is None or area.as_pointer() != layout['area']:
        return False
    space = _image_editor(area)
    return (space is not None and space.as_pointer() == layout['editor'] and space.image == layout['marker']
            and _blank_marker(space.image))


def _partner(window, layout):
    area = _find(window, layout['partner'])
    if (area is not None and area.type == 'VIEW_3D' and
            area.spaces.active.as_pointer() == layout['partner_space']):
        return area
    return None


def _owns_context(context):
    return (_SESSION and context.window is not None and _origin(context.window) == _SESSION['origin']
            and _owns_editor(context.area, _SESSION))


def _draw():
    if not _owns_context(bpy.context):
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    from gpu_extras.presets import draw_texture_2d
    import blf
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
    errors = stream.diagnostics()
    if stream.state != 'LIVE' and errors:
        blf.size(font, 12)
        blf.position(font, 12, region.height - 44, 0)
        blf.draw(font, errors[-1][:160])


def _texture(frame, width, height):
    """Upload one float RGBA frame; GPUTexture takes initial data only as a FLOAT buffer."""
    import gpu
    data = gpu.types.Buffer('FLOAT', frame.size, frame)
    return gpu.types.GPUTexture((width, height), format='RGBA8', data=data)


def _tick():
    _update_closing_status()
    _restore_ready_layouts()
    if not _SESSION:
        return _RESTORE_S if _needs_tick() else None
    window = _window(_SESSION['origin'])
    area = (_find(window, _SESSION['area'])
            if window is not None and _origin(window) == _SESSION['origin'] else None)
    if not _owns_editor(area, _SESSION):
        stop()
        return _RESTORE_S if _needs_tick() else None
    if not _SESSION.get('arranged'):
        _arrange(window, area)
    stream = _SESSION['stream']
    errors = stream.diagnostics()
    status = (*stream.status(), errors[-1] if errors else '')
    if stream.sequence != _SESSION['shown'] or status != _SESSION.get('drawn_status'):
        _SESSION['drawn_status'] = status
        area.tag_redraw()
    return _TICK_S


def _update_closing_status():
    if not _CLOSING:
        return
    previous = _CLOSING.get('drawn_status')
    closing = _closing()
    current = closing['stream'].status() if closing else None
    if closing and current == previous:
        return
    if closing:
        closing['drawn_status'] = current
    wm = bpy.context.window_manager
    if wm is not None:
        for window in wm.windows:
            for area in _areas(window):
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def _arrange(window, area):
    """After the split has a real size, put the stream on the right half."""
    other = _partner(window, _SESSION)
    if other is None or area.width <= 1 or other.width <= 1 or area.x == other.x:
        return
    layout, caller = dict(_SESSION), other
    try:
        if area.x < other.x:
            # The split placed the new area on the left: swap roles so the
            # stream sits on the right beside the simulated view.
            area.spaces.active.image = None
            area.type = 'VIEW_3D'
            _copy_view(_SESSION['view'], area.spaces.active)
            area.spaces.active.show_region_ui = True
            _SESSION.update(area=other.as_pointer(), partner=area.as_pointer(),
                            partner_space=area.spaces.active.as_pointer())
            _SESSION['editor'] = _prepare_stream_area(other, _SESSION['marker'])
            area, other = other, area
        _record_layout(window.screen, area, other, _SESSION['marker'])
        _SESSION['arranged'] = True
    except Exception:
        # A partial role swap still owns only the newly split area and its caller.
        _SESSION.update(layout, editor=None)
        try:
            caller.type = 'VIEW_3D'
            _SESSION['partner_space'] = caller.spaces.active.as_pointer()
            _copy_view(layout['saved_view'], caller.spaces.active)
            caller.tag_redraw()
        finally:
            stop()
        raise


def _view_state(space):
    region, shading = space.region_3d, space.shading
    return dict(local=space.use_local_camera, camera=space.camera, perspective=region.view_perspective,
                matrix=region.view_matrix.copy(),
                location=region.view_location.copy(), rotation=region.view_rotation.copy(),
                distance=region.view_distance, camera_zoom=region.view_camera_zoom,
                camera_offset=tuple(region.view_camera_offset), clip=(space.clip_start, space.clip_end),
                shading=(shading.type, shading.color_type, shading.use_scene_world, shading.use_scene_lights),
                overlays=space.overlay.show_overlays, region_ui=space.show_region_ui)


def _copy_view(saved, space):
    """Apply a 3D view snapshot to its owner or the split's simulated view."""
    region, shading = space.region_3d, space.shading
    space.use_local_camera = saved['local']
    space.camera = saved['camera']
    region.view_matrix = saved['matrix']
    region.view_location, region.view_rotation = saved['location'], saved['rotation']
    region.view_distance = saved['distance']
    region.view_perspective = saved['perspective']
    region.view_camera_zoom, region.view_camera_offset = saved['camera_zoom'], saved['camera_offset']
    space.clip_start, space.clip_end = saved['clip']
    shading.type, shading.color_type, shading.use_scene_world, shading.use_scene_lights = saved['shading']
    space.overlay.show_overlays = saved['overlays']
    space.show_region_ui = saved['region_ui']


def _prepare_stream_area(area, marker):
    area.ui_type = 'IMAGE_EDITOR'
    space = area.spaces.active
    space.image = marker
    space.show_region_header = True
    space.show_region_toolbar = False
    space.show_region_ui = False
    area.tag_redraw()
    return space.as_pointer()


def _record_layout(screen, area, partner, marker):
    areas = list(screen.areas)
    side = int(area.x > partner.x) - int(area.x < partner.x)
    screen[_LAYOUT_KEY] = dict(token=marker[_LAYOUT_KEY], screen=screen.name, area=areas.index(area),
                               partner=areas.index(partner), count=len(areas), side=side)


def _needs_tick():
    return bool(_SESSION or _closing() or any(_LAYOUT_KEY in screen for screen in bpy.data.screens))


def _ensure_tick(interval=_RESTORE_S):
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=interval)


def start(context, url, *, turbine='T1', size='1280x720'):
    """Split the calling 3D view and play ``url`` on the right half."""
    global _HANDLE
    from .manual_down import ffmpeg_executable
    from . import manual_control
    url = live_stream.check_url(url)
    label = f'{turbine} field  {live_stream.display_url(url)}'
    dimensions = live_stream.SIZES.get(size)
    if dimensions is None:
        raise ValueError('现场视频解码尺寸无效')
    ffmpeg = ffmpeg_executable(context)
    if not ffmpeg:
        raise ValueError('播放现场视频需要 FFmpeg；请在扩展偏好设置“相机视频输出”中选择 ffmpeg.exe')
    area, window = context.area, context.window
    if area is None or area.type != 'VIEW_3D' or window is None:
        raise ValueError('请在三维视图中点击')
    args = live_stream.command(ffmpeg, url, dimensions)
    if _SESSION:
        raise RuntimeError('现场视频正在播放，请先关闭后再开始')
    if _closing():
        raise RuntimeError('上一路现场视频正在停止，请稍后重试')
    _restore_ready_layouts()
    space = area.spaces.active
    saved_view = _view_state(space)
    partner = area.as_pointer()
    screen = window.screen
    before = {item.as_pointer() for item in screen.areas}
    try:
        if manual_control.is_manual(context.scene):
            with context.temp_override(window=window, screen=screen, area=area,
                                       region=context.region):
                result = bpy.ops.wfrl.manual_down_view(turbine=turbine, side='CENTER')
            if 'FINISHED' not in result:
                raise ValueError(f'无法切换到 {turbine} Down 相机')
        view = _view_state(space)
        try:
            with context.temp_override(window=window, screen=screen, area=area):
                result = bpy.ops.screen.area_split(direction='VERTICAL', factor=.5)
        finally:
            added = [item for item in screen.areas if item.as_pointer() not in before]
            if len(added) == 1:
                stream_area = added[0]
                _SESSION.update(stream=None, origin=_origin(window), area=stream_area.as_pointer(),
                                editor=None, marker=None, partner=partner, partner_space=space.as_pointer(),
                                view=view, saved_view=saved_view, shown=0, texture=None, arranged=False,
                                turbine=turbine, label=label)
        if len(added) != 1:
            raise ValueError('无法分割当前视图')
        if 'FINISHED' not in result:
            raise ValueError('无法分割当前视图')
        marker = bpy.data.images.new('.WFRL Live Split', width=1, height=1, alpha=False)
        _SESSION['marker'] = marker
        marker[_LAYOUT_KEY] = uuid4().hex
        _SESSION['editor'] = _prepare_stream_area(stream_area, marker)
        _record_layout(screen, stream_area, area, marker)
        _SESSION['stream'] = live_stream.LiveStream(ffmpeg, url, dimensions, args=args)
        if _HANDLE is None:
            _HANDLE = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
        _ensure_tick(_TICK_S)
    except Exception:
        try:
            stop()
        finally:
            _copy_view(saved_view, space)
            area.tag_redraw()
        raise


def stop(*, close_area=True):
    """Detach the viewer and request asynchronous process cleanup."""
    global _HANDLE
    session_data = dict(_SESSION)
    _SESSION.clear()
    stream = session_data.get('stream')
    try:
        if stream is not None:
            _CLOSING.update(stream=stream, turbine=session_data['turbine'])
            stream.stop()
    finally:
        try:
            if _HANDLE is not None:
                handle, _HANDLE = _HANDLE, None
                bpy.types.SpaceImageEditor.draw_handler_remove(handle, 'WINDOW')
            if close_area and session_data:
                _restore_current_layout(session_data)
        finally:
            if close_area and _needs_tick():
                _ensure_tick()
            elif bpy.app.timers.is_registered(_tick):
                bpy.app.timers.unregister(_tick)


def _restore_current_layout(layout):
    origin = layout['origin']
    window = _window(origin)
    if window is None or _origin(window) != origin:
        return
    screen = window.screen
    area = _find(window, layout['area'])
    try:
        if area is not None and (layout['editor'] is None or _owns_editor(area, layout)):
            _restore_layout(window, area, _partner(window, layout), layout['marker'], unarranged=not layout['arranged'])
    finally:
        _forget_layout(screen, layout['marker'])


def _restore_ready_layouts():
    wm = bpy.context.window_manager
    if wm is not None:
        for window in wm.windows:
            if _SESSION and _origin(window) == _SESSION['origin']:
                continue
            record = window.screen.get(_LAYOUT_KEY)
            if record is not None:
                _restore_saved_layout(window, record)


def _restore_saved_layout(window, record):
    screen = window.screen
    marker = None
    try:
        area, marker = _saved_editor(screen, record)
        if area is None:
            return
        areas = list(screen.areas)
        partner = None
        partner_index = record['partner']
        if len(areas) == record['count'] and areas.index(area) == record['area'] and 0 <= partner_index < len(areas):
            candidate = areas[partner_index]
            side = int(area.x > candidate.x) - int(area.x < candidate.x)
            if candidate.type == 'VIEW_3D' and record['side'] in (0, side):
                partner = candidate
        _restore_layout(window, area, partner, marker)
    except (KeyError, TypeError, ValueError, ReferenceError):
        pass
    finally:
        _forget_layout(screen, marker)


def _saved_editor(screen, record):
    token = record['token']
    if record['screen'] != screen.name or not isinstance(token, str) or len(token) != 32:
        return None, None
    matches = [image for image in bpy.data.images if image.get(_LAYOUT_KEY) == token and _blank_marker(image)]
    if len(matches) == 1:
        marker = matches[0]
        spaces = _marker_spaces(marker)
        if len(spaces) == 1 and spaces[0][0] == screen and not _marker_has_data_users(marker):
            for area in screen.areas:
                space = _image_editor(area)
                if space is not None and space == spaces[0][1]:
                    return area, marker
        return None, marker
    if not matches and (retained := _KEPT_EDITORS.get(screen.as_pointer())) is not None:
        for area in screen.areas:
            space = _image_editor(area)
            if (space is not None and space.image is None
                    and retained == (token, area.as_pointer(), space.as_pointer())):
                return area, None
    return None, None


def _marker_spaces(marker):
    # Screen editor references are independent of the Image ID user count.
    return [(screen, space) for screen in bpy.data.screens for area in screen.areas for space in area.spaces
            if space.type == 'IMAGE_EDITOR' and space.image == marker]


def _marker_has_data_users(marker):
    return marker.use_fake_user or bool(bpy.data.user_map(subset=[marker]).get(marker))


def _forget_layout(screen, marker):
    _KEPT_EDITORS.pop(screen.as_pointer(), None)
    if _LAYOUT_KEY in screen:
        del screen[_LAYOUT_KEY]
    try:
        if not _blank_marker(marker) or _marker_has_data_users(marker):
            return
        spaces = _marker_spaces(marker)
        if len(spaces) > 1 or (spaces and spaces[0][0] != screen):
            return
        if spaces:
            spaces[0][1].image = None
        bpy.data.images.remove(marker)
    except ReferenceError:
        pass


def _restore_layout(window, area, partner, marker, *, unarranged=False):
    pointer = area.as_pointer()
    space = _image_editor(area)
    if space is not None and space.image == marker:
        space.image = None
    if partner is not None:
        _merge_split(window, area, partner, unarranged=unarranged)
    area = _find(window, pointer)
    if area is not None:
        area.type = 'VIEW_3D'


def _merge_split(window, area, partner, *, unarranged):
    left, right = sorted((area, partner), key=lambda item: item.x)
    gap = right.x - left.x - left.width
    tolerance = 8 * max(1, bpy.context.preferences.system.ui_scale)
    try:
        if (left.width > 1 and right.width > 1 and left.y == right.y and left.height == right.height
                and 0 <= gap <= tolerance):
            source = (partner.x + partner.width // 2, partner.y + partner.height // 2)
            target = (area.x + area.width // 2, area.y + area.height // 2)
            with bpy.context.temp_override(window=window, screen=window.screen, area=partner):
                bpy.ops.screen.area_join(source_xy=source, target_xy=target)
        elif unarranged and gap < 0:
            # Immediately after splitting, the viewport rectangles can still overlap.
            region = next((item for item in area.regions if item.type == 'WINDOW'), None)
            with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
                bpy.ops.screen.area_close()
    except (RuntimeError, TypeError, ValueError, ReferenceError):
        pass


def _draw_header(self, context):
    if _owns_context(context):
        self.layout.separator_spacer()
        self.layout.operator('wfrl.live_stream_stop', text='关闭现场视频', icon='X')


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    # Keeping the UI preserves screen and editor addresses while image IDs can be remapped away.
    kept_editors = {}
    for screen in bpy.data.screens:
        record = screen.get(_LAYOUT_KEY)
        if record is None:
            continue
        try:
            area, _marker = _saved_editor(screen, record)
            if area is not None:
                kept_editors[screen.as_pointer()] = (record['token'], area.as_pointer(),
                                                     area.spaces.active.as_pointer())
        except (KeyError, TypeError, ValueError, ReferenceError):
            continue
    _KEPT_EDITORS.clear()
    _KEPT_EDITORS.update(kept_editors)
    stop(close_area=False)


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    if _KEPT_EDITORS:
        screens = {screen.as_pointer() for screen in bpy.data.screens}
        for pointer in _KEPT_EDITORS.keys() - screens:
            del _KEPT_EDITORS[pointer]
    _ensure_tick()


_LOAD_HANDLERS = (('load_pre', _on_load_pre), ('load_post', _on_load_post), ('load_post_fail', _on_load_post))
_STATE_TEXT = {'CONNECTING': '正在连接', 'LIVE': '正在播放', 'RECONNECTING': '重连中',
               'STOPPING': '正在停止', 'ERROR': '错误', 'STOPPED': '已停止'}


def status():
    """(icon, text) lines for the panel."""
    current = session()
    if current is None:
        return []
    stream = current['stream']
    state, message = stream.status()
    lines = [('PLAY' if state == 'LIVE' else 'TIME',
              f'{current["turbine"]} 现场 · {_STATE_TEXT.get(state, state)}'
              + (f' · {stream.fps:.1f} fps' if state == 'LIVE' else ''))]
    if state != 'LIVE':
        lines.append(('ERROR' if state == 'ERROR' else 'INFO', message[:120]))
    return lines


class WFRL_OT_LiveStreamStart(bpy.types.Operator):
    bl_idname = 'wfrl.live_stream_start'
    bl_label = '分屏播放现场视频'
    bl_description = '左侧显示所选风机 Down 仿真相机，右侧播放现场相机视频流（需要 FFmpeg）'

    turbine: bpy.props.EnumProperty(items=(('T1', 'T1', ''), ('T2', 'T2', ''), ('T3', 'T3', '')),
                                    options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if context.area is None or context.area.type != 'VIEW_3D':
            return False
        if session() is not None:
            # A live or still-stopping stream keeps the button unavailable; start() refuses as well.
            cls.poll_message_set('现场视频正在播放，请先关闭' if _SESSION else '上一路现场视频正在停止，请稍候')
            return False
        return True

    def execute(self, context):
        wm = context.window_manager
        try:
            start(context, wm.wfrl_live_url, turbine=self.turbine, size=wm.wfrl_live_size)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({'ERROR'}, live_stream.scrub_text(str(exc), wm.wfrl_live_url))
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
    for name, callback in _LOAD_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)
    bpy.types.IMAGE_HT_header.remove(_draw_header)
    bpy.types.IMAGE_HT_header.append(_draw_header)
    _ensure_tick()


def unregister():
    stop(close_area=False)
    bpy.types.IMAGE_HT_header.remove(_draw_header)
    for name, callback in _LOAD_HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    for name in ('wfrl_live_url', 'wfrl_live_size'):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)
    for cls in reversed(CLASSES):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
