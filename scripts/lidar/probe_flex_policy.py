"""Record a real MPI three-turbine policy probe before building flexible replay.

Run with mpiexec -n 1 python -m scripts.lidar.probe_flex_policy.
Outputs are development evidence, never a certified replay package.
"""
from pathlib import Path
import argparse
import json
import hashlib
import shutil
import numpy as np

from wfrl.blender_bridge.flex_policy import FlexPolicy
from wfrl.fastfarm_driver import FastFarmDriver
from scripts.lidar.run_physics import replace
from scripts.experiments.calib_blade_flex import read_fast_out

ROOT = Path(__file__).resolve().parents[2]


def measurement(m, key, n=3):
    raw = m.get(key)
    values = np.full(n, np.nan) if raw is None else np.asarray(raw, float).ravel()
    if values.size != n:
        raise ValueError(f'{key}: expected {n} turbine values, got {values.size}')
    valid = np.isfinite(values)
    if f'{key}_valid' in m:
        declared = np.asarray(m[f'{key}_valid'])
        if declared.dtype.kind != 'b' or declared.size != n:
            raise ValueError(f'{key}_valid: expected {n} boolean turbine flags')
        valid &= declared.ravel()
    return values, valid


def infer_validated(policy, m):
    for key in policy.metadata['obs_keys']:
        _, valid = measurement(m, key)
        if not valid.all():
            turbines = (np.flatnonzero(~valid) + 1).tolist()
            raise ValueError(f'拒绝策略观测: {key} 无效，机组 {turbines}；不做插补')
    return policy.infer(m)


def record_measurement(m):
    row = {}
    for key, label in (('yaw', 'returned_yaw_deg'), ('yaw_command', 'command_yaw_deg'),
                       ('yaw_measured', 'measured_yaw_deg'), ('power', 'power_mw'),
                       ('load', 'load')):
        values, valid = measurement(m, key)
        row[label] = [float(v) if ok else None for v, ok in zip(values, valid)]
        row[f'{key}_valid'] = valid.tolist()
        row[f'{key}_valid_count'] = int(valid.sum())
        row[f'{key}_status'] = '有效数据' if valid.any() else '无有效数据'
    return row


