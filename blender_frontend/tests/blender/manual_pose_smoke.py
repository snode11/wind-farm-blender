"""Native smoke for the paused Local Demo display-pose contract."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender import runtime


def main():
    wfrl_blender.register()
    try:
        bpy.ops.wfrl.load_demo()
        scene = bpy.context.scene
        scene.wfrl_manual_enabled = True
        scene.wfrl_manual_yaw = 12.0
        scene.wfrl_manual_pitch = 18.0
        scene['wfrl_run_status'] = 'PAUSED'
        assert runtime.desired_mode() == 'demo'
        assert scene.wfrl_manual_enabled
        assert scene['wfrl_power_available'] is False
        assert 'Manual pose' in scene['wfrl_demo_phase']
        print('WFRL_MANUAL_POSE_SMOKE=PASS')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()
