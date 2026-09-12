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
        from wfrl_blender.protocol import TRAINING_STATS_CHANNELS
        runtime.training_dashboard.reset('run-1')
        stats={name: dict(value=None,unit='MW' if name=='mean_power' else '',
            validity='unsupported',error='not supplied',fidelity='EXPORTED',
            provenance=dict(file='run.jsonl',channel=name),source_age_seconds=0.,
            stale_after_seconds=30.) for name in TRAINING_STATS_CHANNELS}
        stats['mean_power'].update(value=1.2,validity='valid',error=None)
        payload=dict(run_id='run-1',record_kind='iteration_stats',mode='formal_training',
            step=4,agent_step=16,timestamp=dict(value=1000.,timebase='unix_seconds'),
            iteration=2,phase='sampling',source_age_seconds=0.,stale_after_seconds=30.,stats=stats)
        runtime.training_dashboard.ingest(dict(protocol_version=1,type='training_stats',
            session_id='session',sequence=2,payload=payload),negotiated=True)
        assert runtime.training_dashboard.phase()['step'] == 4
        assert runtime.training_dashboard.metric('mean_power', negotiated=True)['value'] == 1.2
        print('WFRL_TRAINING_STATS_SMOKE=PASS')
    finally:
        wfrl_blender.unregister()


if __name__ == '__main__':
    main()
