"""Live records retain per-channel units, fidelity, validity and provenance."""
import bpy
from .. import runtime, charts
from .diagnostics import draw_diagnostic


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
        return not farm_flex.is_active(context.scene)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        if hasattr(scene, 'wfrl_chart_visible'):
            layout.prop(scene, 'wfrl_chart_visible')
            layout.prop(scene, 'wfrl_chart_channel')
        if hasattr(scene, 'wfrl_selected_turbine'):
            layout.prop(scene, 'wfrl_selected_turbine', text='Turbine / curve filter')
        layout.label(text='History: up to 600 samples per turbine/channel')
        row = layout.row()
        row.enabled = charts.export_job.poll() != 'WRITING'
        row.operator('wfrl.export_history', icon='EXPORT')
        layout.label(text=scene.get('wfrl_history_export_status', 'History export ready'))
        state = runtime.kinematics
        data = state.snapshot
        if not data:
            layout.label(text='WAITING / no backend snapshot')
        else:
            layout.label(text=f"Step {data['step']} / {data['mode']}")
            stamp = data['timestamp']
            layout.label(text=f"{stamp['value']} {stamp['timebase']}")
            selected = getattr(scene, 'wfrl_selected_turbine', 'ALL')
            groups = [(t['turbine_id'], t['channels']) for t in data['turbines']
                      if selected == 'ALL' or t['turbine_id'] == selected]
            groups.append(('Farm', data['farm']))
            for label, records in groups:
                box = layout.box(); box.label(text=label)
                for name, record in records.items():
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
                    draw_diagnostic(box, f'{label} / {name}', record['error'] or 'Channel source details',
                                    provenance=record['provenance'], error=bool(record['error']))
        layout.label(text=f'Safety events retained: {len(runtime.safety_events)}')
        for event in list(runtime.safety_events)[-5:]:
            draw_diagnostic(layout, 'Safety event', event['payload'])


CLASSES = (WFRL_PT_live_telemetry,)
