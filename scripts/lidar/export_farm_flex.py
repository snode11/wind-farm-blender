"""Export all nine physical blade surfaces and nacelle-local lidar to one clip."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np

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
    fps=40
    times=np.arange(round(start*fps),round((start+duration)*fps)+1)/fps
    transforms=np.empty((len(times),3,3,19,3,4),np.float32)
    poses=np.empty((len(times),3,6),np.float32)
    replay=dict(threshold_m=7.,hysteresis_m=.1,max_hold_s=5.,passage_margin=1.25)
    calibration=Calibration()
    data={}; hashes={}; max_error=0.; certificates=0
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
        motion=[]; measurements=[]
        for j,t in enumerate(times):
            frame=round(t*fps)
            yaw=float(states['YawPzn'][frame]); az=float(azimuth[frame])
            pitch=[float(states.get(f'BlPitch{b}',states['BlPitch1'])[frame]) for b in (1,2,3)]
            rpm=float(states['RotSpeed'][frame])
            poses[j,turbine]=[yaw,az,rpm,*pitch]
            motion.append(dict(time_s=float(t),azimuth_deg=az,pitch_deg=pitch,yaw_deg=yaw,
                rotor_speed_rpm=rpm,nacelle_position_m=[x,0,87.6],nacelle_orientation_deg=[0,0,yaw]))
            a=np.radians(yaw); rotation=np.array([[np.cos(a),-np.sin(a),0],[np.sin(a),np.cos(a),0],[0,0,1]])
            surfaces=[surface(b,frame) for b in (1,2,3)]
            local=[(points@rotation,tris) for points,tris in surfaces]
            for b,(points,tris) in enumerate(surfaces,1):
                r,d,error=fit_sections(reference[b-1],points.reshape(19,-1,3))
                max_error=max(max_error,error)
                if error>.002:
                    raise ValueError(f'Section fit exceeds 2 mm: {tid} {b} {t} {error}')
                transforms[j,turbine,b-1]=np.concatenate((r,d[:,:,None]),axis=2)
                if not collision_excluded(points,tris):
                    raise ValueError(f'Cannot exclude saved-surface intersection: {tid} {b} {t}')
                certificates+=1
                delta=(az+(b-1)*120-180+180)%360-180
                if abs(delta)>3:
                    continue
                tip=tip_reference(local[b-1][0],19)
                truth,wall=truth_clearance(tip)
                row=dict(time_s=float(t),blade_id=b,expected=True,
                    passage_id=f'b{b}-p{round((az+(b-1)*120-180)/360)}',
                    truth_m=truth,truth_tip_point_m=(tip@rotation.T+[x,0,0]).tolist(),
                    truth_wall_point_m=(np.asarray(wall)@rotation.T+[x,0,0]).tolist(),beams={})
                for n,direction in enumerate(calibration.directions(),1):
                    candidates=[]
                    for other,(pts,tri) in enumerate(local,1):
                        hit=first_hit(calibration.origin_m,direction,pts,tri)
                        if hit: candidates.append((hit[0],other,hit))
                    hit=background_first_hit(calibration.origin_m,direction)
                    if hit: candidates.append((hit[0],hit[2],hit))
                    nearest=min(candidates,key=lambda h:h[0]) if candidates else None
                    hit=nearest[2] if nearest else None
                    valid=bool(hit and nearest[1]==b and calibration.min_range_m<=hit[0]<=calibration.max_range_m)
                    estimate=simplified_estimate(hit[0] if hit else None,valid,
                        calibration.angles_deg[n-1],calibration.y_lidar_m,calibration.r_tip_m)
                    row['beams'][f'B{n}']=dict(valid=valid,slant_range_m=hit[0] if hit else None,
                        estimate_m=estimate,error_m=estimate-truth if valid else None,
                        reason='valid' if valid else 'no_hit_or_other_surface',
                        hit_point_m=(np.asarray(hit[1])@rotation.T+[x,0,0]).tolist() if hit else None)
                measurements.append(row)
            if j%400==0:print(tid,float(t),'exported',flush=True)
        cumulative,statistics=precompute(measurements,motion,replay)
        data[tid]=dict(motion=motion,measurements=measurements,cumulative=cumulative,statistics=statistics)
    np.savez_compressed(destination/'geometry.npz',times=times,transforms=transforms,poses=poses)
    (destination/'data.json').write_text(json.dumps(data,allow_nan=False,separators=(',',':')))
    (destination/'source-surfaces.json').write_text(json.dumps(hashes,separators=(',',':')))
    manifest=dict(schema='wfrl.farm-flex-review.v1',status='REVIEW_ONLY',source='FAST.Farm',
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
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('FARM_FLEX_EXPORTED',json.dumps(manifest),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('probe');p.add_argument('destination')
    p.add_argument('--start',type=float,default=117);p.add_argument('--duration',type=float,default=60)
    p.add_argument('--diagnostic-only',action='store_true',help='Internal debugging of a finite but unaccepted controller run')
    a=p.parse_args();export(a.probe,a.destination,a.start,a.duration,a.diagnostic_only)
