"""Native flat-layout direction comparison; outputs require an explicit new directory."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np
import psutil


def solver_processes(farm):
    result = []
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            if 'FAST.Farm' in process.info['name'] and str(farm) in process.info['cmdline']:
                result.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return result


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def worker(farm, direction, seconds, executable, turbulent):
    from wfrl.mpi_runtime import configure_local_mpi
    configure_local_mpi()
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    speed = None if turbulent else 8.
    itf = FastFarmInterface(3, str(farm), max_iter=seconds//3, fast_farm_executable=executable)
    itf.set_wind_request_context(dict(wind_speed=speed, wind_direction=direction),
                                record_dir=farm.parent/'wind_conditions')
    itf.init(wind_speed=speed, wind_direction=direction)
    children = solver_processes(farm)
    if not children:
        raise RuntimeError('Spawned FAST.Farm process could not be identified')
    for _ in range(seconds//3):
        itf.update_command(yaw=np.zeros(3))
    deadline = time.monotonic()+90
    while any(alive(process) for process in children):
        if time.monotonic() >= deadline:
            raise TimeoutError('FAST.Farm process did not exit after MPI finalization')
        time.sleep(.1)
    print('SOLVER_PROCESS_EXIT_CONFIRMED', flush=True)


def summarize(farm, seconds):
    """Read closed files only after the exact solver processes have exited."""
    if solver_processes(farm):
        raise RuntimeError('Cannot read solver output while FAST.Farm is running')
    outputs = []
    for i in range(1, 4):
        path = farm.with_name(f'{farm.stem}.T{i}.out')
        lines = path.read_text(encoding='utf-8').splitlines()
        header = next(j for j, line in enumerate(lines) if line.startswith('Time\t'))
        names, units = lines[header].split(), lines[header+1].split()
        if any('INVALID' in value.upper() for value in names+units):
            raise ValueError(f'Invalid native channel in {path}')
        data = np.loadtxt(lines[header+2:])
        if not np.isfinite(data).all() or abs(data[-1, 0]-seconds) > 1e-4:
            raise ValueError(f'Incomplete/nonfinite native output: {path}')
        index = names.index('GenPwr')
        if units[index] != '(kW)':
            raise ValueError('GenPwr must be in kW')
        power = data[data[:, 0] >= seconds-30., index]/1000.
        outputs.append(dict(turbine=f'T{i}', mean_power_mw=float(power.mean()),
                            min_power_mw=float(power.min()), max_power_mw=float(power.max()), samples=len(power)))
    return dict(window_seconds=[seconds-30., seconds], turbines=outputs,
                total_mean_power_mw=sum(item['mean_power_mw'] for item in outputs))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--bts', default='ts_u8_ti8_s42')
    parser.add_argument('--fastfarm', default='/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm')
    parser.add_argument('--timeout', type=float, default=1200.)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--direction', type=float, default=300., help=argparse.SUPPRESS)
    parser.add_argument('--turbulent', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.seconds < 150 or args.seconds % 3:
        parser.error('--seconds must be at least 150 and divisible by 3')
    if not np.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    if args.worker:
        worker(args.worker, args.direction, args.seconds, args.fastfarm, args.turbulent)
        return
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    from wfrl.mpi_runtime import launcher_environment
    from wfcrl.simul_utils import create_ff_case
    results = {}
    for name, direction, bts in [('steady270', 270., None), ('steady300', 300., None), ('bts300', 300., args.bts)]:
        farm = Path(create_ff_case(dict(num_turbines=3, xcoords=[0., 504., 1008.], ycoords=[0., 0., 0.],
                    max_iter=args.seconds//3, dt=3, speed=8., wind_time_series=bts, direction=direction),
                    str(args.output/name)))
        command = [sys.executable, '-m', 'scripts.experiments.compare_fastfarm_directions',
                   '--output', str(args.output), '--worker', str(farm), '--direction', str(direction),
                   '--seconds', str(args.seconds), '--fastfarm', args.fastfarm]
        if bts:
            command.append('--turbulent')
        log = args.output/(name+'.log')
        with log.open('w', encoding='utf-8') as stream:
            process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                       env=launcher_environment(), start_new_session=True)
            try:
                code = process.wait(timeout=args.timeout)
                if code:
                    raise RuntimeError(f'{name} failed ({code}): {log}')
            finally:
                owned = solver_processes(farm)
                try:
                    owned += psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess:
                    pass
                for child in owned:
                    if alive(child):
                        child.terminate()
                if process.poll() is None:
                    process.terminate()
                _, survivors = psutil.wait_procs(owned, timeout=5)
                for child in survivors:
                    child.kill()
                process.wait(timeout=5)
        text = log.read_text(encoding='utf-8')
        if 'FAST.Farm terminated normally.' not in text or 'SOLVER_PROCESS_EXIT_CONFIRMED' not in text:
            raise RuntimeError(f'Native exit not confirmed: {log}')
        results[name] = summarize(farm, args.seconds)
        results[name].update(direction_deg=direction, bts=bts)
    (args.output/'comparison.json').write_text(json.dumps(results, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
