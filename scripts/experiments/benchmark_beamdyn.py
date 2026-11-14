"""Measure BeamDyn candidates against a fixed 60 s reference in owned temporary cases."""
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

REFERENCE_DT = .00125
PRIMARY_SETTINGS = ('DT', 'InterpOrder', 'NumCrctn')
CANDIDATES = {
    'reference': {},
    'dt0025': {'DT': .0025},
    'dt005': {'DT': .005},
    'dt01': {'DT': .01},
    'gauss5': {'quadrature': 1},
    'gauss7': {'quadrature': 1, 'order_elem': 7},
    'order3': {'order_elem': 3},
    'order4': {'order_elem': 4},
    'order6': {'order_elem': 6},
    'refine2': {'refine': 2},
    'tol1e4': {'stop_tol': 1e-4},
    'tol1e3': {'stop_tol': 1e-3},
    'nr5': {'NRMax': 5},
    'nr20': {'NRMax': 20},
    'rho05': {'rhoinf': .5},
    'rho1': {'rhoinf': 1.},
    'retry0': {'load_retries': 0},
    'nfact1': {'n_fact': 1},
    'nfact10': {'n_fact': 10},
    'substep': {'DT': .0025, 'DTBeam': .00125},
    'coarse_beam': {'DTBeam': .0025},
    'interp1': {'InterpOrder': 1},
    'omp2': {'threads': 2},
    'omp3': {'threads': 3},
    'omp6': {'threads': 6},
    'minimal': {'minimal': True},
    'dt0015625': {'DT': .0015625},
    'dt0016667': {'DT': 1 / 600},
    'dt0017857': {'DT': 1 / 560},
    'dt002': {'DT': .002},
    'dt0020833': {'DT': 1 / 480},
    'dt0021739': {'DT': 1 / 460},
    'dt0022727': {'DT': 1 / 440},
    'dt003125': {'DT': .003125},
    'dt00625': {'DT': .00625},
    'retry1': {'load_retries': 1},
    'substep005': {'DT': .005, 'DTBeam': .00125},
    'production_fast': {'profile': 'fast'},
    'production_reference': {'profile': 'reference'},
    'reference_omp3': {'profile': 'reference', 'threads': 3},
    'fast_omp3': {'profile': 'fast', 'threads': 3},
    'fast_omp6': {'profile': 'fast', 'threads': 6},
}
for _order in (3, 4):
    for _dt in (.0025, .005):
        CANDIDATES[f'order{_order}_dt{str(_dt).split(".")[1]}'] = {'order_elem': _order, 'DT': _dt}
for _order in (5, 7):
    CANDIDATES[f'gauss{_order}_dt0025'] = {'quadrature': 1, 'order_elem': _order, 'DT': .0025}
for _dt in (.0025, .005, .00625, .01, .0125):
    CANDIDATES[f'correct1_dt{str(_dt).split(".")[1]}'] = {'DT': _dt, 'NumCrctn': 1}
for _dt in (.0025, .005, .01):
    CANDIDATES[f'interp1_dt{str(_dt).split(".")[1]}'] = {'DT': _dt, 'InterpOrder': 1}
