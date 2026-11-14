"""Derive explicit NREL 5MW UA parameters without altering the source polars."""
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / 'wfcrl-env/wfcrl/simulators/fastfarm/inputs'
SOURCE = INPUTS / 'template/5MW_Baseline/Airfoils'
BUNDLE = INPUTS / 'polar_consistent/Airfoils'
FIELDS = ('alpha0', 'alpha1', 'alpha2', 'C_nalpha', 'Cn1', 'Cn2', 'Cd0', 'Cm0')
METHOD = 'polar-consistent-ua.v1'
REFERENCES = [
    'https://openfast.readthedocs.io/en/v3.5.3/source/user/aerodyn/theory_ua.html#calculating-default-airfoil-coefficients',
    'https://github.com/OpenFAST/openfast/blob/v3.5.3/modules/aerodyn/src/AirfoilInfo.f90',
    'https://github.com/OpenFAST/openfast/blob/v3.5.3/modules/aerodyn/src/UnsteadyAero.f90',
]


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def derive(table):
    """Use the OpenFAST documented slope/anchor method and f=0.7 crossings.

    Linear interpolation preserves the supplied steady table. The triweight
    kernel has support +/-2 degrees; the maximum slope is sought in +/-20
    degrees. Alpha0 is the extrapolated maximum-Cl-slope intercept through
    the middle of the 90%-slope region; Cd0 is the minimum drag in +/-20
    degrees. This is an offline implementation, not a call to solver defaults.
    """
    a, cl, cd, cm = np.asarray(table, dtype=float)[:, :4].T
    if (not np.isfinite(table).all() or np.any(np.diff(a) <= 0)
            or a[0] > -20 or a[-1] < 20):
        raise ValueError('A finite, ordered polar covering +/-20 degrees is required')
    if np.all(cl == 0):
        return None, {'nonlifting_section': True}
    crossings = np.flatnonzero((cl[:-1] <= 0) & (cl[1:] > 0)
                               & (a[:-1] >= -20) & (a[1:] <= 20))
    if len(crossings) != 1:
        raise ValueError('A unique upward zero-lift crossing in +/-20 degrees is required')
    i = crossings[0]
    zero_lift = a[i] - cl[i] * (a[i+1]-a[i]) / (cl[i+1]-cl[i])
    imin = np.flatnonzero(np.abs(a) <= 20.)
    minimum = imin[cd[imin] == np.min(cd[imin])]
    imin = minimum[np.argmin(np.abs(a[minimum]-zero_lift))]
    cd0 = float(cd[imin])
    cn = cl*np.cos(np.deg2rad(a)) + (cd-cd0)*np.sin(np.deg2rad(a))
    mid = (a[1:]+a[:-1])/2
    raw = np.diff(cn)/np.deg2rad(np.diff(a))
    weights = np.maximum(0., 1.-((mid[:, None]-mid[None, :])/2.)**2)**3
    smooth = weights @ raw / weights.sum(axis=1)
    in_window = np.flatnonzero(np.abs(mid) <= 20.)
    imax = in_window[np.argmax(smooth[in_window])]
    slope = float(smooth[imax])
    if slope <= 0:
        raise ValueError('The maximum attached-flow slope must be positive')
    at_min = float(np.interp(a[imin], mid, smooth))

    def attached_boundary(sign):
        indices = in_window[mid[in_window] >= a[imin]] if sign == 1 else in_window[mid[in_window] < a[imin]][::-1]
        peak, boundary = at_min, float(a[imin])
        for j in indices:
            peak = max(peak, smooth[j])
            if smooth[j] < .9*peak:
                break
            boundary = float(mid[j])
        return boundary

    lower, upper = attached_boundary(-1), attached_boundary(1)
    cl_slope = float(np.max((np.diff(cl)/np.deg2rad(np.diff(a)))[in_window]))
    anchor = (lower+upper)/2
    a0 = float(anchor-np.rad2deg(np.interp(anchor, a, cl)/cl_slope))
    cm0 = float(np.interp(a0, a, cm))

    def cn_at(alpha):
        rad = np.deg2rad(alpha)
        return np.interp(alpha, a, cl)*np.cos(rad) + (np.interp(alpha, a, cd)-cd0)*np.sin(rad)

    cn_offset = float((cn_at(lower)+cn_at(upper))/2.)

    def separation_ratio(alpha, sign):
        # AirfoilInfo's offset separation curve uses the boundary tangent.
        boundary = upper if sign == 1 else lower
        j = int(np.clip(np.searchsorted(a, boundary)-1, 0, len(a)-2))
        tangent = (cn[j+1]-cn[j])/(a[j+1]-a[j])
        attached = cn_at(boundary)+tangent*(alpha-boundary)
        numerator, denominator = cn_at(alpha)-cn_offset, attached-cn_offset
        return np.divide(numerator, denominator, out=np.ones_like(np.asarray(alpha, dtype=float)),
                         where=np.abs(denominator) > 1e-14)

    def f_crossing(sign):
        boundary = upper if sign == 1 else lower
        angles = boundary + sign*np.arange(0., 45.-sign*boundary, .01)
        ratio = separation_ratio(angles, sign)
        target = ((1.+np.sqrt(.7))/2.)**2
        candidates = np.flatnonzero((ratio[:-1] >= target) & (ratio[1:] < target))
        if not len(candidates):
            raise ValueError('No outward f=0.7 crossing before +/-45 degrees')
        j = candidates[0]
        lo, hi = angles[j], angles[j+1]
        for _ in range(45):
            middle = (lo+hi)/2
            if separation_ratio(middle, sign) >= target:
                lo = middle
            else:
                hi = middle
        return float((lo+hi)/2)

    a1, a2 = f_crossing(1), f_crossing(-1)
    values = dict(zip(FIELDS, map(float, (a0, a1, a2, slope, cn_at(a1), cn_at(a2), cd0, cm0))))
    # A separate least-squares diagnostic is not used to tune the selected slope.
    fit_a = np.linspace(-2., 5., 71)
    fit = np.polyfit(np.deg2rad(fit_a), cn_at(fit_a), 1)
    diagnostic = dict(nonlifting_section=False, zero_lift_bracket_deg=a[i:i+2].tolist(),
        polar_zero_lift_deg=float(zero_lift), attached_region_deg=[lower, upper],
        anchor_deg=anchor, maximum_cl_slope_per_rad=cl_slope,
        slope_search_deg=[-20., 20.], slope_kernel_half_width_deg=2.,
        maximum_slope_midpoint_deg=float(mid[imax]),
        diagnostic_fit_deg=[-2., 5.], diagnostic_fit_slope_per_rad=float(fit[0]),
        cn_offset=cn_offset,
        f_at_stall=[float((2*np.sqrt(separation_ratio(x, sign))-1)**2)
                    for x, sign in ((a1, 1), (a2, -1))])
    return values, diagnostic


