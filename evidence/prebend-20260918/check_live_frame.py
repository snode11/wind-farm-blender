from pathlib import Path
import json,sys,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'blender_frontend')]
from wfrl.lidar.physics import read_surface
from wfrl.blender_bridge.blade_flex_export import fit_sections
from wfrl_blender.deflection import rigid_frame
from wfrl_blender.turbine_geometry import geometry_data
farm=ROOT/'__simul__/fastfarm/FastFarm__208s__3T_1789663025.003069/FarmInputs'
rest=ROOT/'results/prebend-20260918/C-rest/FarmInputs/vtk'
def ref(kind):
 p=next(p for p in rest.glob(f'FFTest_WT1.{kind}Surface.*.vtp') if int(p.stem.rsplit('.',1)[1])==0);return read_surface(p)[0]
scalars=geometry_data()['scalars'];tip=np.array([-1.,0,63]);results={}
for k,tid in enumerate(('T1','T2','T3')):
 lines=(farm/f'Case.{tid}.out').read_text().splitlines();names=lines[6].split()
 row=next(line for line in lines[8:] if len(line.split())==len(names) and float(line.split()[0])==120.)
 c=dict(zip(names,map(float,row.split())));pose=[c[key] for key in ('YawPzn','Azimuth','RotSpeed','BlPitch1','BlPitch2','BlPitch3')]
 offset=np.array([k*504.,0,0]);n=read_surface(farm/'vtk'/f'Case.{tid}.NacelleSurface.04800.vtp')[0]-offset
 nr,nd,_=fit_sections(ref('Nacelle')[None],n[None]);nacelle=np.column_stack((nr[0],nd[0]))
 rows=[]
 for b in (1,2,3):
  points=read_surface(farm/'vtk'/f'Case.{tid}.Blade{b}Surface.04800.vtp')[0]-offset
  r,d,err=fit_sections(ref(f'Blade{b}').reshape(19,-1,3),points.reshape(19,-1,3))
  hub,_,axes=rigid_frame(scalars,[0]*6,b);rpoint=hub+axes@tip
  actual=r[-1]@rpoint+d[-1]
  hub,_,axes=rigid_frame(scalars,pose,b,nacelle);expected=hub+axes@tip
  delta=axes.T@(actual-expected);sim=np.array([c[f'B{b}TipTD{a}r'] for a in 'xyz'])
  rows.append(dict(blade=b,fit_m=err,error_m=(delta-sim).tolist(),pitch_deg=pose[b+2]))
  assert max(abs(delta-sim))<.005,(tid,b,delta,sim)
 results[tid]=rows
(ROOT/'evidence/prebend-20260918/farm-120s-interface.json').write_text(json.dumps(results,indent=2));print(results)
