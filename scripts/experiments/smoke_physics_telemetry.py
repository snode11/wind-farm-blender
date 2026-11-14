"""Verify live tower displacement through FastFarmDriver and the telemetry codec."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from wfrl.mpi_runtime import configure_local_mpi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--solver-log', type=Path, required=True, help='stdout/stderr destination for this MPI job')
    parser.add_argument('--steps', type=int, default=12)
    args = parser.parse_args()
    if args.steps < 3:
        parser.error('--steps must be at least 3')
    output, log = args.output.resolve(), args.solver_log.resolve()
    output.mkdir(parents=True, exist_ok=False)
    configure_local_mpi()
    from wfrl import paths
    from wfrl.fastfarm_driver import FastFarmDriver
    from wfrl.scene.schema import from_dict
    from wfrl.blender_bridge.snapshot_adapter import SnapshotAdapter
    from wfrl.blender_bridge.messages import encode_message, FrameDecoder
    from wfrl.solver_outputs import OutputReader
    from scripts.experiments.compare_fastfarm_physics import wait_for_normal_termination
    # Generated cases use CWD; persistent episode metadata uses paths.RUNS.
    os.chdir(output)
    paths.RUNS = str(output / 'runs')
    scene = from_dict(dict(layout=[dict(id='T1', x=0., y=0.)], physics={'tower_flex': True}))
    adapter = SnapshotAdapter(scene, 'dq-physics-smoke', 'interactive_training')
    driver = FastFarmDriver('Dec_Turb1_Row1_Fastfarm', max_steps=args.steps, physics={'tower_flex': True})
    frames = []
    try:
        measure = driver.reset(wind_speed=8.)
        while True:
            clock = measure['solver_clock']
            sample = SimpleNamespace(step=measure['step'], episode=0, phase='sampling', events=[],
                reward=measure['reward'], farm_power=float(np.sum(measure['power'])), measure=measure,
                ctx={'t': clock['value'], 'control_elapsed_seconds': measure['step'] * driver.dt})
            payload = adapter.encode(sample)
            message = dict(protocol_version=1, type='snapshot', session_id='dq-physics-smoke',
                           sequence=len(frames) + 1, payload=payload)
            received = FrameDecoder().feed(encode_message(message))[0]['payload']
            channels = received['turbines'][0]['channels']
            frames.append(dict(solver_time=clock['value'], channels={key: channels[key]
                          for key in ('tower_top_fa', 'tower_top_ss')}))
            if measure['done']:
                break
            measure = driver.step(np.zeros(driver.n))
    finally:
        driver.close(purge=False, strict=True)
    wait_for_normal_termination(log, 60.)
    farm = Path(driver.env.mdp.interface._simul_file).resolve()
    reader = OutputReader(farm.with_name(farm.stem + '.T1.out'))
    reader.advance(1e6)
    if reader.error:
        raise RuntimeError(reader.error)
    rows = {stamp: row for stamp, row in reader.interval}
    results = {}
    for key, native in (('tower_top_fa', 'TTDspFA'), ('tower_top_ss', 'TTDspSS')):
        records = [frame['channels'][key] for frame in frames if frame['channels'][key]['validity'] == 'valid']
        if not records or not any(abs(r['value']) > 1e-6 for r in records):
            raise RuntimeError(f'No nonzero valid live measurement: {key}')
        index = reader.names.index(native)
        for record in records:
            row = rows[record['timestamp']['value']]
            expected = float(row[index].replace('D', 'E'))
            if (record['unit'] != 'm' or record['provenance']['channel'] != native or
                    record['provenance']['physics_identity'] != driver.physics_record['identity'] or
                    record['value'] != expected):
                raise RuntimeError(f'Native/Driver/wire mismatch: {key}')
        values = np.array([record['value'] for record in records])
        results[key] = dict(valid_samples=len(records), mean_m=float(values.mean()),
                            min_m=float(values.min()), max_m=float(values.max()),
                            first_timestamp=records[0]['timestamp']['value'],
                            last_timestamp=records[-1]['timestamp']['value'], native_max_error_m=0.)
    report = dict(status='passed', physics=driver.physics_record, frames=frames, channels=results,
                  cleanup_confirmed=driver.cleanup_confirmed,
                  solver_completion='FAST.Farm terminated normally.')
    (output / 'telemetry.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(results, indent=2), flush=True)
    print('DQ_PHYSICS_TELEMETRY_PASS', flush=True)


if __name__ == '__main__':
    main()
