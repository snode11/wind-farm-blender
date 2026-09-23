"""Cancelable, single-viewport installation of T1 observation cameras."""
import math
import time
from dataclasses import replace

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from mathutils import Vector

from .. import custom_cameras as core
from .. import custom_camera_history as history, custom_camera_diagnostics as diagnostics
from .. import camera_projection as projection, custom_camera_preview as preview

_ACTIVE = None
_SYNCING = False
_FIELDS = ('x', 'y', 'z', 'yaw', 'pitch', 'roll', 'fov', 'vfov', 'distance', 'focal', 'near', 'far')
_VIEWING = {'WATCH', 'LAYOUT'}


def dismiss_for_view_switch(scene):
    """Release the overlay before a legacy camera writes its target view.

    Cancel any pending draft first (including its playback/snapshot restore),
    then let the requested camera take ownership. Never restore our old view
    after that target has been assigned.
    """
    if _ACTIVE and _ACTIVE.scene == scene:
        _ACTIVE.finish(restore=False)


def session_identity(scene):
    """Identity rather than timeline position: ordinary playback is not a reload."""
    from .. import clearance_replay, farm_flex, runtime
    parent = scene.objects.get('WFRL.Turbine.T1.YawRoot')
    state = getattr(runtime, '_state', None)
    return (scene.as_pointer(), parent.as_pointer() if parent else None,
            id(clearance_replay.reader_for(scene)), id(farm_flex._ACTIVE),
            getattr(state, 'session_id', None), scene.get('wfrl_clearance_demo'),
            scene.get('wfrl_farm_demo'))


def installation_block_reason(scene):
    """Only the Blender timeline is paused here; never race a live producer."""
    from .. import runtime
    state = runtime.get_state()
    if state.connection != 'LOCAL DEMO':
        if (getattr(runtime, '_pending_command', None) or not state.confirmed
                or state.run_status in {'STARTING', 'RUNNING', 'DRAINING'}):
            return '请先暂停后端并等待暂停确认，再调整自定义相机'
    elif scene.get('wfrl_scene_kind') == 'live' and state.run_status in {'STARTING', 'RUNNING', 'DRAINING'}:
        return '请先暂停实时运行，再调整自定义相机'
    return ''


class ViewportSnapshot:
    def __init__(self, area):
        self.area = area
        space = area.spaces.active
        rv = space.region_3d
        self.local, self.camera, self.lock = space.use_local_camera, space.camera, space.lock_camera
        self.values = {name: getattr(rv, name).copy() if hasattr(getattr(rv, name), 'copy')
                       else getattr(rv, name) for name in
                       ('view_distance', 'view_location', 'view_rotation', 'view_perspective',
                        'view_camera_zoom', 'view_camera_offset')}

    def restore(self):
        try:
            if self.area.type != 'VIEW_3D':
                return
            space = self.area.spaces.active
            space.use_local_camera, space.lock_camera = self.local, self.lock
            try:
                space.camera = self.camera
            except ReferenceError:
                space.camera = None
            for name, value in self.values.items():
                setattr(space.region_3d, name, value)
            self.area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass


class EditSession:
    """Transaction usable in background tests without a window or UI context."""
    def __init__(self, scene, slot, area=None, window=None, wm=None):
        self.scene, self.slot = scene, slot
        self.area, self.window, self.wm = area, window, wm
        self.identity = session_identity(scene)
        self.frame, self.subframe = scene.frame_current, scene.frame_subframe
        self.playing = bool(window and window.screen.is_animation_playing)
        self.snapshot = ViewportSnapshot(area) if area else None
        self.original_camera = core.get_camera(scene, slot)
        self.draft, self.closed = None, False

    def valid(self):
        try:
            if self.closed or session_identity(self.scene) != self.identity or installation_block_reason(self.scene):
                return False
            if self.window and (self.window.scene != self.scene or
                                self.window not in list(self.wm.windows)):
                return False
            if self.area and (self.area.type != 'VIEW_3D' or
                              self.area not in list(self.window.screen.areas)):
                return False
            return True
        except (ReferenceError, RuntimeError):
            return False

    def _play(self, playing):
        if self.window and bool(self.window.screen.is_animation_playing) != playing:
            with bpy.context.temp_override(window=self.window, screen=self.window.screen):
                if playing:
                    bpy.ops.screen.animation_play()
                else:
                    bpy.ops.screen.animation_cancel(restore_frame=False)

    def begin(self):
        reason = installation_block_reason(self.scene)
        if reason:
            raise ValueError(reason)
        self._play(False)
        try:
            self.draft = core.begin_draft(self.scene, self.slot)
        except Exception:
            self._play(self.playing)
            raise
        return self.draft

    def confirm(self):
        if not self.valid():
            self.cancel(restore=False)
            raise ValueError('场景或回放会话已变化，请重新安装')
        camera = core.commit_draft(self.scene, self.slot, self.draft)
        self.draft = None
        self._play(self.playing)
        self.closed = True
        return camera

    def cancel(self, restore=True):
        if self.closed:
            return
        same = self.valid()
        if self.draft is not None:
            try:
                core.cancel_draft(self.draft)
            except (ReferenceError, RuntimeError):
                pass
            self.draft = None
        if restore and same:
            if self.snapshot:
                self.snapshot.restore()
            self._play(self.playing)
        self.closed = True


def _parameters(wm):
    return core.CameraParameters(location=tuple(getattr(wm, 'wfrl_custom_'+k) for k in ('x', 'y', 'z')),
        **{k: getattr(wm, 'wfrl_custom_'+k) for k in ('yaw', 'pitch', 'roll', 'fov', 'vfov')},
        focal_length_mm_record=wm.wfrl_custom_focal if wm.wfrl_custom_has_focal else None,
        output_long_edge_px=wm.wfrl_custom_long_edge,
        clip_near_m=wm.wfrl_custom_near, clip_far_m=wm.wfrl_custom_far)


