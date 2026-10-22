"""Choose a fixed mount by same-blade S2/S3 coverage, never by reconstruction error.

Search uses complete source surfaces restricted by exact ray-plane bounds.
The chosen candidate must subsequently pass the unfiltered full-time exporter.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from wfrl.camera_video.data import SourceGeometry, blade_root_frame
from wfrl.lidar.dual_beam import validate_inner_origins
from wfrl.lidar.physics import first_hit
from wfrl.lidar.dual_beam_replay import digest


def plane_triangles(points, triangles, y):
    """Exact broad phase for d.y=0, with tolerance matching triangle solver."""
    ys=points[triangles,1]
    return triangles[(ys.min(1)<=y+1e-8)&(ys.max(1)>=y-1e-8)]


def mount_hits(source, index, candidates, angles=(4.,6.), min_separation=.25):
    """Nearest opaque surface for both rays; geometry in current nacelle frame."""
    nacelle=source.nacelles[index]
    rotation=nacelle[:,:3];translation=nacelle[:,3]
    tr=source.transforms[index]
    blades=np.einsum('bsij,bsvj->bsvi',tr[...,:3],source.blade_reference)+tr[:,:,None,:,3]
    blades=(blades.reshape(3,-1,3)-translation)@rotation
    tt=source.tower_transforms[index,source.tower_station]
    tower=np.einsum('nij,nj->ni',tt[...,:3],source.tower_reference)+tt[...,3]
    tower=(tower-translation)@rotation
    filtered={}
    for y in {c[1] for c in candidates}:
        filtered[y]=[(p,plane_triangles(p,source.blade_triangles,y),b) for b,p in enumerate(blades,1)]
        filtered[y].append((tower,plane_triangles(tower,source.tower_triangles,y),0))
    directions=[np.array([-math.sin(math.radians(a)),0.,-math.cos(math.radians(a))]) for a in angles]
    results=[]
    for candidate in candidates:
        o=np.asarray(candidate,float);pair=[]
        for d in directions:
            # Ground remains z_world=0 after the nacelle coordinate change.
            oz=(rotation@o+translation)[2];dz=(rotation@d)[2]
            best=(-oz/dz,0) if dz<0 and oz>=0 else (math.inf,0)
            for points,triangles,blade in filtered[candidate[1]]:
                if len(triangles):
                    hit=first_hit(o,d,points,triangles)
                    if hit and hit[0]<best[0]:best=(hit[0],blade)
            pair.append(best)
        a,b=pair
        valid=a[1]>0 and a[1]==b[1] and 5<=a[0]<=100 and 5<=b[0]<=100
        separation=float(np.linalg.norm(a[0]*directions[0]-b[0]*directions[1])) if valid else None
        valid=bool(valid and separation>=min_separation)
        results.append((a[1] if valid else 0,separation if valid else None))
    return results


def evaluate(source_path,candidates,angles=(2.,4.,6.)):
    scores=[dict(origin_m=list(c),by_turbine={}) for c in candidates]
    hashes=None
    for tid in ('T1','T2','T3'):
        s=SourceGeometry(source_path,tid);hashes=s.source_hashes
        per=[defaultdict(list) for c in candidates]
        processed=0
        for i,t in enumerate(s.times):
            phase=s.poses[i,1]+np.arange(3)*120
            delta=phase%360-180
            expected=np.flatnonzero(abs(delta)<=15)
            if not len(expected):continue
            blade=int(expected[0])+1;cycle=int(np.floor(phase[blade-1]/360))
            key=f'b{blade}-cycle{cycle}'
            hits=mount_hits(s,i,candidates,angles=angles[1:])
            for rows,(hit,separation) in zip(per,hits):rows[key].append((float(t),hit==blade,separation))
            processed+=1
            if processed%100==0:print(tid,processed,flush=True)
        for score,passages in zip(scores,per):
            out=[]
            for key,rows in passages.items():
                current=0;best=0
                for _,ok,_ in rows:
                    current=current+1 if ok else 0;best=max(best,current)
                out.append(dict(id=key,samples=len(rows),paired_samples=sum(r[1] for r in rows),max_run=best,
                                first_s=rows[0][0],last_s=rows[-1][0],
                                boundary_truncated=rows[0][0]==float(s.times[0]) or rows[-1][0]==float(s.times[-1])))
            full=[p for p in out if not p['boundary_truncated']]
            score['by_turbine'][tid]=dict(expected_samples=sum(p['samples'] for p in out),
                paired_samples=sum(p['paired_samples'] for p in out),passages=out,
                complete_passages=len(full),covered_passages=sum(p['paired_samples']>0 for p in full),
                min_run=min((p['max_run'] for p in full),default=0))
    for score in scores:
        groups=list(score['by_turbine'].values())
        score.update(complete_passages=sum(g['complete_passages'] for g in groups),
                     covered_passages=sum(g['covered_passages'] for g in groups),
                     paired_samples=sum(g['paired_samples'] for g in groups),
                     min_run=min(g['min_run'] for g in groups))
        x,y,z=score['origin_m']
        radial_x=-x+(z-27.2)*math.tan(math.radians(angles[0]))
        score['nominal_s1_gap_m']=math.hypot(radial_x,y)-2.67
    return sorted(scores,key=lambda s:(s['covered_passages'],s['min_run'],s['paired_samples'],-abs(s['nominal_s1_gap_m']-4.5)),reverse=True),hashes


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'blender_frontend/wfrl_blender/assets/mappo')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--x',type=float,nargs='+',default=[-2.2,-2.5,-2.8])
    p.add_argument('--y',type=float,nargs='+',default=[0.])
    p.add_argument('--z',type=float,nargs='+',default=[87.54756])
    p.add_argument('--angles',type=float,nargs=3,default=[2.,4.,6.],
                   help='Explicit diagnostic angles; changes require separate calibration decision.')
    args=p.parse_args();candidates=[(x,y,z) for x in args.x for y in args.y for z in args.z]
    source=SourceGeometry(args.source, 'T1')
    shell=json.loads((ROOT/'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json').read_text())['shell']
    center_x=shell['nacelle_x_bias']*source.scalars['OverHang']
    hub,_=blade_root_frame(source.scalars,[0]*6,1,np.column_stack((np.eye(3),np.zeros(3))))
    try:
        validate_inner_origins(candidates,hub,source.scalars['ShftTilt'],
                               [center_x-shell['length']/2,center_x+shell['length']/2],shell['width']/2)
    except ValueError as exc:
        p.error(str(exc))
    if not 0<args.angles[0]<args.angles[1]<args.angles[2]<90:
        p.error('--angles must be strictly increasing between 0 and 90')
    implementation_hash=digest(__file__)
    scores,hashes=evaluate(args.source,candidates,args.angles)
    payload=dict(schema='wfrl.dual-beam-mount-search.v1',source_hashes=hashes,
                 permitted_region='INNER_NACELLE',
                 selection_basis='maximize complete passages with same-blade pair, then minimum consecutive source samples, then total samples; no truth error used',
                 predefined_target='all complete passages on all three turbines with >=3 consecutive paired source samples',
                 expected_window_half_angle_deg=15,angles_deg=args.angles,sample_kind='source',
                 scores=scores,implementation_sha256=implementation_hash)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:json.dump(payload,f,ensure_ascii=False,indent=2,allow_nan=False)
    for s in scores[:10]:print({k:v for k,v in s.items() if k!='by_turbine'},flush=True)


if __name__=='__main__':main()
