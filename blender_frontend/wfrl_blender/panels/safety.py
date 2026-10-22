"""Safety rule counts and requested/applied command details from backend events."""
from collections import Counter
import bpy
from .. import runtime


class WFRL_PT_WorkflowSafety(bpy.types.Panel):
    bl_label = 'WFRL / SAFETY HISTORY'
    bl_idname = 'WFRL_PT_workflow_safety'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        events = [item.get('payload', item) for item in runtime.safety_events]
        if not events:
            layout.label(text='No backend safety events received')
            return
        counts = Counter((event.get('data', {}).get('value') or {}).get('rule', 'unknown') for event in events)
        layout.label(text=f'Retained events: {len(events)} / 256')
        for rule, count in sorted(counts.items()): layout.label(text=f'{rule}: {count}')
        for event in events[-5:]:
            raw = event.get('data', {}).get('value') or {}
            box = layout.box()
            box.label(text=f"Step {event.get('step')} / {event.get('turbine_id') or 'farm'} / {event.get('severity')}")
            box.label(text=event.get('message', ''))
            box.label(text=f"{raw.get('control', '')}: requested {raw.get('requested', 'unavailable')} / applied {raw.get('applied', 'unavailable')}")
            box.label(text=event.get('data', {}).get('fidelity', 'UNKNOWN'))


CLASSES = (WFRL_PT_WorkflowSafety,)
