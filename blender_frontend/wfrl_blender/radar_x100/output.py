"""Scene-bound independent X100 output canvas consuming the existing latest frame."""
import bpy
import numpy as np
from . import display

_WINDOWS = {}
_HANDLE = None
_SAVED_CANVAS = 'wfrl_x100_output_canvas'


def for_scene(scene):
    return next((item for item in _WINDOWS.values() if item.scene == scene), None)


class OutputWindow:
    def __init__(self, scene, window, area):
        self.scene = scene
        self.window_id, self.area_id = window.as_pointer(), area.as_pointer()
        self.view, self.color = 'SENSOR', scene.wfrl_x100_color
        self.zoom, self.pan, self.bounds = 1., (0., 0.), None
        self.drag = None
        self.buttons = []
        self.data_key = self.batch_key = self.source_key = None
        self.xy, self.colors = np.empty((0, 2)), np.empty((0, 4))
        self.limits, self.unknown, self.point_batch = None, 0, None
        self.frame = None
        self.scale = 1.
        self.draw_count = 0
        self.error = ''

    def alive(self):
        window = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == self.window_id), None)
        if window is None:
            return None, None
        area = next((a for a in window.screen.areas if a.as_pointer() == self.area_id), None)
        return window, area

    def reset(self, view=None):
        if view:
            self.view = view
        self.bounds, self.zoom, self.pan = None, 1., (0., 0.)
        self.data_key = self.batch_key = None

    def cycle_color(self):
        self.color = display.COLORS[(display.COLORS.index(self.color) + 1) % len(display.COLORS)]

    def consume(self):
        from .runtime import session_for
        session = session_for(self.scene)
        frame = session.frame if session and session.display_valid else None
        if frame is not None and frame.spatial['frame'] != display.SENSOR_AXES and self.view == 'SENSOR':
            self.view = 'FRONT'
        # Keep a strong frame reference so object-id reuse cannot revive a stale GPU batch.
        key = (id(frame), self.color, self.view)
        if key != self.data_key:
            self.frame = frame
            source = (frame.sensor_id, frame.session_id, frame.stream, frame.spatial['frame']) if frame else None
            if source is not None and source != self.source_key:
                self.bounds, self.zoom, self.pan = None, 1., (0., 0.)
                if frame.spatial['frame'] != display.SENSOR_AXES:
                    self.view = 'FRONT'
                self.source_key = source
            if frame is not None:
                self.colors, self.limits, self.unknown = display.point_style(frame, self.color)
                self.xy, keep = display.project(frame.xyz[frame.finite_mask], self.view)
                self.colors = self.colors[keep]
                if self.bounds is None and len(self.xy):
                    self.bounds = display.fit_bounds(self.xy, self.view)
            else:
                self.xy, self.colors = np.empty((0, 2)), np.empty((0, 4))
                self.limits, self.unknown = None, 0
            self.data_key, self.batch_key, self.point_batch = (id(frame), self.color, self.view), None, None
        return session, frame

    def draw(self):
        import blf
        import gpu
        from gpu_extras.batch import batch_for_shader
        region = bpy.context.region
        scale = min(bpy.context.preferences.system.ui_scale, region.width / 760, region.height / 460)
        self.scale = max(.4, scale)
        s = self.scale
        width, height = region.width, region.height
        uniform = gpu.shader.from_builtin('UNIFORM_COLOR')

        def shape(points, color, primitive='LINES'):
            uniform.bind()
            uniform.uniform_float('color', color)
            batch_for_shader(uniform, primitive, {'pos': points}).draw(uniform)

        def box(x, y, w, h, color):
            shape(((x, y), (x+w, y), (x+w, y+h), (x, y+h)), color, 'TRI_FAN')

        def text(value, x, y, size=13, color=(.76, .83, .9, 1), max_width=None):
            blf.size(0, size*s)
            value = str(value)
            if max_width:
                while value and blf.dimensions(0, value)[0] > max_width:
                    value = value[:-2] + '…' if len(value) > 2 else ''
            blf.color(0, *color)
            blf.position(0, x, y, 0)
            blf.draw(0, value)

        session, frame = self.consume()
        box(0, 0, width, height, (.018, .026, .04, 1))
        text('X100  雷达点云输出', 20*s, height-30*s, 19, (.9, .95, 1, 1))
        status = self.scene.get('wfrl_x100_error') or self.scene.get('wfrl_x100_status', '')
        text('当前帧 · 无历史累积 · ' + status, 270*s, height-29*s, 12, max_width=width-290*s)
        self.buttons = []
        x, y = 20*s, height-67*s
        actions = [(v, display.VIEW_LABELS[v]) for v in display.VIEWS] + [('RESET', '复位'), ('COLOR', '着色'), ('CLOSE', '关闭窗口')]
        for action, label in actions:
            w = (92 if action == 'CLOSE' else 74)*s
            active = action == self.view
            box(x, y, w, 25*s, (.09, .3, .43, 1) if active else (.065, .095, .135, 1))
            text(label, x+10*s, y+7*s, 12)
            self.buttons.append((action, (x, y, w, 25*s)))
            x += w+6*s
        if frame is not None:
            text(f'{frame.source_kind}  /  {frame.stream}  /  #{frame.sequence}  ·  {frame.validity}',
                 20*s, height-89*s, 12, max_width=width-40*s)
            stamp = f'{frame.time.source_time_s:.3f} s' if frame.time.source_time_s is not None else '未知'
            text(f'源时间 {stamp}  ·  {len(frame.points)} 点  ·  可绘制 {len(self.xy)} 点  ·  {frame.time.quality}',
                 20*s, height-109*s, 12, max_width=width-40*s)
        else:
            status = self.scene.get('wfrl_x100_error') or self.scene.get('wfrl_x100_status', 'X100 未启动')
            if session is not None and session.frame is not None:
                status = session.frame.validity + ' · ' + status
            text('暂无有效点云 · ' + status, 20*s, height-96*s, 13, (1, .68, .35, 1), width-40*s)
        sensor_axes = frame is not None and frame.spatial['frame'] == display.SENSOR_AXES
        coords = '+X 前 / +Y 左 / +Z 上' if sensor_axes else '原始坐标轴 · 未确认方向'
        text(coords + (' · 传感器角度投影' if self.view == 'SENSOR' else ' · 正交投影 (m)'),
             20*s, height-130*s, 11, max_width=width-40*s)
        rect = (76*s, 112*s, max(1, width-112*s), max(1, height-270*s))
        left, bottom, pw, ph = rect
        bounds = self.bounds or display.fit_bounds([], self.view)
        factor, tx, ty = display.fit_canvas(bounds, rect, self.zoom, self.pan)
        self.factor = factor
        xlo, xhi = (left-tx)/factor, (left+pw-tx)/factor
        ylo, yhi = (bottom-ty)/factor, (bottom+ph-ty)/factor
        for tick in display.ticks(xlo, xhi):
            px = tx+tick*factor
            shape(((px, bottom), (px, bottom+ph)), (.09, .14, .2, 1))
            label = -tick if self.view in {'SENSOR', 'FRONT', 'TOP'} else tick
            text(f'{label:g}', px-10*s, bottom-21*s, 10)
        for tick in display.ticks(ylo, yhi):
            py = ty+tick*factor
            shape(((left, py), (left+pw, py)), (.09, .14, .2, 1))
            text(f'{tick:g}', 22*s, py-4*s, 10)
        if left <= tx <= left+pw:
            shape(((tx, bottom), (tx, bottom+ph)), (.2, .34, .43, 1))
        if bottom <= ty <= bottom+ph:
            shape(((left, ty), (left+pw, ty)), (.2, .34, .43, 1))
        shape(((left, bottom), (left+pw, bottom), (left+pw, bottom), (left+pw, bottom+ph),
               (left+pw, bottom+ph), (left, bottom+ph), (left, bottom+ph), (left, bottom)), (.22, .3, .39, 1))
        horizontal = '方位角 (°，+Y 左)' if self.view == 'SENSOR' else ('X (m)' if self.view == 'SIDE' else 'Y (m，左为正)' if sensor_axes else 'Y (m)')
        vertical = '俯仰角 (°)' if self.view == 'SENSOR' else ('X (m)' if self.view == 'TOP' else 'Z (m)')
        text(horizontal, left+pw/2-65*s, bottom-43*s, 11)
        text(vertical, left, bottom+ph+10*s, 11)
        cache_key = (self.data_key, width, height, self.zoom, self.pan, bounds)
        if self.batch_key != cache_key:
            pixels = self.xy*factor + (tx, ty)
            visible = ((pixels[:, 0] >= left+3*s) & (pixels[:, 0] <= left+pw-3*s)
                       & (pixels[:, 1] >= bottom+3*s) & (pixels[:, 1] <= bottom+ph-3*s))
            self.point_batch = None
            if visible.any():
                shader = gpu.shader.from_builtin('POINT_FLAT_COLOR')
                self.point_batch = (shader, batch_for_shader(shader, 'POINTS',
                    {'pos': pixels[visible].astype(np.float32), 'color': self.colors[visible]}))
            self.batch_key = cache_key
        if self.point_batch:
            shader, batch = self.point_batch
            shader.bind()
            shader.uniform_float('size', 4.5*s)
            batch.draw(shader)
        if frame is None or not len(self.xy):
            message = '有效空帧 · 当前检测点数为 0' if frame is not None and not len(frame.points) else '当前视图没有可绘制点' if frame is not None else '等待当前雷达帧'
            text(message, left+20*s, bottom+ph/2, 15, (.95, .7, .4, 1))
        label = display.COLOR_LABELS[self.color]
        if self.color == 'HEIGHT' and frame is not None:
            label = '世界高度 (m)' if frame.spatial.get('world_from_sensor') is not None and frame.spatial.get('alignment') in {'SIMULATION', 'STATIC_CALIBRATION', 'VERIFIED'} else '原始 Z (m)'
        if self.limits is None:
            text(label + ' · 未知（灰色）', 20*s, 44*s, 12)
        else:
            lo, hi = self.limits
            text(f'{label}  {lo:.3g} ～ {hi:.3g}  ·  当前帧色标  ·  未知 {self.unknown}', 20*s, 44*s, 12)
            for i in range(40):
                v = i/39
                box(width-190*s+i*4*s, 43*s, 4*s, 10*s, (v, 1-abs(2*v-1), 1-v, 1))
        text('滚轮缩放 · 中键拖动平移 · 1–4 切视图 · F 复位 · C 着色 · Esc 关闭', 20*s, 20*s, 11, max_width=width-40*s)
        if frame is not None and self.color == 'VELOCITY':
            sign = frame.provenance.get('velocity_sign', 'UNKNOWN')
            text('速度正值远离传感器' if sign == 'positive receding' else '速度符号沿用来源（未确认）',
                 20*s, 65*s, 10)
        self.draw_count += 1


