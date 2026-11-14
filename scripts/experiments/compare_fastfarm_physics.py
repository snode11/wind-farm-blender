"""Compare independent physics options using identical short FAST.Farm inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import numpy as np

from wfrl.mpi_runtime import configure_local_mpi

VARIANTS = {'off': {}, 'tower_flex': {'tower_flex': True},
            'dynamic_stall': {'dynamic_stall': True}, 'tower_shadow': {'tower_shadow': True},
            'all': dict(tower_flex=True, dynamic_stall=True, tower_shadow=True)}


def wait_for_normal_termination(log, timeout):
    """Wait for the native completion marker written after output files close."""
    if not np.isfinite(timeout) or timeout <= 0:
        raise ValueError('completion timeout must be finite and positive')
    deadline = time.monotonic() + timeout
    while True:
        with Path(log).open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - 65536))
            tail = stream.read()
        if b'FAST.Farm terminated normally.' in tail:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'FAST.Farm normal termination not confirmed: {log}')
        time.sleep(min(.025, remaining))


def summarize(path, seconds):
    from wfrl.solver_outputs import OutputReader
    reader = OutputReader(path)
    reader.advance(seconds + 1.)
    if reader.error or not reader.interval:
        raise RuntimeError(f'{path}: {reader.error or "no native samples"}')
    channels = {'GenPwr': 'kW', 'RootMyc1': 'kN-m', 'RootMxc1': 'kN-m',
                'TwrBsMxt': 'kN-m', 'TwrBsMyt': 'kN-m', 'TTDspFA': 'm', 'TTDspSS': 'm'}
    data = np.array([[float(value.replace('D', 'E')) for value in row]
                     for _, row in reader.interval])
    if reader.partial or reader.future or abs(data[-1, 0] - seconds) > 5.1e-5:
        raise RuntimeError(f'Incomplete native output: last complete time {data[-1, 0]}, expected {seconds}')
    window = data[(data[:, 0] >= seconds / 2) & (data[:, 0] <= seconds)]
    result = dict(window_seconds=[seconds / 2, seconds], samples=len(window), channels={})
    for channel, unit in channels.items():
        index = reader.names.index(channel)
        values = window[:, index]
        if reader.units[index] != unit or not np.isfinite(values).all():
            raise RuntimeError(f'{path}: invalid {channel} or units')
        result['channels'][channel] = dict(unit=unit, mean=float(values.mean()),
                                          std=float(values.std()), min=float(values.min()),
                                          max=float(values.max()), peak_abs=float(np.abs(values).max()))
    return result


def run_case(farm, seconds, turbines, executable, completion_timeout=60.):
    """Worker process owns its MPI lifetime; the parent bounds its wall time."""
    configure_local_mpi()
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    itf = FastFarmInterface(turbines, farm, max_iter=seconds // 3,
                           fast_farm_executable=executable, default_avg_window=1)
    itf.set_wind_request_context(dict(wind_speed=8., wind_direction=None),
                                record_dir=Path(farm).parent / 'wind_conditions')
    start = time.perf_counter()
    itf.init(wind_speed=8.)
    for _ in range(seconds // 3):
        itf.update_command(yaw=np.zeros(turbines))
    mpi_wall = time.perf_counter() - start
    wait_for_normal_termination(Path(farm).with_suffix('.solver.log'), completion_timeout)
    wall = time.perf_counter() - start
    result = dict(wall_seconds=wall, mpi_wall_seconds=mpi_wall,
                  completion_wait_seconds=wall - mpi_wall, completion='FAST.Farm terminated normally.',
                  turbine_outputs=[summarize(Path(farm).with_name(f'Case.T{i + 1}.out'), seconds)
                                   for i in range(turbines)])
    Path(farm).with_suffix('.comparison.json').write_text(
        json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def execute_case(farm, args, executable):
    command = [sys.executable, '-c',
               'import sys; from scripts.experiments.compare_fastfarm_physics import run_case; '
               'run_case(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], float(sys.argv[5]))',
               farm, str(args.seconds), str(args.turbines), str(executable), str(args.timeout)]
    log = Path(farm).with_suffix('.solver.log')
    with log.open('wb') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                   env={**os.environ, 'OMP_NUM_THREADS': '1'})
        try:
            code = process.wait(timeout=args.timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10.)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    if code:
        raise RuntimeError(f'Solver worker exited {code}; inspect {log}')
    return json.loads(Path(farm).with_suffix('.comparison.json').read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new directory for all inputs and results')
    parser.add_argument('--seconds', type=int, choices=(30, 45, 60), default=45)
    parser.add_argument('--turbines', type=int, choices=(1, 3), default=1)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--variants', nargs='+', choices=tuple(VARIANTS), default=list(VARIANTS),
                        help='selected comparisons; must include off as the reference')
    parser.add_argument('--timeout', type=float, default=600., help='maximum worker wall seconds per case (POSIX)')
    parser.add_argument('--fastfarm', required=True, type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if 'off' not in args.variants or len(set(args.variants)) != len(args.variants):
        parser.error('--variants must include off and contain no duplicates')
    if not np.isfinite(args.timeout) or args.timeout <= 0 or os.name != 'posix':
        parser.error('requires POSIX process groups and a finite positive --timeout')
    output, executable = args.output.resolve(), args.fastfarm.resolve()
    if not executable.is_file():
        parser.error('--fastfarm must be an existing executable')
    output.mkdir(parents=True, exist_ok=False)
    os.chdir(output)
    configure_local_mpi()
    from wfcrl.simul_utils import create_ff_case
    from wfcrl.fastfarm_physics import configure_physics, write_physics_record
    from wfrl.solver_outputs import prepare_case
    report = dict(seconds=args.seconds, turbines=args.turbines, wind_speed_m_s=8.,
                  solver=str(executable), solver_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                  wall_time_scope='MPI spawn through native normal-termination confirmation; excludes case generation',
                  results=[])
    jobs = []
    for repeat in range(args.repeats):
        for name in args.variants:
            physics = VARIANTS[name]
            case_dir = output / f'{name}-{repeat + 1}'
            farm = create_ff_case(dict(num_turbines=args.turbines,
                xcoords=[504. * i for i in range(args.turbines)], ycoords=[0.] * args.turbines,
                max_iter=args.seconds // 3, dt=3, speed=8., wind_time_series=None, physics=physics),
                output_dir=str(case_dir))
            prepare_case(farm, physics, tower_outputs=True)
            identity = configure_physics(farm, physics)
            write_physics_record(farm, identity)
            result = dict(variant=name, repeat=repeat + 1, physics=identity, status='prepared',
                          farm_file=str(farm))
            report['results'].append(result)
            jobs.append((farm, result))
    target = output / 'comparison.json'

    def save():
        target.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    save()
    if args.prepare_only:
        return
    for farm, result in jobs:
        result['status'] = 'running'
        save()
        try:
            result.update(execute_case(farm, args, executable))
            result['status'] = 'completed'
        except BaseException as exc:
            result.update(status='failed', error=str(exc))
            save()
            raise
        save()
        print(result['variant'], result['wall_seconds'], flush=True)
    baseline = [r for r in report['results'] if r['variant'] == 'off']
    for result in report['results']:
        reference = baseline[result['repeat'] - 1]
        result['wall_change_percent'] = 100 * (result['wall_seconds'] / reference['wall_seconds'] - 1)
        result['change_vs_off'] = [
            {channel: {metric: values[metric] - base['channels'][channel][metric]
                       for metric in ('mean', 'std', 'peak_abs')}
             for channel, values in current['channels'].items()}
            for current, base in zip(result['turbine_outputs'], reference['turbine_outputs'])]
    save()
    print(target, flush=True)


if __name__ == '__main__':
    main()
