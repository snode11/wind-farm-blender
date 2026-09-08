"""Native smoke for formal-training statistics presentation."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import wfrl_blender
from wfrl_blender import runtime


def main():
    wfrl_blender.register()
    try:
        runtime.select_mode('formal_training')
        runtime.training_dashboard.ingest({
            'type': 'training_stats',
            'payload': {'phase': 'training', 'step': 4, 'agent_step': 16,
                        'iteration': 2, 'metrics': {'mean_power': 1.2,
                        'mean_reward': 0.4}}
        }, negotiated=True)
        assert runtime.training_dashboard.phase()['step'] == 4
        assert runtime.training_dashboard.metric('mean_power', negotiated=True)['value'] == 1.2
        print('WFRL_TRAINING_STATS_SMOKE=PASS')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()