def _draw():
    import gpu
    context = bpy.context
    if context.window is None or context.area is None:
        return
    item = _WINDOWS.get(context.window.as_pointer())
    if item is None or context.area.as_pointer() != item.area_id:
        return
    depth, blend = gpu.state.depth_test_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('ALPHA')
        item.draw()
        item.error = ''
        if item.scene.get('wfrl_x100_output_error'):
            item.scene['wfrl_x100_output_error'] = ''
    except Exception as exc:
        if item.error != str(exc):
            print('X100 output draw failed:', str(exc))
        item.error = str(exc)
        item.scene['wfrl_x100_output_error'] = str(exc)
    finally:
        gpu.state.depth_test_set(depth)
        gpu.state.blend_set(blend)


def close(item, *, close_window=True):
    global _HANDLE
    _WINDOWS.pop(item.window_id, None)
    if not _WINDOWS and _HANDLE is not None:
        bpy.types.SpaceImageEditor.draw_handler_remove(_HANDLE, 'WINDOW')
        _HANDLE = None
    item.point_batch, item.frame = None, None
    window, _ = item.alive()
    if window is not None and close_window:
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.window_close()


def close_all():
    for item in list(_WINDOWS.values()):
        close(item)
    for callback in (tick, discard_saved_windows):
        if bpy.app.timers.is_registered(callback):
            bpy.app.timers.unregister(callback)


