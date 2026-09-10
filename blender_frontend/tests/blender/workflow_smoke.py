"""Background smoke for Part 6 workflow registration and mode isolation."""
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender import runtime
from wfrl_blender.state import MODES


def main():
    wfrl_blender.register()
    try:
        assert MODES == {'demo', 'interactive_training', 'formal_training', 'replay'}
        assert hasattr(bpy.types.Scene, 'wfrl_workflow')
        for operator in (
                bpy.ops.wfrl.load_configured_scene,
                bpy.ops.wfrl.set_channel,
                bpy.ops.wfrl.backend_run,
                bpy.ops.wfrl.gimbal_mode,
                bpy.ops.wfrl.gimbal_preset,
                bpy.ops.wfrl.presentation_mode,
                bpy.ops.wfrl.capture_screenshot,
                bpy.ops.wfrl.capture_recording,
                bpy.ops.wfrl.export_history):
            assert operator.get_rna_type() is not None
        from wfrl_blender.panels.training import WFRL_PT_TrainingDashboard
        assert WFRL_PT_TrainingDashboard.is_registered

        assert bpy.ops.wfrl.load_demo() == {'FINISHED'}
        scene = bpy.context.scene
        assert runtime.desired_mode() == 'demo'
        assert runtime.configuration_editable()
        scene.wfrl_workflow.override_scene = True
        scene.wfrl_workflow.backend = 'fastfarm'
        scene.wfrl_workflow.controls = 'yaw,pitch,torque'
        assert 'torque' in scene.wfrl_workflow.controls

        for mode in ('interactive_training', 'formal_training', 'replay'):
            runtime.select_mode(mode)
            assert runtime.desired_mode() == mode
            assert runtime.get_state().connection == 'DISCONNECTED'
            assert runtime.configuration_editable()
        runtime.select_mode('demo')
        assert runtime.get_state().connection == 'LOCAL DEMO'
        print('WFRL_PART6_WORKFLOW_SMOKE=PASS')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()
