"""Main-thread GPU images for single-camera editing and explicit capture.

Textures contain only scene geometry. UI and layout guides are drawn separately.
A group is replaced only after every enabled camera at that state has succeeded.
"""
from contextlib import contextmanager
from dataclasses import asdict
import time
import math

from . import custom_camera_diagnostics as diagnostics

from . import camera_projection as projection, custom_cameras as core

_DRAWING = False
_REVISION = 0
_RENDERERS = set()


def changed(*_args):
    global _REVISION
    if not _DRAWING:
        _REVISION += 1


def render_profile(scene, fast=False):
    return {'shading': 'SOLID' if fast else 'MATERIAL', 'studio_light': 'studio.sl' if fast else 'forest.exr',
            'scene_lights': False, 'scene_world': False, 'studio_rotation': 0.,
            'studio_intensity': 1., 'studio_background_alpha': 1.,
            'studio_background_blur': .5, 'studio_view_rotation': False,
            'color_management': {key: getattr(scene.view_settings, key) for key in
                ('view_transform', 'look', 'exposure', 'gamma', 'use_curve_mapping')},
            'display_device': scene.display_settings.display_device,
            'curve_mapping': ([[list(point.location) for point in curve.points] for curve in scene.view_settings.curve_mapping.curves]
                if scene.view_settings.use_curve_mapping else None),
            'excluded_annotations': ['WFRL.Label.*', 'WFRL.Grid.*', 'WFRL.Inflow.*', 'WFRL.Deflection.*',
                'WFRL.Wake*', '*.TipTrail.*', '*.ClearanceRadar.Beam*', '*.SensorFrustum', 'WFRL.Fixture.T1.LidarRay*'],
            'image_encoding': 'RGBA8 display-transformed once; top-left rows; PNG sRGB'}


@contextmanager
def drawing_settings(space, fast=False):
    """Restore every changed viewport property, including on GPU failure."""
    shader = space.shading
    desired = {'type': 'MATERIAL', 'use_scene_lights': False, 'use_scene_world': False,
               'studio_light': 'forest.exr', 'studiolight_rotate_z': 0.,
               'studiolight_intensity': 1., 'studiolight_background_alpha': 1.,
               'studiolight_background_blur': .5, 'use_studiolight_view_rotation': False}
    if fast:
        desired.update(type='SOLID', studio_light='studio.sl', light='STUDIO', color_type='MATERIAL',
                       show_shadows=False, show_cavity=False, show_specular_highlight=False)
    saved = {key: getattr(shader, key) for key in desired}
    overlays, gizmos = space.overlay.show_overlays, space.show_gizmo
    try:
        for key, value in desired.items():
            setattr(shader, key, value)
        space.overlay.show_overlays, space.show_gizmo = False, False
        yield
    finally:
        for key, value in saved.items():
            setattr(shader, key, value)
        space.overlay.show_overlays, space.show_gizmo = overlays, gizmos


@contextmanager
def without_annotations(scene, view_layer):
    """Exclude only declared display aids, never the nacelle or physical fittings."""
    changed_objects = []
    try:
        for obj in scene.objects:
            name = obj.name
            annotation = (name.startswith(('WFRL.Label.', 'WFRL.Grid.', 'WFRL.Inflow.',
                'WFRL.Deflection.', 'WFRL.Wake', 'WFRL.Fixture.T1.LidarRay'))
                or '.TipTrail.' in name or '.ClearanceRadar.Beam' in name or name.endswith('.SensorFrustum'))
            if annotation and not obj.hide_get(view_layer=view_layer):
                obj.hide_set(True, view_layer=view_layer)
                changed_objects.append(obj)
        view_layer.update()
        yield
    finally:
        for obj in changed_objects:
            obj.hide_set(False, view_layer=view_layer)
        view_layer.update()


def matrices(camera, depsgraph, long_edge, max_size=None):
    from mathutils import Matrix
    params = core.parameters(camera)
    width, height = projection.resolution(params.fov, params.vfov, long_edge, max_size)
    # Analytic P avoids Blender lens RNA limits and is checked against Blender's
    # calc_matrix_camera using the half-angle pixel-aspect correction in tests.
    p = Matrix(projection.projection(params.fov, params.vfov, params.clip_near_m, params.clip_far_m))
    world = camera.evaluated_get(depsgraph).matrix_world.copy()
    return width, height, world.inverted(), p, world


