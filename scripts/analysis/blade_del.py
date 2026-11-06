"""Estimate research-only blade-root moment DEL from saved 40 Hz BeamDyn data.

No solver is invoked. Loads are ranges in N m, with finite-record half cycles,
no mean-load correction, and a 1 Hz equivalent cycle frequency. The default
composite-blade exponent m=10 and sensitivities m=8,12 are research assumptions,
not a calibrated material model or certification-level lifetime assessment.
"""

import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import NamedTuple

import numpy as np


REPO = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO / (
    'outputs/dual-beam-tls-20261002/stages/02-diagnostics/raw-evidence/'
    '__simul__/fastfarm/FastFarm__208s__3T_1789663025.003069/FarmInputs'
)
CHANNELS = tuple(f'B{blade}RootM{axis}r' for blade in (1, 2, 3) for axis in ('x', 'y'))
WINDOWS = {'replay': (117., 177.), 'full': (0., 183.)}
SAMPLING = {'40_Hz': 1, '4_Hz': 10, '1_Hz': 40, '1_over_3_Hz': 120}
SOURCE_HZ = 40.


class Cycle(NamedTuple):
    range: float
    mean: float
    count: float
    start_index: int
    end_index: int


def turning_points(values):
    """Return indexed extrema and endpoints; keep the last point of a plateau."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all():
        raise ValueError('Expected a finite one-dimensional load history')
    if not len(values):
        return []
    indices = np.r_[np.flatnonzero(np.diff(values) != 0), len(values) - 1]
    if len(indices) < 2:
        return [(int(indices[0]), float(values[indices[0]]))]
    slopes = np.diff(values[indices])
    reverse = np.sign(slopes[:-1]) != np.sign(slopes[1:])
    extrema = indices[np.r_[True, reverse, True]]
    return [(int(i), float(values[i])) for i in extrema]


def rainflow_four_point(values):
    """Four-point closed loops with ASTM E1049 finite-record half-cycle residue.

    For consecutive reversals A,B,C,D, count B-C once and remove B,C when
    |B-C| <= |A-B| and |B-C| <= |C-D|. Recheck the reduced stack. Remaining
    adjacent reversals each count as one half cycle; no periodic closure,
    hysteresis threshold, range binning, or mean correction is applied.
    """
    stack, cycles = [], []

    def cycle(first, second, count):
        i, a = first
        j, b = second
        return Cycle(abs(b - a), a / 2 + b / 2, count, i, j)

    for point in turning_points(values):
        stack.append(point)
        while len(stack) >= 4:
            a, b, c, d = stack[-4:]
            inner = abs(b[1] - c[1])
            if inner > abs(a[1] - b[1]) or inner > abs(c[1] - d[1]):
                break
            cycles.append(cycle(b, c, 1.))
            del stack[-3:-1]
    cycles.extend(cycle(a, b, .5) for a, b in zip(stack, stack[1:]))
    return cycles


def damage_equivalent_load(cycles, duration_s, m=10., equivalent_frequency_hz=1.):
    """Range-based DEL; N_eq is elapsed time times equivalent cycle frequency."""
    if any(not math.isfinite(v) or v <= 0 for v in (duration_s, m, equivalent_frequency_hz)):
        raise ValueError('Duration, exponent, and equivalent frequency must be finite and positive')
    cycles = list(cycles)
    if any(not math.isfinite(c.range) or c.range < 0
           or not math.isfinite(c.count) or c.count <= 0 for c in cycles):
        raise ValueError('Cycle ranges and counts must be finite and nonnegative/positive')
    scale = max((c.range for c in cycles), default=0.)
    if scale == 0:
        return 0.
    # Normalized powers avoid overflow from large moments raised to m.
    damage = math.fsum(c.count * (c.range / scale) ** m for c in cycles)
    return scale * (damage / (duration_s * equivalent_frequency_hz)) ** (1. / m)


def validate_clock(times):
    times = np.asarray(times, dtype=float)
    if (times.ndim != 1 or len(times) < 2 or not np.isfinite(times).all()
            or not np.allclose(np.diff(times), 1 / SOURCE_HZ, rtol=0, atol=1e-8)):
        raise ValueError('Expected finite, increasing, uninterrupted 40 Hz times')
    return times


def read_beamdyn(path):
    """Read only Time and valid BeamDyn moments; never substitute ElastoDyn."""
    path = Path(path)
    with path.open(encoding='utf-8') as stream:
        header = [stream.readline() for _ in range(8)]
    names, units = header[6].split(), header[7].split()
    if not names or len(names) != len(units) or len(set(names)) != len(names):
        raise ValueError('Invalid OpenFAST channel header')
    unit_map = dict(zip(names, units))
    selected = ('Time',) + CHANNELS
    for name in selected:
        expected = '(s)' if name == 'Time' else '(N-m)'
        if unit_map.get(name) != expected:
            raise ValueError(f'Invalid or missing channel {name}: expected {expected}, got {unit_map.get(name)!r}')
    raw = np.loadtxt(path, skiprows=8, usecols=[names.index(n) for n in selected],
                     ndmin=2, encoding='utf-8')
    if not np.isfinite(raw).all():
        raise ValueError('Non-finite time or BeamDyn moment samples')
    times = validate_clock(raw[:, 0])
    source = dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  sample_count=len(times), time_span_s=[float(times[0]), float(times[-1])],
                  source_sample_rate_hz=SOURCE_HZ,
                  selected_units={name: unit_map[name] for name in selected},
                  excluded_invalid_channels=[n for n, u in unit_map.items()
                                             if u.upper().strip('()') == 'INVALID'])
    return times, {name: raw[:, i + 1] for i, name in enumerate(CHANNELS)}, source


def window_slice(times, start_s, end_s):
    """Require both exact window endpoints; do not interpolate or shorten time."""
    if not math.isfinite(start_s) or not math.isfinite(end_s) or end_s <= start_s:
        raise ValueError('Invalid statistical window')
    bounds = [np.flatnonzero(np.isclose(times, t, rtol=0, atol=1e-8))
              for t in (start_s, end_s)]
    if any(len(found) != 1 for found in bounds):
        raise ValueError('Statistical window endpoints are missing from the source clock')
    return slice(int(bounds[0][0]), int(bounds[1][0]) + 1)


def analyze_series(times, values, exponents=(8., 10., 12.)):
    """Recount each unfiltered stride sample on the identical closed interval."""
    times = validate_clock(times)
    values = np.asarray(values, dtype=float)
    if values.shape != times.shape or not np.isfinite(values).all():
        raise ValueError('Expected one finite moment per time sample')
    exponents = tuple(exponents)
    if not exponents or any(not math.isfinite(m) or m <= 0 for m in exponents):
        raise ValueError('Expected finite positive fatigue exponents')
    duration = float(times[-1] - times[0])
    reference_cycles = rainflow_four_point(values)
    maximum_range = max((c.range for c in reference_cycles), default=0.)
    edges = np.array([0., .1, .25, .5, .75, 1.]) * maximum_range
    rates = {}
    for label, stride in SAMPLING.items():
        if (len(times) - 1) % stride:
            raise ValueError('Window endpoint must lie on every downsampling grid')
        samples = values[::stride]
        cycles = reference_cycles if stride == 1 else rainflow_four_point(samples)
        counts = [c.count for c in cycles]
        ranges = [c.range for c in cycles]
        histogram = (np.histogram(ranges, bins=edges, weights=counts)[0].tolist()
                     if maximum_range else [0.] * 5)
        rates[label] = dict(
            sample_rate_hz=SOURCE_HZ / stride, stride=stride,
            nyquist_hz=SOURCE_HZ / stride / 2, sample_count=len(samples),
            sampled_interval_s=[float(times[0]), float(times[-1])], duration_s=duration,
            equivalent_cycles=duration, reversal_count=len(turning_points(samples)),
            full_cycles=sum(c.count == 1 for c in cycles),
            residual_half_cycles=sum(c.count == .5 for c in cycles),
            weighted_cycle_count=math.fsum(counts),
            max_cycle_range_Nm=max(ranges, default=0.),
            range_histogram_weighted_counts=histogram,
            del_Nm={f'{m:g}': damage_equivalent_load(cycles, duration, m) for m in exponents})
    reference = rates['40_Hz']
    comparison = {}
    for label, row in rates.items():
        comparison[label] = dict(
            del_change_pct={m: 100 * (value / reference['del_Nm'][m] - 1)
                            if reference['del_Nm'][m] else None
                            for m, value in row['del_Nm'].items()},
            weighted_cycle_count_change=row['weighted_cycle_count'] - reference['weighted_cycle_count'],
            weighted_cycle_count_change_pct=100 * (
                row['weighted_cycle_count'] / reference['weighted_cycle_count'] - 1)
            if reference['weighted_cycle_count'] else None)
    return dict(unit='N m', range_histogram_edges_Nm=edges.tolist(),
                sampling=rates, comparison_to_40_Hz=comparison)


def build_report(input_dir=DEFAULT_INPUT, default_m=10.):
    if not math.isfinite(default_m) or default_m <= 0:
        raise ValueError('Default fatigue exponent must be finite and positive')
    exponents = sorted({8., float(default_m), 12.})
    result = dict(
        schema='wfrl.blade-del.v1', status='research_estimate',
        method=dict(
            rainflow='four-point closed loops; ASTM E1049 finite-record half-cycle residue',
            closed_cycle_rule='|B-C| <= |A-B| and |B-C| <= |C-D|; remove B,C and recheck',
            residual='Each adjacent pair of remaining reversals counts as 0.5; no periodic closure',
            del_formula='(sum(n_i * S_i**m) / N_eq)**(1/m)',
            range_definition='S_i is peak-to-trough moment range, not amplitude',
            default_m=default_m, exponents=exponents,
            exponent_assumption='m=10 is a composite-blade research assumption; m=8,12 are sensitivities',
            equivalent_frequency_hz=1., equivalent_cycles='N_eq = 1 Hz * (end_s - start_s)',
            mean_load_correction='none', range_binning_for_del=False,
            sampling='Window first, then take every 1,10,40,120 samples starting at its first sample',
            anti_alias_filter=False, interpolation=False, endpoint_policy='Include both endpoints',
            histogram='Common edges per channel/window; bins include left edge, final bin includes right edge',
            cycle_comparison='Net count change after recounting, not one-to-one identification of lost cycles',
            aliasing='Components above the new Nyquist frequency fold into its baseband; signed DEL changes are measured'),
        limitations=[
            'Research estimates only; not a certification-level fatigue-life assessment.',
            '40 Hz is the available saved-data reference, not continuous-time ground truth.',
            'Moment DEL is not stress or lifetime; no calibrated S-N curve or lifetime extrapolation is provided.',
            'Finite windows retain their boundary half cycles; full duration includes startup/transient response.',
            'Unfiltered decimation at one fixed sampling phase combines missed extrema/cycles and aliasing.',
            'No mean-load correction is applied; exponent choices are research assumptions.'],
        sources={}, windows={})
    for tid in ('T1', 'T2', 'T3'):
        times, channels, source = read_beamdyn(Path(input_dir) / f'Case.{tid}.out')
        result['sources'][tid] = source
        for name, (start, end) in WINDOWS.items():
            window = result['windows'].setdefault(name, dict(
                interval_s=[start, end], duration_s=end - start,
                equivalent_cycles=end - start, turbines={}))
            use = window_slice(times, start, end)
            blades = window['turbines'][tid] = {}
            for blade in (1, 2, 3):
                blades[f'B{blade}'] = {
                    suffix: analyze_series(times[use], channels[f'B{blade}{suffix}'][use], exponents)
                    for suffix in ('RootMxr', 'RootMyr')}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--m', type=float, default=10., help='Primary research exponent; also report m=8,12')
    parser.add_argument('--output', type=Path, help='New JSON file; default is a system temporary directory')
    args = parser.parse_args(argv)
    output = args.output
    if output is not None and output.exists():
        raise FileExistsError('Choose a new output file: ' + str(output))
    result = build_report(args.input_dir, args.m)
    if output is None:
        output = Path(tempfile.mkdtemp(prefix='blade-del-')) / 'results.json'
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(output.resolve())
    return output


if __name__ == '__main__':
    main()
