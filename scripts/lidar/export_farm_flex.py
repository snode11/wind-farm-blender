"""Export all nine physical blade surfaces and nacelle-local lidar to one clip."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from scipy.spatial.transform import Rotation

from scripts.experiments.calib_blade_flex import read_fast_out
from wfrl.blender_bridge.blade_flex_export import fit_sections
from wfrl.lidar.physics import (Calibration, read_surface, tip_reference, truth_clearance,
    first_hit, simplified_estimate, collision_excluded, background_first_hit)
from wfrl.lidar.replay import precompute


def add_provenance(destination,wind_config,controller_build):
    destination=Path(destination)
    manifest=json.loads((destination/'manifest.json').read_text())
    wind=json.loads(Path(wind_config).read_text())
    controller=json.loads(Path(controller_build).read_text())
    if not any(i['path'].endswith('/random-gust.bts') and i['sha256']==manifest['wind_sha256'] for i in wind['inputs']):
        raise ValueError('Wind metadata does not match the run')
    if controller['binary_sha256']!=manifest['controller_sha256']:
        raise ValueError('Controller metadata does not match the run')
    manifest['wind_profile']=wind['wind_profile']
    manifest['controller_build']=controller
    manifest['wind_config_sha256']=hashlib.sha256(Path(wind_config).read_bytes()).hexdigest()
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))


def export(probe, destination, start=117., duration=60., diagnostic=False):
    probe, destination = Path(probe), Path(destination)
    report = json.loads(probe.read_text())
    if report['status'] != 'CONTROL_PROBE_COMPLETE' and not (
            diagnostic and report.get('physical_checks')
            and all(c['finite'] for c in report['physical_checks'])):
        raise ValueError('Physical controller validation must pass before export')
    farm = Path(report['case_dir'])/'FarmInputs'
    destination.mkdir(parents=True, exist_ok=False)
    flexible = report.get("tower") == "flexible"
    beamdyn = report.get('blade_model') == 'beamdyn-prebend'
    if beamdyn and not flexible: raise ValueError('BeamDyn v3 requires moving tower')
    rest_farm = Path(report['unloaded_reference'])/'FarmInputs' if beamdyn else None
    fps=40
    times=np.arange(round(start*fps),round((start+duration)*fps)+1)/fps
    transforms=np.empty((len(times),3,3,19,3,4),np.float32)
    poses=np.empty((len(times),3,6),np.float32)
    replay=dict(threshold_m=7.,hysteresis_m=.1,max_hold_s=5.,passage_margin=1.25)
    calibration=Calibration()
    data={}; hashes={}; max_error=0.; certificates=0
    tower_transforms=[]; nacelle_transforms=[]; tower_heights=None; support_error=0.
    from wfrl.lidar.moving_tower import horizontal_clearance, background_hit, separated_from_tower
    for turbine, x in enumerate((0.,504.,1008.)):
        tid=f'T{turbine+1}'
        states=read_fast_out(farm/f'Case.{tid}.out')
        azimuth=np.degrees(np.unwrap(np.radians(states['Azimuth'])))
        if not np.allclose(states['Time'],np.arange(len(states['Time']))/fps,atol=5.1e-5,rtol=0):
            raise ValueError('Output grid mismatch')
        def surface(bid,frame):
            path=farm/'vtk'/f'Case.{tid}.Blade{bid}Surface.{frame:05d}.vtp'
            hashes[str(path.relative_to(farm))]=hashlib.sha256(path.read_bytes()).hexdigest()
            points, triangles=read_surface(path)
            return points-np.array([x,0,0]),triangles
        reference=[surface(b,0)[0].reshape(19,-1,3) for b in (1,2,3)]
        if beamdyn:
            reference=[]
            for b in (1,2,3):
                path=next(p for p in (rest_farm/'vtk').glob(f'FFTest_WT1.Blade{b}Surface.*.vtp') if int(p.stem.rsplit('.',1)[1])==0)
                hashes[str(path.resolve())]=hashlib.sha256(path.read_bytes()).hexdigest()
                reference.append(read_surface(path)[0].reshape(19,-1,3))
        def support(kind, frame):
            path=farm/'vtk'/f'Case.{tid}.{kind}Surface.{frame:05d}.vtp'
            hashes[str(path.relative_to(farm))]=hashlib.sha256(path.read_bytes()).hexdigest()
            points,triangles=read_surface(path)
            return points-np.array([x,0,0]),triangles
        if flexible:
            tower0,_=support('Tower',0); nacelle0,_=support('Nacelle',0)
            heights=np.unique(tower0[:,2])
            if tower_heights is not None and not np.array_equal(heights,tower_heights):
                raise ValueError('Tower station mismatch')
            tower_heights=heights
            ring_ids=np.array([np.flatnonzero(tower0[:,2]==z) for z in heights])
            tower_series=[]; nacelle_series=[]
        motion=[]; measurements=[]
        for j,t in enumerate(times):
            frame=round(t*fps)
            yaw=float(states['YawPzn'][frame]); az=float(azimuth[frame])
            pitch=[float(states.get(f'BlPitch{b}',states['BlPitch1'])[frame]) for b in (1,2,3)]
            rpm=float(states['RotSpeed'][frame])
            poses[j,turbine]=[yaw,az,rpm,*pitch]
            if flexible:
                tower_pts,tower_tri=support('Tower',frame)
                nacelle_pts,_=support('Nacelle',frame)
                nr,nd,ne=fit_sections(nacelle0[None],nacelle_pts[None])
                tr,td,te=fit_sections(tower0[ring_ids],tower_pts[ring_ids])
                support_error=max(support_error,ne,te)
                if max(ne,te)>.002: raise ValueError('Support fit exceeds 2 mm')
                nr,nd=nr[0],nd[0]
                tower_series.append(np.concatenate((tr,td[:,:,None]),axis=2))
                nacelle_series.append(np.column_stack((nr,nd)))
                top=nr@np.array([0.,0.,87.6])+nd
                numerical=np.array([states[c][frame] for c in ('YawBrTDxt','YawBrTDyt','YawBrTDzt')])+[0,0,87.6]
                if np.linalg.norm(top-numerical)>.002: raise ValueError('Nacelle surface/output reference mismatch')
                origin=nr@np.array(calibration.origin_m)+nd
                directions=[nr@d for d in calibration.directions()]
            motion.append(dict(time_s=float(t),azimuth_deg=az,pitch_deg=pitch,yaw_deg=yaw,
                rotor_speed_rpm=rpm,nacelle_position_m=(top+[x,0,0]).tolist() if flexible else [x,0,87.6],
                nacelle_orientation_deg=Rotation.from_matrix(nr).as_euler("xyz",degrees=True).tolist() if flexible else [0,0,yaw],
                **({'nacelle_transform':nacelle_series[-1].tolist()} if flexible else {})))
            a=np.radians(yaw); rotation=np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1]])
            surfaces=[surface(b,frame) for b in (1,2,3)]
            local=surfaces if flexible else [(points@rotation,tris) for points,tris in surfaces]
            for b,(points,tris) in enumerate(surfaces,1):
                r,d,error=fit_sections(reference[b-1],points.reshape(19,-1,3))
                max_error=max(max_error,error)
                if error>.002:
                    raise ValueError(f'Section fit exceeds 2 mm: {tid} {b} {t} {error}')
                transforms[j,turbine,b-1]=np.concatenate((r,d[:,:,None]),axis=2)
                separated=separated_from_tower(points,tris,tower_pts,tower_tri) if flexible else collision_excluded(points,tris)
                if not separated:
                    raise ValueError(f'Cannot exclude saved-surface intersection: {tid} {b} {t}')
                certificates+=1
                delta=(az+(b-1)*120-180+180)%360-180
                if abs(delta)>3:
                    continue
                tip=tip_reference(local[b-1][0],19)
                truth,wall=horizontal_clearance(tip,tower_pts,tower_tri) if flexible else truth_clearance(tip)
                center_truth=truth
                if beamdyn:
                    from wfrl.lidar.moving_tower import tip_surface_clearance
                    truth,tip,wall=tip_surface_clearance(local[b-1][0],tower_pts,tower_tri)
                to_world=lambda p: (np.asarray(p)+(np.array([x,0,0]))).tolist() if flexible else (np.asarray(p)@rotation.T+[x,0,0]).tolist()
                row=dict(time_s=float(t),blade_id=b,expected=True,
                    passage_id=f'b{b}-p{round((az+(b-1)*120-180)/360)}',
                    truth_m=truth,center_truth_m=center_truth,truth_tip_point_m=to_world(tip),
                    truth_wall_point_m=to_world(wall),beams={})
                for n,direction in enumerate(directions if flexible else calibration.directions(),1):
                    candidates=[]
                    for other,(pts,tri) in enumerate(local,1):
                        hit=first_hit(origin if flexible else calibration.origin_m,direction,pts,tri)
                        if hit: candidates.append((hit[0],other,hit))
                    hit=background_hit(origin,direction,tower_pts,tower_tri) if flexible else background_first_hit(calibration.origin_m,direction)
                    if hit: candidates.append((hit[0],hit[2],hit))
                    nearest=min(candidates,key=lambda h:h[0]) if candidates else None
                    hit=nearest[2] if nearest else None
                    valid=bool(hit and nearest[1]==b and calibration.min_range_m<=hit[0]<=calibration.max_range_m)
                    estimate=simplified_estimate(hit[0] if hit else None,valid,
                        calibration.angles_deg[n-1],calibration.y_lidar_m,calibration.r_tip_m)
                    row['beams'][f'B{n}']=dict(valid=valid,slant_range_m=hit[0] if hit else None,
                        estimate_m=estimate,error_m=estimate-truth if valid else None,
                        reason='valid' if valid else 'no_hit_or_other_surface',
                        hit_point_m=to_world(hit[1]) if hit else None)
                measurements.append(row)
            if j%400==0:print(tid,float(t),'exported',flush=True)
        if flexible:
            tower_transforms.append(tower_series); nacelle_transforms.append(nacelle_series)
        cumulative,statistics=precompute(measurements,motion,replay)
        data[tid]=dict(motion=motion,measurements=measurements,cumulative=cumulative,statistics=statistics)
    np.savez_compressed(destination/'geometry.npz',times=times,transforms=transforms,poses=poses)
    (destination/'data.json').write_text(json.dumps(data,allow_nan=False,separators=(',',':')))
    (destination/'source-surfaces.json').write_text(json.dumps(hashes,separators=(',',':')))
    if flexible:
        np.savez_compressed(destination/'tower-motion.npz',times=times,heights=tower_heights,
            transforms=np.asarray(tower_transforms,np.float32).swapaxes(0,1),
            nacelle=np.asarray(nacelle_transforms,np.float64).swapaxes(0,1))
    manifest=dict(schema='wfrl.farm-flex-review.v2' if flexible else 'wfrl.farm-flex-review.v1',status='REVIEW_ONLY',source='FAST.Farm',
        segment=dict(start_s=float(times[0]),end_s=float(times[-1])),
        turbine_ids=['T1','T2','T3'],layout_m=[[0,0,0],[504,0,0],[1008,0,0]],
        source_fps=fps,reference_frame=0,includes_rigid_motion=True,
        max_surface_fit_error_m=max_error,discrete_collision_certificates=certificates,
        replay=replay,policy=report['policy'],controller_sha256=report['controller_sha256'],
        wind_sha256=report.get('wind_sha256'),
        calibration=dict(origin_m=list(calibration.origin_m),beam_directions=[d.tolist() for d in calibration.directions()],
                         frame='nacelle yaw-local; rigid tower; translated separately for each turbine'),
        validation='discrete surfaces only; no temporal/span refinement certification',
        diagnostic_only=diagnostic,controller_validation=report.get('physical_checks'),
        files={name:hashlib.sha256((destination/name).read_bytes()).hexdigest()
               for name in ('geometry.npz','data.json','source-surfaces.json')})
    if flexible:
        manifest.update(tower_model='elastodyn-flexible',max_support_fit_error_m=support_error,
            clearance_definition='Tip section centroid to same-global-height horizontal section of solver triangulated moving tower; not whole-blade minimum',
            estimator_boundary='Fixed calibrated slant-range formula retained; tower bending is NOT compensated in the estimate; error is against moving-tower truth')
        manifest['files']['tower-motion.npz']=hashlib.sha256((destination/'tower-motion.npz').read_bytes()).hexdigest()
        manifest['calibration']['frame']='nacelle material frame at rest; transported by solver NacelleSurface rotation and translation'
    if beamdyn:
        from scripts.lidar.export_prebend_sidecars import supplement
        supplement(destination, report, manifest, times, poses, transforms)
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('FARM_FLEX_EXPORTED',json.dumps(manifest),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('probe');p.add_argument('destination')
    p.add_argument('--start',type=float,default=117);p.add_argument('--duration',type=float,default=60)
    p.add_argument('--diagnostic-only',action='store_true',help='Internal debugging of a finite but unaccepted controller run')
    a=p.parse_args();export(a.probe,a.destination,a.start,a.duration,a.diagnostic_only)
