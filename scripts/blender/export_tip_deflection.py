"""Export same-run T1 outputs/rigid pose; never derive reference from deflection.

Adds a versioned sidecar to a copy of an existing farm package. Existing surface
transforms, clearance readings, and geometry samples are retained byte for byte.
"""
from pathlib import Path
import hashlib
import json
import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(package, case, geometry_asset):
    package, case, geometry_asset = Path(package), Path(case), Path(geometry_asset)
    manifest = json.loads((package / 'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        if sha(package / name) != digest:
            raise ValueError('Package integrity mismatch: ' + name)
    with np.load(package / 'geometry.npz') as archive:
        times, poses = archive['times'], archive['poses']
    farm = case / 'FarmInputs'
    output = farm / 'Case.T1.out'
    lines = output.read_text().splitlines()
    names, units = lines[6].split(), lines[7].split()
    raw = np.loadtxt(output, skiprows=8)
    columns = dict(zip(names, raw.T))
    indices = np.searchsorted(columns['Time'], times)
    if not np.allclose(columns['Time'][indices], times, atol=1e-8, rtol=0):
        raise ValueError('Source time mismatch')
    telemetry = json.loads((package / 'telemetry.json').read_text())
    if sha(output) != telemetry['sources']['T1']['sha256']:
        raise ValueError('Output does not belong to this farm package')
    ed = farm / 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat'
    inputs = {}
    for line in ed.read_text().splitlines():
        words = line.split()
        if len(words) > 1: inputs[words[1]] = words[0]
    flexible = manifest.get('tower_model') == 'elastodyn-flexible'
    tower_keys = () if flexible else ('TwFADOF1', 'TwFADOF2', 'TwSSDOF1', 'TwSSDOF2')
    for key in tower_keys + ('TeetDOF',
                'PtfmSgDOF', 'PtfmSwDOF', 'PtfmHvDOF', 'PtfmRDOF', 'PtfmPDOF', 'PtfmYDOF', 'Furling'):
        if inputs[key].lower() != 'false':
            raise ValueError('Unsupported moving reference: ' + key)
    for key in ('OoPDefl', 'IPDefl', 'Azimuth', 'NacYaw', 'AzimB1Up', 'PtfmRefzt',
                'BlPitch(1)', 'BlPitch(2)', 'BlPitch(3)'):
        if float(inputs[key]) != 0:
            raise ValueError('Initial surface is not the supported rest frame: ' + key)
    if float(inputs['NumBl']) != 3:
        raise ValueError('Expected three blades')
    scalars = json.loads(geometry_asset.read_text())['scalars']
    for key in ('TipRad', 'HubRad', 'OverHang', 'TowerHt', 'Twr2Shft', 'ShftTilt', 'PreCone(1)'):
        if float(inputs[key]) != scalars[key]:
            raise ValueError('Display geometry and solver disagree: ' + key)
    if any(float(inputs[f'PreCone({b})']) != scalars['PreCone(1)'] for b in (2, 3)):
        raise ValueError('Unsupported unequal precone')
    # Legacy runs recorded only BlPitch1; their collective assumption needs proof.
    separate_pitch = all(key in columns for key in ('BlPitch2', 'BlPitch3'))
    if flexible and not separate_pitch:
        raise ValueError('Flexible reference requires all actual blade pitch outputs')
    if not separate_pitch and not (np.array_equal(columns['BlPitchC1'], columns['BlPitchC2']) and
            np.array_equal(columns['BlPitchC1'], columns['BlPitchC3'])):
        raise ValueError('Individual pitch needs separately recorded actual pitch')
    azimuth = np.degrees(np.unwrap(np.radians(columns['Azimuth'])))
    precise = np.column_stack([columns['YawPzn'], azimuth, columns['RotSpeed'],
                               columns['BlPitch1'], columns.get('BlPitch2', columns['BlPitch1']),
                               columns.get('BlPitch3', columns['BlPitch1'])])[indices]
    if not np.allclose(precise, poses[:, 0], rtol=0, atol=.002):
        raise ValueError('Pose/source mismatch')
    simulation = np.stack([np.column_stack([columns[f'OoPDefl{b}'], columns[f'IPDefl{b}']])[indices]
                           for b in (1, 2, 3)], axis=1)
    for name in [f'{prefix}{b}' for prefix in ('OoPDefl', 'IPDefl') for b in (1, 2, 3)]:
        if units[names.index(name)] != '(m)': raise ValueError('Incorrect units: ' + name)
    if not (np.isfinite(precise).all() and np.isfinite(simulation).all()):
        raise ValueError('Nonfinite source samples')
    payload = dict(schema='wfrl.tip-deflection.t1.v1', turbine_id='T1', unit='m',
        reference='ElastoDyn structural tip; local (0,0,TipRad)',
        frame='ElastoDyn unpitched coned xc,yc,zc',
        geometry_sha256=manifest['files']['geometry.npz'],
        times=times.tolist(), poses=precise.tolist(), simulation=simulation.tolist(), scalars=scalars,
        channels=[[f'OoPDefl{b}', f'IPDefl{b}'] for b in (1, 2, 3)],
        sources={str(p.relative_to(case)): sha(p) for p in (output, ed)},
        geometry_asset_sha256=sha(geometry_asset),
        solver=lines[1].strip(),
        method='Rigid reference from geometry + yaw/azimuth/pitch; actual structural reference transported by saved section transforms; compare in unpitched coned axes.',
        scope='Fixed tower/platform; collective pitch BlPitch1 with equal commands verified; initial blade deflections and pose zero. No axial output recorded. Interpolated frames are not solver samples.',
        source_code='https://github.com/OpenFAST/openfast/blob/v3.5.3/modules/elastodyn/src/ElastoDyn.f90#L744-L755')
    if flexible:
        payload.update(schema='wfrl.tip-deflection.t1.v2',
            tower_motion_sha256=manifest['files']['tower-motion.npz'],
            method='Rigid reference uses independent solver nacelle surface rigid transform plus azimuth/pitch; compare in transported unpitched coned axes.',
            scope='Flexible ElastoDyn tower; fixed platform; independently recorded actual blade pitch; same-run saved surface transforms. Interpolated frames are not solver samples.')
    target = package / 'deflection-t1.json'
    target.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
    manifest['files'][target.name] = sha(target)
    (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return payload


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package'); parser.add_argument('case'); parser.add_argument('geometry_asset')
    args = parser.parse_args()
    export(args.package, args.case, args.geometry_asset)
