"""Numerical evidence: analytic geometry, independent mesh/time refinement comparisons."""
from pathlib import Path
import json,sys,math
import numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from wfrl.lidar.physics import *

def analytic_evidence():
    tip=np.array([3.,4.,0.]);distance,wall=truth_clearance(tip)
    errors=[]
    # Regular polygon distance converges to independent analytic circular wall.
    for n in [32,64,128,256]:
        theta=np.linspace(0,2*np.pi,n,endpoint=False);p=np.stack([3*np.cos(theta),3*np.sin(theta)],axis=1);q=np.roll(p,-1,axis=0);v=q-p
        u=np.clip(np.einsum('ij,ij->i',tip[:2]-p,v)/np.einsum('ij,ij->i',v,v),0,1)
        nearest=p+u[:,None]*v;d=float(np.linalg.norm(nearest-tip[:2],axis=1).min());errors.append(dict(vertices=n,distance_m=d,error_m=abs(d-2)))
    assert abs(distance-2)<1e-12 and abs(np.linalg.norm(tip-wall)-2)<1e-12
    assert errors[-1]['error_m']<errors[0]['error_m']
    # Rigid vertical plane at x=-8; sensor(-2,0,10); theta30 -> slant12 -> clearance5.
    points=np.array([[-8,-20,-20],[-8,20,-20],[-8,20,20],[-8,-20,20.]])
    hit=first_hit([-2,0,10],[-.5,0,-math.sqrt(.75)],points,np.array([[0,1,2],[0,2,3]]))
    estimate=simplified_estimate(hit[0],True,30,2,3);assert abs(estimate-5)<1e-12
    return dict(analytic_truth_error_m=abs(distance-2),rigid_formula_error_m=abs(estimate-5),circle_polygon_convergence=errors)

def compare(base,refined,kind):
    base=Path(base);refined=Path(refined);bc=json.loads((base/'run_config.json').read_text());rc=json.loads((refined/'run_config.json').read_text())
    measurements=json.loads((base/'processed.json').read_text())['measurements'];fs={b:sorted((refined/'FarmInputs/vtk').glob(f'Case.T1.Blade{b}Surface.*.vtp')) for b in range(1,4)}
    differences=[];errors=[];ranges=[]
    for r in measurements:
        i=round(r['time_s']*rc['fps']);pts,tris=read_surface(fs[r['blade_id']][i]);tip=tip_reference(pts,37 if rc['span_refined'] else 19);truth,_=truth_clearance(tip)
        differences.append(abs(truth-r['truth_m']))
        hit=first_hit(Calibration().origin_m,Calibration().directions()[1],pts,tris)
        if hit and r['beams']['B2']['valid']:ranges.append(abs(hit[0]-r['beams']['B2']['slant_range_m']))
    return dict(kind=kind,base_run=base.name,refined_run=refined.name,paired_samples=len(differences),max_truth_difference_m=max(differences),mean_truth_difference_m=float(np.mean(differences)),max_paired_slant_difference_m=max(ranges) if ranges else None,baseline_dt_s=bc['dt_s'],refined_dt_s=rc['dt_s'],baseline_fps=bc['fps'],refined_fps=rc['fps'],baseline_span_sections=37 if bc['span_refined'] else 19,refined_span_sections=37 if rc['span_refined'] else 19)

def main():
    import argparse
    from wfrl.lidar.sampling import compare_grids
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base',type=Path);parser.add_argument('spatial',type=Path);parser.add_argument('temporal',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    paths=[args.base,args.spatial,args.temporal]
    configs=[json.loads((p/'run_config.json').read_text()) for p in paths]
    for p in paths:
        if json.loads((p/'exit_status.json').read_text())['exit_code']!=0 or 'FAST.Farm terminated normally.' not in (p/'solver.log').read_text():raise ValueError('validation solver incomplete: '+str(p))
    if any(len({c[k] for c in configs})!=1 for k in ['wind_mps','duration_s','startup_discard_s']):raise ValueError('incompatible physical cases')
    b,space,time=configs
    if b['span_refined'] or not space['span_refined'] or not time['span_refined']:raise ValueError('requires 19->37 sections then same37 temporal refinement')
    if b['dt_s']!=space['dt_s'] or b['fps']!=space['fps']:raise ValueError('spatial comparison changes temporal grid')
    if time['dt_s']>=space['dt_s'] or time['fps']<=space['fps']:raise ValueError('temporal grid is not finer')
    spatial_data=json.loads((args.spatial/'processed.json').read_text());temporal_data=json.loads((args.temporal/'processed.json').read_text())
    evidence=dict(run_id=args.base.name,analytic=analytic_evidence(),spatial=compare(args.base,args.spatial,'span_only_19_to_37'),temporal=compare(args.spatial,args.temporal,'integration_and_output_timestep_halved'))
    evidence['sampling']=compare_grids(spatial_data,temporal_data,space['fps'],time['fps'])
    evidence['sampling'].update(base_run=str(args.spatial),refined_run=str(args.temporal))
    evidence['truth_numerical_error_m']=max(evidence['spatial']['max_truth_difference_m'],evidence['temporal']['max_truth_difference_m'],1e-5)
    evidence['interpretation']='Empirical two-level differences, not a rigorous uncertainty bound. No field accuracy or continuous-time collision claim.'
    args.output.parent.mkdir(exist_ok=True,parents=True);args.output.write_text(json.dumps(evidence,indent=2,allow_nan=False))
    print(args.output)

if __name__=='__main__':main()
