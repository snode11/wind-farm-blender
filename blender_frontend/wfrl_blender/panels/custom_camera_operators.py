"""Custom-camera modal editing, slot actions and coordinate help operators."""
import bpy
from bpy.props import EnumProperty, IntProperty
from .. import custom_cameras as core


def _removed_handle(error):
    """Only confirmed invalid handles count as successful deregistration."""
    return isinstance(error, ReferenceError) or any(text in str(error).lower() for text in (
        'already removed', 'already been removed', 'timer not found',
        'handler not found', 'invalid handle'))


def _timer_advanced(op, event):
    """Consume at most one poll per advance of this operator's own timer.

    Blender Event RNA does not expose the originating timer. Its Timer RNA does
    expose time_duration, so unrelated TIMER events cannot multiply our polls.
    """
    if getattr(event, 'timer', None) not in (None, op.timer):
        return False
    duration = op.timer.time_duration
    if duration <= op._timer_duration:
        return False
    op._timer_duration = duration
    return True


class Available:
    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and _panel.core.is_available(context.scene))


class WFRL_OT_CustomCamera(Available, bpy.types.Operator):
    bl_idname = 'wfrl.custom_camera'
    bl_label = '自定义固定相机'
    mode: EnumProperty(items=[(v, v, '') for v in ('PLACE', 'INPUT', 'EDIT', 'WATCH', 'LAYOUT')])

    def invoke(self, context, event):
        if context.scene.get('wfrl_stacked_camera_rig') and self.mode in {'PLACE', 'INPUT'}:
            self.report({'WARNING'}, '请先整体安装盒体，再调整各相机俯仰角')
            return {'CANCELLED'}
        if _panel._ACTIVE and (getattr(_panel._ACTIVE, '_finishing', False)
                               or _panel._ACTIVE.stage not in _panel._VIEWING):
            self.report({'WARNING'}, '请先确认或取消当前调整')
            return {'CANCELLED'}
        if self.mode not in _panel._VIEWING:
            reason = _panel.installation_block_reason(context.scene)
            if reason:
                self.report({'WARNING'}, reason)
                return {'CANCELLED'}
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            self.report({'WARNING'}, '请先完成或取消图像采集')
            return {'CANCELLED'}
        _panel.core.migrate_scene(context.scene)
        previous = _panel._ACTIVE
        self.timer = self.handle = self.session = self.preview = None
        self.snapshot = self.external_snapshot = None
        self._modal_registered = self._finishing = False
        self.closed, self.ready, self.error = False, False, ''
        self.stage, self.identity, self.camera = self.mode, None, None
        self.drag, self.stick, self.last = None, (0, 0), (0, 0)
        self.hover, self.candidates, self.press = None, [], None
        self.area, self.scene, self.wm = context.area, context.scene, context.window_manager
        self.window = context.window
        try:
            self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
            self.slot = self.wm.wfrl_custom_slot
            self.snapshot = _panel.ViewportSnapshot(self.area)
            self.external_snapshot = previous.external_snapshot if previous and previous.area == self.area else self.snapshot
            self.return_stage = previous.stage if previous and previous.area == self.area else None
            if previous:
                previous.finish(restore=False)
            # The legacy controller must not also process the same mouse events.
            from . import gimbal
            if gimbal._ACTIVE:
                gimbal._ACTIVE.finish()
            _panel._ACTIVE = self
            self.preview = _panel.preview.Preview()
            if self.mode in _panel._VIEWING:
                self.camera = _panel.core.get_camera(self.scene, self.slot)
                if not self.camera and self.mode == 'WATCH':
                    raise ValueError('此槽位未安装相机')
                self.stage, self.ready = self.mode, True
                if self.mode == 'LAYOUT':
                    self.external()
                else:
                    self.show_camera()
            else:
                self.session = _panel.EditSession(self.scene, self.slot, self.area, self.window, self.wm)
                self.camera = self.session.begin()
                self.ready = self.session.original_camera is not None
                self.stage = 'AIM' if self.mode == 'EDIT' else self.mode
                if self.stage == 'AIM':
                    self.show_camera()
                else:
                    self.external()
            if self.camera:
                _panel.sync_fields(self.wm, self.camera)
            self.identity = _panel.session_identity(self.scene)
            self.last_time = _panel.time.monotonic()
            self.timer = self.wm.event_timer_add(.025, window=self.window)
            self._timer_duration = self.timer.time_duration
            self.handle = _panel.bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), 'WINDOW', 'POST_PIXEL')
            if self.wm.modal_handler_add(self) is False:
                raise RuntimeError('无法注册相机预览交互处理器')
            self._modal_registered = True
            _panel._redraw_if_changed(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            try:
                self.finish(restore=_panel._ACTIVE is self)
            except (RuntimeError, ValueError) as cleanup:
                self.report({'ERROR'}, f'{exc}；预览资源待清理：{cleanup}')
                return {'CANCELLED'}
            finally:
                # Blender retires a modal handler when invoke returns CANCELLED.
                self._modal_registered = False
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def show_camera(self):
        # The actual image is an independent offscreen texture, not a native
        # camera frame borrowing scene.render's aspect ratio.
        self.area.spaces.active.lock_camera = False
        self.frame_fits = True
        self.area.tag_redraw()

    def select_slot(self, slot):
        # Empty slots are real selections too; never leave a previous camera
        # attached to a newly selected empty card.
        self.wm.wfrl_custom_slot = self.slot = slot
        self.camera = _panel.core.get_camera(self.scene, slot)
        if self.camera:
            _panel.sync_fields(self.wm, self.camera)
        self.area.tag_redraw()

    def return_to_view(self, confirmed=False):
        self.preview.free()
        self.camera = _panel.core.get_camera(self.scene, self.slot)
        stage = self.return_stage
        if stage == 'LAYOUT' or (stage == 'WATCH' and self.camera):
            self.stage, self.ready, self.error = stage, True, ''
            self.drag = None
            if stage == 'LAYOUT':
                self.external()
            self.show_camera()
        elif confirmed:
            self.stage, self.ready, self.error, self.drag = 'WATCH', True, '', None
            self.show_camera()
        else:
            self.finish(restore=False)

    def external(self):
        parent = self.scene.objects.get('WFRL.Turbine.T1.YawRoot')
        rv = self.area.spaces.active.region_3d
        center = parent.matrix_world @ _panel.Vector((0, 0, 1.5))
        eye = parent.matrix_world @ _panel.Vector((12, -13, 9))
        rv.view_perspective, rv.view_location = 'PERSP', center
        rv.view_rotation = (center-eye).to_track_quat('-Z', 'Y')
        rv.view_distance = (center-eye).length
        self.area.spaces.active.lock_camera = False

    def finish(self, restore=True, resume=True):
        if getattr(self, 'closed', True):
            return
        if not getattr(self, '_finishing', False):
            self._finish_options = restore, resume
        self._finishing = True
        restore, resume = self._finish_options
        # An invalidated identity lets the watchdog retry without modal events.
        self.identity = None
        self.drag = None
        try:
            # Modal callbacks become inert immediately and retire on their next event.
            if self.handle is not None:
                try:
                    _panel.bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
                except (ReferenceError, RuntimeError, ValueError) as exc:
                    if not _removed_handle(exc):
                        raise
                self.handle = None
            if self.timer is not None:
                try:
                    self.wm.event_timer_remove(self.timer)
                except (ReferenceError, RuntimeError, ValueError) as exc:
                    if not _removed_handle(exc):
                        raise
                self.timer = None
            if self.session and not self.session.closed:
                self.session.cancel(restore=resume)
            if self.preview is not None:
                self.preview.free()
            if restore and self.external_snapshot is not None:
                self.external_snapshot.restore()
        except Exception as exc:
            self.error = str(exc)
            self.report({'ERROR'}, self.error)
            raise RuntimeError(self.error) from exc
        try:
            self.area.header_text_set(None)
            self.area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass
        self.closed, self.drag = True, None
        if _panel._ACTIVE is self:
            _panel._ACTIVE = None

    def cancel(self, context):
        self.finish()

    def geometry(self):
        x,y,width,height = _panel.preview.available_rectangle(self.area,self.region)
        ui = max(1.,_panel.bpy.context.preferences.system.ui_scale)
        return x+width-42*ui, y+height-89*ui, 18*ui

    def inside(self, event):
        from .. import viewport
        return viewport.in_window_region(self.area, event)

    def change(self, dx=0, dy=0, zoom=0):
        if not (dx or dy or zoom):
            return
        _panel.diagnostics.record('t1', input='direction', slot=self.slot, dx=dx, dy=dy, zoom=zoom)
        p = _panel.core.parameters(self.camera)
        yaw, pitch = _panel.core.screen_direction_delta(p.yaw, p.pitch, p.roll, dx, dy)
        h, v = p.fov, p.vfov
        if zoom and self.wm.wfrl_custom_linked_zoom:
            try:
                h, v = _panel.projection.linked_fov(h, v, _panel.math.exp(zoom * .025))
            except ValueError:
                return
        updated = _panel.replace(p, yaw=yaw, pitch=pitch, fov=h, vfov=v)
        if updated == p:
            return
        _panel.core.apply_parameters(self.camera, updated)
        _panel.sync_fields(self.wm, self.camera)
        _panel._redraw_if_changed(self)

    def pick(self, x, y):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        rv = self.area.spaces.active.region_3d
        return _panel.core.raycast_surface(self.scene, region_2d_to_origin_3d(self.region, rv, (x,y)),
            region_2d_to_vector_3d(self.region, rv, (x,y)), _panel.bpy.context.evaluated_depsgraph_get())

    def modal(self, context, event):
        if self.closed:
            self._modal_registered = False
            return {'FINISHED'}
        if getattr(self, '_finishing', False):
            try:
                self.finish()
            except (RuntimeError, ValueError):
                return {'RUNNING_MODAL'}
            self._modal_registered = False
            return {'FINISHED'}
        try:
            if context.window != self.window:
                return {'PASS_THROUGH'}
            if event.type == 'TIMER' and not _panel._timer_advanced(self, event):
                return {'PASS_THROUGH'}
            valid = (self.window in list(self.wm.windows) and self.window.scene == self.scene
                     and self.area in list(self.window.screen.areas) and self.area.type == 'VIEW_3D'
                     and _panel.session_identity(self.scene) == self.identity)
            if not valid or (self.session and not self.session.closed and not self.session.valid()):
                self.finish(restore=False, resume=False)
                return {'CANCELLED'}
            try:
                camera_exists = self.camera is not None and self.scene.objects.get(self.camera.name) == self.camera
            except ReferenceError:
                camera_exists = False
            if not camera_exists and self.stage != 'LAYOUT':
                self.finish(restore=True, resume=False)
                return {'CANCELLED'}
            if event.type == 'ESC' and event.value == 'PRESS':
                # Native numeric/text editing owns Esc outside the WINDOW region.
                # Do not consume the same key at both field and draft layers.
                if not self.inside(event):
                    return {'PASS_THROUGH'}
                from .. import custom_camera_capture
                if custom_camera_capture.active():
                    return {'PASS_THROUGH'}
                if self.stage in _panel._VIEWING:
                    self.finish()
                else:
                    self.cancel_edit()
                return {'FINISHED'} if self.closed else {'RUNNING_MODAL'}
            if event.type == 'TIMER':
                now = _panel.time.monotonic()
                dt, self.last_time = max(0., min(now-self.last_time, .1)), now
                if self.stage not in _panel._VIEWING:
                    self.session._play(False)
                    if (self.scene.frame_current != self.session.frame or
                            self.scene.frame_subframe != self.session.subframe):
                        self.error = '回放时刻已变化，已取消本次调整'
                        self.finish(restore=False, resume=False)
                        return {'CANCELLED'}
                if self.stage == 'AIM' and self.drag == 'stick':
                    self.change(self.stick[0]*dt*45, self.stick[1]*dt*45)
                if self.stage in {'AIM', 'WATCH'}:
                    from .. import custom_camera_capture
                    if not custom_camera_capture.active():
                        cameras = [self.camera]
                        try:
                            with context.temp_override(window=self.window, area=self.area, region=self.region):
                                self.preview.refresh(context, cameras)
                        except Exception as exc:
                            self.preview.error = str(exc)
                    if self.area.spaces.active.lock_camera:
                        self.area.spaces.active.lock_camera = False
                _panel._redraw_if_changed(self)
                return {'PASS_THROUGH'}
            from .. import custom_camera_capture
            if custom_camera_capture.active():
                return {'PASS_THROUGH'}
            inside = self.inside(event)
            x,y = event.mouse_x-self.region.x, event.mouse_y-self.region.y
            if self.stage not in _panel._VIEWING and event.type in {'SPACE', 'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW'} and inside:
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                if self.drag:
                    _panel.diagnostics.record('drag_release', slot=self.slot)
                    self.drag, self.stick = None, (0,0)
                    _panel._redraw_if_changed(self)
                    return {'RUNNING_MODAL'}
                if self.stage == 'PLACE' and self.press:
                    px,py = self.press
                    self.press = None
                    if inside and _panel.math.hypot(x-px,y-py) < 5:
                        anchor = self.pick(x,y)
                        if anchor:
                            try:
                                _panel.core.place_on_surface(self.camera, anchor, self.wm.wfrl_custom_distance)
                                _panel.sync_fields(self.wm, self.camera)
                                self.ready, self.error = True, ''
                            except ValueError as exc:
                                self.ready, self.error = False, str(exc)
                        else:
                            self.error = '请选择 T1 机舱本体外壳表面'
                    _panel._redraw_if_changed(self)
                    return {'RUNNING_MODAL'}
            if event.type == 'MOUSEMOVE' and self.drag:
                if self.drag == 'stick':
                    cx,cy,r = self.geometry()
                    dx,dy = (x-cx)/r,(y-cy)/r
                    length = max(1, _panel.math.hypot(dx,dy))
                    self.stick = dx/length,dy/length
                else:
                    self.change((x-self.last[0])*(.04 if event.shift else .2),(y-self.last[1])*(.04 if event.shift else .2))
                self.last = (x,y)
                _panel._redraw_if_changed(self)
                return {'RUNNING_MODAL'}
            if not inside:
                return {'PASS_THROUGH'}
            if self.stage not in _panel._VIEWING and event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                ui = max(1., context.preferences.system.ui_scale)
                buttons = _panel.preview.action_buttons(_panel.preview.available_rectangle(self.area, self.region), ui)
                for action, button in zip(('CONFIRM', 'CANCEL'), buttons):
                    if _panel.preview.contains(button, x, y):
                        _panel.bpy.ops.wfrl.custom_camera_action(action=action)
                        return {'RUNNING_MODAL'}
            if self.stage == 'WATCH' and event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                ui = max(1., context.preferences.system.ui_scale)
                rect = _panel.preview.available_rectangle(self.area, self.region)
                if _panel.preview.contains(_panel.preview.close_button(rect, ui), x, y):
                    self.finish()
                    return {'FINISHED'}
            if self.stage == 'LAYOUT':
                return {'PASS_THROUGH'}
            if self.stage in {'PLACE', 'INPUT'}:
                if event.type in {'MIDDLEMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE'} or event.alt:
                    self.press = None
                if event.type == 'MOUSEMOVE':
                    self.hover = self.pick(x,y) if self.stage == 'PLACE' else None
                    _panel._redraw_if_changed(self)
                if self.stage == 'PLACE' and event.type == 'LEFTMOUSE' and event.value == 'PRESS' and not (event.alt or event.ctrl or event.shift):
                    self.press = (x,y)
                    return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}
            if self.stage == 'AIM':
                if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                    cx,cy,r = self.geometry()
                    self.drag = 'stick' if _panel.math.hypot(x-cx,y-cy) <= r else 'look'
                    self.last = (x,y)
                    self.last_time = _panel.time.monotonic()
                    if self.drag == 'stick':
                        self.stick = ((x-cx)/r,(y-cy)/r)
                    _panel._redraw_if_changed(self)
                    return {'RUNNING_MODAL'}
                if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                    self.change(zoom=-3 if event.type == 'WHEELUPMOUSE' else 3)
                    return {'RUNNING_MODAL'}
            # Native view operations, including keymap alternatives, may never move
            # a mounted view. N/T and sidebar clicks remain available to exit it.
            if self.stage in _panel._VIEWING and event.type == 'SPACE':
                return {'PASS_THROUGH'}
            if event.type not in {'N', 'T', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'WINDOW_DEACTIVATE'}:
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        except (ReferenceError, RuntimeError, ValueError, KeyError) as exc:
            self.error = str(exc)
            try:
                self.finish(restore=False, resume=False)
            except (RuntimeError, ValueError):
                return {'RUNNING_MODAL'}
            return {'CANCELLED'}

    def state_text(self):
        if self.stage == 'AIM':
            return f'正在修改 C{self.slot} 方向与视场 · XYZ 已锁定 · 草稿未确认'
        if self.stage in {'PLACE', 'INPUT'}:
            return f'正在修改 C{self.slot} 安装位置 · 草稿未确认'
        if self.stage == 'LAYOUT':
            return f'外部布局 · 当前选中 C{self.slot}；观察视角不修改安装参数'
        return f'C{self.slot} · 已锁定；拖动不会修改相机'

    def cancel_edit(self):
        self.session.cancel()
        self.return_to_view()

    def draw_overlay(self):
        if getattr(self, '_finishing', False) or self.closed:
            return
        return _overlay.draw_overlay(self)


