"""Consume persistent solver surfaces, save reviewable measurements (not READY)."""
from pathlib import Path
import argparse, json, math, sys, hashlib
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from wfrl.lidar.physics import Calibration,read_surface,tip_reference,truth_clearance,first_hit,simplified_estimate,collision_excluded,background_first_hit

def process(run,evaluation_only=False):
    run=Path(run)
    if (run/'processed.json').exists():raise FileExistsError(run/'processed.json')
    config=json.loads((run/'run_config.json').read_text());fps=config['fps'];start=config['startup_discard_s'];end=config['duration_s'];sections=37 if config['span_refined'] else 19
    if json.loads((run/'exit_status.json').read_text())['exit_code']!=0 or 'FAST.Farm terminated normally.' not in (run/'solver.log').read_text():raise ValueError('solver not completed')
    farm=run/'FarmInputs';lines=(farm/'Case.T1.out').read_text().splitlines();header=next(i for i,l in enumerate(lines) if l.startswith('Time\t'))
    names=lines[header].split();state=np.loadtxt(lines[header+2:]);col={k:i for i,k in enumerate(names)}
    # OpenFAST ASCII time uses four decimals regardless of OutFmt; index grid restores
    # the configured physical output times only after bounded rounding validation.
    if not np.allclose(state[:,0],np.arange(len(state))/fps,atol=5.1e-5,rtol=0):raise ValueError('output time grid mismatch')
    unwrapped=np.degrees(np.unwrap(np.radians(state[:,col['Azimuth']])))
    files={b:sorted((farm/'vtk').glob(f'Case.T1.Blade{b}Surface.*.vtp')) for b in range(1,4)}
    if any(len(fs)!=len(state) for fs in files.values()):raise ValueError('surface/state frame mismatch')
    calibration=Calibration();motion=[];measurements=[];collisions=[];hashes=[];true_passages={}
    for i,row in enumerate(state):
        t=i/fps
        if t<start or t>end:continue
        az=float(row[col['Azimuth']]);rpm=float(row[col['RotSpeed']]);yaw=float(row[col['YawPzn']]);pitch=float(row[col['BlPitch1']])
        if abs(yaw)>1e-6:raise ValueError('fixed calibration requires zero yaw')
        motion.append(dict(time_s=t,azimuth_deg=float(unwrapped[i]),pitch_deg=[pitch]*3,yaw_deg=yaw,rotor_speed_rpm=rpm,nacelle_position_m=[0,0,87.6],nacelle_orientation_deg=[0,0,yaw]))
        for b in range(1,4):
            phase=az+(b-1)*120;delta=(phase-180+180)%360-180
            expected=abs(delta)<=3
            if evaluation_only and not expected:continue
            # Certificate checks every saved physical time sample, independent of hits.
            pts,tris=read_surface(files[b][i]);collisions.append(collision_excluded(pts,tris)) if not evaluation_only else None
            if not expected:continue
            tip=tip_reference(pts,sections);truth,wall=truth_clearance(tip)
            passage=f'b{b}-p{round((float(unwrapped[i])+(b-1)*120-180)/360)}';true_passages.setdefault(passage,[]).append(truth)
            record=dict(time_s=t,blade_id=b,expected=True,passage_id=passage,truth_m=truth,truth_tip_point_m=tip.tolist(),truth_wall_point_m=wall,beams={})
            for n,direction in enumerate(calibration.directions()):
                # Other blades cannot be excluded by the angular gate; test their first hits too.
                candidates=[]
                for other in range(1,4):
                    op,ot=(pts,tris) if other==b else read_surface(files[other][i])
                    hit=first_hit(calibration.origin_m,direction,op,ot)
                    if hit:candidates.append((hit[0],other,hit))
                background=background_first_hit(calibration.origin_m,direction)
                if background:candidates.append((background[0],background[2],background))
                nearest=min(candidates,key=lambda h:h[0]) if candidates else None
                hit=nearest[2] if nearest else None
                valid=bool(hit and nearest[1]==b and calibration.min_range_m<=hit[0]<=calibration.max_range_m)
                estimate=simplified_estimate(hit[0] if hit else None,valid,calibration.angles_deg[n],calibration.y_lidar_m,calibration.r_tip_m)
                record['beams'][f'B{n+1}']=dict(valid=valid,slant_range_m=hit[0] if hit else None,estimate_m=estimate,error_m=estimate-truth if valid else None,reason='valid' if valid else ('no_hit' if hit is None else str(nearest[1]) if nearest and isinstance(nearest[1],str) else 'other_blade_or_range'),hit_point_m=hit[1] if hit else None)
            measurements.append(record)
        if i%400==0:print(run.name,'processed',t,flush=True)
    # Retain input+original output hashes. Surface set gets its own ordered digest index.
    for fs in ([] if evaluation_only else files.values()):
        for p in fs:
            hashes.append(dict(path=str(p.relative_to(run)),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    if not evaluation_only:(run/'surface_hashes.json').write_text(json.dumps(hashes,separators=(',',':')))
    output=dict(motion=motion,measurements=measurements,collision_excluded=all(collisions) if collisions else None,evaluation_only=evaluation_only,collision_certificates=len(collisions),calibration=dict(origin_m=list(calibration.origin_m),beam_directions=[d.tolist() for d in calibration.directions()],angles_deg=list(calibration.angles_deg),y_lidar_m=calibration.y_lidar_m,r_tip_m=calibration.r_tip_m,min_range_m=calibration.min_range_m,max_range_m=calibration.max_range_m),passage_mean_truth_m={p:float(np.mean(v)) for p,v in true_passages.items()},tip_reference='perimeter-coordinate centroid of outermost AeroDyn airfoil section; not global surface collision clearance')
    target=run/'processed.json';target.write_text(json.dumps(output,indent=2,allow_nan=False));print(target,flush=True)
    print('B2',sum(r['beams']['B2']['valid'] for r in measurements),'/',len(measurements),'collision',all(collisions) if collisions else 'not_checked_evaluation_only',flush=True)
    return output
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('run');p.add_argument('--evaluation-only',action='store_true');a=p.parse_args();process(a.run,a.evaluation_only)
