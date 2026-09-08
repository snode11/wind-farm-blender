"""Native bounded-history export; Blender access stays on the main thread."""
from pathlib import Path

import bpy

from .. import charts


def _poll_export():
    """Publish worker state from Blender's main-thread timer."""
    status = charts.export_job.poll()
    scene = getattr(getattr(bpy, 'context', None), 'scene', None)
    if scene is not None:
        if status == 'COMPLETE':
            scene['wfrl_history_export_status'] = f'COMPLETE / {charts.export_job.path}'
        elif status == 'FAILED':
            scene['wfrl_history_export_status'] = f'FAILED / {charts.export_job.error}'
        else:
            scene['wfrl_history_export_status'] = f'WRITING / {charts.export_job.path}'
    return .1 if status == 'WRITING' else None


class WFRL_OT_ExportHistory(bpy.types.Operator):
    bl_idname = 'wfrl.export_history'
    bl_label = 'Export Bounded History'
    bl_description = 'Write the current run bounded history to JSON without blocking Blender'

    filepath: bpy.props.StringProperty(subtype='FILE_PATH', default='//wfrl-history.json')
    filename_ext = '.json'
    filter_glob: bpy.props.StringProperty(default='*.json', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return charts.export_job.poll() != 'WRITING'

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        path = Path(bpy.path.abspath(self.filepath)).expanduser()
        if path.suffix.lower() != '.json':
            path = path.with_suffix('.json')
        try:
            # This bounded detached copy is the only history work on Blender's
            # main thread. ExportJob performs serialization and disk I/O.
            snapshot = charts.raw_history.snapshot()
            charts.export_job.start(path, snapshot)
        except (OSError, ValueError) as exc:
            context.scene['wfrl_history_export_status'] = f'FAILED / {exc}'
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.scene['wfrl_history_export_status'] = f'WRITING / {path}'
        if not bpy.app.timers.is_registered(_poll_export):
            bpy.app.timers.register(_poll_export, first_interval=.1)
        return {'FINISHED'}


def unregister_timer():
    if bpy.app.timers.is_registered(_poll_export):
        bpy.app.timers.unregister(_poll_export)


CLASSES = (WFRL_OT_ExportHistory,)