class RenderTarget:
    """One reusable viewport engine; committed images own only colour textures."""
    def __init__(self):
        self.offscreen = None
        self.key = None
        self.allocations = 0
        self.releases = 0

    def get(self, context, width, height):
        import gpu
        key = (context.window.as_pointer(), width, height)
        if self.key != key:
            self.free()
            self.offscreen = gpu.types.GPUOffScreen(width, height, format='RGBA8')
            self.key = key
            self.allocations += 1
        return self.offscreen

    def free(self):
        if self.offscreen is not None:
            try:
                self.offscreen.free()
            except (ReferenceError, RuntimeError):
                pass
            self.releases += 1
        self.offscreen, self.key = None, None


class RenderTargetPool:
    """Capture-owned targets, reused per output size until transaction close.

    Preview keeps its single-target resize behavior. A capture has at most three
    fixed camera sizes; its pool avoids resize/reallocation on every sample.
    """
    def __init__(self):
        self.targets = {}
        self.window = None
        self.allocations = 0
        self.releases = 0

    def get(self, context, width, height):
        window = context.window.as_pointer()
        if self.window is not None and self.window != window:
            self.free()
        self.window = window
        target = self.targets.setdefault((width, height), RenderTarget())
        previous = target.allocations
        offscreen = target.get(context, width, height)
        self.allocations += target.allocations - previous
        return offscreen

    def free(self):
        for target in self.targets.values():
            previous = target.releases
            target.free()
            self.releases += target.releases - previous
        self.targets.clear()
        self.window = None

    def usage(self):
        return {'allocations': self.allocations, 'releases': self.releases,
                'active_targets': len(self.targets), 'policy': 'transaction / output size'}


class CameraImage:
    def __init__(self, camera, depsgraph, long_edge):
        import gpu
        self.width, self.height, self.view, self.projection, self.world = matrices(
            camera, depsgraph, long_edge, gpu.capabilities.max_texture_size_get())
        self.camera_id = f"C{camera['wfrl_custom_slot']}"
        self.params = core.parameters(camera)
        self.texture = None
        self.pixels = None

    def snapshot(self, offscreen):
        import gpu
        with offscreen.bind():
            buffer = gpu.state.active_framebuffer_get().read_color(
                0, 0, self.width, self.height, 4, 0, 'UBYTE')
            buffer.dimensions = self.width * self.height * 4
            self.pixels = bytes(buffer)
            import numpy as np
            values = np.frombuffer(self.pixels, dtype=np.uint8).astype(np.float32) / 255.
            pixels = gpu.types.Buffer('FLOAT', len(values), values)
            self.texture = gpu.types.GPUTexture((self.width, self.height), format='RGBA8', data=pixels)

    def free(self):
        self.texture = self.pixels = None

    def rgba(self):
        return self.pixels


def simulation_state_hash(scene, depsgraph):
    """Version evaluated turbine geometry and transforms, not only the timeline.

    This catches callbacks that mutate pose/shape without advancing frame_current.
    Temporary annotation visibility is intentionally not part of the state.
    """
    from array import array
    import hashlib
    import struct
    digest = hashlib.sha256()
    for obj in sorted(scene.objects, key=lambda item: item.name):
        if not obj.name.startswith(('WFRL.Turbine.', 'WFRL.Camera.T1.Custom.')):
            continue
        evaluated = obj.evaluated_get(depsgraph)
        digest.update(obj.name.encode())
        digest.update(struct.pack('16d', *(v for row in evaluated.matrix_world for v in row)))
        if obj.type == 'MESH':
            mesh = evaluated.to_mesh()
            try:
                coords = array('f', [0.]) * (len(mesh.vertices) * 3)
                mesh.vertices.foreach_get('co', coords)
                digest.update(coords.tobytes())
            finally:
                evaluated.to_mesh_clear()
    return digest.hexdigest()