CANDIDATES.update(correct2_dt01={'DT': .01, 'NumCrctn': 2},
                  nr20_dt0025={'DT': .0025, 'NRMax': 20}, nfact1_dt0025={'DT': .0025, 'n_fact': 1})


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def summarize_metrics(path, seconds, *, blade_torsion=True, discard=30.):
    """Called only after the native solver exits; keep every blade's acceptance metrics."""
    from wfrl.solver_outputs import OutputReader
    from wfrl.blade_torsion import elastic_twist_degrees
    reader = OutputReader(path)
    rows = []
    size = Path(path).stat().st_size
    while reader.offset < size and not reader.error:
        reader.advance(seconds + 1.)
        rows.extend(reader.interval)
    if reader.error or not rows or reader.partial or reader.future:
        raise ValueError(f'Incomplete output: {path}: {reader.error}')
    data = np.array([[float(x.replace('D', 'E')) for x in row] for _, row in rows])
    if not np.isfinite(data).all() or abs(data[-1, 0] - seconds) > 5.1e-5:
        raise ValueError('Nonfinite or unfinished native output')
    window = data[(data[:, 0] >= discard) & (data[:, 0] <= seconds)]
    if len(window) < 2:
        raise ValueError('Insufficient statistics window')

    def series(name, unit):
        i = reader.names.index(name)
        if reader.units[i] != unit:
            raise ValueError(f'{name}: expected {unit}, got {reader.units[i]}')
        return window[:, i]

    values = {'power': ('kW', series('GenPwr', 'kW'))}
    for blade in (1, 2, 3):
        pitch = np.radians(series(f'BlPitch{blade}', 'deg'))
        mx, my = (series(f'B{blade}RootM{a}r', 'N-m') for a in 'xy')
        dx, dy = (series(f'B{blade}TipTD{a}r', 'm') for a in 'xy')
        rotation = np.column_stack([series(f'B{blade}TipRD{a}r', '-') for a in 'xyz'])
        values.update({
            f'B{blade}_root_edge': ('kN-m', (mx * np.cos(pitch) + my * np.sin(pitch)) / 1000),
            f'B{blade}_root_flap': ('kN-m', (-mx * np.sin(pitch) + my * np.cos(pitch)) / 1000),
            f'B{blade}_tip_flap': ('m', dx * np.cos(pitch) + dy * np.sin(pitch)),
            f'B{blade}_tip_twist': ('deg', elastic_twist_degrees(rotation)),
        })
    return dict(samples=len(window), window_seconds=[discard, seconds], output_sha256=sha256(path),
                metrics={key: dict(unit=unit, mean=float(v.mean()), std=float(v.std()),
                                   min=float(v.min()), max=float(v.max()))
                         for key, (unit, v) in values.items()})


def compare_metrics(current, reference):
    """Strict per-channel thresholds, with absolute degrees only for torsion mean."""
    checks = {}
    if len(current) != len(reference):
        raise ValueError('Turbine count mismatch')
    for t, (cur, ref) in enumerate(zip(current, reference), 1):
        if cur['samples'] != ref['samples'] or cur['window_seconds'] != ref['window_seconds']:
            raise ValueError('Sampling window mismatch')
        for key, r in ref['metrics'].items():
            c = cur['metrics'][key]
            if c['unit'] != r['unit']:
                raise ValueError('Metric unit mismatch')
            twist = key.endswith('tip_twist')
            dm, ds = abs(c['mean'] - r['mean']), abs(c['std'] - r['std'])
            mean_limit = .05 if twist else .01 * abs(r['mean'])
            std_limit = (.1 if twist else .05) * abs(r['std'])
            checks[f'T{t}/{key}'] = dict(mean_abs=dm, std_abs=ds,
                mean_percent=100 * dm / abs(r['mean']) if r['mean'] else None,
                std_percent=100 * ds / abs(r['std']) if r['std'] else None,
                passed=bool(dm <= mean_limit and ds <= std_limit))
    return dict(passed=all(v['passed'] for v in checks.values()), checks=checks)


