"""Box placement before per-camera pitch adjustment."""
import bpy
from bpy.props import FloatProperty, FloatVectorProperty, StringProperty
from .. import stacked_camera_rig as rig
from .. import custom_cameras as core

_ACTIVE = None


class Placement:
    """One transaction for picking, rotating, previewing and confirming a box."""
    def __init__(self, scene):
        from .custom_cameras import session_identity
        from .. import custom_camera_history as history
        self.scene, self.identity = scene, session_identity(scene)
        self.before = history.before_change(scene)
        self.originals = {slot:core.get_camera(scene, slot) for slot in core.SLOTS}
        self.model = core.model_identity(scene)
        self.frame = (scene.frame_current, scene.frame_subframe)
        self.anchor = None
        self.ready = False
        self.error = ''
        self.closed = False

    def same_session(self):
        from .custom_cameras import session_identity
        try:
            return session_identity(self.scene) == self.identity and core.model_identity(self.scene) == self.model
        except (ReferenceError,RuntimeError):
            return False

    def preview(self, anchor, spin, aim=0.):
        from .. import custom_camera_preview
        if not self.same_session(): raise ValueError('场景或模型已变化，请重新安装')
        proposed = rig.surface_pose(self.scene, anchor, spin, aim=aim)
        # Validate everything before changing any object; camera IDs stay stable.
        layout = rig.transformed_layout(self.scene, self.before[1], proposed)
        for record in layout['cameras']:
            params = core.CameraParameters(**record['parameters'])
            core.apply_research(self.scene, self.originals[record['slot_id']], params, confirmed=True)
        rig.apply_pose(self.scene, proposed)
        bpy.context.view_layer.update()
        custom_camera_preview.changed()
        self.anchor, self.ready, self.error = anchor, True, ''

    def finish(self, confirm=False):
        from .. import custom_camera_history as history
        if self.closed:return
        if confirm and not self.same_session():raise ValueError('场景或模型已变化，请重新安装')
        if self.same_session():
            if confirm:
                if not self.ready or self.error:raise ValueError('请先选择有效的安装面')
                history.committed(self.scene, self.before, '贴合安装整个盒体')
            else:
                core.restore_layout(self.scene, self.before[1])
        self.closed = True


def shutdown(restore=True):
    global _ACTIVE
    op = _ACTIVE
    if op:
        try:
            if restore:op.transaction.finish()
            else:op.transaction.closed = True
        finally:
            op.transaction.closed = True
            op.cleanup()


def spin_changed(wm, context):
    if _ACTIVE and _ACTIVE.transaction.anchor:
        _ACTIVE.update(_ACTIVE.transaction.anchor)


def register_settings():
    bpy.types.WindowManager.wfrl_box_spin = FloatProperty(
        name='贴面旋转 (°)', default=0., precision=1, soft_min=-180., soft_max=180.,
        options={'SKIP_SAVE'}, update=spin_changed)
    bpy.types.WindowManager.wfrl_box_aim = FloatProperty(
        name='盒体转向 (°)', description='支架保持贴合；盒体绕支架外端水平转向',
        default=0., precision=1, soft_min=-90., soft_max=90.,
        options={'SKIP_SAVE'}, update=spin_changed)


def unregister_settings():
    shutdown()
    for name in ('wfrl_box_spin', 'wfrl_box_aim'):
        if hasattr(bpy.types.WindowManager, name):
            delattr(bpy.types.WindowManager, name)


def available(context):
    from .custom_camera_output import editable
    return bool(context.scene.objects.get(rig.PREFIX) and editable(context))


def finish_view():
    from . import custom_cameras as panel
    from .. import native_camera_views
    native_camera_views.shutdown()
    if panel._ACTIVE:
        panel._ACTIVE.finish()


class WFRL_OT_BoxPosition(bpy.types.Operator):
    bl_idname = 'wfrl.stacked_box_position'
    bl_label = '整体安装盒体'
    center: FloatVectorProperty(name='盒体中心 XYZ (m)', size=3)
    yaw: FloatProperty(name='盒体水平朝向 (°)')

    @classmethod
    def poll(cls, context): return available(context)

    def invoke(self, context, event):
        state=rig.pose(context.scene)
        self.center=state['center'];self.yaw=state['housing_yaw_deg']
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.label(text='三台相机随盒体一起移动和转向')
        self.layout.label(text='自由坐标调整会解除支架贴合')
        self.layout.prop(self,'center')
        self.layout.prop(self,'yaw')

    def execute(self, context):
        try:
            finish_view()
            rig.move_box(context.scene, self.center, self.yaw)
            self.report({'INFO'}, '盒体已整体移动；现在可分别调整 C1–C3 俯仰角')
            return {'FINISHED'}
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc));return {'CANCELLED'}


