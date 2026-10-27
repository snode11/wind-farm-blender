"""Connection and environment operators."""
import bpy
from .. import runtime
from ..preferences import get_preferences

class WFRL_OT_Mode(bpy.types.Operator):
    bl_idname = 'wfrl.connection_mode'
    bl_label = 'Select WFRL Mode'
    mode: bpy.props.StringProperty(default='demo')

    @classmethod
    def poll(cls, context): return runtime.configuration_editable()

    def execute(self, context):
        try:
            runtime.select_mode(self.mode)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_Connect(bpy.types.Operator):
    bl_idname = 'wfrl.bridge_connect'
    bl_label = 'Connect / Reconnect'

    @classmethod
    def poll(cls, context):
        state = runtime.get_state()
        ready = state.connection == 'DISCONNECTED' or (
            state.connection == 'LOCAL DEMO' and runtime.configuration_editable())
        return ready and runtime.progress() not in {'Connecting', 'Awaiting handshake'}

    def execute(self, context):
        prefs = get_preferences(context)
        try:
            runtime.connect(prefs.port if prefs else 8765)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_Disconnect(bpy.types.Operator):
    bl_idname = 'wfrl.bridge_disconnect'
    bl_label = 'Disconnect'

    def execute(self, context):
        runtime.disconnect(); return {'FINISHED'}


class WFRL_OT_ConnectNewBackend(bpy.types.Operator):
    bl_idname = 'wfrl.bridge_connect_new'
    bl_label = '连接新后端身份…'
    bl_description = '明确放弃旧连接身份并连接新后端；不会停止旧后端作业'
    confirm_forget: bpy.props.BoolProperty(name='我确认放弃旧连接身份；旧后端可能仍在运行', default=False,
                                         options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return runtime.can_connect_new_backend()

    def invoke(self, context, event):
        self.confirm_forget = False
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        ui = runtime.get_state()
        self.layout.label(text='忘记连接不会停止之前的后端或训练作业。', icon='ERROR')
        self.layout.label(text='旧作业状态保持未确认；请自行核对旧后端。')
        self.layout.label(text=f'旧会话：{ui.session_id}')
        if ui.run_id:
            self.layout.label(text=f'旧作业：{ui.run_id} · 最后状态 {ui.run_status}')
        self.layout.prop(self, 'confirm_forget')

    def execute(self, context):
        prefs = get_preferences(context)
        try:
            runtime.connect_new_backend(prefs.port if prefs else 8765, confirmed=self.confirm_forget)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}
        return {'FINISHED'}


class WFRL_OT_Health(bpy.types.Operator):
    bl_idname = 'wfrl.check_environment'
    bl_label = 'Check Environment'

    def execute(self, context):
        runtime.check_environment(get_preferences(context))
        return {'FINISHED'}


CLASSES = (WFRL_OT_Mode, WFRL_OT_Connect, WFRL_OT_Disconnect, WFRL_OT_ConnectNewBackend, WFRL_OT_Health)