def render_group(context, cameras, long_edge=None, target=None, fast=False):
    """Synchronous draw on one frozen main-thread state; no event yield per route."""
    global _DRAWING
    import bpy
    if _DRAWING:
        raise RuntimeError('相机离屏绘制不可重入')
    if context.scene.display_settings.display_device != 'sRGB':
        raise ValueError('当前 PNG 编码要求 sRGB 显示设备；不将其他显示空间误标为 sRGB')
    if context.area.type != 'VIEW_3D':
        raise ValueError('离屏预览需要有效的 3D 视口')
    from .panels.custom_cameras import session_identity
    scene = context.scene
    identity = session_identity(scene)
    stamp = (scene.frame_current, scene.frame_subframe)
    region = next(r for r in context.area.regions if r.type == 'WINDOW')
    images = {}
    owned_target = target is None
    target = target if target is not None else RenderTarget()
    _DRAWING = True
    try:
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        state_hash = simulation_state_hash(scene, depsgraph)
        from contextlib import nullcontext
        import sys
        native = sys.modules.get(__package__ + '.native_camera_views')
        quality = native.capture_quality(scene) if native else nullcontext()
        with quality, drawing_settings(context.space_data, fast), without_annotations(scene, context.view_layer):
            for camera in cameras:
                params = core.parameters(camera)
                image = CameraImage(camera, depsgraph, long_edge if long_edge is not None else params.output_long_edge_px)
                image.state_hash = state_hash
                images[int(camera['wfrl_custom_slot'])] = image
                offscreen = target.get(context, image.width, image.height)
                offscreen.draw_view3d(scene, context.view_layer, context.space_data, region,
                    image.view, image.projection, do_color_management=True, draw_background=True)
                image.snapshot(offscreen)
        if (session_identity(scene) != identity or stamp != (scene.frame_current, scene.frame_subframe)
                or simulation_state_hash(scene, depsgraph) != state_hash):
            raise RuntimeError('采集期间场景 / 仿真时刻变化，本组无效')
        return images
    except Exception:
        for image in images.values():
            image.free()
        raise
    finally:
        if owned_target:
            target.free()
        _DRAWING = False


def available_rectangle(area, region):
    import bpy
    width, height = region.width, region.height
    left, bottom = 0, 0
    if bpy.context.preferences.system.use_region_overlap:
        for r in area.regions:
            if r.type in {'UI', 'TOOLS'} and r.width > 1:
                if r.x <= region.x:
                    left = max(left, r.width)
                else:
                    width -= r.width
            elif r.type in {'HEADER', 'TOOL_HEADER'} and r.height > 1:
                # Blender can overlay its header on the WINDOW region. Keep
                # the board title and its hit areas below that native header.
                if region.y < r.y < region.y + region.height:
                    height = min(height, r.y - region.y)
                elif r.y <= region.y < r.y + r.height:
                    bottom = max(bottom, r.y + r.height - region.y)
    return left, bottom, max(1, width - left), max(1, height - bottom)


def cells(rectangle, scale_ui=1., editing=False):
    x, y, width, height = rectangle
    # Drawing and hit testing share this layout, including the board title and
    # gutters. The image inside each card keeps its own optical aspect ratio.
    margin = 10 * scale_ui
    x, y = x + margin, y + margin
    width, height = max(1, width - 2 * margin), max(1, height - 2 * margin - (154 if editing else 78) * scale_ui)
    return [(x, y, width, height)]


def close_button(rectangle, scale_ui=1.):
    x, y, width, height = rectangle
    return x + width - 116 * scale_ui, y + height - 29 * scale_ui, 106 * scale_ui, 24 * scale_ui


def image_rectangle(cell, scale_ui=1.):
    x, y, width, height = cell
    return x + 8 * scale_ui, y + 24 * scale_ui, max(1, width - 16 * scale_ui), max(1, height - 60 * scale_ui)


def contains(rectangle, x, y):
    left, bottom, width, height = rectangle
    return left <= x < left + width and bottom <= y < bottom + height


def draw_image(image, rectangle):
    import gpu
    from gpu_extras.batch import batch_for_shader
    x, y, w, h = rectangle
    scale = min(w / image.width, h / image.height)
    if scale <= 0:
        return
    width, height = image.width * scale, image.height * scale
    x, y = x + (w - width) / 2, y + (h - height) / 2
    shader = gpu.shader.from_builtin('IMAGE')
    batch = batch_for_shader(shader, 'TRI_FAN', {'pos': [(x,y),(x+width,y),(x+width,y+height),(x,y+height)],
        'texCoord': [(0,0),(1,0),(1,1),(0,1)]})
    shader.bind()
    shader.uniform_sampler('image', image.texture)
    batch.draw(shader)