def replace_parameters(content, values):
    """Replace numeric tokens only, preserving every original line ending."""
    for key, value in values.items():
        pattern = rb'(?m)^([ \t]*)[^ \t\r\n]+([ \t]+' + key.encode('ascii') + rb'\b)'
        content, count = re.subn(pattern, lambda m: m[1]+format(value, '.12g').encode('ascii')+m[2], content)
        if count != 1:
            raise ValueError(f'Expected one parameter token: {key}')
    return content


def generate(source=SOURCE, output=BUNDLE, *, check=False):
    from wfcrl.simul_utils import FASTInputFile
    source, output = Path(source).resolve(), Path(output).resolve()
    if (output == source or output.is_relative_to(source)
            or output.is_relative_to(INPUTS / 'template')):
        raise ValueError('Derived resources must be outside the immutable template copy tree')
    entries, contents = {}, {}
    for path in sorted(source.glob('*.dat')):
        raw = path.read_bytes()
        af = FASTInputFile(str(path))
        values, diagnostic = derive(af['AFCoeff'])
        contents[path.name] = replace_parameters(raw, values) if values else raw
        entries[path.name] = dict(input_sha256=sha256(raw), output_sha256=sha256(contents[path.name]),
            official_parameters={key: float(af[key]) for key in FIELDS},
            parameters=values or {key: float(af[key]) for key in FIELDS}, **diagnostic)
    if len(entries) != 8:
        raise ValueError('The NREL 5MW bundle must contain eight airfoils')
    manifest = dict(schema=METHOD, source='template/5MW_Baseline/Airfoils',
        official_revision='OpenFAST/r-test 548bf2143633a7066f4582167fdf4180951fd7f6',
        references=REFERENCES, derivation_script='scripts/experiments/derive_polar_consistent_ua.py',
        derivation_script_sha256=sha256(Path(__file__).read_bytes()),
        unchanged='Steady polar tables, coordinates, time constants and all other UA inputs', files=entries)
    contents['manifest.json'] = (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False)+'\n').encode('utf-8')
    if check:
        for name, raw in contents.items():
            if (output/name).read_bytes() != raw:
                raise ValueError(f'Derived resource does not reproduce: {name}')
    else:
        output.mkdir(parents=True, exist_ok=False)
        for name, raw in contents.items():
            (output/name).write_bytes(raw)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--output', type=Path, default=BUNDLE)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    manifest = generate(args.source, args.output, check=args.check)
    print(json.dumps({key: value['parameters'] for key, value in manifest['files'].items()}, indent=2))


if __name__ == '__main__':
    main()