def prepare(output, settings, seconds, turbines, speed):
    from wfcrl.simul_utils import FASTInputFile, create_ff_case
    from wfcrl.fastfarm_physics import normalize_physics
    from wfrl.solver_outputs import prepare_case
    physics = {'blade_torsion': True}
    try:
        normalize_physics({**physics, 'blade_torsion_fidelity': 'reference'})
    except ValueError:
        if 'profile' in settings:
            raise ValueError('Production fidelity profiles are not supported by this checkout')
        pass
    else:
        physics['blade_torsion_fidelity'] = settings.get('profile', 'reference')
    farm = Path(create_ff_case(dict(num_turbines=turbines, xcoords=[504. * i for i in range(turbines)],
        ycoords=[0.] * turbines, max_iter=seconds // 3, dt=3, speed=speed,
        wind_time_series=None, physics=physics), str(output)))
    prepare_case(farm, physics, tower_outputs=True)
    if 'profile' in settings:
        hashes = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}
        return farm, hashes
    primary_files = [farm.with_name(f'FFTest_WT{i + 1}.fst') for i in range(turbines)]
    first = FASTInputFile(str(primary_files[0]))
    original = (farm.parent / first['BDBldFile(1)'].strip('"')).resolve()
    bd = FASTInputFile(str(original))
    derived = original.with_name('DW_BeamDyn.dat')
    for key, value in settings.items():
        if key not in (*PRIMARY_SETTINGS, 'threads', 'minimal'):
            bd[key] = value
    if settings.get('minimal') or int(bd['quadrature']) == 1:
        bd['NNodeOuts'] = 0
        bd['OutList'] = ['', 'RootMxr', 'RootMyr', 'TipTDxr', 'TipTDyr', 'TipRDxr', 'TipRDyr', 'TipRDzr']
    elif settings.get('refine') == 2:
        bd['OutNd'] = [1, 19, 37, 55, 73, 97]
    bd.write(str(derived))
    for path in primary_files:
        primary = FASTInputFile(str(path))
        for key in PRIMARY_SETTINGS:
            if key in settings:
                primary[key] = settings[key]
        for blade in (1, 2, 3):
            primary[f'BDBldFile({blade})'] = '"../5MW_Baseline/DW_BeamDyn.dat"'
        primary.write(str(path))
    # Experimental inputs intentionally bypass the production profile validator.
    # Their own complete file hashes replace, rather than reuse, the pre-patch identity.
    farm.with_suffix('.physics.json').unlink(missing_ok=True)
    hashes = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}
    return farm, hashes


def cleanup(process, descendants):
    """Terminate only this worker's captured descendants and its private process group."""
    import psutil
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    alive = []
    for p in descendants.values():
        try:
            if p.is_running():
                p.terminate()
                alive.append(p)
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    if process.poll() is None:
        process.kill()
    process.wait()
    for p in alive:
        try:
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                raise RuntimeError('Owned worker descendants survived cleanup')
        except psutil.NoSuchProcess:
            pass


