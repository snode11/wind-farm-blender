"""Live records retain per-channel units, fidelity, validity and provenance."""
import bpy
from .. import runtime, charts
from .diagnostics import draw_diagnostic


class WFRL_OT_select_telemetry_curve(bpy.types.Operator):
    bl_idname = 'wfrl.select_telemetry_curve'
    bl_label = 'Search telemetry curves'
    bl_property = 'channel'

    channel: bpy.props.EnumProperty(name='Curve', items=[
        (key, f'{label} [{key}]', '') for key, label in {**charts.LABELS, **charts.STAT_LABELS}.items()])

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.scene.wfrl_chart_channel = self.channel
        return {'FINISHED'}


class WFRL_PT_live_telemetry(bpy.types.Panel):
    bl_label = 'WFRL / LIVE TELEMETRY'
    bl_idname = 'WFRL_PT_live_telemetry'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_order = -99

    @classmethod
    def poll(cls, context):
        from .. import farm_flex
        return context.scene.get("wfrl_scene_kind") != "manual" and not farm_flex.is_active(context.scene)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        if hasattr(scene, 'wfrl_chart_visible'):
            layout.prop(scene, 'wfrl_chart_visible')
            channel = scene.wfrl_chart_channel
            layout.operator('wfrl.select_telemetry_curve',
                            text=charts.LABELS.get(channel, charts.STAT_LABELS.get(channel, channel)),
                            icon='VIEWZOOM')
        if hasattr(scene, 'wfrl_selected_turbine'):
            layout.prop(scene, 'wfrl_selected_turbine', text='Turbine / curve filter')
        layout.label(text='History: up to 600 samples per turbine/channel')
        row = layout.row()
        row.enabled = charts.export_job.poll() != 'WRITING'
        row.operator('wfrl.export_history', icon='EXPORT')
        layout.label(text=scene.get('wfrl_history_export_status', 'History export ready'))
        state = runtime.kinematics
        try:
            owner = runtime.backend_scene()
        except (ValueError, ReferenceError):
            owner = None
        owned = owner is not None and owner == scene
        data = state.snapshot if owned else None
        if not data:
            layout.label(text='WAITING / no backend snapshot')
        else:
            layout.label(text=f"Step {data['step']} / {data['mode']}")
            stamp = data['timestamp']
            layout.label(text=f"{stamp['value']} {stamp['timebase']}")
            if 'control_elapsed_seconds' in data:
                layout.label(text=f"Control elapsed: {data['control_elapsed_seconds']:g} s")
            selected = getattr(scene, 'wfrl_selected_turbine', 'ALL')
            groups = [(t['turbine_id'], t['channels']) for t in data['turbines']
                      if selected == 'ALL' or t['turbine_id'] == selected]
            groups.append(('Farm', data['farm']))
            for label, records in groups:
                box = layout.box()
                box.label(text=label)
                visible = charts.selected_channels(getattr(scene, 'wfrl_chart_channel', 'power'))
                for name in sorted(records, key=lambda key: key not in visible):
                    record = records[name]
                    if name == 'yaw_measured' or name in ('m_flap', 'm_edge'):
                        continue
                    if name in charts.SOLVER_OUTPUT_CHANNELS and name != 'rotor_speed' and name not in visible:
                        continue
                    validity = state.validity(record, runtime.get_state().connection == 'CONNECTED')
                    value = record['value']
                    if validity != 'valid':
                        value = None
                    unit = record['unit']
                    if name == 'torque' and unit in {'N m', 'N·m', 'Nm'} and charts.scalar(value):
                        value, unit = value / 1000.0, 'kN·m'
                    rendered = f'{value:.5g}' if isinstance(value, (int, float)) else str(value) if value is not None else '—'
                    box.label(text=f"{charts.LABELS.get(name, name)}: {rendered} {unit}")
                    box.label(text=f"{record['fidelity']} / {validity}")
                    if name in charts.SOLVER_OUTPUT_CHANNELS and 'timestamp' in record:
                        box.label(text=f"Sample: {record['timestamp']['value']:g} s")
                    draw_diagnostic(box, f'{label} / {name}', record['error'] or 'Channel source details',
                                    provenance=record['provenance'], error=bool(record['error']))
        events = list(runtime.safety_events) if owned else []
        layout.label(text=f'Safety events retained: {len(events)}')
        for event in events[-5:]:
            draw_diagnostic(layout, 'Safety event', event['payload'])


CLASSES = (WFRL_OT_select_telemetry_curve, WFRL_PT_live_telemetry)
