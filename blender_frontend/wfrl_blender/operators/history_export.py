"""Native bounded-history export; Blender access stays on the main thread."""
from pathlib import Path

import bpy

from .. import charts

_EXPORT_TARGET = globals().get('_EXPORT_TARGET')


def _poll_export():
    """Publish worker state from Blender's main-thread timer."""
    global _EXPORT_TARGET
    if _EXPORT_TARGET is None:
        return None
    scene, job = _EXPORT_TARGET
    status = job.poll()
    try:
        if status == 'COMPLETE':
            label = f'COMPLETE / {job.path}'
        elif status == 'FAILED':
            label = f'FAILED / {job.error}'
        else:
            label = f'WRITING / {job.path}'
        if scene.get('wfrl_history_export_status') != label:
            scene['wfrl_history_export_status'] = label
    except ReferenceError:
        pass  # The initiating scene may have been deleted or its file closed.
    if status != 'WRITING':
        _EXPORT_TARGET = None
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
        job = _EXPORT_TARGET[1] if _EXPORT_TARGET is not None else charts.export_job
        return job.poll() != 'WRITING'

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        global _EXPORT_TARGET
        path = Path(bpy.path.abspath(self.filepath)).expanduser()
        if path.suffix.lower() != '.json':
            path = path.with_suffix('.json')
        try:
            if _poll_export() is not None:
                raise ValueError('A history export is already running')
            # This bounded detached copy is the only history work on Blender's
            # main thread. ExportJob performs serialization and disk I/O.
            snapshot = charts.raw_history_for(context.scene).snapshot()
            charts.export_job.start(path, snapshot)
        except (OSError, ValueError) as exc:
            context.scene['wfrl_history_export_status'] = f'FAILED / {exc}'
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _EXPORT_TARGET = (context.scene, charts.export_job)
        context.scene['wfrl_history_export_status'] = f'WRITING / {path}'
        register_timer()
        return {'FINISHED'}


def register_timer():
    if _EXPORT_TARGET is not None and not bpy.app.timers.is_registered(_poll_export):
        bpy.app.timers.register(_poll_export, first_interval=.1, persistent=True)


def unregister_timer():
    if bpy.app.timers.is_registered(_poll_export):
        bpy.app.timers.unregister(_poll_export)


CLASSES = (WFRL_OT_ExportHistory,)
