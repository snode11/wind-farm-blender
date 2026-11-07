"""Native smoke for the independent paused manual-pose contract."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender import runtime


def main():
    wfrl_blender.register()
    try:
        assert bpy.ops.wfrl.manual_enter() == {'FINISHED'}
        from wfrl_blender import manual_control
        scene=bpy.context.scene
        assert manual_control.is_manual(scene)
        entry=scene.wfrl_manual_turbines[0]
        manual_control._apply_poses(scene, {'T1':dict(manual_control.pose_snapshot(scene)['T1'],yaw_deg=12.,pitch_deg=18.,pitch_b1_deg=18.,pitch_b2_deg=18.,pitch_b3_deg=18.)},pause=True,ramp=False)
        values=manual_control.pose_snapshot(scene)
        assert values['T1']['yaw_deg']==12. and values['T1']['pitch_deg']==18.
        assert not entry.running
        print('WFRL_MANUAL_POSE_SMOKE=PASS')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()
