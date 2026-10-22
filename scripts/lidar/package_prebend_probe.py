"""Put one actual short solve through the production v3 frontend contract."""
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from wfrl.lidar.replay import precompute


def save_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def reference_payload(config, sources):
    return dict(schema='wfrl.blade-reference.v1', structural_module='BeamDyn',
        model=config['model'], curve=config['reference_curve'], tip_local_m=[-1.,0.,63.],
        definition='x=-(z/61.5)^2; y=0; metres; z from blade root',
        reference_state='independent zero-load stationary solve', precone_deg=-2.5,
        source_note='NREL 5MW derivative; Jonkman 2016 quadratic upwind prebend example; no claim of real-machine equivalence',
        sources=sources)


def package(source, output):
    source, output = Path(source), Path(output)
    m = json.loads((source/'manifest.json').read_text())
    for name, digest in m['files'].items():
        assert hashlib.sha256((source/name).read_bytes()).hexdigest() == digest
    with np.load(source/'probe.npz', allow_pickle=False) as a:
        d = {k:a[k] for k in a.files}
    output.mkdir(parents=True, exist_ok=False)
    times, poses = d['times'], d['poses']
    # Continuous azimuth is necessary for time interpolation around 360 degrees.
    poses[:,1] = np.degrees(np.unwrap(np.radians(poses[:,1])))
    np.savez_compressed(output/'geometry.npz',times=times,poses=poses[:,None],transforms=d['transforms'][:,None])
    np.savez_compressed(output/'tower-motion.npz',times=times,heights=d['tower_heights'],
        transforms=d['towers'][:,None],nacelle=d['nacelles'][:,None])
    replay=dict(threshold_m=7.,hysteresis_m=.1,max_hold_s=5.,passage_margin=1.25)
    motion=[]
    for t,p,n in zip(times,poses,d['nacelles']):
        motion.append(dict(time_s=float(t),yaw_deg=p[0],azimuth_deg=p[1],rotor_speed_rpm=p[2],pitch_deg=p[3:].tolist(),
            nacelle_transform=n.tolist(),nacelle_position_m=(n[:,:3]@[0,0,87.6]+n[:,3]).tolist(),
            nacelle_orientation_deg=Rotation.from_matrix(n[:,:3]).as_euler('xyz',degrees=True).tolist()))
    rows=json.loads((source/'measurements.json').read_text())
    rows=[r for r in rows if abs(r['angle_deg'])<=3]
    from wfrl.lidar.moving_tower import tip_surface_clearance
    for r in rows:
        i=int(np.argmin(abs(times-r['time_s'])));b=r['blade_id']-1
        tr=d['transforms'][i,b]
        points=np.einsum('sij,snj->sni',tr[:,:,:3],d['reference_surfaces'][b])+tr[:,:,3][:,None]
        tw=d['tower_reference'].copy();tr=d['towers'][i];ids=d['tower_rings']
        tw[ids]=np.einsum('sij,snj->sni',tr[:,:,:3],tw[ids])+tr[:,:,3][:,None]
        truth,tip,wall=tip_surface_clearance(points,tw,d['tower_triangles'])
        r['center_truth_m']=r['truth_m'];r['truth_m']=truth
        r['truth_tip_point_m']=tip;r['truth_wall_point_m']=wall
        for beam in r['beams'].values():
            beam['error_m']=beam['estimate_m']-truth if beam['valid'] else None
        r['expected']=True
        az=np.interp(r['time_s'],times,poses[:,1])
        r['passage_id']=f"b{r['blade_id']}-p{round((az+(r['blade_id']-1)*120-180)/360)}"
    cumulative, statistics=precompute(rows,motion,replay)
    save_json(output/'data.json',{'T1':dict(motion=motion,measurements=rows,cumulative=cumulative,statistics=statistics)})
    save_json(output/'source-surfaces.json',m['sources'])
    save_json(output/'blade-reference.json',reference_payload(m,m['sources']))
    np.savez_compressed(output/'reference-surfaces.npz',blades=d['reference_surfaces'].reshape(3,-1,3),triangles=d['triangles'],tower=d['tower_reference'],tower_triangles=d['tower_triangles'])
    files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    save_json(output/'deflection-t1.json',dict(schema='wfrl.tip-deflection.t1.v3',turbine_id='T1',unit='m',
        reference='BeamDyn structural tip; independent unloaded prebend', frame='BeamDyn pitched root xyz',
        geometry_sha256=files['geometry.npz'], tower_motion_sha256=files['tower-motion.npz'],
        reference_sha256=files['blade-reference.json'],times=times.tolist(),poses=poses.tolist(),
        simulation=d['solver_components'].tolist(),scalars=m['scalars']))
    files['deflection-t1.json']=hashlib.sha256((output/'deflection-t1.json').read_bytes()).hexdigest()
    manifest=dict(schema='wfrl.farm-flex-review.v3',status='REVIEW_ONLY',source='OpenFAST',
        turbine_ids=['T1'],layout_m=[[0,0,0]],segment=dict(start_s=float(times[0]),end_s=float(times[-1])),
        source_fps=40,includes_rigid_motion=True,tower_model='elastodyn-flexible',structural_module='BeamDyn',
        model=m['model'],provenance_label='预弯改型 · BeamDyn · 单机接口验证 · 8 m/s 恒定风 / 9 rpm',
        reference_frame='independent zero-load stationary solve',replay=replay,files=files,
        calibration=dict(origin_m=[-2,0,87.6]),interface_only=True,measurement_window_deg=3,
        clearance_definition='Terminal contour surface minimum to same-global-height moving tower section; not whole-blade minimum',
        max_surface_fit_error_m=m['max_surface_fit_error_m'],max_component_error_m=m['max_component_error_m'])
    save_json(output/'manifest.json',manifest)
    return manifest


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output')
    a=p.parse_args();package(a.source,a.output)
