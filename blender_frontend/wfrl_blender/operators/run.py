"""Enqueue controls; the backend alone confirms lifecycle transitions."""
import bpy
from .. import runtime
from ..preferences import get_preferences


class WFRL_OT_BackendRun(bpy.types.Operator):
    bl_idname = 'wfrl.backend_run'
    bl_label = 'Backend Control'
    action: bpy.props.StringProperty(default='start')

    def execute(self, context):
        arguments = {}
        try:
            if self.action == 'start':
                from .workflow import launch_options
                arguments = {'mode': runtime.desired_mode(),
                             'options': launch_options(context, runtime.desired_mode())}
            runtime.send_command(self.action, arguments)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


CLASSES = (WFRL_OT_BackendRun,)
