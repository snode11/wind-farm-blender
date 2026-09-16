"""Compact section transforms from verified OpenFAST surfaces for a source preview.

Transforms retain each ordered contour's orientation, relative to the saved
initial surface. They include rigid rotor motion: consumers must not apply it
twice. This archive complements the matching clearance package, not its truth.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .tip_replay import FastFarmTipReplay
from wfrl.lidar.physics import read_surface


def fit_sections(reference, current):
    """Least-squares proper rigid transforms of corresponding contour points."""
    a, b = reference.mean(axis=1), current.mean(axis=1)
    x, y = reference-a[:, None], current-b[:, None]
    u, _, vt = np.linalg.svd(np.einsum('sni,snj->sij', x, y))
    fix = np.broadcast_to(np.eye(3), u.shape).copy()
    fix[:, 2, 2] = np.linalg.det(u @ vt)
    rotation = (u @ fix @ vt).transpose(0, 2, 1)
    translation = b-np.einsum('sij,sj->si', rotation, a)
    mapped = np.einsum('sij,snj->sni', rotation, reference)+translation[:, None]
    return rotation, translation, float(np.max(np.linalg.norm(mapped-current, axis=-1)))


def export(package, output, start=18., duration=10., wind=None):
    reader = FastFarmTipReplay(package)
    return export_reader(reader, package, output, start, duration, wind=wind)


def export_reader(reader, package, output, start, duration, wind=None):
    """Export from an already checked source; package identifies its manifest."""
    rows = [(i, t) for i, t in enumerate(reader.times) if start-1e-8 <= t <= start+duration+1e-8]
    if duration <= 0 or not rows or abs(rows[0][1]-start)>1e-8 or abs(rows[-1][1]-start-duration)>1e-8:
        raise ValueError('Requested clip must be covered by the verified source')
    def surface(bid, frame):
        key = f'FarmInputs/vtk/Case.{reader.package.manifest["turbine_id"]}.Blade{bid}Surface.{frame:05d}.vtp'
        path = reader.run/key
        if hashlib.sha256(path.read_bytes()).hexdigest() != reader.hashes.get(key):
            raise ValueError('Surface integrity mismatch: '+key)
        return read_surface(path)[0].reshape(reader.section_count, -1, 3)
    reference = [surface(bid, 0) for bid in (1, 2, 3)]
    transforms, max_error = [], 0.
    for number, (i, t) in enumerate(rows):
        frame = []
        for bid in (1, 2, 3):
            r, d, err = fit_sections(reference[bid-1], surface(bid, round(t*reader.fps)))
            max_error = max(max_error, err)
            frame.append(np.concatenate((r, d[:, :, None]), axis=2))
        transforms.append(frame)
        if number % 160 == 0:
            print(f'Exported {number}/{len(rows)} physical samples', flush=True)
    if max_error > .002:
        raise ValueError(f'Sections are not rigid within 2 mm: {max_error}')
    metadata = dict(schema='wfrl.section-transforms.preview.v1', source='FAST.Farm',
                    run_id=reader.package.manifest['run_id'], turbine_id=reader.package.manifest['turbine_id'],
                    package_manifest_sha256=hashlib.sha256((Path(package)/'manifest.json').read_bytes()).hexdigest(),
                    reference_frame=0, includes_rigid_motion=True, source_fps=reader.fps,
                    max_surface_fit_error_m=max_error,
                    scope='single turbine fixed yaw/pitch archive; no policy inference; true amplitude')
    # Preserve the physical wind envelope with the lightweight archive.  This
    # is descriptive provenance only; it never scales or fabricates motion.
    if wind is not None:
        required = ('mean_mps', 'ti_percent', 'gust_increment_mps',
                    'gust_peak_mps', 'gust_duration_s')
        if not isinstance(wind, dict) or any(k not in wind for k in required):
            raise ValueError('wind must provide mean_mps, ti_percent, gust_increment_mps, gust_peak_mps, gust_duration_s')
        vals = {k: float(wind[k]) for k in required}
        if vals['mean_mps'] <= 0 or not 0 <= vals['ti_percent'] <= 100 or vals['gust_increment_mps'] < 0 \
                or vals['gust_peak_mps'] < vals['mean_mps'] or vals['gust_duration_s'] <= 0:
            raise ValueError('Invalid wind envelope')
        metadata['wind_conditions'] = vals
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, times=np.array([t for _, t in rows]),
                        transforms=np.asarray(transforms, dtype=np.float32), metadata=json.dumps(metadata))
    Path(output).with_suffix('.json').write_text(json.dumps(metadata, indent=2))
    print(json.dumps(metadata), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('package'); parser.add_argument('output')
    parser.add_argument('--start', type=float, default=18.)
    parser.add_argument('--duration', type=float, default=10.)
    parser.add_argument('--mean-wind', type=float, default=None)
    parser.add_argument('--ti', type=float, default=None)
    parser.add_argument('--gust-increment', type=float, default=None)
    parser.add_argument('--gust-peak', type=float, default=None)
    parser.add_argument('--gust-duration', type=float, default=None)
    args = parser.parse_args()
    wind_values = (args.mean_wind, args.ti, args.gust_increment, args.gust_peak, args.gust_duration)
    wind = None if all(v is None for v in wind_values) else dict(mean_mps=args.mean_wind, ti_percent=args.ti,
        gust_increment_mps=args.gust_increment, gust_peak_mps=args.gust_peak, gust_duration_s=args.gust_duration)
    export(args.package, args.output, args.start, args.duration, wind=wind)