def sync_fields(wm, camera):
    global _SYNCING
    _SYNCING = True
    try:
        p = core.parameters(camera)
        for key, value in zip(('x', 'y', 'z'), p.location):
            setattr(wm, 'wfrl_custom_'+key, value)
        for key in ('yaw', 'pitch', 'roll', 'fov', 'vfov'):
            setattr(wm, 'wfrl_custom_'+key, getattr(p, key))
        wm.wfrl_custom_has_focal = p.focal_length_mm_record is not None
        wm.wfrl_custom_focal = p.focal_length_mm_record or 35.
        wm.wfrl_custom_long_edge = p.output_long_edge_px
        wm.wfrl_custom_near, wm.wfrl_custom_far = p.clip_near_m, p.clip_far_m
        wm.wfrl_custom_mount_mode = camera.get('custom_mount_mode', 'SURFACE')
        wm.wfrl_custom_research_confirmed = camera.get('custom_research_confirmed', False)
        wm.wfrl_custom_label = camera.get('custom_label', '')
        anchor = core.anchor(camera)
        if anchor:
            wm.wfrl_custom_distance = anchor.distance
    finally:
        _SYNCING = False


def fields_changed(wm, context):
    if _SYNCING or not _ACTIVE or _ACTIVE.stage in _VIEWING:
        return
    op = _ACTIVE
    diagnostics.record('t1', input='numeric', slot=op.slot)
    try:
        params = _parameters(wm)
        if op.stage == 'INPUT':
            op.ready = False
            op.error = ''
            return
        op.camera['custom_label'] = wm.wfrl_custom_label
        if wm.wfrl_custom_mount_mode == 'RESEARCH':
            core.apply_research(op.scene, op.camera, params, wm.wfrl_custom_research_confirmed)
            op.ready, op.error = True, ''
            op.area.tag_redraw()
            return
        old = core.parameters(op.camera)
        if any(abs(a-b) > 1e-7 for a,b in zip(params.location, old.location)):
            anchors = core.validate_position(op.scene, params.location)
            op.candidates = anchors
            if len(anchors) > 1:
                op.ready = False
                op.error = '多个候选安装面：请在外部视图选择锚点'
                op.stage = 'INPUT'
                op.external()
                return
            core.apply_parameters(op.camera, params, anchor=anchors[0])
        else:
            core.apply_parameters(op.camera, params)
        op.ready = core.anchor(op.camera) is not None
        op.error = '' if op.ready else '请先点击机舱外壳选择有效安装位置'
        op.area.tag_redraw()
    except (ValueError, RuntimeError) as exc:
        op.ready, op.error = False, str(exc)


def distance_changed(wm, context):
    if _SYNCING or not _ACTIVE or _ACTIVE.stage not in {'PLACE', 'AIM'}:
        return
    try:
        core.set_distance(_ACTIVE.camera, wm.wfrl_custom_distance)
        sync_fields(wm, _ACTIVE.camera)
        _ACTIVE.ready, _ACTIVE.error = True, ''
    except (ValueError, RuntimeError) as exc:
        _ACTIVE.ready, _ACTIVE.error = False, str(exc)


class Available:
    @classmethod
    def poll(cls, context):
        return (context.area is not None and context.area.type == 'VIEW_3D'
                and core.is_available(context.scene))


