"""Prepare/run deterministic prescribed-inflow gust preview (visual inspection only)."""
from pathlib import Path
import argparse, hashlib, json, math, os, subprocess
from run_physics import prepare, replace
p=argparse.ArgumentParser(); p.add_argument('--name',default='down-gust-v2-14ms'); p.add_argument('--mean-wind',type=float,default=14.0); p.add_argument('--ti',type=float,default=.18); p.add_argument('--gust-increment',type=float,default=5.0); p.add_argument('--duration',type=float,default=40.0); p.add_argument('--run',action='store_true'); a=p.parse_args()
if not (a.mean_wind>0 and 0<a.ti<1 and 0<a.gust_increment<=6): p.error('invalid wind parameters')
run=prepare(a.name,a.mean_wind,duration=a.duration,fps=80); farm=run/'FarmInputs'
knots=[(0,a.mean_wind),(18,a.mean_wind),(20,a.mean_wind+a.gust_increment),(24,a.mean_wind+a.gust_increment),(28,a.mean_wind),(30,a.mean_wind+a.gust_increment),(34,a.mean_wind+a.gust_increment),(36,a.mean_wind),(a.duration,a.mean_wind)]
rows=[]
for i in range(round(a.duration*10)+1):
 t=i/10; j=min(next((j for j in range(len(knots)-1) if knots[j][0]<=t<=knots[j+1][0]),len(knots)-2),len(knots)-2); x0,u=knots[j]; x1,v=knots[j+1]; gust=u+(v-u)*(.5-.5*math.cos(math.pi*(t-x0)/(x1-x0))) if x1>x0 else u; turb=a.mean_wind*a.ti*(math.sin(2*math.pi*t/5.7)+.55*math.sin(2*math.pi*t/2.9+.8))/math.sqrt(1+.55**2); rows.append(f'{t:.3f} {max(.1,gust+turb):.8f} 0 0 0 0.2 0 0')
(farm/'gust.wnd').write_text('! Time Speed Direction Vertical HShear PowerShear VShear Gust\n'+'\n'.join(rows)+'\n'); replace(farm/'InflowWind.dat',{'WindType':2,'Filename_Uni':'"gust.wnd"'}); replace(farm/'Case.fstf',{'DT_Low':.5})
speeds=[float(r.split()[1]) for r in rows]; cfg=json.loads((run/'run_config.json').read_text()); cfg['wind_profile']={'kind':'deterministic_prescribed_surrogate','mean_wind_mps':a.mean_wind,'target_ti':a.ti,'gust_increment_mps':a.gust_increment,'gust_peak_target_mps':a.mean_wind+a.gust_increment,'gust_duration_s':[4,4],'knots_s_mps':knots,'transition':'half cosine sampled at 0.1 s','shear_exponent':.2,'realized_speed_range_mps':[min(speeds),max(speeds)],'purpose':'single-turbine Down-view flexibility preview','evidence_boundary':'prescribed deterministic inflow; not stochastic turbulence, field data, controller training, or MAPPO validation'}; cfg['inputs']=[{'path':str(x.relative_to(run)),'sha256':hashlib.sha256(x.read_bytes()).hexdigest()} for x in sorted(run.rglob('*')) if x.is_file() and x.name!='run_config.json']; (run/'run_config.json').write_text(json.dumps(cfg,indent=2))
exe=Path('/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm'); status={'status':'PREPARED_ONLY','exit_code':None,'executable':str(exe),'sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),'note':'FAST.Farm execution skipped; deterministic preview input only'}
if a.run:
 with (run/'solver.log').open('w') as log: r=subprocess.run([str(exe),'Case.fstf'],cwd=farm,env={**os.environ,'OMP_NUM_THREADS':'2'},stdout=log,stderr=subprocess.STDOUT)
 status.update(status='COMPLETE' if r.returncode==0 else 'FAILED',exit_code=r.returncode)
 if r.returncode: raise SystemExit(r.returncode)
(run/'exit_status.json').write_text(json.dumps(status,indent=2)); print(run,flush=True)
