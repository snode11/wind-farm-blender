"""Compare official and polar-consistent UA with completed FAST.Farm runs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from scripts.experiments.audit_fastfarm_physics import prepare_diagnostics, summarize_comparison
from scripts.experiments.compare_fastfarm_physics import execute_case

VARIANTS = {
    'off': {},
    'official': {'dynamic_stall': True},
    'polar_consistent': {'dynamic_stall': True, 'dynamic_stall_parameters': 'polar_consistent'},
    'tower_flex': {'tower_flex': True},
    'tower_shadow': {'tower_shadow': True},
    'all': dict(tower_flex=True, dynamic_stall=True, tower_shadow=True),
}
CHANNELS = ('GenPwr', 'RootMyc1', 'RootMxc1', 'TwrBsMyt', 'TwrBsMxt')


def convergence(current, reference, absolute_fraction=1e-4):
    """Suggested A3: 5% fluctuation change OR 0.01% of reference RMS.

    Means use 1% of reference RMS; RMS and absolute peaks keep their 5%
    relative limits. Undefined relative changes remain null. The additional
    dimensional allowance applies only to standard deviation and DEL.
    """
    if not np.isfinite(absolute_fraction) or absolute_fraction < 0:
        raise ValueError('absolute_fraction must be finite and nonnegative')
    result = {}
    for key in ('mean', 'rms', 'std', 'peak_abs', 'del_1hz'):
        denominator = reference['rms'] if key == 'mean' else abs(reference[key])
        difference = abs(current[key]-reference[key])
        relative_limit = (.01 if key == 'mean' else .05)*denominator
        absolute_limit = absolute_fraction*reference['rms'] if key in ('std', 'del_1hz') else 0.
        result[key] = dict(absolute_difference=difference,
            relative_percent=100*difference/denominator if denominator else None,
            absolute_difference_over_reference_rms_percent=100*difference/reference['rms'] if reference['rms'] else None,
            relative_limit=relative_limit, absolute_limit=absolute_limit,
            original_relative_pass=bool(difference <= relative_limit),
            suggested_pass=bool(difference <= max(relative_limit, absolute_limit)))
    return result


def run_group(output, names, seconds, speed, executable, timeout, *, wind_box=None, step_scale=1.):
    from wfcrl.simul_utils import create_ff_case
    from wfcrl.fastfarm_physics import configure_physics, write_physics_record
    from wfrl.solver_outputs import prepare_case
    output.mkdir(parents=True, exist_ok=False)
    report = dict(seconds=seconds, discard_seconds=60., turbines=1, wind_speed_m_s=speed,
                  solver=str(executable), solver_sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
                  wind_box_sha256=hashlib.sha256(wind_box.read_bytes()).hexdigest() if wind_box else None,
                  results=[])
    target = output/'comparison.json'

    def save():
        target.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')

    for name in names:
        physics = VARIANTS[name]
        farm = create_ff_case(dict(num_turbines=1, xcoords=[0.], ycoords=[0.],
            max_iter=seconds//3, dt=3, speed=speed,
            wind_time_series=str(wind_box) if wind_box else None, physics=physics), str(output/name))
        prepare_case(farm, physics, tower_outputs=True)
        diagnostic = prepare_diagnostics(farm, uniform_wind=wind_box is None,
            step_scale=step_scale, output_dt=.025, aero_nodes=True)
        identity = configure_physics(farm, physics)
        write_physics_record(farm, identity)
        result = dict(variant=name, repeat=1, physics=identity, farm_file=farm,
                      diagnostics=diagnostic, status='running')
        report['results'].append(result)
        save()
        try:
            result.update(execute_case(farm, SimpleNamespace(seconds=seconds, turbines=1,
                timeout=timeout, wind_speed=speed, discard=60.), executable))
            result['status'] = 'completed'
        except BaseException as exc:
            result.update(status='failed', error=str(exc))
            save()
            raise
        save()
        print(output.name, name, round(result['wall_seconds'], 3), flush=True)
    audit = summarize_comparison(target, discard=60.)
    (output/'audit.json').write_text(json.dumps(audit, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--fastfarm', type=Path, required=True)
    parser.add_argument('--wind-box', type=Path)
    parser.add_argument('--groups', nargs='+', choices=('uniform8', 'uniform12', 'turbulence8', 'half8'),
                        default=['uniform8', 'uniform12', 'turbulence8', 'half8'])
    parser.add_argument('--timeout', type=float, default=600.)
    args = parser.parse_args()
    if 'turbulence8' in args.groups and not args.wind_box:
        parser.error('turbulence8 requires --wind-box')
    if not np.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('--timeout must be finite and positive')
    executable = args.fastfarm.resolve(strict=True)
    wind_box = args.wind_box.resolve(strict=True) if args.wind_box else None
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Simulator helper files and compiler/module caches belong to the temporary run.
    os.chdir(output)
    audits = {}
    for group in args.groups:
        audits[group] = run_group(output/group,
            list(VARIANTS) if group in ('uniform8', 'half8') else list(VARIANTS)[:3],
            360 if group == 'turbulence8' else 180, 12. if group == 'uniform12' else 8.,
            executable, args.timeout, wind_box=wind_box if group == 'turbulence8' else None,
            step_scale=.5 if group == 'half8' else 1.)
    if {'uniform8', 'half8'} <= audits.keys():
        fine = {c['variant']: c for c in audits['half8']['cases']}
        a3 = {c['variant']: {channel: convergence(fine[c['variant']]['metrics'][channel], c['metrics'][channel])
                           for channel in CHANNELS} for c in audits['uniform8']['cases']}
        (output/'A3.json').write_text(json.dumps(a3, indent=2, allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
