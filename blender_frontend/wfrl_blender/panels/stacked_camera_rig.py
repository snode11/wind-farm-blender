"""Box placement before per-camera pitch adjustment."""
import math
import bpy
from bpy.props import FloatProperty, FloatVectorProperty
from .. import stacked_camera_rig as rig
from .. import custom_cameras as core


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
        self.layout.label(text='三台相机一起移动，俯仰角保持不变')
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
    bl_label = '在机舱上选择盒体位置'
    offset: FloatProperty(name='光心离安装面 (m)', default=.40, min=.20, max=2.)

    @classmethod
    def poll(cls, context):
        return available(context) and context.area and context.area.type=='VIEW_3D'

    def invoke(self, context, event):
        from .custom_cameras import installation_block_reason
        reason=installation_block_reason(context.scene)
        if reason:self.report({'ERROR'},reason);return {'CANCELLED'}
        finish_view()
        self.area=context.area;self.scene=context.scene
        self.region=next(r for r in self.area.regions if r.type=='WINDOW')
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        # Reuse the existing external nacelle view, then detach its controller.
        bpy.ops.wfrl.custom_camera('INVOKE_DEFAULT',mode='LAYOUT')
        from . import custom_cameras as panel
        if panel._ACTIVE:panel._ACTIVE.finish(restore=False,resume=False)
        self.area.header_text_set('点击 T1 机舱外壳安装整个盒体；Esc / 右键取消；中键可转动视图')
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if context.scene != self.scene or not self.scene.objects.get(rig.PREFIX):
            self.area.header_text_set(None);return {'CANCELLED'}
        if event.type in {'ESC','RIGHTMOUSE'} and event.value=='PRESS':
            self.area.header_text_set(None);return {'CANCELLED'}
        if event.type=='LEFTMOUSE' and event.value=='PRESS':
            from bpy_extras.view3d_utils import region_2d_to_origin_3d, region_2d_to_vector_3d
            xy=(event.mouse_x-self.region.x,event.mouse_y-self.region.y)
            if not (0<=xy[0]<self.region.width and 0<=xy[1]<self.region.height):return {'RUNNING_MODAL'}
            rv=self.area.spaces.active.region_3d
            hit=core.raycast_surface(self.scene,region_2d_to_origin_3d(self.region,rv,xy),region_2d_to_vector_3d(self.region,rv,xy))
            if hit is None:
                self.report({'WARNING'},'请选择 T1 机舱外壳');return {'RUNNING_MODAL'}
            center=[a+b*self.offset for a,b in zip(hit.point,hit.normal)]
            yaw=rig.pose(self.scene)['housing_yaw_deg']
            if math.hypot(hit.normal[0],hit.normal[1])>.01:
                yaw=math.degrees(math.atan2(hit.normal[1],hit.normal[0]))+90
            try:rig.move_box(self.scene,center,yaw)
            except (ValueError,RuntimeError) as exc:
                self.report({'ERROR'},str(exc));return {'RUNNING_MODAL'}
            self.area.header_text_set(None)
            self.report({'INFO'},'盒体已安装；接下来选择相机调整俯仰角')
            return {'FINISHED'}
        return {'PASS_THROUGH'}


CLASSES=(WFRL_OT_BoxPosition,WFRL_OT_BoxPick)
