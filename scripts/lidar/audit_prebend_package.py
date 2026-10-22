"""Same-input fixed estimator audit, separate +/-3 and +/-15 degree populations."""
from pathlib import Path
import argparse,hashlib,json
import numpy as np
from scripts.lidar.audit_prebend_baseline import metrics
from wfrl.lidar.physics import Calibration,first_hit,tip_reference,simplified_estimate
from wfrl.lidar.moving_tower import horizontal_clearance,tip_surface_clearance,background_hit
from scripts.lidar.package_prebend_probe import save_json


def audit(package,output):
    package,output=Path(package),Path(output);output.mkdir(parents=True,exist_ok=False)
    m=json.loads((package/'manifest.json').read_text())
    for n,h in m['files'].items():
        if hashlib.sha256((package/n).read_bytes()).hexdigest()!=h:raise ValueError('Package integrity mismatch')
    with np.load(package/'geometry.npz') as a:times=a['times'];poses=a['poses'];transforms=a['transforms']
    with np.load(package/'tower-motion.npz') as a:towers=a['transforms'];nacelles=a['nacelle'];heights=a['heights']
    with np.load(package/'reference-surfaces.npz') as a:refs=a['blades'];tri=a['triangles'];tref=a['tower'];ttri=a['tower_triangles']
    ring=np.array([np.flatnonzero(tref[:,2]==h) for h in heights]);cal=Calibration()
    data=json.loads((package/'data.json').read_text());allrows={};report={}
    budget=json.loads((Path(__file__).resolve().parents[2]/'evidence/prebend-20260918/acceptance-budget.json').read_text())
    for k,tid in enumerate(m['turbine_ids']):
        recorded={(r['time_s'],r['blade_id']):r for r in data[tid]['measurements']};rows=[]
        for i,t in enumerate(times):
            angles=(poses[i,k,1]+np.arange(3)*120)%360-180
            blades=np.flatnonzero(abs(angles)<=15)
            if not len(blades):continue
            surfaces=[(np.einsum('sij,snj->sni',tr[:,:,:3],ref.reshape(19,-1,3))+tr[:,:,3][:,None]).reshape(-1,3) for ref,tr in zip(refs,transforms[i,k])]
            tr=towers[i,k];tw=tref.copy();tw[ring]=np.einsum('sij,snj->sni',tr[:,:,:3],tw[ring])+tr[:,:,3][:,None]
            n=nacelles[i,k];origin=n[:,:3]@np.asarray(cal.origin_m)+n[:,3]
            for b in blades:
                key=(float(t),int(b+1));old=recorded.get(key)
                if old is not None:
                    row=dict(old)
                else:
                    center=tip_reference(surfaces[b],19)
                    center_truth,_=horizontal_clearance(center,tw,ttri)
                    truth,tip,wall=tip_surface_clearance(surfaces[b],tw,ttri)
                    row=dict(time_s=float(t),blade_id=int(b+1),expected=True,truth_m=truth,center_truth_m=center_truth,
                        passage_id=f'b{b+1}-p{round((float(poses[i,k,1])+b*120-180)/360)}',beams={})
                    for j,direction in enumerate(cal.directions(),1):
                        direction=n[:,:3]@direction;candidates=[]
                        for other,surface in enumerate(surfaces,1):
                            hit=first_hit(origin,direction,surface,tri)
                            if hit:candidates.append((hit[0],other,hit))
                        hit=background_hit(origin,direction,tw,ttri)
                        if hit:candidates.append((hit[0],hit[2],hit))
                        nearest=min(candidates,key=lambda h:h[0]) if candidates else None
                        valid=bool(nearest and nearest[1]==b+1 and cal.min_range_m<=nearest[0]<=cal.max_range_m)
                        distance=nearest[0] if nearest else None
                        estimate=simplified_estimate(distance,valid,cal.angles_deg[j-1],cal.y_lidar_m,cal.r_tip_m)
                        row['beams'][f'B{j}']=dict(valid=valid,slant_range_m=distance,estimate_m=estimate,error_m=estimate-truth if valid else None)
                row.update(angle_deg=float(angles[b]),yaw_deg=float(poses[i,k,0]),tower_top_displacement_m=float(np.linalg.norm(n[:,:3]@[0,0,87.6]+n[:,3]-[0,0,87.6])))
                rows.append(row)
        narrow=[r for r in rows if (r['time_s'],r['blade_id']) in recorded]
        nm,wm=metrics(narrow),metrics(rows)
        diff=np.array([r['center_truth_m']-r['truth_m'] for r in rows])
        low=[min((r for r in rows if r['passage_id']==p),key=lambda r:r['truth_m']) for p in sorted({r['passage_id'] for r in rows})]
        median_tower=np.median([r['tower_top_displacement_m'] for r in narrow])
        q=dict(old_window=nm,extended_window=wm,
            by_blade={str(b):metrics([r for r in narrow if r['blade_id']==b]) for b in (1,2,3)},
            by_pose={'lower_tower_motion':metrics([r for r in narrow if r['tower_top_displacement_m']<=median_tower]),
                     'higher_tower_motion':metrics([r for r in narrow if r['tower_top_displacement_m']>median_tower]),
                     'yaw_below_5deg':metrics([r for r in narrow if abs(r['yaw_deg'])<5]),
                     'yaw_at_least_5deg':metrics([r for r in narrow if abs(r['yaw_deg'])>=5])},
            tip_definition=dict(max_difference_m=float(diff.max()),p95_difference_m=float(np.percentile(diff,95)),
                threshold_changes=sum((r['truth_m']<=7)!=(r['center_truth_m']<=7) for r in rows)),
            lowest_truth_frames=[{key:r[key] for key in ('time_s','blade_id','truth_m','angle_deg')}|dict(B2_valid=r['beams']['B2']['valid'],in_old_window=(r['time_s'],r['blade_id']) in recorded) for r in low])
        q['passes_error']=nm['p95_m'] is not None and nm['p95_m']<=budget['E95_m'] and nm['max_abs_m']<=budget['Emax_m']
        q['passes_availability']=nm['valid_fraction']>=budget['Vmin_sample_fraction'] and 1-nm['missed_passages']/nm['passages']>=budget['Vmin_passage_fraction']
        report[tid]=q;allrows[tid]=rows
        print(tid,nm,q['passes_error'],q['passes_availability'],flush=True)
        save_json(output/'summary.json',report)
    save_json(output/'rows.json',allrows)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('package');p.add_argument('output')
    a=p.parse_args();audit(a.package,a.output)