def clock_text(time_s, frame, subframe):
    if isinstance(time_s, (int, float)) and math.isfinite(time_s):
        return f'{time_s:.4f} s'
    return f'帧 {frame} + {subframe:.3f} · 无仿真时钟'


def playback_preview(context):
    return bool(getattr(getattr(context, 'screen', None), 'is_animation_playing', False))


def provenance(context, cameras, long_edge):
    from .panels.custom_cameras import session_identity
    from .custom_camera_capture import current_time
    scene = context.scene
    return {'identity': session_identity(scene), 'frame': scene.frame_current,
        'subframe': scene.frame_subframe, 'time_s': current_time(scene),
        'revision': _REVISION, 'profile': repr(render_profile(scene, playback_preview(context))), 'long_edge': long_edge,
        'slots': tuple(int(cam['wfrl_custom_slot']) for cam in cameras),
        'cameras': tuple((cam.as_pointer(), bool(cam.get('wfrl_custom_draft')),
            repr(asdict(core.parameters(cam))), tuple(v for row in cam.matrix_world for v in row)) for cam in cameras)}


def change_reasons(old, new):
    if old is None:
        return '画面准备中'
    reasons = []
    if any(old[k] != new[k] for k in ('frame', 'subframe', 'time_s')):
        reasons.append('时间已变化')
    if old['slots'] != new['slots']:
        reasons.append('参与相机已变化')
    if old['cameras'] != new['cameras']:
        reasons.append('相机参数 / 姿态已修改')
    if old['profile'] != new['profile']:
        reasons.append('绘制 / 颜色配置已修改')
    if old['long_edge'] != new['long_edge']:
        reasons.append('预览像素预算已修改')
    return '；'.join(reasons) or '场景已变化'


