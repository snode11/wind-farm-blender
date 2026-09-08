"""Real Trainer -> Bridge codec numerical acceptance; run under mpiexec -n 1.

This checks migration fidelity against the same source Snapshot, not policy
quality or equivalence of two independently seeded simulator runs.
"""
import argparse
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import numpy as np
from wfrl.blender_bridge.backend_session import BackendSession
from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
from wfrl.blender_bridge.messages import encode_message, FrameDecoder
from wfrl.studio.trainer import Trainer


def verify(mode, output):
    records = []
    def factory(scene, on_snapshot, **options):
        adapter = SnapshotAdapter(scene, 'acceptance', mode)
        def consume(source):
            encoded = adapter.encode(source)
            message = dict(protocol_version=1, type='snapshot', session_id='acceptance',
                           sequence=len(records) + 1, payload=encoded)
            payload = FrameDecoder().feed(encode_message(message))[0]['payload']
            assert payload['step'] == source.step
            assert [t['turbine_id'] for t in payload['turbines']] == [t.id for t in scene.turbines]
            for index, turbine in enumerate(payload['turbines']):
                for key in ('yaw', 'pitch', 'rotor_speed', 'power', 'torque'):
                    source_key = ('pitch_meas' if key == 'pitch' and 'pitch_meas' in source.measure
                                  else 'rotorspeed' if key == 'rotor_speed' and 'rotorspeed' in source.measure else key)
                    values = source.measure.get(source_key)
                    channel = turbine['channels'][key]
                    if values is not None:
                        value = float(np.asarray(values)[index])
                        if np.isfinite(value):
                            assert channel['value'] == value, (key, index, value, channel)
                        else:
                            assert channel['value'] is None
                    else:
                        assert channel['value'] is None
            for key, expected in [('power', source.farm_power), ('reward', source.reward)]:
                actual = payload['farm'][key]['value']
                assert actual == float(expected) if np.isfinite(expected) else actual is None
            records.append(dict(phase=source.phase, payload=payload))
            on_snapshot(source)
        return Trainer(scene, on_snapshot=consume, **options)

    session = BackendSession(trainer_factory=factory)
    scene = 'turb3_stagger.yaml' if mode == 'replay' else 'turb3_ctrl3.yaml'
    options = dict(scene=str(ROOT / 'scenes' / scene), warmup_steps=0)
    if mode == 'replay':
        options.update(replay_steps=3, ckpt_path=str(ROOT / 'results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E400_none_stagE400.pt'))
    else:
        options.update(iters=2, n_steps=4, episode_steps=0)
    report = dict(mode=mode, backend='FAST.Farm', os=platform.platform(),
                  blender_version='5.2.1 LTS (codec consumer; no Blender process in this check)',
                  options=options, boundary=__doc__, passed=False)
    lifecycle = []
    try:
        session.start(mode, options)
        deadline = time.monotonic() + 240
        stopped = False
        while time.monotonic() < deadline:
            lifecycle.extend(p for kind, p in session.poll() if kind == 'lifecycle')
            if mode != 'replay' and not stopped and any(r['payload']['step'] >= 1 for r in records):
                session.stop(120)
                stopped = True
            if session.status in {'STOPPED', 'FAILED'} and not session.alive():
                break
            time.sleep(.02)
        assert session.status == 'STOPPED', (session.status, getattr(session._trainer, 'error', None))
        assert any(r['payload']['step'] >= 1 for r in records)
        if mode == 'replay':
            assert max(r['payload']['step'] for r in records) == 3
        assert not session.alive()
        report['passed'] = True
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        if session.alive():
            session.stop(120)
        if session._stop_worker:
            session._stop_worker.join(130)
        report.update(records=records, lifecycle=lifecycle, final_status=session.status,
                      backend_alive=session.alive())
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: report[k] for k in ('mode', 'passed', 'final_status', 'backend_alive')}), flush=True)
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['replay', 'interactive_training'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if verify(args.mode, args.output) else 1)
