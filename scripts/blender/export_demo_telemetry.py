"""Add verified power/torque/load channels from the same existing FAST.Farm run."""
from pathlib import Path
import hashlib
import json
import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def export(package, case):
    package, case = Path(package), Path(case)
    manifest = json.loads((package / 'manifest.json').read_text())
    with np.load(package / 'geometry.npz') as geometry:
        times, poses = geometry['times'], geometry['poses']
    turbines, sources = {}, {}
    for k, tid in enumerate(manifest['turbine_ids']):
        path = case / 'FarmInputs' / f'Case.{tid}.out'
        lines = path.read_text().splitlines()
        names, units = lines[6].split(), lines[7].split()
        raw = np.loadtxt(path, skiprows=8)
        columns = dict(zip(names, raw.T))
        stamps = columns['Time']
        indices = np.searchsorted(stamps, times)
        if not np.allclose(stamps[indices], times, rtol=0, atol=1e-5):
            raise ValueError('Telemetry time axis does not match geometry')
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
    target = package / 'telemetry.json'
    target.write_text(json.dumps(payload, separators=(',', ':')) + '\n')
    manifest['files']['telemetry.json'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package'); parser.add_argument('case')
    args = parser.parse_args()
    export(args.package, args.case)