class Preview:
    def __init__(self):
        self.target = RenderTarget()
        self.images, self.key, self.error = {}, None, ''
        self.request = None
        self.metadata = None
        self.last = 0.
        self.attempt = 0.
        self.elapsed_s = 0.
        self.time_s = None
        self.updating = False
        self.version = 0
        self.displayed_version = -1
        _RENDERERS.add(self)

    def free(self):
        self.target.free()
        for image in self.images.values():
            image.free()
        self.images.clear()
        self.key = self.metadata = self.request = None
        self.time_s, self.error = None, ''
        _RENDERERS.discard(self)

    def observe(self, context, cameras, long_edge=640):
        long_edge = min(long_edge, 320) if playback_preview(context) else long_edge
        requested = provenance(context, cameras, long_edge)
        if self.key and self.key['identity'] != requested['identity']:
            self.free()
        # Draft and confirmed groups must never share the same old image.
        if self.key and tuple(c[1] for c in self.key['cameras']) != tuple(c[1] for c in requested['cameras']):
            self.free()
        self.request = requested
        return requested

    def status(self):
        if self.error:
            return ('更新失败，显示上次画面：' if self.images else '更新失败：') + self.error
        if self.updating:
            return '正在更新'
        if self.key is None:
            return '画面准备中'
        if self.request != self.key:
            return '等待更新 · ' + change_reasons(self.key, self.request)
        return '已更新 · 轻量播放预览；暂停恢复材质' if self.key and "'shading': 'SOLID'" in self.key['profile'] else '已更新'

    def refresh(self, context, cameras, long_edge=640):
        if _DRAWING:
            return
        long_edge = min(long_edge, 320) if playback_preview(context) else long_edge
        key = self.observe(context, cameras, long_edge)
        now = time.monotonic()
        if key == self.key or (self.images and now - self.last < max(.25, self.elapsed_s)) or (self.error and now - self.attempt < .5):
            return
        started = time.perf_counter()
        self.attempt, self.updating = now, True
        images = None
        try:
            # RNA edits can leave matrix_world pending until depsgraph evaluation.
            # Freeze provenance only after that evaluation, before GPU drawing.
            context.view_layer.update()
            key = self.observe(context, cameras, long_edge)
            diagnostics.record('t2', renderer=id(self), version=self.version + 1, request=core.layout_hash(key))
            images = render_group(context, cameras, long_edge, self.target, playback_preview(context))
            # Source metadata belongs to the state requested before drawing.
            # render_group independently guards geometry, frame and session.
            finished = provenance(context, cameras, long_edge)
            if any(finished[k] != key[k] for k in key if k != 'revision'):
                raise RuntimeError('绘制期间相机参数或来源变化，本组未提交')
            metadata = {**key, 'sizes': {slot: (im.width, im.height) for slot, im in images.items()}}
            old = self.images
            self.images, self.key, self.metadata = images, key, metadata
            self.last = time.monotonic()
            self.elapsed_s = time.perf_counter() - started
            self.time_s, self.error = key['time_s'], ''
            self.version += 1
            _RENDERERS.add(self)
            diagnostics.record('t3', renderer=id(self), version=self.version, elapsed_s=self.elapsed_s, request=core.layout_hash(key), cameras=key['cameras'])
            for image in old.values():
                image.free()
        except Exception as exc:
            if images is not None and images is not self.images:
                for image in images.values():
                    image.free()
            self.error = str(exc)
            diagnostics.record('preview_failure', reason=self.error)
        finally:
            self.updating = False

    def draw(self, context, cameras, slot=1, hint='', closable=True):
        if _DRAWING:
            return
        import blf
        import bpy
        import gpu
        from gpu_extras.batch import batch_for_shader
        region = next(r for r in context.area.regions if r.type == 'WINDOW')
        rect = available_rectangle(context.area, region)
        scale_ui = max(1., bpy.context.preferences.system.ui_scale)
        x,y,w,h = rect
        ui = scale_ui
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        def fill(rectangle, color):
            left, bottom, width, height = rectangle
            shader.bind(); shader.uniform_float('color', color)
            batch_for_shader(shader, 'TRI_FAN', {'pos': [(left,bottom),(left+width,bottom),
                (left+width,bottom+height),(left,bottom+height)]}).draw(shader)
        def text(value, left, bottom, width, size=12, color=(.83,.87,.93,1)):
            if width <= 0:
                return
            blf.size(0, size * ui)
            if blf.dimensions(0, value)[0] > width:
                while value and blf.dimensions(0, value + '…')[0] > width:
                    value = value[:-1]
                value += '…'
            blf.color(0, *color); blf.position(0, left, bottom, 0); blf.draw(0, value)
        fill(rect, (.018,.022,.03,1))
        self.observe(context, cameras)
        current = self.request
        at = clock_text(current['time_s'], current['frame'], current['subframe'])
        playing = bool(context.window and context.window.screen.is_animation_playing)
        reserve = (270 if closable else 150) * ui
        text(hint or f'C{slot} · 已锁定；拖动不会修改相机', x+12*ui, y+h-23*ui, w-reserve, size=13)
        text('当前回放：' + at + (' · 播放中' if playing else ' · 已暂停'), x+12*ui, y+h-45*ui, w-24*ui, size=11)
        source = '尚无成功图像' if self.metadata is None else clock_text(
            self.metadata['time_s'], self.metadata['frame'], self.metadata['subframe'])
        text('看板画面：' + source + ' · ' + self.status(), x+12*ui, y+h-65*ui, w-24*ui, size=11,
             color=(1,.65,.3,1) if self.request != self.key or self.error else (.56,.73,.66,1))
        if closable:
            button = close_button(rect, ui)
            fill(button, (.18,.24,.32,1))
            text('关闭画面 · Esc', button[0]+9*ui, button[1]+7*ui,
                 button[2]-18*ui, size=11, color=(.93,.96,1,1))
        slots = (slot,)
        for number, cell in zip(slots, cells(rect, ui, editing=not closable)):
            cx, cy, cw, ch = cell
            cam = next((cam for cam in cameras if cam['wfrl_custom_slot'] == number), None)
            installed = cam if cam else core.get_camera(context.scene, number)
            image = self.images.get(number) if cam else None
            selected = number == slot
            fill(cell, (.18,.65,.9,1) if selected else (.16,.19,.24,1))
            border = (2 if selected else 1) * ui
            fill((cx+border,cy+border,cw-2*border,ch-2*border), (.033,.041,.055,1))
            fill((cx+border,cy+ch-34*ui,cw-2*border,34*ui-border), (.065,.084,.11,1))
            name = installed.get('custom_label', '') if installed else ''
            title = f'C{number}' + (f' · {name}' if name and name != f'C{number}' else '')
            text(title, cx+12*ui, cy+ch-23*ui, cw-24*ui, size=13,
                 color=(.4,.82,1,1) if selected else (.83,.87,.93,1))
            body = image_rectangle(cell, ui)
            if image:
                draw_image(image, body)
            else:
                message = '画面准备中…' if cam else '已安装，未参与三路预览与采集' if installed else '尚未设置安装位置'
                hint = '单路仍可检查；参与设置见 View 面板' if installed and not cam else '点击右上角「设置位置」' if not installed else ''
                for line, value in enumerate((message, hint)):
                    blf.size(0, (13 if line == 0 else 11) * ui)
                    tw = min(blf.dimensions(0, value)[0], body[2]-16*ui)
                    text(value, body[0]+(body[2]-tw)/2, body[1]+body[3]/2+(8-24*line)*ui,
                         tw+1, size=13 if line == 0 else 11, color=(.64,.71,.81,1))
            if installed:
                footer = ('已安装 · 参与三路预览与采集' if installed.get('custom_enabled', True)
                          else '已安装 · 未参与三路预览与采集')
                if context.window_manager.wfrl_custom_engineering:
                    p = core.parameters(installed)
                    footer = f'XYZ {tuple(round(v, 2) for v in p.location)} · YPR {p.yaw:.1f}/{p.pitch:.1f}/{p.roll:.1f} · FOV {p.fov:.1f}/{p.vfov:.1f}'
                if installed.get('custom_mount_warning'):
                    footer += ' · ' + installed['custom_mount_warning']
            else:
                footer = '未安装 · 此槽位由你单独选点'
            text(footer, cx+12*ui, cy+9*ui, cw-24*ui, size=10, color=(.53,.61,.71,1))
        if self.key is not None and self.displayed_version != self.version:
            diagnostics.record('t4', renderer=id(self), version=self.version, request=core.layout_hash(self.key))
            self.displayed_version = self.version


