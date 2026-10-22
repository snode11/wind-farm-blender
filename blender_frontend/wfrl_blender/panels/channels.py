"""Actual SceneRuntime subscriptions; unsupported sources reject requests."""
import bpy
from .. import runtime


class WFRL_PT_WorkflowChannels(bpy.types.Panel):
    bl_label = 'WFRL / SENSOR SUBSCRIPTIONS'
    bl_idname = 'WFRL_PT_workflow_channels'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        rows = getattr(runtime, 'channel_table', ())
        for topic, sensor, fidelity, enabled, provenance in rows:
            layout.label(text=f'{topic} / {fidelity} / {"ON" if enabled else "OFF"}')
            layout.label(text=str(provenance))
        if not rows:
            layout.label(text='No active SceneRuntime sensor topics', icon='INFO')
        col = layout.column(); col.enabled = bool(rows) and runtime.get_state().connection == 'CONNECTED'
        col.prop(context.scene.wfrl_workflow, 'topic')
        row = col.row(align=True)
        row.operator('wfrl.set_channel', text='Subscribe').enabled = True
        row.operator('wfrl.set_channel', text='Unsubscribe').enabled = False
        layout.label(text='Changes apply at control-step boundary')
        layout.label(text='Sensor sampling is separate from pose telemetry')


CLASSES = (WFRL_PT_WorkflowChannels,)
