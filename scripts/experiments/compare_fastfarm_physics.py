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
            'all': dict(tower_flex=True, dynamic_stall=True, tower_shadow=True),
            'blade_torsion': {'blade_torsion': True},
            'all_on': dict(tower_flex=True, dynamic_stall=True, tower_shadow=True, blade_torsion=True)}


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


def summarize(path, seconds, *, blade_torsion=False, discard=None):
    from wfrl.solver_outputs import OutputReader
    reader = OutputReader(path)
    rows = []
    size = Path(path).stat().st_size
    while reader.offset < size and not reader.error:
        reader.advance(seconds + 1.)
        rows.extend(reader.interval)
    if reader.error or not rows:
        raise RuntimeError(f'{path}: {reader.error or "no native samples"}')
    channels = {'GenPwr': 'kW', 'RootMyc1': 'kN-m', 'RootMxc1': 'kN-m',
                'TwrBsMxt': 'kN-m', 'TwrBsMyt': 'kN-m', 'TTDspFA': 'm', 'TTDspSS': 'm'}
    data = np.array([[float(value.replace('D', 'E')) for value in row]
                     for _, row in rows])
    if not np.isfinite(data).all():
        raise RuntimeError(f'{path}: nonfinite native output')
    if reader.partial or reader.future or abs(data[-1, 0] - seconds) > 5.1e-5:
        raise RuntimeError(f'Incomplete native output: last complete time {data[-1, 0]}, expected {seconds}')
    start = seconds / 2 if discard is None else discard
    if not np.isfinite(start) or not 0 <= start < seconds:
        raise ValueError('discard must be finite and in [0, seconds)')
    window = data[(data[:, 0] >= start) & (data[:, 0] <= seconds)]
    if not len(window):
        raise RuntimeError('No samples in the requested statistics window')
    result = dict(window_seconds=[start, seconds], samples=len(window), channels={})
    if blade_torsion:
        channels.pop('RootMyc1')
        channels.pop('RootMxc1')
        channels.update(B1RootMyr='N-m', B1RootMxr='N-m', B1TipTDxr='m', BlPitch1='deg')
    elif 'OoPDefl1' in reader.names:
        channels['OoPDefl1'] = 'm'
    for channel, unit in channels.items():
        index = reader.names.index(channel)
        values = window[:, index]
        if reader.units[index] != unit or not np.isfinite(values).all():
            raise RuntimeError(f'{path}: invalid {channel} or units')
        result['channels'][channel] = dict(unit=unit, mean=float(values.mean()),
                                          std=float(values.std()), min=float(values.min()),
                                          max=float(values.max()), peak_abs=float(np.abs(values).max()))
    def series(name, unit):
        index = reader.names.index(name)
        if reader.units[index] != unit or not np.isfinite(window[:, index]).all():
            raise RuntimeError(f'{path}: invalid {name} or units')
        return window[:, index]

    common = {'power': ('kW', series('GenPwr', 'kW'))}
    if blade_torsion:
        # OpenFAST v3.5.3 FAST_Solver.f90: SrvD_InputSolve, root r -> coned c.
        pitch = np.radians(series('BlPitch1', 'deg'))
        mx, my = series('B1RootMxr', 'N-m'), series('B1RootMyr', 'N-m')
        dx, dy = series('B1TipTDxr', 'm'), series('B1TipTDyr', 'm')
        common.update(root_edge=('kN-m', (mx * np.cos(pitch) + my * np.sin(pitch)) / 1000.),
                      root_flap=('kN-m', (-mx * np.sin(pitch) + my * np.cos(pitch)) / 1000.),
                      tip_flap_coned=('m', dx * np.cos(pitch) + dy * np.sin(pitch)))
    else:
        common.update(root_edge=('kN-m', series('RootMxc1', 'kN-m')),
                      root_flap=('kN-m', series('RootMyc1', 'kN-m')))
        if 'TipDxc1' in reader.names:
            common['tip_flap_coned'] = ('m', series('TipDxc1', 'm'))
    result['common_frame'] = 'ElastoDyn coned blade frame c; B1, positive x flap displacement'
    result['common_channels'] = {key: dict(unit=unit, mean=float(v.mean()), std=float(v.std()),
                                          min=float(v.min()), max=float(v.max()), peak_abs=float(np.abs(v).max()))
                                 for key, (unit, v) in common.items()}
    if blade_torsion:
        from wfrl.blade_torsion import elastic_twist_degrees, rotation_channels, TORSION_DEFINITION
        from wfcrl.fastfarm_physics import BEAMDYN_NODES
        import csv
        result['torsion_definition'] = TORSION_DEFINITION
        result['torsion'] = {}
        derived = {'Time': data[:, 0]}
        for blade in (1, 2, 3):
            for gauge, node in enumerate(BEAMDYN_NODES, 1):
                names = rotation_channels(blade, gauge)
                indices = [reader.names.index(name) for name in names]
                if any(reader.units[index] != '-' for index in indices):
                    raise RuntimeError('BeamDyn rotation parameters must be dimensionless')
                angles = elastic_twist_degrees(data[:, indices])
                key = f'B{blade}Node{node}Twist'
                derived[key] = angles
                values = angles[(data[:, 0] >= start) & (data[:, 0] <= seconds)]
                result['torsion'][key] = dict(unit='deg', mean=float(values.mean()),
                    std=float(values.std()), min=float(values.min()), max=float(values.max()),
                    peak_abs=float(np.abs(values).max()))
        with Path(path).with_suffix('.torsion.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(derived)
            writer.writerow(['s', *(['deg'] * (len(derived) - 1))])
            writer.writerows(zip(*derived.values()))
    return result


def owned_solver_processes(farm, executable):
    """Capture only this case's live solver, while MPI holds it at a control boundary."""
    import psutil
    found = []
    for process in psutil.process_iter(['pid', 'exe', 'cmdline']):
        try:
            args = process.info['cmdline'] or []
            if str(Path(farm).resolve()) in args and process.info['exe'] and Path(process.info['exe']).resolve() == Path(executable).resolve():
                found.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not found:
        raise RuntimeError('Cannot identify the owned FAST.Farm process; output must not be read')
    return found


def run_case(farm, seconds, turbines, executable, completion_timeout=60., wind_speed=8., discard=None):
    """Worker process owns its MPI lifetime; the parent bounds its wall time."""
    configure_local_mpi()
    from wfcrl import environments  # noqa: F401
    from wfcrl.interface import FastFarmInterface
    from wfcrl.simul_utils import FASTInputFile
    ff = FASTInputFile(str(farm))
    inflow = FASTInputFile(str(Path(farm).parent / ff['InflowFile'].strip('"')))
    requested_speed = wind_speed if int(inflow['WindType']) == 1 else None
    itf = FastFarmInterface(turbines, farm, max_iter=seconds // 3,
                           fast_farm_executable=executable, default_avg_window=1)
    itf.set_wind_request_context(dict(wind_speed=requested_speed, wind_direction=None),
                                record_dir=Path(farm).parent / 'wind_conditions')
    start = time.perf_counter()
    itf.init(wind_speed=requested_speed)
    processes = owned_solver_processes(farm, executable)
    steps = []
    for _ in range(seconds // 3):
        boundary = time.perf_counter()
        itf.update_command(yaw=np.zeros(turbines))
        steps.append(time.perf_counter() - boundary)
    mpi_wall = time.perf_counter() - start
    wait_for_normal_termination(Path(farm).with_suffix('.solver.log'), completion_timeout)
    for process in processes:
        process.wait(timeout=completion_timeout)
    wall = time.perf_counter() - start
    blade_torsion = int(FASTInputFile(str(Path(farm).with_name('FFTest_WT1.fst')))['CompElast']) == 2
    result = dict(wall_seconds=wall, mpi_wall_seconds=mpi_wall,
                  completion_wait_seconds=wall - mpi_wall, completion='FAST.Farm terminated normally; owned native processes exited',
                  control_step_wall_seconds=steps, solver_pids=[p.pid for p in processes],
                  turbine_outputs=[summarize(Path(farm).with_name(f'Case.T{i + 1}.out'), seconds, blade_torsion=blade_torsion, discard=discard)
                                   for i in range(turbines)])
    Path(farm).with_suffix('.comparison.json').write_text(
        json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def execute_case(farm, args, executable):
    command = [sys.executable, '-c',
               'import sys; from scripts.experiments.compare_fastfarm_physics import run_case; '
               'run_case(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], float(sys.argv[5]), float(sys.argv[6]), '
               'None if sys.argv[7] == "None" else float(sys.argv[7]))',
               farm, str(args.seconds), str(args.turbines), str(executable), str(args.timeout), str(args.wind_speed),
               str(getattr(args, 'discard', None))]
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
    parser.add_argument('--seconds', type=int, default=45, help='positive multiple of the 3 s control interval')
    parser.add_argument('--discard', type=float, help='discard seconds; default is the first half')
    parser.add_argument('--uniform-wind', action='store_true', help='explicitly set steady-wind PLExp=0')
    parser.add_argument('--step-scale', type=float, choices=(1., .5), default=1.)
    parser.add_argument('--output-dt', type=float, help='native output interval in seconds')
    parser.add_argument('--aero-nodes', action='store_true', help='output all B1 AeroDyn stations')
    parser.add_argument('--wind-box', type=Path, help='explicit TurbSim BTS file')
    parser.add_argument('--turbines', type=int, choices=(1, 3), default=1)
    parser.add_argument('--wind-speed', type=float, choices=(8., 12.), default=8.)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--variants', nargs='+', choices=tuple(VARIANTS), default=['off', 'tower_flex', 'dynamic_stall', 'tower_shadow', 'all'],
                        help='selected comparisons; must include off as the reference')
    parser.add_argument('--timeout', type=float, default=600., help='maximum worker wall seconds per case (POSIX)')
    parser.add_argument('--fastfarm', required=True, type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds % 3:
        parser.error('--seconds must be a positive multiple of 3')
    if args.discard is not None and (not np.isfinite(args.discard) or not 0 <= args.discard < args.seconds):
        parser.error('--discard must be finite and in [0, seconds)')
    if args.output_dt is not None and (not np.isfinite(args.output_dt) or args.output_dt <= 0):
        parser.error('--output-dt must be finite and positive')
    if args.uniform_wind and args.wind_box:
        parser.error('--uniform-wind and --wind-box are mutually exclusive')
    if args.wind_box:
        args.wind_box = args.wind_box.resolve(strict=True)
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
    report = dict(seconds=args.seconds, turbines=args.turbines, wind_speed_m_s=args.wind_speed,
                  discard_seconds=args.discard, wind_box=(dict(file=str(args.wind_box),
                  sha256=hashlib.sha256(args.wind_box.read_bytes()).hexdigest()) if args.wind_box else None),
                  solver=str(executable), solver_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                  wall_time_scope='MPI spawn through native process exit; excludes case generation',
                  results=[])
    jobs = []
    for repeat in range(args.repeats):
        for name in args.variants:
            physics = VARIANTS[name]
            case_dir = output / f'{name}-{repeat + 1}'
            farm = create_ff_case(dict(num_turbines=args.turbines,
                xcoords=[504. * i for i in range(args.turbines)], ycoords=[0.] * args.turbines,
                max_iter=args.seconds // 3, dt=3, speed=args.wind_speed,
                wind_time_series=str(args.wind_box) if args.wind_box else None, physics=physics),
                output_dir=str(case_dir))
            prepare_case(farm, physics, tower_outputs=True)
            from scripts.experiments.audit_fastfarm_physics import prepare_diagnostics
            diagnostic = prepare_diagnostics(farm, uniform_wind=args.uniform_wind,
                step_scale=args.step_scale, output_dt=args.output_dt, aero_nodes=args.aero_nodes)
            identity = configure_physics(farm, physics)
            write_physics_record(farm, identity)
            result = dict(variant=name, repeat=repeat + 1, physics=identity, status='prepared',
                          farm_file=str(farm), diagnostics=diagnostic)
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
             for channel, values in current['channels'].items() if channel in base['channels']}
            for current, base in zip(result['turbine_outputs'], reference['turbine_outputs'])]
        result['common_change_vs_off'] = [
            {channel: {metric: values[metric] - base['common_channels'][channel][metric]
                       for metric in ('mean', 'std', 'peak_abs')}
             for channel, values in current['common_channels'].items() if channel in base['common_channels']}
            for current, base in zip(result['turbine_outputs'], reference['turbine_outputs'])]
    save()
    print(target, flush=True)


if __name__ == '__main__':
    main()