def physical_check(columns, selected, turbine_id):
    def bounds(key):
        values = np.asarray(columns[key], float)[selected]
        values = values[np.isfinite(values)]
        return ([float(values.min()), float(values.max())] if values.size else None,
                int(values.size))

    rpm, rpm_count = bounds('RotSpeed')
    pitch, pitch_count = bounds('BlPitch1')
    yaw = np.asarray(columns['YawPzn'], float)
    final_yaw = float(yaw[-1]) if yaw.size and np.isfinite(yaw[-1]) else None
    finite = all(np.isfinite(v).all() for v in columns.values())
    return dict(turbine_id=turbine_id, rpm_range=rpm, rpm_valid_count=rpm_count,
                rpm_status='有效数据' if rpm_count else '无有效数据',
                pitch_deg_range=pitch, pitch_valid_count=pitch_count,
                pitch_status='有效数据' if pitch_count else '无有效数据',
                final_actual_yaw_deg=final_yaw, final_actual_yaw_valid=final_yaw is not None,
                finite=finite, pitch_tolerance_deg=.05,
                passed=bool(finite and rpm_count and pitch_count and
                            rpm[0] > 0 and rpm[1] < 13.2 and
                            pitch[0] >= -.05 and pitch[1] <= 90.05))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, default=ROOT/'results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--steps', type=int, default=3)
    wind_input = p.add_mutually_exclusive_group()
    wind_input.add_argument('--wind', type=float, default=14,
                            help='Steady wind speed in m/s; mutually exclusive with --turbulence')
    p.add_argument('--controller', type=Path)
    wind_input.add_argument('--turbulence', type=Path,
                            help='BTS input; wind speed is determined by the file')
    p.add_argument('--warmup-steps', type=int, default=0)
    p.add_argument('--surfaces', action='store_true')
    p.add_argument('--prebend', action='store_true', help='Use validated quadratic BeamDyn derivative and separate unloaded reference')
    p.add_argument('--flexible-tower', action='store_true', help='Enable both fore-aft and side-side tower modes')
    p.add_argument('--solver-dt', type=float, help='OpenFAST integration step for numerical sensitivity runs')
    p.add_argument('--tower-nodes', type=int, help='ElastoDyn tower integration nodes (same modal basis)')
    args = p.parse_args()
    if args.steps < 1 or not 3 <= args.wind <= 28:
        p.error('steps must be positive and wind between 3 and 28 m/s')
    if args.solver_dt is not None and (not np.isfinite(args.solver_dt) or args.solver_dt <= 0
            or args.solver_dt > .00625
            or not np.isclose(.00625 / args.solver_dt, round(.00625 / args.solver_dt))):
        p.error('solver-dt must divide the fixed 0.00625 s controller period')
    if args.tower_nodes is not None and not 20 <= args.tower_nodes <= 99:
        p.error('tower-nodes must be between 20 and 99')
    policy = FlexPolicy(args.checkpoint)
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(status='RUNNING', policy=policy.metadata, records=[],
                  wind_mps=None if args.turbulence else args.wind,
                  boundary=('BTS wind input; not random-gust acceptance' if args.turbulence else
                            'steady-wind control probe; not random-gust acceptance'))
    driver = None
    try:
        driver = FastFarmDriver(max_steps=args.steps+args.warmup_steps+2, controls=('yaw',),
                               wind_time_series=str(args.turbulence.resolve()) if args.turbulence else None)
        farm = Path(driver.case_dir)/'FarmInputs'
        if args.turbulence:
            # OpenFAST's parser cannot consume the writer's unquoted absolute
            # filename when the workspace path contains spaces.
            shutil.copyfile(args.turbulence, farm/'replay-wind.bts')
            inflow = farm/'InflowWind.dat'
            lines = inflow.read_text(encoding='utf-8').splitlines()
            matches = [i for i,l in enumerate(lines) if 'FileName_BTS' in l]
            if len(matches) != 1:
                raise ValueError('Missing BTS input field')
            lines[matches[0]] = '"replay-wind.bts" FileName_BTS - local turbulent wind file'
            inflow.write_text('\n'.join(lines)+'\n', encoding='utf-8')
        report.update(case_dir=str(driver.case_dir), control_period_s=driver.dt)
        report['limits'] = dict(yaw_abs_deg=10, yaw_rate_deg_s=.3,
                                driver_sliding_duty_fraction=.3)
        if args.controller:
            for dll in (Path(driver.case_dir)/'5MW_Baseline/ServoData').glob('DISCON*.dll'):
                shutil.copyfile(args.controller, dll)
            report['controller_sha256'] = hashlib.sha256(args.controller.read_bytes()).hexdigest()
        # Record actual OpenFAST poses, separate from the policy's target yaw.
        for f in farm.glob('FFTest_WT*.fst'):
            replace(f, dict(DT_Out=.025, OutFmt='"ES16.8E3"', WrVTK=2 if args.surfaces else 0,
                            VTK_type=1, VTK_fps=40))
            if args.solver_dt is not None:
                replace(f, dict(DT=args.solver_dt))
        if args.solver_dt is not None:
            # Keep the baseline controller sample period while refining physics.
            for f in farm.glob('*ServoDyn*.dat'):
                replace(f, dict(DLL_DT=.00625))
        # Tower flexibility changes the reference frame and requires a matching exporter.
        for f in farm.glob('*ElastoDyn*.dat'):
            if 'TwFADOF1' in f.read_text(encoding='utf-8'):
                value = 'True' if args.flexible_tower else 'False'
                replace(f, dict(TwFADOF1=value, TwFADOF2=value, TwSSDOF1=value, TwSSDOF2=value))
                if args.tower_nodes is not None:
                    replace(f, dict(TwrNodes=args.tower_nodes))
                if args.flexible_tower:
                    lines = f.read_text(encoding='utf-8').splitlines()
                    end = next(i for i, line in enumerate(lines) if line.startswith('END'))
                    channels = ['TTDspFA', 'TTDspSS', 'YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt',
                                'YawBrRDxt', 'YawBrRDyt', 'YawBrRDzt', 'BlPitch2', 'BlPitch3']
                    lines[end:end] = ['"' + c + '"' for c in channels if '"' + c + '"' not in lines]
                    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        if args.prebend:
            if not args.flexible_tower:
                raise ValueError('Prebend v3 requires the validated flexible-tower path')
            source = ROOT/'results/prebend-20260918/C'
            baseline = Path(driver.case_dir)/'5MW_Baseline'
            for name in ('NRELOffshrBsline5MW_BeamDyn.dat', 'NRELOffshrBsline5MW_BeamDyn_Blade.dat',
                         'NRELOffshrBsline5MW_AeroDyn_blade.dat'):
                shutil.copyfile(source/'5MW_Baseline'/name, baseline/name)
            for f in farm.glob('FFTest_WT*.fst'):
                replace(f, dict(CompElast=2, **{f'BDBldFile({b})': '"../5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat"' for b in (1,2,3)}))
            for f in farm.glob('*ElastoDyn*.dat'):
                if 'FlapDOF1' in f.read_text(encoding='utf-8'):
                    replace(f, dict(FlapDOF1='False', FlapDOF2='False', EdgeDOF='False'))
            report['blade_model'] = 'beamdyn-prebend'
            report['blade_config'] = json.loads((source/'probe-config.json').read_text(encoding='utf-8'))
            report['unloaded_reference'] = str((ROOT/'results/prebend-20260918/C-rest').resolve())
            report['blade_input_hashes'] = {f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                for f in baseline.glob('*BeamDyn*')}
        report['tower'] = 'flexible' if args.flexible_tower else 'rigid'
        report['output_fps'] = 40
        report['numerical_settings'] = dict(solver_dt_s=args.solver_dt or .00625,
            controller_dt_s=.00625, tower_nodes=args.tower_nodes or 20,
            surfaces=args.surfaces, aerodynamic_nodes=19, farm_dt_low_s=3., farm_dt_high_s=.05)
        report['input_hashes'] = {str(f.relative_to(farm)): hashlib.sha256(f.read_bytes()).hexdigest()
                                  for f in farm.glob('*.fst')}
        # MPI abort can terminate Python before finally; preserve inputs first.
        (args.output/'probe.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
        m = driver.reset() if args.turbulence else driver.reset(wind_speed=args.wind)
        for _ in range(args.warmup_steps):
            m = driver.step(np.zeros(3))
        report['boundary'] = ('random full-field wind, policy-controlled yaw, native pitch/torque; '
                              'development run, numerical and visual acceptance pending') if args.turbulence else report['boundary']
        if args.turbulence:
            report['wind_sha256'] = hashlib.sha256(args.turbulence.read_bytes()).hexdigest()
        for k in range(args.steps):
            raw, norm = infer_validated(policy, m)
            # Execute limits in the physics driver, never scale display poses.
            cmd = np.clip(raw, -0.3*driver.dt, 0.3*driver.dt)
            cmd = np.clip(np.asarray(m['yaw'])+cmd, -10., 10.)-m['yaw']
            previous = np.asarray(m['yaw']).copy()
            m = driver.step(cmd)
            if m.get('done'):
                raise RuntimeError('Solver ended before the requested control samples')
            row = dict(step=k+1, normalized_obs=norm.tolist(),
                       policy_delta_deg=raw.tolist(), bounded_delta_deg=cmd.tolist(),
                       previous_yaw_deg=previous.tolist(), **record_measurement(m))
            report['records'].append(row)
            (args.output/'probe.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
            print(json.dumps(row), flush=True)
        driver.close()
        report['physical_checks'] = []
        for tid in ('T1', 'T2', 'T3'):
            c = read_fast_out(farm/f'Case.{tid}.out')
            selected = c['Time'] >= 27+args.warmup_steps*driver.dt
            checks = physical_check(c, selected, tid)
            report['physical_checks'].append(checks)
        if not all(c['passed'] for c in report['physical_checks']):
            raise ValueError('Native output failed rotor-speed/pitch validation')
        report['status'] = 'CONTROL_PROBE_COMPLETE'
    except Exception as exc:
        report.update(status='FAILED', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        if driver is not None:
            driver.close()
        (args.output/'probe.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
