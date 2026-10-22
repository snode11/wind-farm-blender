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


class WFRL_OT_Health(bpy.types.Operator):
    bl_idname = 'wfrl.check_environment'
    bl_label = 'Check Environment'

    def execute(self, context):
        runtime.check_environment(get_preferences(context))
        return {'FINISHED'}


CLASSES = (WFRL_OT_Mode, WFRL_OT_Connect, WFRL_OT_Disconnect, WFRL_OT_Health)
