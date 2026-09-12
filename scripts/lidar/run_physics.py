"""Run traceable prescribed-speed FAST.Farm cases; never modify source cases.
Usage: python scripts/lidar/run_physics.py --name normal --wind 8
"""
from pathlib import Path
import argparse, json, re, shutil, subprocess, os, hashlib, math
ROOT=Path(__file__).resolve().parents[2]
DEFAULT_SOURCE=ROOT/'__simul__/fastfarm/FastFarm__604s__3T_1789056088.7327092'
def replace(p, values):
    text=p.read_text()
    for key,value in values.items():
        text,n=re.subn(r'^\s*\S+\s+('+re.escape(key)+r')(?=\s)',lambda m:f'{value}    {m[1]}',text,flags=re.M)
        if n!=1: raise ValueError(f'{p}: {key}: {n} matches')
    p.write_text(text)
def prepare(name,wind,duration=36,fps=80,dt=.00625,refine=False,source_case=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name):
        raise ValueError('name must be a single run directory name')
    if not all(math.isfinite(v) and v > 0 for v in (wind,duration,fps,dt)):
        raise ValueError('wind, duration, fps and dt must be finite and positive')
    if duration <= 18:
        raise ValueError('duration must exceed the 18 second startup discard window')
    steps_per_frame=1/(fps*dt)
    if steps_per_frame < 1 or not math.isclose(steps_per_frame,round(steps_per_frame),abs_tol=1e-8):
        raise ValueError('output interval 1/fps must be an integer multiple of DT')
    source=Path(source_case or DEFAULT_SOURCE).resolve()
    if not (source/'FarmInputs/Case.fstf').is_file():
        raise ValueError(f'missing source case: {source}; provide --source-case')
    dest=ROOT/'results/lidar/raw'/name
    if dest.exists(): raise FileExistsError(dest)
    shutil.copytree(source,dest,ignore=shutil.ignore_patterns('*.out','*.outb','vtk','*.log','*.bts','*.dll','*.ech','*.sum'))
    farm=dest/'FarmInputs';case=farm/'Case.fstf'
    replace(case,{'TMax':duration,'UseSC':'False','NumTurbines':1,'NX_Low':25,'NY_Low':20,'NZ_Low':20,'X0_Low':-86,'Y0_Low':-90,'Z0_Low':0,'dX_Low':9.6,'dY_Low':10,'dZ_Low':10})
    case.write_text('\n'.join(l for l in case.read_text().splitlines() if not re.match(r'^(504|1008)\s',l))+'\n')
    replace(farm/'FFTest_WT1.fst',{'TMax':duration,'CompServo':0,'DT':dt,'WrVTK':2,'VTK_type':1,'VTK_fps':fps,'DT_Out':1/fps,'OutFmt':'"ES16.8E3"'})
    replace(farm/'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat',{'GenDOF':'False','DrTrDOF':'False','YawDOF':'False','RotSpeed':9.0,'BlPitch(1)':0.,'BlPitch(2)':0.,'BlPitch(3)':0.,'FlapDOF1':'True','FlapDOF2':'True','EdgeDOF':'True','TwFADOF1':'False','TwFADOF2':'False','TwSSDOF1':'False','TwSSDOF2':'False'})
    replace(farm/'InflowWind.dat',{'WindType':1,'HWindSpeed':wind})
    if refine:
        blade=dest/'5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat'
        lines=blade.read_text().splitlines(); count=int(lines[3].split()[0]);rows=[list(map(float,l.split())) for l in lines[6:6+count]];new=[]
        for i,row in enumerate(rows):
            if i:
                mid=[(a+b)/2 for a,b in zip(rows[i-1],row)];mid[6]=row[6];new.append(mid)
            new.append(row)
        lines[3]=f'{len(new)} NumBlNds - span-refined aerodynamic mesh'
        blade.write_text('\n'.join(lines[:6]+[' '.join(str(int(x)) if j==6 else f'{x:.9e}' for j,x in enumerate(r)) for r in new])+'\n')
    inputs=[{'path':str(p.relative_to(dest)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(dest.rglob('*')) if p.is_file()]
    config=dict(name=name,source_case=str(source),wind_mps=wind,duration_s=duration,fps=fps,dt_s=dt,span_refined=refine,controller='prescribed constant 9 rpm; fixed pitch 0 deg; no ServoDyn; no policy checkpoint',blade_dofs=['FlapDOF1','FlapDOF2','EdgeDOF'],tower='rigid',startup_discard_s=18,inputs=inputs)
    (dest/'run_config.json').write_text(json.dumps(config,indent=2))
    return dest
def main():
    p=argparse.ArgumentParser();p.add_argument('--name',required=True);p.add_argument('--wind',type=float,required=True);p.add_argument('--duration',type=float,default=36);p.add_argument('--fps',type=int,default=80);p.add_argument('--dt',type=float,default=.00625);p.add_argument('--refine',action='store_true');p.add_argument('--source-case',type=Path,default=DEFAULT_SOURCE);p.add_argument('--executable',default='/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm');a=p.parse_args()
    dest=prepare(a.name,a.wind,a.duration,a.fps,a.dt,a.refine,a.source_case)
    status={'exit_code':None,'executable':a.executable,'status':'FAILED'}
    try:
        executable=Path(shutil.which(a.executable) or a.executable).resolve()
        status.update(executable=str(executable),sha256=hashlib.sha256(executable.read_bytes()).hexdigest())
        with (dest/'solver.log').open('w') as log:
            r=subprocess.run([str(executable),'Case.fstf'],cwd=dest/'FarmInputs',env={**os.environ,'OMP_NUM_THREADS':'2'},stdout=log,stderr=subprocess.STDOUT)
        status.update(exit_code=r.returncode,status='COMPLETE' if r.returncode==0 else 'FAILED')
    except Exception as exc:
        status['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        (dest/'exit_status.json').write_text(json.dumps(status,indent=2))
    if r.returncode: raise SystemExit(r.returncode)
    print(dest)
if __name__=='__main__': main()
