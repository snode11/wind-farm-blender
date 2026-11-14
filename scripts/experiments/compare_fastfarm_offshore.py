"""Run owned onshore/OC3 cases and summarize completed native output only."""
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

from wfrl.mpi_runtime import configure_local_mpi, launcher_environment

SEA_STATE = dict(model='oc3_monopile', hs_m=2., tp_s=8., direction_deg=0., seed=123456789)
VARIANTS = {'off': {}, 'offshore': {'tower_flex': True},
            'offshore_aero': dict(tower_flex=True, dynamic_stall=True, tower_shadow=True)}


def native_table(path, seconds):
    from wfrl.solver_outputs import OutputReader
    reader = OutputReader(path)
    reader.advance(seconds + 1.)
    if reader.error or not reader.interval or reader.partial or reader.future:
        raise ValueError(f'Incomplete or invalid output: {path}: {reader.error}')
    data = np.asarray([[float(x.replace('D', 'E')) for x in row] for _, row in reader.interval])
    if abs(data[-1, 0] - seconds) > 5.1e-5 or not np.isfinite(data).all():
        raise ValueError(f'Output did not finish at {seconds} s with finite values: {path}')
    return reader.names, reader.units, data


def summarize(path, seconds, offshore):
    names, units, data = native_table(path, seconds)
    window = data[(data[:, 0] >= seconds / 2) & (data[:, 0] <= seconds)]
    channels = dict(GenPwr='kW', TwrBsMxt='kN-m', TwrBsMyt='kN-m', TTDspFA='m', TTDspSS='m')
    if offshore:
        channels.update({'-ReactFXss': 'N', '-ReactFYss': 'N', '-ReactMXss': 'N*m', '-ReactMYss': 'N*m',
                         'PtfmSurge': 'm', 'PtfmPitch': 'deg', 'Wave1Elev': 'm'})
    result = dict(window_seconds=[seconds / 2, seconds], samples=len(window), channels={})
    for name, unit in channels.items():
        i = names.index(name)
        if units[i] != unit:
            raise ValueError(f'{name}: expected {unit}, found {units[i]}')
        values = window[:, i]
        result['channels'][name] = dict(unit=unit, mean=float(values.mean()), std=float(values.std()),
                                       min=float(values.min()), max=float(values.max()), peak_abs=float(abs(values).max()))
    result['output_sha256'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return result


def worker(farm, seconds, turbines, executable, timeout):
    """Own the spawned native rank and wait past MPI disconnection before reading files."""
    import psutil
    configure_local_mpi()
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    farm, executable = str(Path(farm).resolve()), str(Path(executable).resolve())
    itf = FastFarmInterface(turbines, farm, max_iter=seconds // 3,
                           fast_farm_executable=executable, default_avg_window=1)
    itf.set_wind_request_context(dict(wind_speed=8., wind_direction=None),
                                record_dir=Path(farm).parent / 'wind_conditions')
    started, owned = time.perf_counter(), []
    try:
        itf.init(wind_speed=8.)
        for process in psutil.process_iter(['exe', 'cmdline']):
            try:
                if (farm in (process.info['cmdline'] or []) and process.info['exe']
                        and Path(process.info['exe']).resolve() == Path(executable)):
                    owned.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if not owned:
            raise RuntimeError('Unable to identify this case FAST.Farm PID; output will not be read')
        Path(farm).with_suffix('.pids.json').write_text(json.dumps([p.pid for p in owned]), encoding='utf-8')
        for _ in range(seconds // 3):
            itf.update_command(yaw=np.zeros(turbines))
        mpi_wall = time.perf_counter() - started
        for process in owned:
            process.wait(timeout=timeout)
        wall = time.perf_counter() - started
        log = Path(farm).with_suffix('.solver.log').read_bytes()
        if b'FAST.Farm terminated normally.' not in log:
            raise RuntimeError('Native process exited without the normal completion marker')
        from wfcrl.simul_utils import FASTInputFile
        offshore = int(FASTInputFile(str(Path(farm).with_name('FFTest_WT1.fst')))['CompHydro']) == 1
        outputs = [Path(farm).with_name(f'Case.T{i + 1}.out') for i in range(turbines)]
        result = dict(wall_seconds=wall, mpi_wall_seconds=mpi_wall, solver_pids=[p.pid for p in owned],
                      completion='normal termination and native process exit confirmed before reading output',
                      turbines=[summarize(path, seconds, offshore) for path in outputs])
        if offshore and turbines > 1:
            waves = [native_table(path, seconds) for path in outputs]
            series = [data[:, names.index('Wave1Elev')] for names, _, data in waves]
            result['wave_elevation_correlation'] = np.corrcoef(series).tolist()
        Path(farm).with_suffix('.result.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    finally:
        for process in owned:
            if process.is_running():
                process.terminate()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


def execute(farm, seconds, turbines, executable, timeout):
    command = [sys.executable, '-m', 'scripts.experiments.compare_fastfarm_offshore', '--worker',
               farm, str(seconds), str(turbines), str(executable), str(timeout)]
    log = Path(farm).with_suffix('.solver.log')
    with log.open('wb') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                                   env={**launcher_environment(), 'OMP_NUM_THREADS': '1'})
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    if code:
        raise RuntimeError(f'Worker exited {code}: {log}')
    return json.loads(Path(farm).with_suffix('.result.json').read_text(encoding='utf-8'))


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        worker(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], float(sys.argv[6]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new directory for inputs and outputs')
    parser.add_argument('--fastfarm', type=Path, required=True)
    parser.add_argument('--turbines', type=int, nargs='+', choices=(1, 3), default=[1, 3])
    parser.add_argument('--seconds', type=int, default=120)
    parser.add_argument('--timeout', type=float, default=600.)
    parser.add_argument('--variants', nargs='+', choices=tuple(VARIANTS), default=['off', 'offshore'])
    args = parser.parse_args()
    if args.seconds < 6 or args.seconds % 3 or not 0 < args.timeout < float('inf'):
        parser.error('seconds must be >=6 and divisible by 3; timeout must be finite and positive')
    output, executable = args.output.resolve(), args.fastfarm.resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.chdir(output)
    configure_local_mpi()
    from wfcrl.simul_utils import create_ff_case
    from wfcrl.fastfarm_physics import configure_physics
    from wfrl.solver_outputs import prepare_case
    report = dict(solver=str(executable), solver_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                  seconds=args.seconds, wind_speed_m_s=8., OMP_NUM_THREADS=1,
                  wall_scope='MPI spawn through confirmed native exit; excludes case generation', results=[])
    for count in args.turbines:
        for variant in args.variants:
            physics = VARIANTS[variant]
            substructure = SEA_STATE if variant != 'off' else None
            farm = create_ff_case(dict(num_turbines=count, xcoords=[504. * i for i in range(count)],
                ycoords=[0.] * count, max_iter=args.seconds // 3, dt=3, speed=8., wind_time_series=None,
                physics=physics, substructure=substructure), str(output / f'{count}t-{variant}'))
            prepare_case(farm, physics, substructure=substructure, tower_outputs=True)
            identity = configure_physics(farm, physics, substructure=substructure)
            result = dict(variant=variant, count=count, physics=identity,
                          **execute(farm, args.seconds, count, executable, args.timeout))
            report['results'].append(result)
            (output / 'comparison.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
            print(f'{count} turbine(s), {variant}: {result["wall_seconds"]:.3f} s wall', flush=True)


if __name__ == '__main__':
    main()