class WFRL_OT_BoxPick(bpy.types.Operator):
    bl_idname = 'wfrl.stacked_box_pick'
    bl_label = '贴合安装 / 调整整个盒体'

    @classmethod
    def poll(cls, context):
        return available(context) and context.area and context.area.type=='VIEW_3D'

    def invoke(self, context, event):
        global _ACTIVE
        from .custom_cameras import installation_block_reason
        reason=installation_block_reason(context.scene)
        if reason:self.report({'ERROR'},reason);return {'CANCELLED'}
        finish_view()
        self.area=context.area;self.scene=context.scene;self.window=context.window;self.wm=context.window_manager
        self.region=next(r for r in self.area.regions if r.type=='WINDOW')
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        # Reuse the existing external nacelle view, then detach its controller.
        bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT',mode='LAYOUT')
        from . import custom_cameras as panel
        if panel._ACTIVE:panel._ACTIVE.finish(restore=False,resume=False)
        self.transaction=Placement(self.scene)
        mount=(rig.pose(self.scene) or {}).get('surface_mount', {})
        self.wm.wfrl_box_spin=mount.get('spin_deg',0.)
        self.wm.wfrl_box_aim=mount.get('aim_deg',0.)
        _ACTIVE=self
        if mount.get('type') == 'SUPPORT':
            self.update(core.SurfaceAnchor(tuple(mount['point']),tuple(mount['normal']),0.,mount['surface_name']))
        self.timer=self.wm.event_timer_add(.15,window=self.window)
        self.area.header_text_set('点击机舱安装支架；盒体保持外伸；侧栏调整方向；Enter 确认；Esc 取消')
        context.window_manager.modal_handler_add(self)
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def update(self, anchor):
        try:
            self.transaction.preview(anchor, self.wm.wfrl_box_spin, self.wm.wfrl_box_aim)
        except (ValueError,RuntimeError) as exc:
            self.transaction.error=str(exc)
        self.area.tag_redraw()

    def cleanup(self):
        global _ACTIVE
        if getattr(self,'timer',None):
            try:self.wm.event_timer_remove(self.timer)
            except (ReferenceError,RuntimeError):pass
            self.timer=None
        try:
            self.area.header_text_set(None);self.area.tag_redraw()
        except (ReferenceError,RuntimeError):pass
        if _ACTIVE == self:_ACTIVE=None

    def cancel(self, context):
        shutdown()

    def modal(self, context, event):
        if self.transaction.closed:return {'FINISHED'}
        if (context.scene != self.scene or not self.transaction.same_session()
                or self.area.type!='VIEW_3D'):
            shutdown(restore=self.transaction.same_session());return {'CANCELLED'}
        if event.type in {'ESC','RIGHTMOUSE'} and event.value=='PRESS':
            shutdown();return {'CANCELLED'}
        if event.type in {'RET','NUMPAD_ENTER'} and event.value=='PRESS':
            if self.transaction.ready and not self.transaction.error:
                self.transaction.finish(confirm=True);self.cleanup();return {'FINISHED'}
            return {'RUNNING_MODAL'}
        if event.type=='TIMER':
            if context.screen.is_animation_playing:bpy.ops.screen.animation_cancel(restore_frame=False)
            if (self.scene.frame_current,self.scene.frame_subframe)!=self.transaction.frame:
                shutdown();return {'CANCELLED'}
            return {'PASS_THROUGH'}
        if event.type in {'SPACE','LEFT_ARROW','RIGHT_ARROW','UP_ARROW','DOWN_ARROW','G','R','S','X','DEL','TAB'}:
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE','WHEELDOWNMOUSE'} and event.shift:
            self.wm.wfrl_box_spin += 5. if event.type=='WHEELUPMOUSE' else -5.
            return {'RUNNING_MODAL'}
        if event.type=='LEFTMOUSE' and event.value=='PRESS':
            from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
            xy=(event.mouse_x-self.region.x,event.mouse_y-self.region.y)
            if not (0<=xy[0]<self.region.width and 0<=xy[1]<self.region.height):return {'PASS_THROUGH'}
            # The sidebar overlays the WINDOW region; let its controls receive clicks.
            if any(r.type in {'UI','TOOLS'} and r.width>1 and r.x<=event.mouse_x<r.x+r.width
                   and r.y<=event.mouse_y<r.y+r.height for r in self.area.regions):return {'PASS_THROUGH'}
            rv=self.area.spaces.active.region_3d
            hit=rig.pick_surface(self.scene,region_2d_to_origin_3d(self.region,rv,xy),region_2d_to_vector_3d(self.region,rv,xy))
            if hit is None:
                self.report({'WARNING'},'请选择 T1 机舱外壳');return {'RUNNING_MODAL'}
            self.update(hit)
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


class WFRL_OT_BoxAction(bpy.types.Operator):
    bl_idname='wfrl.stacked_box_action'
    bl_label='调整盒体安装'
    action:StringProperty()

    def execute(self,context):
        op=_ACTIVE
        if not op:return {'CANCELLED'}
        if self.action=='CANCEL':shutdown()
        elif self.action=='CONFIRM':
            try:op.transaction.finish(confirm=True)
            except ValueError as exc:
                self.report({'ERROR'},str(exc));return {'CANCELLED'}
            op.cleanup()
        elif self.action in {'LEFT','RIGHT'}:
            op.wm.wfrl_box_spin += -15. if self.action=='LEFT' else 15.
        elif self.action in {'AIM_LEFT','AIM_RIGHT'}:
            op.wm.wfrl_box_aim += -15. if self.action=='AIM_LEFT' else 15.
        return {'FINISHED'}


CLASSES=(WFRL_OT_BoxPosition,WFRL_OT_BoxPick,WFRL_OT_BoxAction)
