"""Compare same-time three-turbine numerical refinements; never certify model accuracy."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from scripts.experiments.calib_blade_flex import read_fast_out


def metrics(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('Invalid comparison arrays')
    d = b - a
    return dict(rms_difference=float(np.sqrt(np.mean(d*d))),
                max_absolute_difference=float(np.max(np.abs(d))),
                baseline_mean=float(a.mean()), refined_mean=float(b.mean()),
                baseline_min=float(a.min()), refined_min=float(b.min()),
                baseline_max=float(a.max()), refined_max=float(b.max()),
                baseline_std=float(a.std()), refined_std=float(b.std()))


def compare_runs(first, second):
    first, second = map(Path, (first, second))
    reports = [json.loads(p.read_text()) for p in (first, second)]
    for report in reports:
        if report['status'] != 'CONTROL_PROBE_COMPLETE' or report['tower'] != 'flexible':
            raise ValueError('Expected completed flexible-tower probes')
    for key in ('wind_sha256', 'controller_sha256', 'control_period_s'):
        if reports[0][key] != reports[1][key]:
            raise ValueError('Different numerical experiment inputs: '+key)
    if reports[0]['policy']['sha256'] != reports[1]['policy']['sha256']:
        raise ValueError('Different policies')
    rows = []
    sources = []
    for tid in ('T1', 'T2', 'T3'):
        channels = []
        for report in reports:
            path = Path(report['case_dir'])/'FarmInputs'/f'Case.{tid}.out'
            source = read_fast_out(path)
            t = source['Time']; keep = (t >= 117) & (t <= 177)
            if not np.allclose(t[keep], np.linspace(117, 177, 2401), atol=1e-8, rtol=0):
                raise ValueError('Incorrect output time grid')
            if not all(np.isfinite(x).all() for x in source.values()):
                raise ValueError('Nonfinite solver output')
            channels.append({key: values[keep] for key, values in source.items()})
            sources.append(dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        a, b = channels
        names = ['YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt', 'YawBrRDxt', 'YawBrRDyt', 'YawBrRDzt',
                 'TTDspFA', 'TTDspSS', 'RotSpeed', 'GenPwr', 'YawPzn', 'Azimuth']
        names += [f'{prefix}{blade}' for prefix in ('OoPDefl', 'IPDefl', 'BlPitch') for blade in (1, 2, 3)]
        values = {name: metrics(a[name], b[name]) for name in names}
        xyz = np.column_stack([b[k]-a[k] for k in ('YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt')])
        norm = np.linalg.norm(xyz, axis=1)
        tower_rms, tower_max = float(np.sqrt(np.mean(norm**2))), float(norm.max())
        blades = [values[f'{p}{i}'] for p in ('OoPDefl', 'IPDefl') for i in (1, 2, 3)]
        blade_rms = max(x['rms_difference'] for x in blades)
        blade_max = max(x['max_absolute_difference'] for x in blades)
        mean_power_relative = abs(float(b['GenPwr'].mean()-a['GenPwr'].mean())) / max(abs(float(a['GenPwr'].mean())), 1e-12)
        checks = dict(tower_rms=tower_rms <= .01, tower_max=tower_max <= .03,
                      blade_rms=blade_rms <= .02, blade_max=blade_max <= .05,
                      rpm_rms=values['RotSpeed']['rms_difference'] <= .05,
                      power_mean=mean_power_relative <= .02)
        rows.append(dict(turbine=tid, channels=values, tower_vector_rms_m=tower_rms,
                         tower_vector_max_m=tower_max, blade_component_rms_max_m=blade_rms,
                         blade_component_abs_max_m=blade_max, power_mean_relative_difference=mean_power_relative,
                         checks=checks, passed=all(checks.values())))
    commands = [np.array([r['bounded_delta_deg'] for r in x['records']]) for x in reports]
    return dict(first_probe=str(first), second_probe=str(second), sources=sources, turbines=rows,
                max_policy_command_difference_deg=float(np.max(np.abs(commands[1]-commands[0]))),
                passed=all(row['passed'] for row in rows))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline', type=Path); p.add_argument('half', type=Path)
    p.add_argument('quarter', type=Path); p.add_argument('tower40', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    comparisons = dict(base_half=compare_runs(a.baseline, a.half),
                       half_quarter=compare_runs(a.half, a.quarter),
                       base_quarter=compare_runs(a.baseline, a.quarter),
                       tower_20_40=compare_runs(a.baseline, a.tower40))
    trends = []
    for coarse, fine in zip(comparisons['base_half']['turbines'], comparisons['half_quarter']['turbines']):
        trends.append(dict(turbine=coarse['turbine'],
            tower_rms_nonincreasing=fine['tower_vector_rms_m'] <= coarse['tower_vector_rms_m'],
            blade_rms_nonincreasing=fine['blade_component_rms_max_m'] <= coarse['blade_component_rms_max_m']))
    result = dict(schema='wfrl.tower-numerical-sensitivity.v1', segment_s=[117,177], samples_per_turbine=2401,
                  comparisons=comparisons, refinement_trends=trends,
                  scope='OpenFAST integration time and ElastoDyn tower modal integration quadrature only; no global convergence certificate',
                  numerical_screen_passed=all(c['passed'] for c in comparisons.values()) and
                     all(t['tower_rms_nonincreasing'] and t['blade_rms_nonincreasing'] for t in trends))
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'comparisons'}, indent=2))


if __name__ == '__main__':
    main()
