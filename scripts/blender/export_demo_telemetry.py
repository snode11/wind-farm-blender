"""Add verified power/torque/load channels from the same existing FAST.Farm run."""
from pathlib import Path
import hashlib
import json
import numpy as np

if __package__:
    from .export_geometry import write_export_files
else:
    from export_geometry import write_export_files

ROOT = Path(__file__).resolve().parents[2]


def validate_times(times, label):
    if (times.ndim != 1 or not times.size or not np.isfinite(times).all()
            or not np.all(np.diff(times) > 0)):
        raise ValueError(label + ' time axis must be nonempty, finite and strictly increasing')


def validate_geometry(times, poses, turbine_ids):
    validate_times(times, 'Geometry')
    if (not turbine_ids or len(set(turbine_ids)) != len(turbine_ids)
            or poses.shape != (len(times), len(turbine_ids), 6) or not np.isfinite(poses).all()):
        raise ValueError('Geometry poses must be finite with shape (times, turbine_ids, 6)')


def source_indices(stamps, times, *, atol, mismatch):
    validate_times(stamps, 'Source')
    validate_times(times, 'Geometry')
    indices = np.searchsorted(stamps, times)
    out_of_range = (times < stamps[0]) | (indices >= len(stamps))
    if np.any(out_of_range):
        raise ValueError(
            f'Requested time {times[out_of_range][0]} s is outside available source time '
            f'range [{stamps[0]}, {stamps[-1]}] s')
    if not np.allclose(stamps[indices], times, rtol=0, atol=atol):
        raise ValueError(mismatch)
    return indices


def validate_units(names, units, expected):
    for name, unit in expected.items():
        if name not in names or names.index(name) >= len(units) or units[names.index(name)] != unit:
            raise ValueError('Unexpected or missing source unit: ' + name)


def write_sidecar(package, manifest, name, text):
    manifest['files'][name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    manifest_text = json.dumps(manifest, indent=2, allow_nan=False) + '\n'
    write_export_files({package / name: text, package / 'manifest.json': manifest_text})


def export(package, case):
    package, case = Path(package), Path(case)
    manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
    if hashlib.sha256((package / 'geometry.npz').read_bytes()).hexdigest() != manifest['files']['geometry.npz']:
        raise ValueError('Package integrity mismatch: geometry.npz')
    with np.load(package / 'geometry.npz') as geometry:
        times, poses = geometry['times'], geometry['poses']
    validate_geometry(times, poses, manifest['turbine_ids'])
    turbines, sources = {}, {}
    for k, tid in enumerate(manifest['turbine_ids']):
        path = case / 'FarmInputs' / f'Case.{tid}.out'
        lines = path.read_text(encoding='utf-8').splitlines()
        names, units = lines[6].split(), lines[7].split()
        raw = np.loadtxt(path, skiprows=8, ndmin=2)
        columns = dict(zip(names, raw.T))
        stamps = columns['Time']
        validate_units(names, units, {'Time': '(s)', 'YawPzn': '(deg)',
                                     'RotSpeed': '(rpm)', 'BlPitch1': '(deg)'})
        indices = source_indices(stamps, times, atol=1e-5,
                                 mismatch='Telemetry time axis does not match geometry')
        for key, j in [('YawPzn', 0), ('RotSpeed', 2), ('BlPitch1', 3)]:
            if not np.allclose(columns[key][indices], poses[:, k, j], rtol=0, atol=.001):
                raise ValueError('Telemetry belongs to a different run: ' + tid)
        record = {}
        for channel, key, expected_unit, unit, scale in [
                ('power', 'GenPwr', '(kW)', 'MW', .001),
                ('torque', 'GenTq', '(kN-m)', 'kN·m', 1),
                ('load', 'RootMyc1', '(kN-m)', 'kN·m', 1),
                ('pitch_command', 'BlPitchC1', '(deg)', 'deg', 1)]:
            if units[names.index(key)] != expected_unit:
                raise ValueError('Unexpected source unit: ' + key)
            values = columns[key][indices] * scale
            if not np.isfinite(values).all():
                raise ValueError('Nonfinite telemetry: ' + key)
            record[channel] = dict(values=values.tolist(), unit=unit, source_channel=key)
        turbines[tid] = record
        sources[tid] = dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    payload = dict(schema='wfrl.farm-telemetry.v1', times=times.tolist(), turbines=turbines,
                   sources=sources, geometry_sha256=manifest['files']['geometry.npz'])
    write_sidecar(package, manifest, 'telemetry.json',
                  json.dumps(payload, separators=(',', ':'), allow_nan=False) + '\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package'); parser.add_argument('case')
    args = parser.parse_args()
    export(args.package, args.case)