class WFRL_OT_CustomCamera(Available, bpy.types.Operator):
    bl_idname = 'wfrl.custom_camera'
    bl_label = '自定义固定相机'
    mode: EnumProperty(items=[(v, v, '') for v in ('PLACE', 'INPUT', 'EDIT', 'WATCH', 'LAYOUT')])

    def invoke(self, context, event):
        global _ACTIVE
        if context.scene.get('wfrl_stacked_camera_rig') and self.mode in {'PLACE', 'INPUT'}:
            self.report({'WARNING'}, '请先整体安装盒体，再调整各相机俯仰角')
            return {'CANCELLED'}
        if _ACTIVE and _ACTIVE.stage not in _VIEWING:
            self.report({'WARNING'}, '请先确认或取消当前调整')
            return {'CANCELLED'}
        if self.mode not in _VIEWING:
            reason = installation_block_reason(context.scene)
            if reason:
                self.report({'WARNING'}, reason)
                return {'CANCELLED'}
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            self.report({'WARNING'}, '请先完成或取消图像采集')
            return {'CANCELLED'}
        core.migrate_scene(context.scene)
        previous = _ACTIVE
        self.area, self.scene, self.wm = context.area, context.scene, context.window_manager
        self.window = context.window
        self.region = next(r for r in self.area.regions if r.type == 'WINDOW')
        self.slot = self.wm.wfrl_custom_slot
        self.snapshot = ViewportSnapshot(self.area)
        self.external_snapshot = previous.external_snapshot if previous and previous.area == self.area else self.snapshot
        self.return_stage = previous.stage if previous and previous.area == self.area else None
        if previous:
            previous.finish(restore=False)
        # The legacy controller must not also process the same mouse events.
        from . import gimbal
        if gimbal._ACTIVE:
            gimbal._ACTIVE.finish()
        self.closed, self.ready, self.error = False, False, ''
        self.drag, self.stick, self.last = None, (0, 0), (0, 0)
        self.hover, self.candidates, self.press = None, [], None
        self.session = None
        self.preview = preview.Preview()
        try:
            if self.mode in _VIEWING:
                self.camera = core.get_camera(self.scene, self.slot)
                if not self.camera and self.mode == 'WATCH':
                    raise ValueError('此槽位未安装相机')
                self.stage, self.ready = self.mode, True
                if self.mode == 'LAYOUT':
                    self.external()
                else:
                    self.show_camera()
            else:
                self.session = EditSession(self.scene, self.slot, self.area, self.window, self.wm)
                self.camera = self.session.begin()
                self.ready = self.session.original_camera is not None
                self.stage = 'AIM' if self.mode == 'EDIT' else self.mode
                if self.stage == 'AIM':
                    self.show_camera()
                else:
                    self.external()
            if self.camera:
                sync_fields(self.wm, self.camera)
            self.identity = session_identity(self.scene)
            self.last_time = time.monotonic()
            self.timer = self.wm.event_timer_add(.025, window=self.window)
            self.handle = bpy.types.SpaceView3D.draw_handler_add(self.draw_overlay, (), 'WINDOW', 'POST_PIXEL')
            _ACTIVE = self
            self.wm.modal_handler_add(self)
            self.area.tag_redraw()
            return {'RUNNING_MODAL'}
        except (ValueError, RuntimeError) as exc:
            if self.session:
                self.session.cancel()
            self.preview.free()
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
        self.camera = core.get_camera(self.scene, slot)
        if self.camera:
            sync_fields(self.wm, self.camera)
        self.area.tag_redraw()

    def return_to_view(self, confirmed=False):
        self.preview.free()
        self.camera = core.get_camera(self.scene, self.slot)
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
        center = parent.matrix_world @ Vector((0, 0, 1.5))
        eye = parent.matrix_world @ Vector((12, -13, 9))
        rv.view_perspective, rv.view_location = 'PERSP', center
        rv.view_rotation = (center-eye).to_track_quat('-Z', 'Y')
        rv.view_distance = (center-eye).length
        self.area.spaces.active.lock_camera = False

    def finish(self, restore=True, resume=True):
        global _ACTIVE
        if getattr(self, 'closed', True):
            return
        if self.session and not self.session.closed:
            self.session.cancel(restore=resume)
        if restore:
            self.external_snapshot.restore()
        if getattr(self, 'timer', None):
            try:
                self.wm.event_timer_remove(self.timer)
            except (ReferenceError, RuntimeError):
                pass
            self.timer = None
        if getattr(self, 'handle', None):
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
            except (ReferenceError, RuntimeError, ValueError):
                pass
            self.handle = None
        try:
            self.area.header_text_set(None)
            self.area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass
        self.preview.free()
        self.closed, self.drag = True, None
        if _ACTIVE is self:
            _ACTIVE = None

    def cancel(self, context):
        self.finish()

    def geometry(self):
        x,y,width,height = preview.available_rectangle(self.area,self.region)
        ui = max(1.,bpy.context.preferences.system.ui_scale)
        return x+width-42*ui, y+height-89*ui, 18*ui

    def inside(self, event):
        for r in self.area.regions:
            if r.type != 'WINDOW' and r.width > 1 and r.x <= event.mouse_x < r.x+r.width and r.y <= event.mouse_y < r.y+r.height:
                return False
        r = self.region
        return r.x <= event.mouse_x < r.x+r.width and r.y <= event.mouse_y < r.y+r.height

    def change(self, dx=0, dy=0, zoom=0):
        diagnostics.record('t1', input='direction', slot=self.slot, dx=dx, dy=dy, zoom=zoom)
        p = core.parameters(self.camera)
        yaw, pitch = core.screen_direction_delta(p.yaw, p.pitch, p.roll, dx, dy)
        h, v = p.fov, p.vfov
        if zoom and self.wm.wfrl_custom_linked_zoom:
            try:
                h, v = projection.linked_fov(h, v, math.exp(zoom * .025))
            except ValueError:
                return
        core.apply_parameters(self.camera, replace(p, yaw=yaw, pitch=pitch, fov=h, vfov=v))
        sync_fields(self.wm, self.camera)
        self.area.tag_redraw()

    def pick(self, x, y):
        from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
        rv = self.area.spaces.active.region_3d
        return core.raycast_surface(self.scene, region_2d_to_origin_3d(self.region, rv, (x,y)),
            region_2d_to_vector_3d(self.region, rv, (x,y)), bpy.context.evaluated_depsgraph_get())

    def modal(self, context, event):
        if self.closed:
            return {'FINISHED'}
        try:
            if context.window != self.window:
                return {'PASS_THROUGH'}
            valid = (self.window in list(self.wm.windows) and self.window.scene == self.scene
                     and self.area in list(self.window.screen.areas) and self.area.type == 'VIEW_3D'
                     and session_identity(self.scene) == self.identity)
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
                if self.stage in _VIEWING:
                    self.finish()
                else:
                    self.cancel_edit()
                return {'FINISHED'} if self.closed else {'RUNNING_MODAL'}
            if event.type == 'TIMER':
                if getattr(event, 'timer', self.timer) != self.timer:
                    return {'PASS_THROUGH'}
                now = time.monotonic()
                dt, self.last_time = min(now-self.last_time, .1), now
                if self.stage not in _VIEWING:
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
                    self.show_camera()
                self.area.tag_redraw()
                return {'PASS_THROUGH'}
            from .. import custom_camera_capture
            if custom_camera_capture.active():
                return {'PASS_THROUGH'}
            inside = self.inside(event)
            x,y = event.mouse_x-self.region.x, event.mouse_y-self.region.y
            if self.stage not in _VIEWING and event.type in {'SPACE', 'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW'} and inside:
                return {'RUNNING_MODAL'}
            if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
                if self.drag:
                    diagnostics.record('drag_release', slot=self.slot)
                    self.drag, self.stick = None, (0,0)
                    return {'RUNNING_MODAL'}
                if self.stage == 'PLACE' and self.press:
                    px,py = self.press
                    self.press = None
                    if inside and math.hypot(x-px,y-py) < 5:
                        anchor = self.pick(x,y)
                        if anchor:
                            try:
                                core.place_on_surface(self.camera, anchor, self.wm.wfrl_custom_distance)
                                sync_fields(self.wm, self.camera)
                                self.ready, self.error = True, ''
                            except ValueError as exc:
                                self.ready, self.error = False, str(exc)
                        else:
                            self.error = '请选择 T1 机舱本体外壳表面'
                    return {'RUNNING_MODAL'}
            if event.type == 'MOUSEMOVE' and self.drag:
                if self.drag == 'stick':
                    cx,cy,r = self.geometry()
                    dx,dy = (x-cx)/r,(y-cy)/r
                    length = max(1, math.hypot(dx,dy))
                    self.stick = dx/length,dy/length
                else:
                    self.change((x-self.last[0])*(.04 if event.shift else .2),(y-self.last[1])*(.04 if event.shift else .2))
                self.last = (x,y)
                return {'RUNNING_MODAL'}
            if not inside:
                return {'PASS_THROUGH'}
            if self.stage not in _VIEWING and event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                ui = max(1., context.preferences.system.ui_scale)
                buttons = preview.action_buttons(preview.available_rectangle(self.area, self.region), ui)
                for action, button in zip(('CONFIRM', 'CANCEL'), buttons):
                    if preview.contains(button, x, y):
                        bpy.ops.wfrl.custom_camera_action(action=action)
                        return {'RUNNING_MODAL'}
            if self.stage == 'WATCH' and event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                ui = max(1., context.preferences.system.ui_scale)
                rect = preview.available_rectangle(self.area, self.region)
                if preview.contains(preview.close_button(rect, ui), x, y):
                    self.finish()
                    return {'FINISHED'}
            if self.stage == 'LAYOUT':
                return {'PASS_THROUGH'}
            if self.stage in {'PLACE', 'INPUT'}:
                if event.type in {'MIDDLEMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE'} or event.alt:
                    self.press = None
                if event.type == 'MOUSEMOVE':
                    self.hover = self.pick(x,y) if self.stage == 'PLACE' else None
                    self.area.tag_redraw()
                if self.stage == 'PLACE' and event.type == 'LEFTMOUSE' and event.value == 'PRESS' and not (event.alt or event.ctrl or event.shift):
                    self.press = (x,y)
                    return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}
            if self.stage == 'AIM':
                if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
                    cx,cy,r = self.geometry()
                    self.drag = 'stick' if math.hypot(x-cx,y-cy) <= r else 'look'
                    self.last = (x,y)
                    if self.drag == 'stick':
                        self.stick = ((x-cx)/r,(y-cy)/r)
                    return {'RUNNING_MODAL'}
                if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                    self.change(zoom=-3 if event.type == 'WHEELUPMOUSE' else 3)
                    return {'RUNNING_MODAL'}
            # Native view operations, including keymap alternatives, may never move
            # a mounted view. N/T and sidebar clicks remain available to exit it.
            if self.stage in _VIEWING and event.type == 'SPACE':
                return {'PASS_THROUGH'}
            if event.type not in {'N', 'T', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'WINDOW_DEACTIVATE'}:
                return {'RUNNING_MODAL'}
            return {'PASS_THROUGH'}
        except (ReferenceError, RuntimeError, ValueError, KeyError):
            self.finish(restore=False, resume=False)
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
        if self.closed or bpy.context.area != self.area or preview._DRAWING:
            return
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            return
        if self.stage in {'AIM', 'WATCH'}:
            cams = [self.camera]
            hint = self.state_text() if self.stage == 'AIM' else ''
            self.preview.draw(bpy.context, cams, slot=self.slot, hint=hint,
                              closable=self.stage != 'AIM')
            if self.stage in _VIEWING:
                return
        preview.draw_controls(bpy.context, self.state_text(), editing=self.stage not in _VIEWING,
            confirmable=self.ready and self.stage == 'AIM', reason=self.error or (
                '请先确定位置并进入方向阶段' if self.stage != 'AIM' else ''),
            external=self.stage not in {'AIM', 'WATCH'})
        import blf
        import gpu
        from gpu_extras.batch import batch_for_shader
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        def lines(points, color):
            batch = batch_for_shader(shader, 'LINES', {'pos': points})
            shader.bind(); shader.uniform_float('color', color); batch.draw(shader)
        def circle(x,y,r,color):
            points = [(x+r*math.cos(i*math.tau/48), y+r*math.sin(i*math.tau/48)) for i in range(49)]
            lines([p for a,b in zip(points,points[1:]) for p in (a,b)], color)
        def label(x,y,text,color=(1,1,1,1)):
            blf.size(0,13 * max(1., bpy.context.preferences.system.ui_scale)); blf.color(0,*color); blf.position(0,x,y,0); blf.draw(0,text)
        def project(local):
            parent = self.scene.objects.get('WFRL.Turbine.T1.YawRoot')
            return location_3d_to_region_2d(self.region,self.area.spaces.active.region_3d,parent.matrix_world @ Vector(local))
        gpu.state.blend_set('ALPHA')
        try:
            if self.stage == 'AIM':
                cx,cy,r = self.geometry()
                circle(cx,cy,r,(.2,.8,1,1))
                circle(cx+self.stick[0]*r*.7,cy+self.stick[1]*r*.7,12,(1,.6,.1,1))
                # Direction stick stays in the reserved action strip.
            else:
                origin = project((0,0,0))
                if origin:
                    label(*origin,'T1.YawRoot 原点')
                    for axis,text,color in [((3,0,0),'+X 尾部',(1,.3,.3,1)),((0,3,0),'+Y 右侧',(.3,1,.3,1)),((0,0,3),'+Z 上方',(.3,.6,1,1))]:
                        end = project(axis)
                        if end:
                            lines([origin,end],color); label(*end,text,color)
                anchors = list(self.candidates)
                if self.hover:
                    anchors.append(self.hover)
                anchor = core.anchor(self.camera) if self.camera else None
                if anchor and self.ready:
                    anchors.append(anchor)
                for i,a in enumerate(anchors):
                    p = project(a.point)
                    end = project(Vector(a.point)+Vector(a.normal)*.8)
                    if p:
                        circle(*p,7,(1,.6,.1,1))
                        if end: lines([p,end],(1,.6,.1,1))
                        label(p.x+10,p.y+10, '锚点 '+str(i+1) if self.candidates else '安装落点')
                if self.ready and self.camera:
                    p = project(core.parameters(self.camera).location)
                    if p: circle(*p,4,(.2,1,1,1)); label(p.x+8,p.y-18,'镜头中心')
            if self.stage in {'LAYOUT', 'PLACE', 'INPUT'}:
                from bpy_extras.view3d_utils import location_3d_to_region_2d
                cameras = [core.get_camera(self.scene, slot) for slot in core.SLOTS]
                if self.stage != 'LAYOUT':
                    cameras[self.slot-1] = self.camera if self.ready else None
                for cam in filter(None, cameras):
                    matrix = cam.matrix_world
                    p = core.parameters(cam)
                    selected = int(cam['wfrl_custom_slot']) == self.slot
                    color = (.3,.85,1,1) if selected else (.8,.66,.35,.8)
                    def world_project(xyz):
                        return location_3d_to_region_2d(self.region, self.area.spaces.active.region_3d, matrix @ Vector(xyz))
                    center = world_project((0,0,0))
                    if center:
                        circle(*center,8 if selected else 5,color)
                        label(center.x+12,center.y+12,f'C{cam["wfrl_custom_slot"]}',color)
                    if self.wm.wfrl_custom_show_axis:
                        end = world_project((0,0,-8))
                        if center and end: lines([center,end],color)
                    if self.wm.wfrl_custom_show_frustum and (self.stage == 'LAYOUT' or selected):
                        corners = [world_project(v) for v in projection.frustum_corners(p.fov,p.vfov,8)]
                        for a,b in zip(corners,corners[1:]+corners[:1]):
                            if a and b: lines([a,b],color)
                        for end in corners:
                            if center and end: lines([center,end],(*color[:3],.4))
            hint = {'AIM':f'C{self.slot} · 调整方向：拖动 / 摇杆；Shift 细调；Esc 取消',
                    'PLACE':f'C{self.slot} · 为此相机单击机舱选点；中键旋转；Esc 取消',
                    'INPUT':f'C{self.slot} · 在 View 面板输入此相机的位置；Esc 取消',
                    'LAYOUT':f'当前选择 C{self.slot} · 在 View 面板单独设置位置；Esc 退出'}
            if self.stage != 'AIM':
                label(20,35,hint[self.stage])
        except (ReferenceError, RuntimeError):
            pass
        finally:
            gpu.state.blend_set('NONE')


