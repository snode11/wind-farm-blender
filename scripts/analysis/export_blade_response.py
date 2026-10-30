"""Export phase-aligned blade response from hash-bound saved OpenFAST outputs.

The result is an independent analysis layer. It does not modify the source
replay or infer physical mass/aerodynamic imbalance from response differences.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np


CHANNELS = {
    'tip_x': ('TipTDxr', '(m)', 'm'),
    'tip_y': ('TipTDyr', '(m)', 'm'),
    'tip_z': ('TipTDzr', '(m)', 'm'),
    'root_Mx': ('RootMxr', '(N-m)', 'N m'),
    'root_My': ('RootMyr', '(N-m)', 'N m'),
    'root_Mz': ('RootMzr', '(N-m)', 'N m'),
}
METHOD = 'common-turn-phase-mean.v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def phase_sample(azimuth, values, spacing=5.):
    """Equal-angle interpolation on complete turns covered by all three blades."""
    azimuth, values = np.asarray(azimuth), np.asarray(values)
    if (azimuth.ndim != 1 or len(azimuth) < 2 or values.shape != (len(azimuth), 3)
            or not np.isfinite(azimuth).all() or not np.isfinite(values).all()
            or np.any(np.diff(azimuth) <= 0) or spacing != 5.):
        raise ValueError('Expected finite three-blade samples and increasing azimuth on a 5 degree grid')
    phase = azimuth[:, None] + np.arange(3)[None, :] * 120.
    cycles = np.arange(math.ceil(phase[0].max() / 360), math.floor(phase[-1].min() / 360))
    if not len(cycles):
        raise ValueError('No complete common blade turns')
    grid = np.arange(spacing / 2, 360, spacing)
    targets = cycles[:, None] * 360 + grid[None, :]
    sampled = np.stack([np.interp(targets.ravel(), phase[:, b], values[:, b]).reshape(targets.shape)
                        for b in range(3)], axis=1)
    return cycles, grid, sampled


def describe(curves):
    """Describe three phase curves, keeping signed offsets in physical units."""
    curves = np.asarray(curves)
    residual = curves - curves.mean(axis=0, keepdims=True)
    return dict(rms_spread=float(np.sqrt(np.mean(residual ** 2))),
                blade_means=curves.mean(axis=1).tolist(),
                blade_offsets=residual.mean(axis=1).tolist())


def build(package, repo):
    package, repo = Path(package), Path(repo)
    manifest = json.loads((package / 'manifest.json').read_bytes())
    if (manifest.get('structural_module') != 'BeamDyn'
            or manifest.get('schema') != 'wfrl.farm-flex-review.v3'):
        raise ValueError('Expected a BeamDyn v3 replay')
    names = ('manifest.json', 'data.json', 'geometry.npz', 'telemetry.json', 'deflection-t1.json')
    source_hashes = {name: digest(package / name) for name in names}
    for name in names[1:]:
        if source_hashes[name] != manifest['files'][name]:
            raise ValueError('Source integrity mismatch: ' + name)
    replay = json.loads((package / 'data.json').read_bytes())
    telemetry = json.loads((package / 'telemetry.json').read_bytes())
    sidecar = json.loads((package / 'deflection-t1.json').read_bytes())
    start, end = manifest['segment']['start_s'], manifest['segment']['end_s']
    result = dict(schema='wfrl.blade-response.v1', method=METHOD,
                  segment_s=[start, end], phase_grid_deg=np.arange(2.5, 360, 5).tolist(),
                  turbines={})
    for tid in manifest['turbine_ids']:
        source = telemetry['sources'][tid]
        path = Path(source['path'])
        path = path if path.is_absolute() else repo / path
        if digest(path) != source['sha256']:
            raise ValueError('Raw output integrity mismatch: ' + tid)
        with path.open() as stream:
            header = [next(stream) for _ in range(8)]
        channels, units = header[6].split(), header[7].split()
        raw = np.loadtxt(path, skiprows=8)
        columns = {name: raw[:, i] for i, name in enumerate(channels)}
        units = dict(zip(channels, units))
        if units.get('Time') != '(s)' or units.get('Azimuth') != '(deg)':
            raise ValueError('Invalid time or azimuth units')
        use = (columns['Time'] >= start) & (columns['Time'] <= end)
        times = columns['Time'][use]
        motion = replay[tid]['motion']
        azimuth = np.degrees(np.unwrap(np.radians(columns['Azimuth'])))[use]
        if (len(times) != len(motion)
                or not np.allclose(times, [r['time_s'] for r in motion], atol=1e-9, rtol=0)
                or not np.allclose(np.diff(times), 1 / manifest['source_fps'], atol=1e-8, rtol=0)
                or not np.allclose(azimuth, [r['azimuth_deg'] for r in motion], atol=1e-6, rtol=0)):
            raise ValueError('Raw output and replay clocks/azimuth differ: ' + tid)
        metrics, turns, tips = {}, [], []
        for key, (suffix, expected_unit, unit) in CHANNELS.items():
            names = [f'B{b}{suffix}' for b in (1, 2, 3)]
            if any(units.get(name) != expected_unit for name in names):
                raise ValueError('Missing BeamDyn channel or incorrect unit: ' + suffix)
            values = np.column_stack([columns[name][use] for name in names])
            cycles, grid, sampled = phase_sample(azimuth, values)
            if key.startswith('tip_'):
                tips.append(values)
            mean = sampled.mean(axis=0)
            metrics[key] = dict(unit=unit, phase_mean=mean.tolist(), **describe(mean))
            if not turns:
                phases = azimuth[:, None] + np.arange(3)[None, :] * 120
                for cycle in cycles:
                    bounds = [[float(np.interp(angle, phases[:, b], times)) for b in range(3)]
                              for angle in (cycle * 360, (cycle + 1) * 360)]
                    turns.append(dict(cycle_index=int(cycle), start_s=min(bounds[0]),
                                      end_s=max(bounds[1]), metrics={}))
            for turn, curves in zip(turns, sampled):
                turn['metrics'][key] = describe(curves)
        if tid == 'T1' and not np.allclose(np.stack(tips, axis=-1), sidecar['simulation'], rtol=0, atol=1e-8):
            raise ValueError('Raw tip displacement does not match the replay reference')
        result['turbines'][tid] = dict(
            source=dict(file=path.name, sha256=source['sha256'], saved_samples=len(times)),
            metrics=metrics, turns=turns,
            physical=dict(mass=dict(status='unavailable'), aerodynamic=dict(status='unavailable')))
    return result, source_hashes


def export(package, repo, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Choose a new analysis directory: ' + str(output))
    result, sources = build(package, repo)
    data = (json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()
    manifest = dict(schema='wfrl.blade-response-package.v1', source_hashes=sources,
                    files={'results.json': hashlib.sha256(data).hexdigest()})
    output.mkdir(parents=True)
    (output / 'results.json').write_bytes(data)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = export(args.package, args.repo, args.output)
    print(json.dumps({tid: len(row['turns']) for tid, row in result['turbines'].items()}))