def execute(farm, args, threads):
    import psutil
    command = [sys.executable, '-m', 'scripts.experiments.benchmark_beamdyn', '--worker', str(farm),
               '--seconds', str(args.seconds), '--turbines', str(args.turbines),
               '--wind-speed', str(args.wind_speed), '--discard', str(args.discard),
               '--timeout', str(args.timeout), '--fastfarm', str(args.fastfarm)]
    log = farm.with_suffix('.solver.log')
    descendants = {}
    start = time.monotonic()
    with log.open('wb') as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, 'OMP_NUM_THREADS': str(threads), 'PYTHONDONTWRITEBYTECODE': '1'})
        parent = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                try:
                    for child in parent.children(recursive=True):
                        descendants[child.pid] = child
                except psutil.NoSuchProcess:
                    break
                if time.monotonic() - start > args.timeout:
                    raise TimeoutError('Solver worker timeout')
                with log.open('rb') as source:
                    source.seek(0, 2)
                    source.seek(max(0, source.tell() - 8192))
                    tail = source.read().decode('utf-8', errors='replace')
                if any(token in tail for token in ('FAST.Farm encountered an error', 'Aborting FAST.Farm',
                                                   'MPI_ABORT was invoked', 'MPI_ABORT was called')):
                    raise RuntimeError(tail[-4000:])
                time.sleep(.1)
            if process.returncode:
                raise RuntimeError(f'Worker exited {process.returncode}: {log.read_text(encoding="utf-8", errors="replace")[-4000:]}')
            return json.loads(farm.with_suffix('.comparison.json').read_text(encoding='utf-8'))
        finally:
            cleanup(process, descendants)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--discard', type=float, default=30.)
    parser.add_argument('--turbines', type=int, choices=(1, 3), default=1)
    parser.add_argument('--wind-speed', type=float, choices=(8., 12.), default=8.)
    parser.add_argument('--winds', nargs='+', type=float, choices=(8., 12.), default=[8., 12.])
    parser.add_argument('--variants', nargs='*', choices=list(CANDIDATES), default=['reference', 'dt0025', 'dt005'])
    parser.add_argument('--settings', type=Path, help='JSON object of additional named candidate settings')
    parser.add_argument('--reference-report', type=Path)
    parser.add_argument('--timeout', type=float, default=600.)
    parser.add_argument('--fastfarm', type=Path, default=Path('/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm'))
    args = parser.parse_args()
    if args.seconds <= 0 or args.seconds % 3 or not 0 <= args.discard < args.seconds:
        parser.error('seconds must be a positive multiple of 3; discard must precede the end')
    if not np.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('timeout must be finite and positive')
    args.fastfarm = args.fastfarm.resolve(strict=True)
    for key in ('settings', 'reference_report'):
        if getattr(args, key) is not None:
            setattr(args, key, getattr(args, key).resolve(strict=True))
    if args.worker:
        from scripts.experiments import compare_fastfarm_physics as runner
        runner.summarize = summarize_metrics
        runner.run_case(str(args.worker), args.seconds, args.turbines, str(args.fastfarm),
                        args.timeout, args.wind_speed, args.discard)
        return
    if not args.output:
        parser.error('--output is required')
    output = args.output.resolve()
    if not output.is_relative_to(Path('/private/tmp')):
        parser.error('benchmark outputs must be under /private/tmp')
    output.mkdir(parents=True, exist_ok=False)
    os.chdir(output)
    from wfrl.mpi_runtime import configure_local_mpi
    configure_local_mpi()
    candidates = {name: CANDIDATES[name] for name in args.variants}
    if args.settings:
        candidates.update(json.loads(args.settings.read_text(encoding='utf-8')))
    references = {}
    if args.reference_report:
        old = json.loads(args.reference_report.read_text(encoding='utf-8'))
        if (old['seconds'], old['discard'], old['turbines'], old['solver_sha256']) != (
                args.seconds, args.discard, args.turbines, sha256(args.fastfarm)):
            parser.error('reference duration, window, turbines or solver does not match')
        references = {r['wind_speed']: r for r in old['results']
                      if r['variant'] in ('reference', 'production_reference') and r['status'] == 'completed'}
    report = dict(seconds=args.seconds, discard=args.discard, turbines=args.turbines,
        solver=str(args.fastfarm), solver_sha256=sha256(args.fastfarm),
        sampling='0.25 s; inclusive window; population standard deviation; all three blades',
        wall_scope='MPI spawn through native exit; control steps exclude initialization and output parsing', results=[])
    target = output / 'benchmark.json'

    def save():
        target.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    for name, settings in candidates.items():
        for speed in args.winds:
            args.wind_speed = speed
            result = dict(variant=name, settings=settings, wind_speed=speed, status='preparing')
            report['results'].append(result)
            save()
            try:
                farm, hashes = prepare(output / f'{name}-{speed:g}', settings, args.seconds, args.turbines, speed)
                result.update(status='running', input_sha256=hashes,
                    input_identity=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest())
                save()
                result.update(execute(farm, args, settings.get('threads', 1)))
                result['status'] = 'completed'
                if name in ('reference', 'production_reference'):
                    references[speed] = result
                if speed in references:
                    result['accuracy'] = compare_metrics(result['turbine_outputs'], references[speed]['turbine_outputs'])
            except Exception as exc:
                result.update(status='failed', error=str(exc))
            save()
            print(json.dumps({k: result[k] for k in ('variant', 'wind_speed', 'status', 'wall_seconds') if k in result}
                             | {'passed': result.get('accuracy', {}).get('passed')}), flush=True)
    print(target, flush=True)


if __name__ == '__main__':
    main()