class WFRL_OT_CustomAction(bpy.types.Operator):
    bl_idname = 'wfrl.custom_camera_action'
    bl_label = '自定义相机操作'
    action: EnumProperty(items=[(v,v,'') for v in ('NEXT','BACK','CONFIRM','CANCEL','EXIT','CLEAR','COPY','SLOT','VALIDATE','ANCHOR','ENABLE','UNDO')])
    slot: IntProperty(default=1, min=1, max=core.MAX_CUSTOM_CAMERAS)
    index: IntProperty(default=0)

    def execute(self, context):
        op = _ACTIVE
        wm,scene = context.window_manager,context.scene
        editing = op is not None and op.stage not in _VIEWING
        stacked = bool(scene.get('wfrl_stacked_camera_rig'))
        if stacked:
            box = layout.box();box.enabled = not editing
            box.label(text='1 · 安装整个盒体')
            box.operator('wfrl.stacked_box_pick', text='在机舱上选择位置', icon='PIVOT_CURSOR')
            box.operator('wfrl.stacked_box_position', text='整体 XYZ / 盒体朝向')
            layout.label(text='2 · 选择相机，调整俯仰角')
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            self.report({'WARNING'}, '图像采集中，请先取消或等待完成')
            return {'CANCELLED'}
        try:
            if self.action in {'CLEAR','ENABLE'} and installation_block_reason(scene):
                raise ValueError(installation_block_reason(scene))
            if self.action in {'SLOT','CLEAR'} and editing:
                raise ValueError('请先确认或取消当前调整')
            if self.action == 'SLOT':
                wm.wfrl_custom_slot = self.slot
                if op and op.stage in _VIEWING:
                    op.select_slot(self.slot)
                    if not op.camera and op.stage == 'WATCH':
                        op.finish()
            elif self.action == 'ENABLE' and not editing:
                camera = core.get_camera(scene, wm.wfrl_custom_slot)
                if camera:
                    core.set_enabled(scene, wm.wfrl_custom_slot, not camera.get('custom_enabled', True))
            elif self.action == 'UNDO':
                history.undo(scene)
            elif self.action == 'CLEAR':
                core.clear_slot(scene,wm.wfrl_custom_slot)
                if op and not op.closed:
                    op.select_slot(wm.wfrl_custom_slot)
            elif self.action == 'COPY':
                camera = op.camera if editing else core.get_camera(scene,wm.wfrl_custom_slot)
                if camera:
                    wm.clipboard = core.copy_text(scene,camera,wm.wfrl_custom_slot)
            elif self.action == 'EXIT' and op:
                op.finish()
            elif self.action == 'CANCEL' and editing:
                op.cancel_edit()
            elif self.action == 'BACK' and editing:
                op.stage,op.drag = ('INPUT' if op.camera.get('custom_mount_mode') == 'RESEARCH' else 'PLACE'),None
                op.external()
            elif self.action == 'VALIDATE' and editing:
                params = _parameters(wm)
                core.validate_parameters(params)
                op.camera['custom_label'] = wm.wfrl_custom_label
                if wm.wfrl_custom_mount_mode == 'RESEARCH':
                    core.apply_research(scene, op.camera, params, wm.wfrl_custom_research_confirmed)
                    op.ready,op.error,op.stage = True,'','AIM'
                    sync_fields(wm,op.camera);op.show_camera()
                    return {'FINISHED'}
                # Validation must run before any draft transform changes.
                anchors = core.validate_position(scene,params.location)
                op.candidates = anchors
                if len(anchors) > 1:
                    op.error,op.ready = '请选择候选锚点；镜头中心和朝向保持输入值',False
                    op.external()
                else:
                    core.apply_parameters(op.camera,params,anchor=anchors[0])
                    op.ready,op.error,op.stage = True,'','AIM'
                    sync_fields(wm,op.camera); op.show_camera()
            elif self.action == 'ANCHOR' and editing:
                anchor = op.candidates[self.index]
                core.apply_parameters(op.camera,_parameters(wm),anchor=anchor)
                op.candidates=[]
                op.ready,op.error,op.stage=True,'','AIM'
                sync_fields(wm,op.camera); op.show_camera()
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


