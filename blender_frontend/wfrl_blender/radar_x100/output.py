"""Scene-bound independent X100 output canvas consuming the existing latest frame."""
import bpy
import numpy as np
from . import display
from .core import analysis, schema

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
        self.roi = ((-100.,-100.,-150.),(100.,100.,50.))
        self.distance_limit = 10.
        self.show_reference = False
        self.original_indices = np.empty(0,dtype=np.int64)
        self.reference_lines = []
        self.color_scale = scene.wfrl_x100_color_scale
        self.zoom, self.pan, self.bounds = 1., (0., 0.), None
        self.drag = None
        self.buttons = []
        self.data_key = self.batch_key = self.source_key = None
        self.xy, self.colors = np.empty((0, 2)), np.empty((0, 4))
        self.limits, self.unknown, self.point_batch = None, 0, None
        self.frame = None
        self.scale = 1.
        self.factor = 1.
        self.saturated = 0
        self.redraw_signature = None
        self.draw_count = 0
        self.error = ''
        self.auxiliary = display.CanvasCache()
        self.uniform_shader = None
        self.stroke_shader = None

    def release_gpu(self):
        self.auxiliary.clear()
        self.uniform_shader = None
        self.stroke_shader = None
        self.point_batch, self.batch_key = None, None

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

    @property
    def effective_color(self):
        return 'DISTANCE' if self.view == 'NACELLE' else self.color

    def cycle_color(self):
        self.color = display.COLORS[(display.COLORS.index(self.color) + 1) % len(display.COLORS)]

    def consume(self):
        from .runtime import session_for
        session = session_for(self.scene)
        frame = session.frame if session and session.display_valid else None
        if frame is not None and frame.spatial['frame'] != display.SENSOR_AXES and self.view == 'SENSOR':
            self.view = 'FRONT'
        # Keep a strong frame reference so object-id reuse cannot revive a stale GPU batch.
        velocity_limit = self.scene.wfrl_x100_velocity_limit
        analysis_identity = session.analysis_key if session and self.view == 'NACELLE' else None
        key = (session.generation if session else None, frame.identity if frame else None,
            frame.time.clock_domain if frame else None, frame.time.source_time_s if frame else None,
            session.frame_index if session else None, id(frame), self.color, self.view,
            self.color_scale, velocity_limit, analysis_identity, self.distance_limit)
        if key != self.data_key:
            self.frame = frame
            source = (frame.sensor_id, frame.session_id, frame.stream, frame.spatial['frame']) if frame else None
            if source is not None and source != self.source_key:
                self.bounds, self.zoom, self.pan = None, 1., (0., 0.)
                if frame.spatial['frame'] != display.SENSOR_AXES:
                    self.view = 'FRONT'
                self.source_key = source
            if frame is not None:
                self.colors, self.limits, self.unknown = display.point_style(frame, self.color,
                    color_scale=self.color_scale, velocity_limit=velocity_limit)
                self.original_indices = np.flatnonzero(frame.finite_mask)
                if self.view == 'NACELLE':
                    result = session.analysis_result
                    if result is None:
                        self.xy, self.colors = np.empty((0,2)), np.empty((0,4))
                        self.limits = None
                        self.original_indices = np.empty(0,dtype=np.int64)
                    else:
                        self.xy = result['points'][frame.finite_mask][:,(0,2)]
                        colors, self.saturated = analysis.distance_colors(result['distance'], self.distance_limit)
                        self.colors = colors[frame.finite_mask]
                        self.limits, self.unknown = (0.,self.distance_limit), int(np.isnan(result['distance']).sum())
                else:
                    self.xy, keep = display.project(frame.xyz[frame.finite_mask], self.view)
                    self.colors = self.colors[keep]
                    self.original_indices = self.original_indices[keep]
                if self.bounds is None:
                    extent = self.xy
                    if self.view == 'NACELLE' and session.context is not None:
                        tower = analysis.local(session.context.vertices,session.context.world_from_analysis)[:,(0,2)]
                        extent = np.concatenate((extent,tower))
                    if len(extent):
                        self.bounds = display.fit_bounds(extent,self.view)
            else:
                self.original_indices = np.empty(0,dtype=np.int64)
                self.xy, self.colors = np.empty((0, 2)), np.empty((0, 4))
                self.limits, self.unknown = None, 0
            self.data_key = key
            self.batch_key, self.point_batch = None, None
        return session, frame

    def draw(self):
        import blf
        import gpu
        from gpu_extras.batch import batch_for_shader
        region = bpy.context.region
        layout = display.canvas_layout(region.width, region.height,
            bpy.context.preferences.system.ui_scale, velocity=self.effective_color == 'VELOCITY')
        self.scale = layout['scale']
        s = self.scale
        width, height = region.width, region.height
        context = bpy.context
        drawing_context = (context.window.as_pointer(), context.window.screen.as_pointer(),
                           context.area.as_pointer(), region.as_pointer())
        if self.auxiliary.bind(drawing_context):
            self.uniform_shader = None
            self.stroke_shader = None
            self.point_batch, self.batch_key = None, None
        if self.uniform_shader is None:
            self.uniform_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        if self.stroke_shader is None:
            self.stroke_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        uniform = self.uniform_shader

        def shape(points, color, primitive='LINES', shader=None):
            return batch_for_shader(shader or uniform, primitive,
                                    {'pos': np.ascontiguousarray(points, dtype=np.float32)}), color

        def box(x, y, w, h, color):
            return shape(((x, y), (x+w, y), (x+w, y+h), (x, y+h)), color, 'TRI_FAN')

        def draw_shapes(shapes, shader=None):
            shader = shader or uniform
            shader.bind()
            for batch, color in shapes:
                shader.uniform_float('color', color)
                batch.draw(shader)

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
        def chrome():
            return [box(0, 0, width, height, (.018, .026, .04, 1)),
                *(box(x, y, w, h, (.09, .3, .43, 1) if action == self.view else (.065, .095, .135, 1))
                  for action, _, (x, y, w, h) in layout['buttons'])]
        self._draw_background(width, height, s, layout, chrome, draw_shapes, text)
        sensor_axes = self._draw_frame_information(session, frame, layout, width, s, text)
        rect = layout['rectangle']
        left, bottom, pw, ph = rect
        bounds = self.bounds or display.fit_bounds([], self.view)
        def grid():
            transform, shapes, labels = display.grid_geometry(self.view, bounds, rect, self.zoom, self.pan, s)
            return transform, [shape(points, color) for points, color in shapes if len(points)], labels
        (factor, tx, ty), shapes, labels = self.auxiliary.get('grid',
            (tuple(rect), s, self.view, bounds, self.zoom, self.pan), grid)
        self._draw_grid_axes(shapes, labels, factor, sensor_axes, left, bottom, pw, ph, s, draw_shapes, text)
        self._prepare_point_batch(rect, s, bounds, factor, tx, ty, left, bottom, pw, ph, gpu, batch_for_shader)
        blade_shapes = []
        if self.view == 'NACELLE' and session and session.context is not None:
            def reference_shapes():
                return self._reference_shapes(session, factor, tx, ty, s, shape)
            blade_shapes = self._draw_reference(
                gpu, left, bottom, pw, ph, session, rect, bounds, reference_shapes, draw_shapes)
        self._draw_points_and_estimates(blade_shapes, gpu, left, bottom, pw, ph, s, draw_shapes)
        self._draw_canvas_labels(frame, left, bottom, ph, width, s, text)
        def legend():
            return self._legend_shapes(s, box)
        draw_shapes(self.auxiliary.get('legend', (s, self.view, self.color, self.color_scale, self.limits), legend))
        self._draw_status_text(session, frame, layout, width, s, text)
        self.draw_count += 1

    def _draw_background(self, width, height, s, layout, chrome, draw_shapes, text):
        draw_shapes(self.auxiliary.get('chrome', (width, height, s, self.view, self.effective_color == 'VELOCITY'), chrome))
        text('X100  雷达点云输出', 16*s, height-28*s, 19, (.9, .95, 1, 1), width-32*s)
        status = self.scene.get('wfrl_x100_error') or self.scene.get('wfrl_x100_status', '')
        text('当前帧 · 无历史累积 · ' + status, 16*s, height-50*s, 12, max_width=width-32*s)
        self.buttons = []
        for action, label, (x, y, w, h) in layout['buttons']:
            if action == 'SCALE':
                label = '固定色标' if self.color_scale == 'FIXED' else '逐帧色标'
            text(label, x+10*s, y+7*s, 12)
            self.buttons.append((action, (x, y, w, h)))

    def _draw_frame_information(self, session, frame, layout, width, s, text):
        info_top = layout['info_top']
        if frame is not None:
            text(f'{display.source_label(frame)}  /  {frame.stream}  /  #{frame.sequence}  ·  {frame.validity}',
                 16*s, info_top, 12, max_width=width-32*s)
            stamp = f'{frame.time.source_time_s:.3f} s' if frame.time.source_time_s is not None else '未知'
            text(f'源时间 {stamp}  ·  {len(frame.points)} 点  ·  可绘制 {len(self.xy)} 点  ·  {frame.time.quality}',
                 16*s, info_top-20*s, 12, max_width=width-32*s)
        else:
            status = self.scene.get('wfrl_x100_error') or self.scene.get('wfrl_x100_status', 'X100 未启动')
            if session is not None and session.frame is not None:
                status = session.frame.validity + ' · ' + status
            text('暂无有效点云 · ' + status, 16*s, info_top, 13, (1, .68, .35, 1), width-32*s)
        sensor_axes = frame is not None and frame.spatial['frame'] == display.SENSOR_AXES
        coords = '+X 前 / +Y 左 / +Z 上' if sensor_axes else '原始坐标轴 · 未确认方向'
        if self.view == 'NACELLE':
            result = session.analysis_result if session else None
            detail = (session.analysis_error if session else '无会话') if result is None else f"ROI {result['selected']} / 有效 {result['valid']} / 饱和 {self.saturated}"
            coords = '局部Z非重力 · '+detail+' · R 参考'+('开' if self.show_reference else '关')
        text(coords + (' · 传感器角度投影' if self.view == 'SENSOR' else ' · 正交投影 (m)'),
             16*s, info_top-40*s, 11, max_width=width-32*s)
        return sensor_axes

    def _draw_grid_axes(self, shapes, labels, factor, sensor_axes, left, bottom, pw, ph, s, draw_shapes, text):
        draw_shapes(shapes)
        for label, x, y in labels:
            text(label, x, y, 10)
        self.factor = factor
        horizontal = '方位角 (°，+Y 左)' if self.view == 'SENSOR' else ('X (m)' if self.view == 'SIDE' else 'Y (m，左为正)' if sensor_axes else 'Y (m)')
        vertical = '俯仰角 (°)' if self.view == 'SENSOR' else ('X (m)' if self.view == 'TOP' else 'Z (m)')
        if self.view == 'NACELLE':
            horizontal, vertical = '机舱局部 X (m)', '机舱局部 Z (m)'
        text(horizontal, left+pw/2-65*s, bottom-43*s, 11)
        text(vertical, left, bottom+ph+10*s, 11)

    def _prepare_point_batch(self, rect, s, bounds, factor, tx, ty, left, bottom, pw, ph, gpu, batch_for_shader):
        cache_key = (self.data_key, tuple(rect), s, self.zoom, self.pan, bounds)
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

    def _reference_shapes(self, session, factor, tx, ty, s, shape):
        context = session.context
        vertices = analysis.local(context.vertices,context.world_from_analysis)[:,(0,2)]
        faces = context.triangles
        edges = np.concatenate((faces[:,(0,1)],faces[:,(1,2)],faces[:,(2,0)]))
        shapes=[shape(vertices[edges].reshape(-1,2)*factor+(tx,ty),(.3,.65,.55,.3))]
        sensor = analysis.local(analysis.transform(np.array([[0,0,0],[15,0,0]]),context.world_from_sensor),context.world_from_analysis)
        shapes.append(shape(sensor[:,(0,2)]*factor+(tx,ty),(1.,.7,.15,1)))
        rays=[]
        for az in (-schema.PROFILE['azimuth_deg'],schema.PROFILE['azimuth_deg']):
            for el in (-schema.PROFILE['elevation_deg'],schema.PROFILE['elevation_deg']):
                a,e=np.radians([az,el])
                length=schema.DISPLAY_DEFAULTS['fov_length_m']
                end=[length*np.cos(e)*np.cos(a),length*np.cos(e)*np.sin(a),length*np.sin(e)]
                pair=analysis.local(analysis.transform(np.array([[0,0,0],end]),context.world_from_sensor),context.world_from_analysis)
                rays.extend(pair[:,(0,2)]*factor+(tx,ty))
        shapes.append(shape(rays,(.15,.65,1,.5)))
        if self.show_reference:
            for curve in session.reference_curves:
                xy=analysis.local(curve,context.world_from_analysis)[:,(0,2)]*factor+(tx,ty)
                shapes.append(shape(xy,(.85,.6,.9,.8),'LINE_STRIP'))
        estimates = []
        if session.blade_result:
            colors = ((1., .45, .2, 1.), (.2, .8, 1., 1.), (.95, .85, .2, 1.))
            for row in session.blade_result['blades']:
                color = colors[row['blade'] - 1]
                if row['curve_world']:
                    xy = analysis.local(row['curve_world'], context.world_from_analysis)[:, (0, 2)] * factor + (tx, ty)
                    estimates.append(shape(xy, color, 'LINE_STRIP', self.stroke_shader))
                if row['tip_world']:
                    xy = analysis.local([row['tip_world']], context.world_from_analysis)[0, (0, 2)] * factor + (tx, ty)
                    cross = xy + np.array([[-7, 0], [7, 0], [0, -7], [0, 7]]) * s
                    estimates.append(shape(cross, color, shader=self.stroke_shader))
        return shapes, estimates

    def _draw_reference(self, gpu, left, bottom, pw, ph, session, rect, bounds, reference_shapes, draw_shapes):
        gpu.state.scissor_set(int(left),int(bottom),int(pw),int(ph))
        gpu.state.scissor_test_set(True)
        try:
            reference, blade_shapes = self.auxiliary.get('analysis',
                (session.context.identity,tuple(rect),self.zoom,self.pan,bounds,
                 self.show_reference,session.reference_key,session.blade_key,
                 bool(session.blade_result)),reference_shapes)
            draw_shapes(reference)
        finally:
            gpu.state.scissor_test_set(False)
        return blade_shapes

    def _draw_points_and_estimates(self, blade_shapes, gpu, left, bottom, pw, ph, s, draw_shapes):
        if self.point_batch:
            shader, batch = self.point_batch
            shader.bind()
            shader.uniform_float('size', 4.5*s)
            batch.draw(shader)
        if blade_shapes:
            gpu.state.scissor_set(int(left), int(bottom), int(pw), int(ph))
            gpu.state.scissor_test_set(True)
            try:
                self.stroke_shader.bind()
                self.stroke_shader.uniform_float('viewportSize', gpu.state.viewport_get()[2:])
                self.stroke_shader.uniform_float('lineWidth', max(2., 2.5*s))
                draw_shapes(blade_shapes, self.stroke_shader)
            finally:
                gpu.state.scissor_test_set(False)

    def _draw_canvas_labels(self, frame, left, bottom, ph, width, s, text):
        if frame is None or not len(self.xy):
            message = '有效空帧 · 当前检测点数为 0' if frame is not None and not len(frame.points) else '当前视图没有可绘制点' if frame is not None else '等待当前雷达帧'
            text(message, left+20*s, bottom+ph/2, 15, (.95, .7, .4, 1))
        label = '仿真塔筒侧壁距离 (m)' if self.view == 'NACELLE' else display.COLOR_LABELS[self.color]
        if self.view != 'NACELLE' and self.color == 'HEIGHT' and frame is not None:
            label = '世界高度 (m)' if frame.spatial.get('world_from_sensor') is not None and frame.spatial.get('alignment') in {'SIMULATION', 'STATIC_CALIBRATION', 'VERIFIED'} else '原始 Z (m)'
        if self.limits is None:
            text(label + ' · 未知（灰色）', 20*s, 44*s, 12)
        else:
            lo, hi = self.limits
            scale_label = ('固定研究色标 · 非安全阈值' if self.view == 'NACELLE' else '固定分类 0 / 1' if self.color == 'MS' else
                '固定对称 · 超限用端点色' if self.color == 'VELOCITY' and self.color_scale == 'FIXED' else '当前帧色标')
            text(f'{label}  {lo:.3g} ～ {hi:.3g}  ·  {scale_label}  ·  未知 {self.unknown}',
                 16*s, 55*s, 12, max_width=width-32*s)

    def _legend_shapes(self, s, box):
        shapes = []
        if self.limits is not None:
            for i in range(40):
                v = float(i/39 >= .5) if self.effective_color == 'MS' else i/39
                color = tuple(analysis.distance_colors([v*self.distance_limit],self.distance_limit)[0][0]) if self.view == 'NACELLE' else (v,1-abs(2*v-1),1-v,1)
                shapes.append(box(16*s+i*4*s, 36*s, 4*s, 8*s, color))
        return shapes

    def _draw_status_text(self, session, frame, layout, width, s, text):
        hints = '滚轮缩放 · 中键平移 · F 复位 · Esc 关闭' if layout['compact'] else '滚轮缩放 · 中键平移 · 1–5 切视图 · F 复位 · C 着色 · V 色标 · Esc 关闭'
        text(hints, 16*s, 16*s, 11, max_width=width-32*s)
        if self.view == 'NACELLE' and session and session.blade_result:
            text('叶片拟合：橙 B1 / 蓝 B2 / 黄 B3 · 十字为模型叶尖',
                 16*s, 76*s, 11, max_width=width-32*s)
        if frame is not None and self.effective_color == 'VELOCITY':
            sign = frame.provenance.get('velocity_sign', 'UNKNOWN')
            text('速度正值远离传感器' if sign == 'positive receding' else '速度符号沿用来源（未确认）',
                 16*s, 76*s, 11, max_width=width-32*s)


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
        item.release_gpu()
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
    item.release_gpu()
    item.frame = None
    item.xy, item.colors = np.empty((0, 2)), np.empty((0, 4))
    item.data_key = item.source_key = None
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
            from .runtime import session_for
            session = session_for(item.scene)
            if session and item.view == 'NACELLE':
                item.roi = (tuple(item.scene.wfrl_x100_analysis_roi_min),tuple(item.scene.wfrl_x100_analysis_roi_max))
                item.distance_limit = item.scene.wfrl_x100_analysis_limit
                session.derive_analysis(item.roi)
                session.prepare_reference(item.show_reference)
            session_signature = None if session is None else (
                session.generation, session.key, id(session.frame), session.frame_index,
                session.display_valid, session.analysis_key, session.analysis_error,
                session.reference_key, session.reference_error, session.render_error,
                session.blade_key, session.blade_error, bool(session.blade_result))
            signature = (session_signature, item.error,
                item.scene.get('wfrl_x100_status',''),item.scene.get('wfrl_x100_error',''),
                item.scene.get('wfrl_x100_record_state',''),item.scene.get('wfrl_x100_record_error',''),
                item.scene.get('wfrl_x100_record_frames',0),item.view,item.color,item.color_scale,
                item.distance_limit,item.show_reference,item.zoom,item.pan,area.width,area.height,
                display.style_key(item.scene))
            if signature != item.redraw_signature:
                item.redraw_signature = signature
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
                    elif action == 'SCALE':
                        item.color_scale = 'FRAME' if item.color_scale == 'FIXED' else 'FIXED'
                    else:
                        item.reset(action if action in display.VIEWS else None)
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
        if event.value == 'PRESS':
            views = dict(ONE='SENSOR', TWO='FRONT', THREE='TOP', FOUR='SIDE', FIVE='NACELLE')
            if event.type in views:
                item.reset(views[event.type])
            elif event.type == 'R':
                item.show_reference = not item.show_reference
            elif event.type in {'F', 'HOME'}:
                item.reset()
            elif event.type == 'C':
                item.cycle_color()
            elif event.type == 'V' and item.effective_color == 'VELOCITY':
                item.color_scale = 'FRAME' if item.color_scale == 'FIXED' else 'FIXED'
            elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                item.zoom = max(.1, min(100., item.zoom*(1.2 if event.type == 'WHEELUPMOUSE' else 1/1.2)))
            elif event.type == 'MIDDLEMOUSE':
                item.drag = (mx, my, item.pan)
        if event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE':
            item.drag = None
        if event.type == 'MOUSEMOVE' and item.drag:
            x, y, pan = item.drag
            item.pan = (pan[0]-(mx-x)/item.factor, pan[1]-(my-y)/item.factor)
        area.tag_redraw()
        # This canvas owns navigation; image-editor shortcuts cannot alter the source scene.
        return {'RUNNING_MODAL'}
