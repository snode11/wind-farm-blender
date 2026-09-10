"""Connection diagnostics and authoritative backend status."""
import bpy
from .. import runtime
from ..preferences import get_preferences
from .diagnostics import CLASSES as DIAGNOSTIC_CLASSES, draw_diagnostic


class WFRL_PT_connection(bpy.types.Panel):
    bl_label = 'WFRL / CONNECTION'
    bl_idname = 'WFRL_PT_connection'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_order = -101

    def draw(self, context):
        ui = runtime.get_state()
        layout = self.layout
        layout.label(text=ui.connection, icon='LINKED' if ui.connection == 'CONNECTED' else 'UNLINKED')
        layout.label(text=f'Run: {ui.run_status}' + ('' if ui.confirmed else ' (unconfirmed)'))
        if runtime.progress(): layout.label(text=runtime.progress())
        for mode, title in [('demo', 'Demo'), ('interactive_training', 'Interactive'),
                            ('formal_training', 'Formal Training'), ('replay', 'Replay')]:
            row = layout.row(); row.enabled = runtime.configuration_editable()
            row.operator('wfrl.connection_mode', text=title, depress=runtime.desired_mode() == mode).mode = mode
        if ui.connection == 'LOCAL DEMO':
            layout.operator('wfrl.bridge_connect', text='Connect Backend Demo')
        else:
            layout.operator('wfrl.bridge_connect', text='Connect / Reconnect')
            layout.operator('wfrl.bridge_disconnect')
            for action in ('start', 'pause', 'resume', 'step', 'stop', 'reset'):
                row = layout.row(); row.enabled = runtime.allows_command(action)
                row.operator('wfrl.backend_run', text=action.title()).action = action
        if ui.error:
            box = layout.box(); box.alert = True
            draw_diagnostic(box, 'Backend connection error', ui.error, component='connection', error=True)
        prefs = get_preferences(context)
        if prefs:
            col = layout.column(); col.enabled = runtime.configuration_editable()
            for key in ('project_dir', 'backend_python', 'mpi_path', 'fastfarm_path', 'default_scene', 'port'):
                col.prop(prefs, key)
        else:
            layout.label(text='Install extension to save paths in Preferences')
        layout.operator('wfrl.check_environment')
        for component in ('blender', 'python', 'mpi', 'fastfarm', 'environment'):
            if component == 'environment' and component not in runtime.health_results:
                continue
            result = runtime.health_results.get(component)
            layout.label(text=f'{component}: {result.status if result else "NOT CHECKED"}')
            if result:
                draw_diagnostic(layout, f'{component}: {result.status}', result.detail, component=component, status=result.status, error=result.status in {'ERROR', 'INVALID', 'MISSING', 'TIMEOUT', 'UNSUPPORTED'})
        layout.label(text=f'Bridge: {ui.connection}')


CLASSES = DIAGNOSTIC_CLASSES + (WFRL_PT_connection,)