class WFRL_PT_CustomCameras(bpy.types.Panel):
    bl_label = '三相机 · T1 · 研发'
    bl_idname = 'WFRL_PT_custom_cameras'
    bl_space_type,bl_region_type,bl_category = 'VIEW_3D','UI','View'
    def draw(self,context):
        layout,wm,scene = self.layout,context.window_manager,context.scene
        op = _ACTIVE if _ACTIVE and _ACTIVE.scene == scene else None
        editing = op is not None and op.stage not in _VIEWING
        stacked = bool(scene.get('wfrl_stacked_camera_rig'))
        if stacked:
            box = layout.box();box.enabled = not editing
            box.label(text='1 · 安装整个盒体')
            box.operator('wfrl.stacked_box_pick', text='在机舱上选择位置', icon='PIVOT_CURSOR')
            box.operator('wfrl.stacked_box_position', text='整体 XYZ / 盒体朝向')
            layout.label(text='2 · 选择相机，调整俯仰角')
        row = layout.row(align=True); row.enabled = not editing
        for slot in core.SLOTS:
            installed = core.get_camera(scene, slot)
            button=row.operator('wfrl.custom_camera_action',text=f'C{slot}',
                icon='CAMERA_DATA' if installed else 'ADD',depress=wm.wfrl_custom_slot==slot)
            button.action,button.slot='SLOT',slot
        if not core.is_available(scene):
            layout.label(text='请先加载包含 T1 的演示场景',icon='INFO')
            return
        from .custom_camera_output import draw_output
        from .. import custom_camera_capture
        if custom_camera_capture.active():
            for part in custom_camera_capture.progress().split('；'):layout.label(text=part)
            layout.label(text='撤销不可用：请先结束采集', icon='INFO')
            layout.operator('wfrl.custom_capture_cancel', text='取消采集')
            return
        if editing:
            layout.label(text='撤销不可用：请先确认或取消草稿', icon='INFO')
        if not editing:
            views=layout.row(align=True)
            views.operator('wfrl.custom_camera',text='外部布局').mode='LAYOUT'
            views.operator('wfrl.native_camera_view',text='三路对照').mode='TRIPLE'
            if scene.get('wfrl_stacked_camera_rig'):
                layout.label(text='共用盒体 · 下 C1 / 中 C2 / 上 C3')
            if op:
                layout.operator('wfrl.custom_camera_action',text='退出相机视图 · Esc',icon='X').action='EXIT'
        block_reason = installation_block_reason(scene)
        if block_reason and not editing:
            for start in range(0,len(block_reason),22):layout.label(text=block_reason[start:start+22],icon='INFO' if start==0 else 'NONE')
        camera=op.camera if editing else core.get_camera(scene,wm.wfrl_custom_slot)
        layout.label(text=f'C{wm.wfrl_custom_slot} · '+('正在调整' if editing else '已安装' if camera else '待设置位置'))
        if op and op.stage in {'AIM', 'WATCH'} and getattr(op, 'frame_fits', True) is False:
            warning = layout.box()
            warning.alert = True
            warning.label(text='视口空间不足，请扩大视图', icon='INFO')
            warning.label(text='或收起侧栏以显示完整取景框')
        def action(text,key,container=layout):
            container.operator('wfrl.custom_camera_action',text=text).action=key
        if camera and not editing:
            action('参与三路预览与采集 ✓' if camera.get('custom_enabled', True) else '未参与三路预览与采集', 'ENABLE')
            layout.label(text='标签：'+camera.get('custom_label', ''))
        if editing:
            layout.prop(wm,'wfrl_custom_label',text='标签')
            if op.stage == 'INPUT':
                layout.prop(wm,'wfrl_custom_mount_mode',text='安装模式')
                if wm.wfrl_custom_mount_mode == 'RESEARCH':
                    layout.prop(wm,'wfrl_custom_research_confirmed',text='确认支架偏置 / 未验证安装')
            layout.label(text={'PLACE':'1 · 外部安装位置','INPUT':'1 · 输入镜头参数','AIM':'2 · 调整方向'}[op.stage])
            if op.stage=='INPUT':
                action('校验并预览','VALIDATE')
                for i,anchor in enumerate(op.candidates):
                    b=layout.operator('wfrl.custom_camera_action',text=f'选择锚点 {i+1} · {anchor.surface_name}')
                    b.action,b.index='ANCHOR',i
            elif op.stage=='PLACE':
                layout.prop(wm,'wfrl_custom_distance',text='离壳距离 (m)')
                row=layout.row();row.enabled=op.ready
                action('下一步：调整方向','NEXT',row)
            elif not stacked:
                action('上一步：安装位置','BACK')
            row=layout.row(align=True)
            confirm=row.row();confirm.enabled=op.ready and op.stage=='AIM'
            action('确认修改','CONFIRM',confirm);action('取消修改','CANCEL',row)
            if op.error:
                box=layout.box();box.alert=True
                # Keep errors readable in narrow sidebars.
                for start in range(0,len(op.error),24):box.label(text=op.error[start:start+24])
        elif camera:
            if not stacked:
                layout.operator('wfrl.custom_camera',text=f'更改 C{wm.wfrl_custom_slot} 安装位置',icon='PIVOT_CURSOR').mode='PLACE'
                layout.operator('wfrl.custom_camera',text='输入 XYZ 位置 / 参数',icon='DRIVER_DISTANCE').mode='INPUT'
            layout.operator('wfrl.custom_camera',text='调整俯仰角 / 方向与视场',icon='ORIENTATION_GIMBAL').mode='EDIT'
            layout.operator('wfrl.native_camera_view',text='查看单路画面',icon='VIEW_CAMERA').mode='WATCH'
            layout.operator('wfrl.custom_camera',text='精确材质预览（暂停检查）').mode='WATCH'
            if not stacked:action(f'清除 C{wm.wfrl_custom_slot}', 'CLEAR')
        else:
            layout.label(text='各槽位分别选点、分别保存',icon='INFO')
            layout.operator('wfrl.custom_camera',text=f'为 C{wm.wfrl_custom_slot} 选安装位置',icon='PIVOT_CURSOR').mode='PLACE'
            layout.operator('wfrl.custom_camera',text='输入 XYZ 位置 / 参数',icon='DRIVER_DISTANCE').mode='INPUT'
        box=layout.box();box.label(text='安装参数 · 相对 T1 机舱')
        if editing:
            for key,label in [('x','镜头 X (m)'),('y','镜头 Y (m)'),('z','镜头 Z (m)'),('pitch','俯仰角 (°)'),('yaw','水平角 (°)'),('roll','画面旋转角 (°)'),('fov','HFOV (°)'),('vfov','VFOV (°)')]:
                if stacked and key in {'x','y','z'}:continue
                row=box.row();row.enabled=not (op.stage=='AIM' and key in {'x','y','z'})
                row.prop(wm,'wfrl_custom_'+key,text=label)
        elif camera:
            p=core.parameters(camera)
            for key,value in zip(('X','Y','Z'),p.location):box.label(text=f'镜头 {key}：{value:.4f} m')
            for label,key in [('水平角','yaw'),('俯仰角','pitch'),('画面旋转角','roll'),('HFOV','fov'),('VFOV','vfov')]:box.label(text=f'{label}：{getattr(p,key):.3f}°')
        else:box.label(text='未安装')
        if editing:
            box.prop(wm,'wfrl_custom_linked_zoom',text='联动视场调整（滚轮）')
            box.prop(wm,'wfrl_custom_has_focal',text='记录焦距（不影响画面）')
            if wm.wfrl_custom_has_focal:box.prop(wm,'wfrl_custom_focal',text='记录焦距 (mm)')
            box.prop(wm,'wfrl_custom_long_edge',text='输出长边 (px)')
            box.prop(wm,'wfrl_custom_near',text='近裁剪 (m)')
            box.prop(wm,'wfrl_custom_far',text='远裁剪 (m)')
        if camera:
            p=core.parameters(camera)
            try:
                w,h=projection.resolution(p.fov,p.vfov,p.output_long_edge_px)
                box.label(text=f'独立输出：{w} × {h} px')
                axis=projection.optical_axis(p.yaw,p.pitch)
                box.label(text='光轴：'+', '.join(f'{v:.3f}' for v in axis))
                box.label(text=f'记录焦距：{p.focal_length_mm_record or "未填写"} mm')
                if min(p.fov,p.vfov)<5 or max(p.fov,p.vfov)>150 or max(w,h)/min(w,h)>8:
                    box.label(text='极端视场 / 画幅：请核对研发用途',icon='INFO')
                warning=camera.get('custom_mount_warning','')
                if warning:box.label(text=warning,icon='INFO')
            except ValueError as exc:box.label(text=str(exc),icon='ERROR')
        undo_row = layout.row()
        undo_row.enabled = not editing and not block_reason and bool(history.HISTORY.entries)
        undo_row.operator('wfrl.custom_camera_action', text='撤销上次相机修改', icon='LOOP_BACK').action='UNDO'
        notice = ('撤销：' + history.HISTORY.entries[-1].description) if history.HISTORY.entries else history.HISTORY.notice or '暂无可撤销的相机修改'
        for start in range(0, len(notice), 22):
            layout.label(text=notice[start:start+22])
        layout.prop(wm, 'wfrl_custom_engineering', text='卡片显示工程参数')
        row=layout.row(align=True)
        row.prop(wm,'wfrl_custom_show_axis',text='光轴')
        row.prop(wm,'wfrl_custom_show_frustum',text='视锥')
        # Capture settings live in their own collapsible View panel.
        row=box.row();row.enabled=camera is not None and (not editing or op.ready)
        action('复制参数','COPY',row)
        box.operator('wfrl.custom_camera_coordinates',text='坐标说明')


