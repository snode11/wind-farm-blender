"""Actual trainer progress and statistics with independent freshness."""
import bpy

from .. import runtime
from ..training import STAT_LABELS
from .diagnostics import draw_diagnostic


class WFRL_PT_TrainingDashboard(bpy.types.Panel):
    bl_idname = 'WFRL_PT_training_dashboard'
    bl_label = 'WFRL / TRAINING DASHBOARD'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'

    @classmethod
    def poll(cls, context):
        return runtime.desired_mode() in {'interactive_training', 'formal_training'}

    def draw(self, context):
        layout = self.layout
        negotiated = runtime.training_stats_negotiated()
        if not negotiated:
            layout.label(text='UNSUPPORTED / training_stats_v1 not negotiated', icon='INFO')
            return
        phase = runtime.training_dashboard.phase()
        if phase['validity'] == 'waiting':
            layout.label(text='WAITING / first trainer progress record', icon='TIME')
        else:
            layout.label(text=f"Phase: {phase['phase']} / {phase['validity'].upper()}")
            layout.label(text=f"Step {phase['step']} / agent {phase['agent_step']} / iteration {phase['iteration']}")
            layout.label(text=f"Progress source age {phase['age']:.1f} s")
        for key, label in STAT_LABELS.items():
            record = runtime.training_dashboard.metric(key, negotiated=negotiated)
            box = layout.box()
            value = record.get('value')
            rendered = f'{value:.6g}' if isinstance(value, (int, float)) else '—'
            box.label(text=f"{label}: {rendered} {record.get('unit', '')}".rstrip())
            box.label(text=str(record.get('validity', 'unsupported')).upper())
            if record.get('age') is not None:
                box.label(text=f"Metric source age {record['age']:.1f} s")
            if record.get('error') or record.get('provenance'):
                draw_diagnostic(box, f'Training / {label}', record.get('error') or 'Metric source details',
                                component='training', provenance=record.get('provenance'), error=bool(record.get('error')))



CLASSES = (WFRL_PT_TrainingDashboard,)