def shutdown():
    for renderer in tuple(_RENDERERS):
        renderer.free()


def action_buttons(rectangle, scale_ui=1.):
    x, y, width, height = rectangle
    unit = min(104 * scale_ui, max(50, (width - 40 * scale_ui) / 2))
    return ((x+10*scale_ui, y+height-100*scale_ui, unit, 25*scale_ui),
            (x+18*scale_ui+unit, y+height-100*scale_ui, unit, 25*scale_ui))


def draw_controls(context, state, editing=False, confirmable=False, reason='', external=False):
    """Same action hit areas in every stage, outside all preview image rectangles."""
    import blf
    import gpu
    from gpu_extras.batch import batch_for_shader
    region = next(r for r in context.area.regions if r.type == 'WINDOW')
    rect = available_rectangle(context.area, region)
    x, y, width, height = rect
    ui = max(1., context.preferences.system.ui_scale)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    def fill(box, color):
        a,b,w,h = box
        shader.bind(); shader.uniform_float('color', color)
        batch_for_shader(shader, 'TRI_FAN', {'pos': [(a,b),(a+w,b),(a+w,b+h),(a,b+h)]}).draw(shader)
    def text(value, a, b, max_width, color=(.9,.94,1,1), size=11):
        blf.size(0, size*ui)
        while value and blf.dimensions(0, value)[0] > max_width:
            value = value[:-2] + '…' if len(value) > 2 else ''
        blf.color(0,*color); blf.position(0,a,b,0); blf.draw(0,value)
    if external:
        fill((x, y+height-154*ui, width, 154*ui), (.025,.035,.05,1))
        for index, line in enumerate(state.split('；')):
            text(line, x+12*ui, y+height-(23+22*index)*ui, width-24*ui, size=13 if index==0 else 11)
    if editing:
        confirm, cancel = action_buttons(rect, ui)
        for box, label, enabled in ((confirm,'确认修改',confirmable),(cancel,'取消修改',True)):
            fill(box, (.12,.28,.36,1) if enabled else (.1,.12,.15,1))
            text(label, box[0]+8*ui, box[1]+8*ui, box[2]-16*ui,
                 (.9,.94,1,1) if enabled else (.45,.48,.52,1))
        if reason:
            text(reason, x+12*ui, y+height-122*ui, width-24*ui, (1,.65,.3,1))
        text('确认后保留在当前场景；持久保存请导出布局或保存 Blender 文件。',
             x+12*ui, y+height-144*ui, width-24*ui, (.6,.68,.77,1), size=10)