@persistent
def cancel_on_load(_unused=None):
    if _ACTIVE:
        _ACTIVE.finish(restore=False,resume=False)
    from .. import custom_camera_capture
    custom_camera_capture.shutdown(restore=False)
    preview.shutdown()
    history.HISTORY.clear('加载文件，相机撤销历史已清理')


def lifecycle_watchdog():
    # Window-close/area replacement can remove modal event delivery entirely.
    # Keep cleanup independent of the viewport event stream.
    from .. import custom_camera_capture
    if custom_camera_capture.active() and not custom_camera_capture._ACTIVE.same_session():
        custom_camera_capture.shutdown(restore=False)
    if _ACTIVE:
        try:
            valid = (_ACTIVE.window in list(_ACTIVE.wm.windows)
                and _ACTIVE.area in list(_ACTIVE.window.screen.areas)
                and _ACTIVE.area.type == 'VIEW_3D'
                and _ACTIVE.window.scene == _ACTIVE.scene
                and session_identity(_ACTIVE.scene) == _ACTIVE.identity)
        except (ReferenceError, RuntimeError):
            valid = False
        if not valid:
            _ACTIVE.finish(restore=False,resume=False)
    if history.HISTORY.entries and getattr(bpy.context, 'scene', None) is not None:
        history.status(bpy.context.scene)
    return .25


