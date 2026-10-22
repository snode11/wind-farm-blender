"""Re-evaluate saved baseline geometry at +/-15 deg; retain separate denominators."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from scripts.lidar.audit_prebend_baseline import metrics
from wfrl.lidar.physics import Calibration, first_hit, read_surface, tip_reference, simplified_estimate
from wfrl.lidar.moving_tower import horizontal_clearance, background_hit


def audit(package, output):
    package, output = Path(package), Path(output)
    m=json.loads((package/'manifest.json').read_text())
    for name,digest in m['files'].items():
        if hashlib.sha256((package/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Package hash mismatch')
    farm=Path(json.loads((package/'source-run.json').read_text())['case_dir'])/'FarmInputs'
    hashes=json.loads((package/'source-surfaces.json').read_text())
    with np.load(package/'geometry.npz') as a:
        times=a['times']; transforms=a['transforms'];poses=a['poses']
    with np.load(package/'tower-motion.npz') as a:
        towers=a['transforms'];nacelles=a['nacelle'];heights=a['heights']
    calibration=Calibration(); result={}; all_rows={}
    for k,tid in enumerate(m['turbine_ids']):
        offset=np.array(m['layout_m'][k])
        def reference(kind):
            key=f'vtk/Case.{tid}.{kind}Surface.00000.vtp';path=farm/key
            if hashlib.sha256(path.read_bytes()).hexdigest()!=hashes[key]:raise ValueError('Source mismatch')
            p,t=read_surface(path);return p-offset,t
        refs=[reference(f'Blade{b}') for b in (1,2,3)]
        twref,twtri=reference('Tower')
        rings=np.array([np.flatnonzero(twref[:,2]==h) for h in heights])
        rows=[]
        for i,t in enumerate(times):
            angles=(poses[i,k,1]+np.arange(3)*120)%360-180
            chosen=np.flatnonzero(np.abs(angles)<=15)
            if not len(chosen):continue
            surfaces=[]
            for b,(ref,tri) in enumerate(refs):
                tr=transforms[i,k,b]
                pts=np.einsum('sij,snj->sni',tr[:,:,:3],ref.reshape(19,-1,3))+tr[:,:,3][:,None]
                surfaces.append((pts.reshape(-1,3),tri))
            tr=towers[i,k];tw=twref.copy()
            tw[rings]=np.einsum('sij,snj->sni',tr[:,:,:3],twref[rings])+tr[:,:,3][:,None]
            nr=nacelles[i,k,:,:3];origin=nr@np.array(calibration.origin_m)+nacelles[i,k,:,3]
            for b in chosen:
                truth,_=horizontal_clearance(tip_reference(surfaces[b][0],19),tw,twtri)
                row=dict(time_s=float(t),blade_id=int(b+1),angle_deg=float(angles[b]),expected=True,
                    passage_id=f'b{b+1}-p{round((float(poses[i,k,1])+b*120-180)/360)}',truth_m=truth,
                    yaw_deg=float(poses[i,k,0]),tower_top_displacement_m=float(np.linalg.norm(nr@np.array([0,0,87.6])+nacelles[i,k,:,3]-[0,0,87.6])),beams={})
                for beam,direction in enumerate(calibration.directions(),1):
                    direction=nr@direction;candidates=[]
                    for other,(p,tri) in enumerate(surfaces,1):
                        hit=first_hit(origin,direction,p,tri)
                        if hit:candidates.append((hit[0],other,hit))
                    hit=background_hit(origin,direction,tw,twtri)
                    if hit:candidates.append((hit[0],hit[2],hit))
                    nearest=min(candidates,key=lambda h:h[0]) if candidates else None
                    valid=bool(nearest and nearest[1]==b+1 and calibration.min_range_m<=nearest[0]<=calibration.max_range_m)
                    distance=nearest[0] if nearest else None
                    est=simplified_estimate(distance,valid,calibration.angles_deg[beam-1],calibration.y_lidar_m,calibration.r_tip_m)
                    row['beams'][f'B{beam}']=dict(valid=valid,slant_range_m=distance,estimate_m=est,error_m=est-truth if valid else None)
                rows.append(row)
        old=[r for r in rows if abs(r['angle_deg'])<=3]
        old_hits={r['passage_id'] for r in old if r['beams']['B2']['valid']}
        wide_hits={r['passage_id'] for r in rows if r['beams']['B2']['valid']}
        lows=[min((r for r in rows if r['passage_id']==p),key=lambda r:r['truth_m']) for p in sorted({r['passage_id'] for r in rows})]
        result[tid]=dict(old_window=metrics(old),extended_window=metrics(rows),
            extra_passages_with_hit=sorted(wide_hits-old_hits),
            by_blade={str(b):metrics([r for r in rows if r['blade_id']==b]) for b in (1,2,3)},
            lowest_truth_frames=[dict(time_s=r['time_s'],blade_id=r['blade_id'],truth_m=r['truth_m'],angle_deg=r['angle_deg'],in_old_window=abs(r['angle_deg'])<=3,B2_valid=r['beams']['B2']['valid']) for r in lows])
        all_rows[tid]=rows
        print(tid, result[tid]['old_window'],result[tid]['extended_window'],flush=True)
    output.mkdir(parents=True,exist_ok=True)
    (output/'window-summary.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    (output/'window-rows.json').write_text(json.dumps(all_rows,separators=(',',':'),allow_nan=False))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('package',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();audit(a.package,a.output)
