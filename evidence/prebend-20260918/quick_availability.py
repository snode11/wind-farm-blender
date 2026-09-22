from pathlib import Path
import sys,json,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from wfrl.lidar.physics import read_surface,first_hit,Calibration
from wfrl.blender_bridge.blade_flex_export import fit_sections
from scripts.experiments.calib_blade_flex import read_fast_out
r=json.loads((ROOT/'evidence/prebend-20260918/farm-dt-quarter/probe.json').read_text());farm=ROOT/r['case_dir']/'FarmInputs';cal=Calibration();report={}
for k,tid in enumerate(('T1','T2','T3')):
 c=read_fast_out(farm/f'Case.{tid}.out');az=np.degrees(np.unwrap(np.radians(c['Azimuth'])));angles=(az[:,None]+np.arange(3)*120)%360-180
 candidates=np.argwhere((abs(angles)<=3)&((c['Time']>=117)&(c['Time']<=177))[:,None]);rows=[]
 nref,_=read_surface(farm/'vtk'/f'Case.{tid}.NacelleSurface.00000.vtp')
 for i,b in candidates:
  nc,_=read_surface(farm/'vtk'/f'Case.{tid}.NacelleSurface.{i:05d}.vtp');nr,nd,_=fit_sections((nref-[k*504,0,0])[None],(nc-[k*504,0,0])[None])
  origin=nr[0]@np.asarray(cal.origin_m)+nd[0]+[k*504,0,0];direction=nr[0]@cal.directions()[1]
  points,tri=read_surface(farm/'vtk'/f'Case.{tid}.Blade{b+1}Surface.{i:05d}.vtp')
  hit=first_hit(origin,direction,points,tri)
  rows.append(dict(t=float(c['Time'][i]),blade=int(b+1),angle=float(angles[i,b]),passage=f'{b}-{round((az[i]+b*120-180)/360)}',hit=bool(hit and cal.min_range_m<=hit[0]<=cal.max_range_m)))
 passages={r['passage'] for r in rows};hit={r['passage'] for r in rows if r['hit']}
 report[tid]=dict(expected=len(rows),candidate_hits=sum(r['hit'] for r in rows),passages=len(passages),passages_with_candidate_hit=len(hit),rows=rows,
  boundary='Direct raw target-blade rays; upper bound until all-surface occlusion verification in full export')
 (ROOT/'evidence/prebend-20260918/raw-availability.json').write_text(json.dumps(report,indent=2));print(tid,{k:v for k,v in report[tid].items() if k!='rows'},flush=True)
