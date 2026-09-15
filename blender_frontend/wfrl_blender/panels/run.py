"""Four business modes with launch contracts and authoritative control state."""
import bpy
from .. import runtime

START_LABELS = {'demo': 'Start Backend Demo', 'interactive_training': 'Start Training',
                'formal_training': 'Submit Formal Training', 'replay': 'Start Replay'}


class WFRL_PT_WorkflowRun(bpy.types.Panel):
    bl_label = 'WFRL / 后端运行'
    bl_idname = 'WFRL_PT_workflow_run'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Item'

    @classmethod
    def poll(cls, context):
        return runtime.get_state().connection not in {'LOCAL DEMO', 'OFFLINE RESULTS'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.wfrl_workflow
        mode = runtime.desired_mode()
        col = layout.column(); col.enabled = runtime.configuration_editable()
        if mode in {'interactive_training', 'formal_training'}:
            for key in ('iters', 'n_steps', 'warmup_steps', 'seed'): col.prop(settings, key)
        if mode in {'formal_training', 'replay'}: col.prop(settings, 'checkpoint')
        if mode == 'replay':
            for key in ('replay_steps', 'warmup_steps', 'seed'): col.prop(settings, key)
            layout.label(text='Deterministic policy / no PPO updates')
        if mode == 'formal_training':
            layout.label(text='Existing train_fastfarm.py / process status')
            layout.label(text='Live telemetry unavailable from formal CLI', icon='INFO')
        for action in ('start', 'pause', 'resume', 'step', 'stop', 'reset'):
            row = layout.row(); row.enabled = runtime.allows_command(action)
            row.operator('wfrl.backend_run', text=START_LABELS[mode] if action == 'start' else action.title()).action = action
        layout.label(text='Real yaw / pitch / torque: backend policy only')
        layout.label(text='Trainer has no manual command override API', icon='INFO')
        if runtime.get_state().run_status == 'DRAINING':
            layout.label(text='Safely draining backend; wait for STOPPED', icon='TIME')


CLASSES = (WFRL_PT_WorkflowRun,)
