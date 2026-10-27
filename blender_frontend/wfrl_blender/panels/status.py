"""Connection diagnostics and authoritative backend status."""
import bpy
from .. import runtime
from ..preferences import get_preferences
from ..home_state import backend_status
from .diagnostics import CLASSES as DIAGNOSTIC_CLASSES, draw_diagnostic


class WFRL_PT_connection(bpy.types.Panel):
    bl_label = 'WFRL / 后端连接'
    bl_idname = 'WFRL_PT_connection'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'
    bl_order = -101

    @classmethod
    def poll(cls, context):
        return context.scene.get("wfrl_scene_kind") != "manual"

    def draw(self, context):
        ui = runtime.get_state()
        layout = self.layout
        offline_results = ui.connection == 'OFFLINE RESULTS'
        layout.label(text=backend_status(ui), icon='LINKED' if ui.connection == 'CONNECTED' else 'UNLINKED')
        layout.label(text='主入口：N → WFRL')
        if runtime.progress(): layout.label(text=runtime.progress())
        # Errors stay visible even when advanced connection controls are folded.
        if ui.error:
            box = layout.box(); box.alert = True
            draw_diagnostic(box, 'Backend connection error', ui.error, component='connection', error=True)
        if offline_results:
            header, body = layout.panel('wfrl_replay_backend_connection', default_closed=True)
            header.label(text='后端连接（高级）')
            if body is None:
                return
            controls = body
        else:
            controls = layout
        for mode, title in [('demo', 'MAPPO · 60 秒'), ('interactive_training', 'Interactive'),
                            ('formal_training', 'Formal Training'), ('replay', 'Replay')]:
            row = controls.row(); row.enabled = runtime.configuration_editable()
            row.operator('wfrl.connection_mode', text=title, depress=runtime.desired_mode() == mode).mode = mode
        if ui.connection == 'LOCAL DEMO':
            controls.operator('wfrl.bridge_connect', text='连接后端 Demo')
        else:
            controls.operator('wfrl.bridge_connect', text='连接 / 重新连接后端')
            if not offline_results:
                controls.operator('wfrl.bridge_disconnect')
        if runtime.can_connect_new_backend():
            controls.operator('wfrl.bridge_connect_new', icon='UNLINKED')
            controls.label(text='放弃旧连接不会停止旧后端作业', icon='INFO')
        if runtime.forgotten_connections:
            previous = runtime.forgotten_connections[-1]
            header, body = controls.panel('wfrl_forgotten_backend_identity', default_closed=True)
            header.label(text='已放弃的连接 · 作业状态未确认')
            if body is not None:
                body.label(text=f"会话：{previous['session_id']}")
                if previous['run_id']:
                    body.label(text=f"作业：{previous['run_id']}")
                body.label(text=f"最后状态：{previous['run_status']} · 当前未确认")
        # Run actions have a single home in the backend run panel.
        header, body = layout.panel('wfrl_backend_environment', default_closed=True)
        header.label(text='环境与路径（高级）')
        if body is None:
            return
        layout = body
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