def discard_saved_windows():
    """Loaded dedicated canvases have no live observer; preserve other editors."""
    for window in list(bpy.context.window_manager.windows):
        if window.screen.get(_SAVED_CANVAS) and window.as_pointer() not in _WINDOWS:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()


def on_load_post(_unused):
    # Window layouts finish restoring after the file-load handlers return.
    if any(screen.get(_SAVED_CANVAS) for screen in bpy.data.screens):
        if not bpy.app.timers.is_registered(discard_saved_windows):
            bpy.app.timers.register(discard_saved_windows)


def tick():
    alive_scenes = {scene.as_pointer() for scene in bpy.data.scenes}
    for item in list(_WINDOWS.values()):
        window, area = item.alive()
        try:
            valid_scene = item.scene.as_pointer() in alive_scenes
        except ReferenceError:
            valid_scene = False
        if window is None or area is None or area.type != 'IMAGE_EDITOR' or not valid_scene or window.scene != item.scene:
            close(item)
        else:
            area.tag_redraw()
    return 1/30 if _WINDOWS else None


class WFRL_OT_X100Output(bpy.types.Operator):
    bl_idname = 'wfrl.x100_output'
    bl_label = '打开雷达点云窗口'
    bl_description = '独立显示当前 X100 帧；不改变雷达安装、运动或相机'

    @classmethod
    def poll(cls, context):
        return context.window is not None and not bpy.app.background

    def execute(self, context):
        global _HANDLE
        existing = for_scene(context.scene)
        if existing:
            window, area = existing.alive()
            if window and area:
                area.tag_redraw()
                self.report({'INFO'}, '此场景的雷达点云窗口已经打开')
                return {'FINISHED'}
            close(existing)
        scene = context.scene
        before = {w.as_pointer() for w in context.window_manager.windows}
        item, window = None, None
        try:
            if bpy.ops.wm.window_new() != {'FINISHED'}:
                raise RuntimeError('无法创建雷达点云窗口')
            window = next(w for w in context.window_manager.windows if w.as_pointer() not in before)
            window.scene = scene
            area = max(window.screen.areas, key=lambda a: a.width*a.height)
            with context.temp_override(window=window, area=area):
                if bpy.ops.screen.screen_full_area(use_hide_panels=True) != {'FINISHED'}:
                    raise RuntimeError('无法建立独立点云画布')
            area = max(window.screen.areas, key=lambda a: a.width*a.height)
            area.type = 'IMAGE_EDITOR'
            window.screen[_SAVED_CANVAS] = True
            space = area.spaces.active
            space.image = None
            space.show_region_header = False
            space.show_region_toolbar = False
            space.show_region_ui = False
            item = OutputWindow(scene, window, area)
            _WINDOWS[item.window_id] = item
            if _HANDLE is None:
                _HANDLE = bpy.types.SpaceImageEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')
            if not bpy.app.timers.is_registered(tick):
                bpy.app.timers.register(tick)
            self.item = item
            with context.temp_override(window=window, area=area):
                context.window_manager.modal_handler_add(self)
            area.tag_redraw()
            scene['wfrl_x100_output_error'] = ''
            return {'RUNNING_MODAL'}
        except Exception as exc:
            if item:
                close(item)
            elif window:
                with context.temp_override(window=window):
                    bpy.ops.wm.window_close()
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def modal(self, context, event):
        item = self.item
        if item.window_id not in _WINDOWS:
            return {'CANCELLED'}
        if context.window is None or context.window.as_pointer() != item.window_id:
            return {'PASS_THROUGH'}
        if event.type == 'ESC' and event.value == 'PRESS':
            close(item)
            return {'FINISHED'}
        window, area = item.alive()
        if area is None:
            close(item)
            return {'CANCELLED'}
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is None:
            return {'PASS_THROUGH'}
        mx, my = event.mouse_x-region.x, event.mouse_y-region.y
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            for action, (x, y, w, h) in item.buttons:
                if x <= mx <= x+w and y <= my <= y+h:
                    if action == 'CLOSE':
                        close(item)
                        return {'FINISHED'}
                    if action == 'COLOR':
                        item.cycle_color()
                    else:
                        item.reset(action if action in display.VIEWS else None)
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
        if event.value == 'PRESS':
            views = dict(ONE='SENSOR', TWO='FRONT', THREE='TOP', FOUR='SIDE')
            if event.type in views:
                item.reset(views[event.type])
            elif event.type in {'F', 'HOME'}:
                item.reset()
            elif event.type == 'C':
                item.cycle_color()
            elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                item.zoom = max(.1, min(100., item.zoom*(1.2 if event.type == 'WHEELUPMOUSE' else 1/1.2)))
            elif event.type == 'MIDDLEMOUSE':
                item.drag = (mx, my, item.pan)
        if event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            item.drag = None
        if event.type == 'MOUSEMOVE' and item.drag:
            x, y, pan = item.drag
            item.pan = (pan[0]-(mx-x)/getattr(item, 'factor', 1.), pan[1]-(my-y)/getattr(item, 'factor', 1.))
        area.tag_redraw()
        # This canvas owns navigation; image-editor shortcuts cannot alter the source scene.
        return {'RUNNING_MODAL'}
