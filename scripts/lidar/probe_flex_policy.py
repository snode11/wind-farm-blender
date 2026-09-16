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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, default=ROOT/'results/checkpoints/mappo_fastfarm_Dec_Turb3_Row1_Fastfarm_mappo_s0_level_E128_none.pt')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--steps', type=int, default=3)
    p.add_argument('--wind', type=float, default=14)
    p.add_argument('--controller', type=Path)
    p.add_argument('--turbulence', type=Path)
    p.add_argument('--warmup-steps', type=int, default=0)
    p.add_argument('--surfaces', action='store_true')
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
                  wind_mps=args.wind, boundary='steady-wind control probe; not random-gust acceptance')
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
            lines = inflow.read_text().splitlines()
            matches = [i for i,l in enumerate(lines) if 'FileName_BTS' in l]
            if len(matches) != 1:
                raise ValueError('Missing BTS input field')
            lines[matches[0]] = '"replay-wind.bts" FileName_BTS - local turbulent wind file'
            inflow.write_text('\n'.join(lines)+'\n')
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
            if 'TwFADOF1' in f.read_text():
                value = 'True' if args.flexible_tower else 'False'
                replace(f, dict(TwFADOF1=value, TwFADOF2=value, TwSSDOF1=value, TwSSDOF2=value))
                if args.tower_nodes is not None:
                    replace(f, dict(TwrNodes=args.tower_nodes))
                if args.flexible_tower:
                    lines = f.read_text().splitlines()
                    end = next(i for i, line in enumerate(lines) if line.startswith('END'))
                    channels = ['TTDspFA', 'TTDspSS', 'YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt',
                                'YawBrRDxt', 'YawBrRDyt', 'YawBrRDzt', 'BlPitch2', 'BlPitch3']
                    lines[end:end] = ['"' + c + '"' for c in channels if '"' + c + '"' not in lines]
                    f.write_text('\n'.join(lines) + '\n')
        report['tower'] = 'flexible' if args.flexible_tower else 'rigid'
        report['output_fps'] = 40
        report['numerical_settings'] = dict(solver_dt_s=args.solver_dt or .00625,
            controller_dt_s=.00625, tower_nodes=args.tower_nodes or 20,
            surfaces=args.surfaces, aerodynamic_nodes=19, farm_dt_low_s=3., farm_dt_high_s=.05)
        report['input_hashes'] = {str(f.relative_to(farm)): hashlib.sha256(f.read_bytes()).hexdigest()
                                  for f in farm.glob('*.fst')}
        m = driver.reset(wind_speed=args.wind)
        for _ in range(args.warmup_steps):
            m = driver.step(np.zeros(3))
        report['boundary'] = ('random full-field wind, policy-controlled yaw, native pitch/torque; '
                              'development run, numerical and visual acceptance pending') if args.turbulence else report['boundary']
        if args.turbulence:
            report['wind_sha256'] = hashlib.sha256(args.turbulence.read_bytes()).hexdigest()
        for k in range(args.steps):
            raw, norm = policy.infer(m)
            # Execute limits in the physics driver, never scale display poses.
            cmd = np.clip(raw, -0.3*driver.dt, 0.3*driver.dt)
            cmd = np.clip(np.asarray(m['yaw'])+cmd, -10., 10.)-m['yaw']
            previous = np.asarray(m['yaw']).copy()
            m = driver.step(cmd)
            if m.get('done'):
                raise RuntimeError('Solver ended before the requested control samples')
            actual = driver._raw_measure('yaw')
            row = dict(step=k+1, normalized_obs=norm.tolist(),
                       policy_delta_deg=raw.tolist(), bounded_delta_deg=cmd.tolist(),
                       previous_yaw_deg=previous.tolist(),
                       returned_yaw_deg=np.asarray(m['yaw']).tolist(),
                       measured_yaw_deg=None if actual is None else np.degrees(actual).tolist(),
                       power_mw=np.asarray(m['power']).tolist())
            report['records'].append(row)
            (args.output/'probe.json').write_text(json.dumps(report, indent=2, allow_nan=False))
            print(json.dumps(row), flush=True)
        driver.close()
        report['physical_checks'] = []
        for tid in ('T1', 'T2', 'T3'):
            c = read_fast_out(farm/f'Case.{tid}.out')
            selected = c['Time'] >= 27+args.warmup_steps*driver.dt
            rpm, pitch = c['RotSpeed'][selected], c['BlPitch1'][selected]
            checks = dict(turbine_id=tid, rpm_range=[float(rpm.min()), float(rpm.max())],
                          pitch_deg_range=[float(pitch.min()), float(pitch.max())],
                          final_actual_yaw_deg=float(c['YawPzn'][-1]),
                          finite=all(np.isfinite(v).all() for v in c.values()))
            # Same documented one-solver-step actuator tolerance as the prior audit.
            checks['pitch_tolerance_deg'] = .05
            checks['passed'] = bool(checks['finite'] and rpm.min()>0 and rpm.max()<13.2
                                    and pitch.min()>=-.05 and pitch.max()<=90.05)
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
        (args.output/'probe.json').write_text(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
