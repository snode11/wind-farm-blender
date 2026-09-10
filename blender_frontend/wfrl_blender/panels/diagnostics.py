"""Shared diagnostics actions; full reports open in a scrollable Text Editor."""
import bpy
from ..diagnostics import report


class WFRL_OT_DiagnosticDetails(bpy.types.Operator):
    bl_idname = 'wfrl.diagnostic_details'
    bl_label = 'Full Diagnostic Details'
    bl_description = 'Open the complete report and suggested action in a scrollable Text Editor; Shift F5 returns to the 3D view'
    report_text: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if context.area is None:
            self.report({'WARNING'}, 'Open details from a panel, or use Copy Error')
            return {'CANCELLED'}
        document = bpy.data.texts.get('WFRL Diagnostic Details') or bpy.data.texts.new('WFRL Diagnostic Details')
        document.clear()
        document.write('Return to the 3D view: Shift F5 (cursor over this editor).\n\n' + self.report_text)
        context.area.type = 'TEXT_EDITOR'
        context.area.spaces.active.text = document
        context.area.spaces.active.show_word_wrap = True
        return {'FINISHED'}


class WFRL_OT_CopyDiagnostic(bpy.types.Operator):
    bl_idname = 'wfrl.copy_diagnostic'
    bl_label = 'Copy Diagnostic'
    bl_description = 'Copy the complete report, source and suggested action to the clipboard'
    report_text: bpy.props.StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        context.window_manager.clipboard = self.report_text
        self.report({'INFO'}, 'Full diagnostic report copied')
        return {'FINISHED'}


def draw_diagnostic(layout, title, detail, *, component='', status='', provenance=None, error=False):
    text = str(detail)
    preview = ' '.join(text.split())
    layout.label(text=preview[:90] + ('…' if len(preview) > 90 else ''), icon='ERROR' if error else 'INFO')
    full = report(title, text, component=component, status=status, provenance=provenance)
    row = layout.row(align=True)
    row.operator('wfrl.diagnostic_details', text='Full Details', icon='TEXT').report_text = full
    row.operator('wfrl.copy_diagnostic', text='Copy Error' if error else 'Copy Report', icon='COPYDOWN').report_text = full


CLASSES = (WFRL_OT_DiagnosticDetails, WFRL_OT_CopyDiagnostic)