class WFRL_OT_CustomAction(bpy.types.Operator):
    bl_idname = 'wfrl.custom_camera_action'
    bl_label = '自定义相机操作'
    action: EnumProperty(items=[(v,v,'') for v in ('NEXT','BACK','CONFIRM','CANCEL','EXIT','CLEAR','COPY','SLOT','VALIDATE','ANCHOR','ENABLE','UNDO')])
    slot: IntProperty(default=1, min=1, max=core.MAX_CUSTOM_CAMERAS)
    index: IntProperty(default=0)

    def execute(self, context):
        op = _panel._ACTIVE
        wm,scene = context.window_manager,context.scene
        if op is not None and op.scene != scene:
            if op.stage not in _panel._VIEWING or self.action in {
                    'NEXT', 'BACK', 'CONFIRM', 'CANCEL', 'EXIT', 'VALIDATE', 'ANCHOR'}:
                self.report({'WARNING'}, '请在相机编辑所属场景中完成或取消操作')
                return {'CANCELLED'}
            op = None
        editing = op is not None and op.stage not in _panel._VIEWING
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            self.report({'WARNING'}, '图像采集中，请先取消或等待完成')
            return {'CANCELLED'}
        try:
            if self.action in {'CLEAR','ENABLE'} and _panel.installation_block_reason(scene):
                raise ValueError(_panel.installation_block_reason(scene))
            if self.action in {'SLOT','CLEAR'} and editing:
                raise ValueError('请先确认或取消当前调整')
            if self.action == 'SLOT':
                wm.wfrl_custom_slot = self.slot
                if op and op.stage in _panel._VIEWING:
                    op.select_slot(self.slot)
                    if not op.camera and op.stage == 'WATCH':
                        op.finish()
            elif self.action == 'ENABLE' and not editing:
                camera = _panel.core.get_camera(scene, wm.wfrl_custom_slot)
                if camera:
                    _panel.core.set_enabled(scene, wm.wfrl_custom_slot, not camera.get('custom_enabled', True))
            elif self.action == 'UNDO':
                _panel.history.undo(scene)
            elif self.action == 'CLEAR':
                _panel.core.clear_slot(scene,wm.wfrl_custom_slot)
                if op and not op.closed:
                    op.select_slot(wm.wfrl_custom_slot)
            elif self.action == 'COPY':
                camera = op.camera if editing else _panel.core.get_camera(scene,wm.wfrl_custom_slot)
                if camera:
                    wm.clipboard = _panel.core.copy_text(scene,camera,wm.wfrl_custom_slot)
            elif self.action == 'EXIT' and op:
                op.finish()
            elif self.action == 'CANCEL' and editing:
                op.cancel_edit()
            elif self.action == 'BACK' and editing:
                op.stage,op.drag = ('INPUT' if op.camera.get('custom_mount_mode') == 'RESEARCH' else 'PLACE'),None
                op.external()
            elif self.action == 'VALIDATE' and editing:
                params = _panel._parameters(wm)
                _panel.core.validate_parameters(params)
                op.camera['custom_label'] = wm.wfrl_custom_label
                if wm.wfrl_custom_mount_mode == 'RESEARCH':
                    _panel.core.apply_research(scene, op.camera, params, wm.wfrl_custom_research_confirmed)
                    op.ready,op.error,op.stage = True,'','AIM'
                    _panel.sync_fields(wm,op.camera)
                    op.show_camera()
                    return {'FINISHED'}
                # Validation must run before any draft transform changes.
                anchors = _panel.core.validate_position(scene,params.location)
                op.candidates = anchors
                if len(anchors) > 1:
                    op.error,op.ready = '请选择候选锚点；镜头中心和朝向保持输入值',False
                    op.external()
                else:
                    _panel.core.apply_parameters(op.camera,params,anchor=anchors[0])
                    op.ready,op.error,op.stage = True,'','AIM'
                    _panel.sync_fields(wm,op.camera)
                    op.show_camera()
            elif self.action == 'ANCHOR' and editing:
                anchor = op.candidates[self.index]
                _panel.core.apply_parameters(op.camera,_panel._parameters(wm),anchor=anchor)
                op.candidates=[]
                op.ready,op.error,op.stage=True,'','AIM'
                _panel.sync_fields(wm,op.camera)
                op.show_camera()
            elif self.action in {'NEXT','CONFIRM'} and editing:
                if not op.ready or (self.action == 'CONFIRM' and op.stage != 'AIM'):
                    raise ValueError(op.error or '请先确定有效安装位置并进入方向阶段')
                if self.action == 'NEXT':
                    op.stage,op.drag = 'AIM',None
                else:
                    op.camera = op.session.confirm()
                    op.return_to_view(confirmed=True)
                op.show_camera()
            context.area.tag_redraw()
            return {'FINISHED'}
        except (ValueError, RuntimeError, IndexError) as exc:
            if editing:
                op.error = str(exc)
            self.report({'ERROR'},str(exc))
            return {'CANCELLED'}
        finally:
            if op and not op.closed:
                _panel._redraw_if_changed(op)


class WFRL_OT_CustomCoordinates(bpy.types.Operator):
    bl_idname = 'wfrl.custom_camera_coordinates'
    bl_label = '坐标说明 · T1.YawRoot.local / v1'
    def execute(self,context):
        return {'FINISHED'}
    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self,width=560)
    def draw(self,context):
        for text in ('原点：T1 塔顶偏航参考点；XYZ 是镜头光学中心，单位 m。',
                     '+X 朝机舱尾部，−X 朝叶轮；+Z 机舱局部上方。',
                     '+Y：站在机舱内面向叶轮时的右侧。',
                     '水平角从 +X 朝 +Y 为正 [0, 360)；俯仰角向上为正。',
                     '画面旋转角：相机局部 +Z 轴旋转；单位 °。',
                     '复现画面还需同一模型、回放时刻、画幅和显示设置。',
                     '确认只提交当前场景；本功能不主动保存 .blend。'):
            self.layout.label(text=text)


from . import custom_camera_overlay as _overlay

# Runtime callbacks share the public namespace; definitions are available first
# so importing either this module or the public entry point is supported.
from . import custom_cameras as _panel