@persistent
def migrate_loaded(_unused=None):
    if not bpy.app.timers.is_registered(lifecycle_watchdog):
        bpy.app.timers.register(lifecycle_watchdog, first_interval=.25)
    for scene in bpy.data.scenes:
        if core.is_available(scene):
            core.migrate_scene(scene)


def register_properties():
    from .. import native_camera_views
    native_camera_views.register()
    if migrate_loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(migrate_loaded)
    # Blender restricts scene access while enabling an extension. Migrate once
    # registration has finished; load_post continues to handle later file loads.
    if not bpy.app.timers.is_registered(migrate_loaded):
        bpy.app.timers.register(migrate_loaded, first_interval=0.)
    if cancel_on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.insert(0,cancel_on_load)
    bpy.types.WindowManager.wfrl_custom_slot=IntProperty(default=1,min=1,max=core.MAX_CUSTOM_CAMERAS,options={'SKIP_SAVE'})
    for key in _FIELDS:
        default={'fov':75.,'vfov':projection.DEFAULT_VFOV,'distance':.1,'focal':35.,'near':.005,'far':30000.}.get(key,0.)
        prop=FloatProperty(default=default,precision=4 if key in {'x','y','z','distance'} else 3,
            options={'SKIP_SAVE'},update=distance_changed if key=='distance' else fields_changed)
        setattr(bpy.types.WindowManager,'wfrl_custom_'+key,prop)
    for key,prop in {
        'engineering':BoolProperty(default=False,options={'SKIP_SAVE'}),
        'linked_zoom':BoolProperty(default=False,options={'SKIP_SAVE'}),
        'has_focal':BoolProperty(default=False,options={'SKIP_SAVE'},update=fields_changed),
        'show_axis':BoolProperty(default=True,options={'SKIP_SAVE'}),
        'show_frustum':BoolProperty(default=True,options={'SKIP_SAVE'}),
        'research_confirmed':BoolProperty(default=False,options={'SKIP_SAVE'},update=fields_changed),
        'label':StringProperty(default='',options={'SKIP_SAVE'},update=fields_changed),
        'mount_mode':EnumProperty(items=[('SURFACE','表面安装',''),('RESEARCH','研发坐标','')],options={'SKIP_SAVE'},update=fields_changed),
        'long_edge':IntProperty(default=1920,min=16,options={'SKIP_SAVE'},update=fields_changed),
    }.items():setattr(bpy.types.WindowManager,'wfrl_custom_'+key,prop)
    from .custom_camera_output import register_settings
    register_settings()
    if preview.changed not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(preview.changed)


