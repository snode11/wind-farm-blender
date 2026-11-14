"""Opt-in diagnostics and finite-window metrics for FAST.Farm physics comparisons."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np


def prepare_diagnostics(farm, *, uniform_wind=False, step_scale=1., output_dt=None,
                        aero_nodes=False):
    """Patch generated inputs only; the all-default call does not write any bytes."""
    from wfcrl.simul_utils import FASTInputFile
    if step_scale not in (1., .5):
        raise ValueError('step_scale must be 1 or 0.5')
    if output_dt is not None and (not np.isfinite(output_dt) or output_dt <= 0):
        raise ValueError('output_dt must be finite and positive')
    if not (uniform_wind or step_scale != 1 or output_dt is not None or aero_nodes):
        return {}
    farm = Path(farm).resolve()
    root = farm.parent.parent
    ff = FASTInputFile(str(farm))
    touched = set()

    def patch(path, values, outputs=()):
        data = FASTInputFile(str(path))
        for key, value in values.items():
            data[key] = value
        if outputs:
            existing = {s.strip('"') for s in data['OutList']}
            data['OutList'] = list(data['OutList']) + [s for s in outputs if s not in existing]
        data.write(str(path))
        touched.add(path)
        return data

    if uniform_wind:
        inflow = (farm.parent / ff['InflowFile'].strip('"')).resolve()
        if int(FASTInputFile(str(inflow))['WindType']) != 1:
            raise ValueError('uniform_wind requires WindType=1')
        patch(inflow, {'PLExp': 0.})
    for row in ff['WindTurbines']:
        primary = (farm.parent / row[3].strip('"')).resolve()
        fst = FASTInputFile(str(primary))
        dt = float(fst['DT']) * step_scale
        if output_dt is not None and not np.isclose(output_dt / dt, round(output_dt / dt)):
            raise ValueError('output_dt must be an integer multiple of structural DT')
        changes = {}
        if step_scale != 1.:
            changes['DT'] = dt
        if output_dt is not None:
            changes['DT_Out'] = output_dt
        if changes:
            patch(primary, changes)
        ed = (primary.parent / fst['EDFile'].strip('"')).resolve()
        patch(ed, {}, ('Azimuth', 'RotSpeed', 'RotThrust', 'TTDspFA', 'TTDspSS'))
        ad = (primary.parent / fst['AeroFile'].strip('"')).resolve()
        patch(ad, {'DTAero': dt} if step_scale != 1. else {}, ('RtAeroFxh', 'RtAeroPwr'))
        if aero_nodes:
            content = ad.read_text(encoding='utf-8')
            content, count = re.subn(r'^\s*\d+([ \t]+BldNd_BladesOut\b)', r'1\1', content, flags=re.M)
            if count != 1:
                raise ValueError('Missing unique AeroDyn all-station output section')
            start = content.index('BldNd_BlOutNd')
            end = content.index('\nEND', start)
            channels = ['Alpha', 'Cl', 'Cd', 'Cn', 'Ct', 'Fx', 'Fy', 'Vrel', 'Phi']
            content = content[:end] + '\n' + '\n'.join('"' + c + '"' for c in channels) + content[end:]
            ad.write_text(content, encoding='utf-8')
            touched.add(ad)
    return dict(uniform_wind=uniform_wind, step_scale=step_scale, output_dt=output_dt,
                aero_nodes=aero_nodes, inputs_sha256={str(p.relative_to(root)):
                hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(touched)})


def read_native(path, seconds):
    """Read complete, finite native output after the caller has joined the solver."""
    lines = Path(path).read_text(encoding='utf-8').splitlines(keepends=True)
    header = next(i for i, line in enumerate(lines) if line.split()[:1] == ['Time'])
    names = lines[header].split()
    units = [s.strip('()') for s in lines[header + 1].split()]
    if not lines[-1].endswith('\n'):
        raise ValueError('Incomplete final native output row')
    data = np.array([[float(s.replace('D', 'E')) for s in line.split()]
                     for line in lines[header + 2:] if line.strip()])
    if (data.ndim != 2 or data.shape[1] != len(names) or len(set(names)) != len(names)
            or len(units) != len(names) or not np.isfinite(data).all()
            or np.any(np.diff(data[:, 0]) <= 0)
            or abs(data[-1, 0] - seconds) > 5.1e-5):
        raise ValueError('Nonfinite, malformed or incomplete native output')
    return dict(zip(names, data.T)), dict(zip(names, units))


def statistics(t, values, m=4):
    """Population statistics and unbinned ASTM rainflow DEL, 1 Hz, no mean correction."""
    from openfast_toolbox.tools.fatigue import rainflow_astm
    t, v = np.asarray(t), np.asarray(values)
    if len(t) < 2 or t.shape != v.shape or not np.isfinite(v).all() or np.any(np.diff(t) <= 0):
        raise ValueError('At least two finite, ordered samples are required')
    ranges = rainflow_astm(v)[0] if np.ptp(v) else np.array([0.])
    return dict(mean=float(v.mean()), std=float(v.std()), rms=float(np.sqrt(np.mean(v*v))),
                peak_abs=float(np.abs(v).max()), min=float(v.min()), max=float(v.max()),
                del_1hz=float((np.sum(ranges**m) * .5 / (t[-1] - t[0]))**(1/m)), wohler_m=m)


def compare_metrics(current, reference):
    """Mean uses reference RMS; undefined relative fluctuation metrics remain null."""
    return {key: (100 * (current[key] - reference[key]) / denominator if denominator else None)
            for key in ('mean', 'std', 'rms', 'peak_abs', 'del_1hz')
            for denominator in [reference['rms'] if key == 'mean' else abs(reference[key])]}


def airfoil_audit(root):
    """Report supplied UA parameters and independently fitted attached polar slopes."""
    from wfcrl.simul_utils import FASTInputFile
    result = []
    for path in sorted((Path(root) / '5MW_Baseline/Airfoils').glob('*.dat')):
        af = FASTInputFile(str(path))
        table = np.asarray(af['AFCoeff'])
        alpha, cl, cd = table[:, :3].T
        cn = cl*np.cos(np.deg2rad(alpha)) + (cd-float(af['Cd0']))*np.sin(np.deg2rad(alpha))
        fit_alpha = np.linspace(-2., 5., 71)
        slope, intercept = np.polyfit(np.deg2rad(fit_alpha), np.interp(fit_alpha, alpha, cn), 1)
        values = {key: float(af[key]) for key in ('alpha0', 'alpha1', 'alpha2', 'C_nalpha', 'Cn1', 'Cn2', 'Cd0')}
        result.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            ua=values, fit_interval_deg=[-2, 5], fitted_cn_slope_per_rad=float(slope),
            fitted_zero_cn_deg=float(np.rad2deg(-intercept/slope)) if slope else None,
            cn_at_alpha1=float(np.interp(values['alpha1'], alpha, cn)),
            cn_at_alpha2=float(np.interp(values['alpha2'], alpha, cn)),
            cd_at_alpha0=float(np.interp(values['alpha0'], alpha, cd))))
    return result


def node_audit(series, root, discard):
    """Compare UA with the supplied static polar at each measured section angle.

    Cn here excludes Cd0, matching Get_f_from_Lookup in OpenFAST v3.5.3;
    it is distinct from the AeroDyn rotor-normal output channel named Cn.
    The compressibility-corrected line is a steady-limit diagnostic, not a
    replacement for the unsteady solver or a fitted airfoil model.
    """
    from wfcrl.simul_utils import FASTInputFile
    root = Path(root)
    ad = FASTInputFile(str(root / '5MW_Baseline/AD.dat'))
    blade = FASTInputFile(str(root / '5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat'))
    table = blade['BldAeroNodes']
    mask = series['Time'] >= discard
    result = []
    for i, station in enumerate(table, 1):
        prefix = f'AB1N{i:03d}'
        if prefix + 'Alpha' not in series:
            return []
        path = (root / '5MW_Baseline' / ad['AFNames'][int(station[6])-1].strip('"')).resolve()
        af = FASTInputFile(str(path))
        polar = np.asarray(af['AFCoeff'])
        alpha = series[prefix+'Alpha'][mask]
        radians = np.deg2rad(alpha)
        cl, cd = (series[prefix+k][mask] for k in ('Cl', 'Cd'))
        cl_st = np.interp(alpha, polar[:, 0], polar[:, 1])
        cd_st = np.interp(alpha, polar[:, 0], polar[:, 2])
        cd0 = float(af['Cd0'])
        cn_st = cl_st*np.cos(radians) + (cd_st-cd0)*np.sin(radians)
        cn = cl*np.cos(radians) + (cd-cd0)*np.sin(radians)
        mach = series[prefix+'Vrel'][mask] / 335.
        line = float(af['C_nalpha']) / np.sqrt(1-mach**2) * np.deg2rad(alpha-float(af['alpha0']))
        lifting = float(af['C_nalpha']) != 0
        result.append(dict(node=i, radius_m=float(station[0]+1.5), airfoil=path.name,
            alpha_mean_deg=float(alpha.mean()), alpha_range_deg=[float(alpha.min()), float(alpha.max())],
            cl_mean=float(cl.mean()), polar_cl_at_actual_alpha_mean=float(cl_st.mean()),
            cn_mean=float(cn.mean()), polar_cn_at_actual_alpha_mean=float(cn_st.mean()),
            cn_linear_mean=float(line.mean()),
            positive_branch_clamped_fraction=float(np.mean((cn_st > line) & (line > 0))) if lifting else None,
            fy_mean_N_m=float(series[prefix+'Fy'][mask].mean())))
    return result


def tower_tip_compliance(tower_path, height):
    """Euler-Bernoulli cantilever tip translation per horizontal force, metres/N."""
    from wfcrl.simul_utils import FASTInputFile
    table = np.asarray(FASTInputFile(str(tower_path))['TowProp'])
    z = np.linspace(0., height, 10001)
    ei = np.interp(z / height, table[:, 0], table[:, 2])
    return float(np.trapz((height-z)**2 / ei, z))


def phase_binned_difference(current, reference, discard=60., width=5.):
    """Compare root flap loads at equal azimuth, avoiding rotor-speed phase drift."""
    edges = np.arange(0., 360. + width, width)
    def bins(series):
        means = []
        for low, high in zip(edges[:-1], edges[1:]):
            mask = ((series['Time'] >= discard) & (series['Azimuth'] >= low)
                    & (series['Azimuth'] < high))
            if not mask.any():
                raise ValueError('Every azimuth bin must contain samples')
            means.append(float(series['RootMyc1'][mask].mean()))
        return np.asarray(means)
    difference = bins(current)-bins(reference)
    index = int(np.argmin(difference))
    return dict(bin_width_deg=width, azimuth_centers_deg=((edges[:-1]+edges[1:])/2).tolist(),
                difference_kNm=difference.tolist(), minimum_kNm=float(difference[index]),
                minimum_azimuth_deg=float((edges[index]+edges[index+1])/2))


def classify_log(log):
    """Keep constant-cylinder UA disabling distinct from operating-range cutout."""
    constant = re.findall(r'Turning off Unsteady Aerodynamics because polar has constant data\. '
                          r'\(node (\d+), blade (\d+)\)', log)
    return dict(constant_polar_ua_disabled=[dict(node=int(n), blade=int(b)) for n, b in constant],
                cutout_mentions=len(re.findall(r'cutout', log, re.I)),
                invalid_nan_mentions=len(re.findall(r'\b(?:invalid|nan)\b', log, re.I)),
                normal_termination_count=log.count('FAST.Farm terminated normally.'))


def summarize_comparison(path, discard=60.):
    report = json.loads(Path(path).read_text(encoding='utf-8'))
    result = dict(seconds=report['seconds'], discard=discard, cases=[])
    for run in report['results']:
        if run['status'] != 'completed':
            raise ValueError('Only completed solver runs may be audited')
        farm = Path(run['farm_file'])
        series, units = read_native(farm.with_name('Case.T1.out'), report['seconds'])
        mask = series['Time'] >= discard
        if not mask.any() or series['Time'][mask][-1] <= series['Time'][mask][0]:
            raise ValueError('Empty statistics interval')
        channels = ('GenPwr', 'RootMyc1', 'RootMxc1', 'TwrBsMyt', 'TwrBsMxt', 'TTDspFA', 'TTDspSS', 'RotThrust', 'RotSpeed', 'RtAeroFxh')
        stats = {name: dict(unit=units[name], **statistics(series['Time'][mask], series[name][mask],
                   m=10 if name.startswith('Root') else 4)) for name in channels if name in series}
        log = farm.with_suffix('.solver.log').read_text(encoding='utf-8')
        warnings = [line.strip() for line in log.splitlines()
                    if re.search(r'warning|cutout|turning off|exceed|invalid|\bnan\b', line, re.I)]
        result['cases'].append(dict(variant=run['variant'], repeat=run['repeat'], metrics=stats,
            revolutions=float(np.trapz(series['RotSpeed'][mask]/60, series['Time'][mask])) if 'RotSpeed' in series else None,
            warnings=warnings, log_checks=classify_log(log), samples=int(mask.sum()), all_channels_finite=True,
            nodes=node_audit(series, farm.parent.parent, discard),
            node_means={name: float(v[mask].mean()) for name, v in series.items() if re.match(r'A?B1N\d+', name)}))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('comparison', type=Path)
    parser.add_argument('--discard', type=float, default=60.)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(summarize_comparison(args.comparison, args.discard),
                                    indent=2, allow_nan=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
