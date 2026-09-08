"""Scene requests and effective backend configuration, kept separate."""
import bpy
from .. import runtime


class WFRL_PT_WorkflowScene(bpy.types.Panel):
    bl_label = 'WFRL / SCENE CONFIGURATION'
    bl_idname = 'WFRL_PT_workflow_scene'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.wfrl_workflow
        col = layout.column(); col.enabled = runtime.configuration_editable()
        col.prop(settings, 'override_scene')
        if settings.override_scene:
            for key in ('backend', 'wind_speed', 'wind_direction', 'turbulence', 'terrain', 'controls'):
                col.prop(settings, key)
            if 'torque' in settings.controls:
                col.label(text='Torque replaces baseline generator control', icon='ERROR')
        col.operator('wfrl.load_configured_scene')
        data = getattr(runtime, 'workflow_scene', None) or {}
        if data:
            layout.label(text=f"{data.get('name', '')} / {data.get('backend', '')}")
            layout.label(text=f"{data.get('turbine', '')} / control step {data.get('dt', '')} s")
            for turbine in data.get('layout', []):
                layout.label(text=f"{turbine['id']}: ({turbine['x']:g}, {turbine['y']:g}) m")
            inflow = data.get('inflow', {})
            layout.label(text=f"Requested: {inflow.get('speed')} m/s @ {inflow.get('direction')} deg")
            if data.get('backend') == 'fastfarm':
                layout.label(text='FAST.Farm ignores requested direction', icon='INFO')
            if inflow.get('turbulence'):
                layout.label(text='Turbulence box overrides requested speed', icon='INFO')
        layout.label(text='Terrain affects rendering only / SYNTH', icon='INFO')
        layout.label(text='Actual inflow: inspect live telemetry')


CLASSES = (WFRL_PT_WorkflowScene,)