def unregister_properties():
    from .. import native_camera_views
    native_camera_views.unregister()
    if bpy.app.timers.is_registered(migrate_loaded):
        bpy.app.timers.unregister(migrate_loaded)
    if bpy.app.timers.is_registered(lifecycle_watchdog):
        bpy.app.timers.unregister(lifecycle_watchdog)
    if _ACTIVE:
        _ACTIVE.finish(restore=True,resume=True)
    if cancel_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(cancel_on_load)
    if migrate_loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(migrate_loaded)
    from .. import custom_camera_capture
    custom_camera_capture.shutdown()
    preview.shutdown()
    if preview.changed in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(preview.changed)
    from .custom_camera_output import unregister_settings
    unregister_settings()
    history.HISTORY.clear()
    core.cleanup_retired()
    for key in (*_FIELDS,'engineering','slot','linked_zoom','has_focal','show_axis','show_frustum','research_confirmed','label','mount_mode','long_edge'):
        name='wfrl_custom_'+key
        if hasattr(bpy.types.WindowManager,name):delattr(bpy.types.WindowManager,name)


from .custom_camera_output import CLASSES as OUTPUT_CLASSES

from ..native_camera_views import CLASSES as NATIVE_CLASSES

from .stacked_camera_rig import CLASSES as BOX_CLASSES

CLASSES=BOX_CLASSES + NATIVE_CLASSES + OUTPUT_CLASSES + (WFRL_OT_CustomCamera,WFRL_OT_CustomAction,WFRL_OT_CustomCoordinates,WFRL_PT_CustomCameras)
