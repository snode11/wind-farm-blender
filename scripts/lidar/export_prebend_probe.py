"""Export a single-turbine BeamDyn interface proof with a separate unloaded rest.

This format is deliberately distinct from production farm replay: no copying
of one turbine into three, no ElastoDyn channel aliases, no loaded-frame rest.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

from scripts.experiments.calib_blade_flex import read_fast_out
from wfrl.blender_bridge.blade_flex_export import fit_sections
from wfrl.lidar.physics import read_surface, first_hit, tip_reference
from wfrl.lidar.moving_tower import horizontal_clearance, background_hit
from wfrl.lidar.physics import Calibration
from wfrl.lidar.physics import simplified_estimate

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'blender_frontend'))
from wfrl_blender.deflection import rigid_frame
from wfrl_blender.turbine_geometry import geometry_data


def export(run, reference, output):
    run, reference, output = map(Path, (run, reference, output))
    config = json.loads((run/'probe-config.json').read_text())
    rest_config = json.loads((reference/'probe-config.json').read_text())
    if (config['structural_module'] != 'BeamDyn' or not rest_config['unloaded']
            or config['reference_curve'] != rest_config['reference_curve']):
        raise ValueError('A matching unloaded BeamDyn reference is required')
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}
    def surface(folder, kind, frame):
        matches = [p for p in (folder/'FarmInputs/vtk').glob(f'FFTest_WT1.{kind}Surface.*.vtp')
                   if int(p.stem.rsplit('.',1)[1]) == frame]
        if len(matches) != 1: raise ValueError('Ambiguous/missing surface')
        path = matches[0]
        hashes[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        return read_surface(path)
    refs = [surface(reference, f'Blade{b}', 0)[0].reshape(19,-1,3) for b in (1,2,3)]
    nref, _ = surface(reference, 'Nacelle', 0)
    tref, triangles_tower = surface(reference, 'Tower', 0)
    heights = np.unique(tref[:,2])
    rings = np.array([np.flatnonzero(tref[:,2] == z) for z in heights])
    scalars = geometry_data()['scalars']
    curve = np.asarray(config['reference_curve'])
    tip_local = np.array([curve[-1,0], curve[-1,1], scalars['TipRad']])
    reference_tip = []
    for b in (1,2,3):
        hub, axes, pitched = rigid_frame(scalars, [0]*6, b)
        reference_tip.append(hub + pitched@tip_local)
    columns = read_fast_out(run/'FarmInputs/FFTest_WT1.out')
    times = columns['Time']
    poses = np.column_stack([columns[k] for k in ('YawPzn','Azimuth','RotSpeed','BlPitch1','BlPitch2','BlPitch3')])
    transforms, nacelles, towers, actuals, rigid_tips, deltas, solver = [], [], [], [], [], [], []
    measurements = []
    calibration = Calibration()
    max_fit = 0.
    for i, t in enumerate(times):
        frame = round(t*40)
        n, _ = surface(run, 'Nacelle', frame)
        nr, nd, err = fit_sections(nref[None], n[None])
        nacelle = np.column_stack((nr[0],nd[0])); nacelles.append(nacelle)
        tw, _ = surface(run, 'Tower', frame)
        tr, td, terr = fit_sections(tref[rings], tw[rings])
        towers.append(np.concatenate((tr,td[:,:,None]),axis=2))
        max_fit = max(max_fit,err,terr)
        mats, actual, rigid, delta, sim = [], [], [], [], []
        surfaces = []
        for b in (1,2,3):
            pts, triangles = surface(run, f'Blade{b}', frame)
            surfaces.append((pts, triangles))
            r, d, err = fit_sections(refs[b-1], pts.reshape(19,-1,3))
            max_fit = max(max_fit,err)
            mats.append(np.concatenate((r,d[:,:,None]),axis=2))
            point = r[-1]@reference_tip[b-1] + d[-1]
            hub, _, axes = rigid_frame(scalars, poses[i], b, nacelle)
            ref = hub + axes@tip_local
            actual.append(point); rigid.append(ref); delta.append(axes.T@(point-ref))
            sim.append([columns[f'B{b}TipTD{axis}r'][i] for axis in 'xyz'])
        transforms.append(mats); actuals.append(actual); rigid_tips.append(rigid); deltas.append(delta); solver.append(sim)
        origin = nr[0]@np.array(calibration.origin_m) + nd[0]
        for b in (1,2,3):
            angle = (poses[i,1]+(b-1)*120) % 360 - 180
            if abs(angle) > 15: continue
            truth, wall = horizontal_clearance(tip_reference(surfaces[b-1][0],19),tw,triangles_tower)
            row = dict(time_s=float(t),blade_id=b,angle_deg=float(angle),truth_m=truth,beams={})
            for j, direction in enumerate(calibration.directions(),1):
                direction = nr[0]@direction
                candidates = []
                for other,(pts,tris) in enumerate(surfaces,1):
                    hit = first_hit(origin,direction,pts,tris)
                    if hit: candidates.append((hit[0],other,hit))
                hit = background_hit(origin,direction,tw,triangles_tower)
                if hit: candidates.append((hit[0],hit[2],hit))
                nearest = min(candidates,key=lambda h:h[0]) if candidates else None
                valid = bool(nearest and nearest[1]==b and calibration.min_range_m<=nearest[0]<=calibration.max_range_m)
                distance = nearest[0] if nearest else None
                estimate = simplified_estimate(distance,valid,calibration.angles_deg[j-1],calibration.y_lidar_m,calibration.r_tip_m)
                row['beams'][f'B{j}'] = dict(valid=valid,slant_range_m=distance,estimate_m=estimate,
                    error_m=estimate-truth if valid else None,
                    origin_m=origin.tolist(),direction=direction.tolist(),
                    hit_point_m=list(nearest[2][1]) if nearest else None)
            measurements.append(row)
    errors = np.asarray(deltas)-solver
    if max_fit > .002 or np.max(np.abs(errors)) > .005:
        raise ValueError(f'Interface tolerance failed: geometry={max_fit}, deflection={np.max(np.abs(errors))}')
    np.savez_compressed(output/'probe.npz',times=times,poses=poses,reference_surfaces=refs,
        reference_tip=reference_tip,triangles=triangles,transforms=transforms,
        nacelles=nacelles,tower_reference=tref,tower_triangles=triangles_tower,
        tower_heights=heights,tower_rings=rings,towers=towers,
        actual_tip=actuals,rigid_tip=rigid_tips,components=deltas,solver_components=solver)
    (output/'measurements.json').write_text(json.dumps(measurements,indent=2,allow_nan=False))
    manifest = dict(schema='wfrl.beamdyn-interface-probe.v1',status='INTERFACE_ONLY',
        model=config['model'],structural_module='BeamDyn',turbine_ids=['T1'],
        reference='Separate zero-gravity, still-air, stationary-rotor solve; no loaded-frame reference',
        reference_curve=config['reference_curve'],tip_local_m=tip_local.tolist(),scalars=scalars,
        time_unit='s',length_unit='m',frame='Turbine-local world; rigid motion included once',
        aerodynamic_sections=19,structural_key_points=49,structural_element_order=5,
        max_surface_fit_error_m=max_fit,max_component_error_m=float(np.max(np.abs(errors))),
        first_loaded_deflection_m=np.asarray(deltas)[0].tolist(),
        mapping='Ordered AeroDyn contours fitted independently of BeamDyn nodes; structural tip transported by terminal-section transform',
        sources=hashes,files={name:hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ('probe.npz','measurements.json')},
        boundary='Single turbine prescribed 9 rpm / steady 8 m/s / startup segment. No MAPPO, no production three-turbine package or field accuracy claim.')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({k:manifest[k] for k in ('schema','max_surface_fit_error_m','max_component_error_m')}))


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path);p.add_argument('reference',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();export(a.run,a.reference,a.output)
