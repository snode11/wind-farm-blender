"""Diagnostic only: static-angle candidates, first-half design and held-out validation."""
from pathlib import Path
import sys,json,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from wfrl.lidar.physics import read_surface,first_hit,Calibration,simplified_estimate
from wfrl.lidar.moving_tower import tip_surface_clearance
from wfrl.blender_bridge.blade_flex_export import fit_sections
from scripts.experiments.calib_blade_flex import read_fast_out
from scripts.lidar.audit_prebend_baseline import metrics
r=json.loads((ROOT/'evidence/prebend-20260918/farm-dt-quarter/probe.json').read_text());farm=ROOT/r['case_dir']/'FarmInputs';cal=Calibration();report={};allrows={}
angles_test=np.arange(4.,11.01,.25)
for k,tid in enumerate(('T1','T2','T3')):
 c=read_fast_out(farm/f'Case.{tid}.out');az=np.degrees(np.unwrap(np.radians(c['Azimuth'])));angles=(az[:,None]+np.arange(3)*120)%360-180
 candidates=np.argwhere((abs(angles)<=3)&((c['Time']>=117)&(c['Time']<=177))[:,None]);rows={str(a):[] for a in angles_test}
 nref,_=read_surface(farm/'vtk'/f'Case.{tid}.NacelleSurface.00000.vtp');offset=np.array([k*504,0,0])
 for i,b in candidates:
  nc,_=read_surface(farm/'vtk'/f'Case.{tid}.NacelleSurface.{i:05d}.vtp');nr,nd,_=fit_sections((nref-offset)[None],(nc-offset)[None])
  origin=nr[0]@np.asarray(cal.origin_m)+nd[0]+offset
  points,tri=read_surface(farm/'vtk'/f'Case.{tid}.Blade{b+1}Surface.{i:05d}.vtp');tw,tt=read_surface(farm/'vtk'/f'Case.{tid}.TowerSurface.{i:05d}.vtp')
  truth,_,_=tip_surface_clearance(points,tw,tt)
  for angle in angles_test:
   rad=np.radians(angle);direction=nr[0]@[-np.sin(rad),0,-np.cos(rad)]
   hit=first_hit(origin,direction,points,tri);valid=bool(hit and cal.min_range_m<=hit[0]<=cal.max_range_m)
   estimate=simplified_estimate(hit[0] if hit else None,valid,float(angle),2.,2.67)
   rows[str(angle)].append(dict(time_s=float(c['Time'][i]),blade_id=int(b+1),expected=True,truth_m=truth,
      passage_id=f'b{b}-p{round((az[i]+b*120-180)/360)}',beams={'B2':dict(valid=valid,estimate_m=estimate)}))
 report[tid]={a:{split:metrics([r for r in rr if (r['time_s']<147 if split=='design' else r['time_s']>=147)]) for split in ('design','validation')} for a,rr in rows.items()}
 allrows[tid]=rows
 (ROOT/'evidence/prebend-20260918/calibration-candidates.json').write_text(json.dumps(report,indent=2))
 (ROOT/'evidence/prebend-20260918/calibration-candidate-rows.json').write_text(json.dumps(allrows,separators=(',',':')))
 def passes(m):return m['p95_m'] is not None and m['p95_m']<=.5 and m['max_abs_m']<=1 and m['valid_fraction']>=.25 and m['missed_passages']/m['passages']<=.1
 print(tid,'design passes',[a for a,v in report[tid].items() if passes(v['design'])], 'validation passes',[a for a,v in report[tid].items() if passes(v['validation'])],flush=True)
