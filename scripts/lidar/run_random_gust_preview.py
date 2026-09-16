"""Seeded Kaimal full-field wind plus random smooth gusts for a REVIEW_ONLY clip.

The 14 m/s setting is the background mean; gusts raise the clip mean. TI is
measured on the background hub-height longitudinal velocity, before gusts.
No clipping of turbulent extremes is applied. This is not an IEC load case.
Template: https://openfast.readthedocs.io/en/dev/source/user/turbsim/appendix.html
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import numpy as np
from run_physics import prepare, replace

ROOT = Path(__file__).resolve().parents[2]


def pulses(times, seed, duration, increment_range=(4,6), duration_range=(2,5)):
    rng = np.random.default_rng(seed)
    events = []
    start = 19.0 + rng.uniform(0, 2)
    signal = np.zeros_like(times)
    while start + 5 < duration:
        width = float(rng.uniform(*duration_range))
        amplitude = float(rng.uniform(*increment_range))
        phase = (times-start)/width
        signal += np.where((phase >= 0) & (phase <= 1),
                           amplitude * (1-np.cos(2*np.pi*phase))/2, 0)
        events.append(dict(start_s=float(start), duration_s=width, increment_mps=amplitude))
        start += width + rng.uniform(3, 7)
    return signal, events


def execute(exe, args, cwd, log):
    with log.open('w') as stream:
        result = subprocess.run([str(exe), *args], cwd=cwd,
                                env={**os.environ, 'OMP_NUM_THREADS': '2'},
                                stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{exe.name} failed ({result.returncode}); see {log}')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--name', default='down-random-gust-v3')
    p.add_argument('--seed', type=int, default=20260915)
    p.add_argument('--ti', type=float, default=.18)
    p.add_argument('--duration', type=float, default=40)
    p.add_argument('--mean-wind',type=float,default=14)
    p.add_argument('--gust-min',type=float,default=4)
    p.add_argument('--gust-max',type=float,default=6)
    p.add_argument('--run', action='store_true')
    args = p.parse_args()
    if (not .05 <= args.ti <= .20 or args.duration < 30
            or not 3<=args.mean_wind<=20 or not 0<=args.gust_min<=args.gust_max<=6):
        p.error('Invalid wind mean, TI, gust range or duration')
    mean_wind=args.mean_wind
    run = prepare(args.name, mean_wind, duration=args.duration, fps=80)
    farm = run/'FarmInputs'
    template = ROOT/'scripts/lidar/templates/TurbSim.inp'
    inp = farm/'random.inp'
    inp.write_bytes(template.read_bytes())
    replace(inp, dict(RandSeed1=args.seed, NumGrid_Z=21, NumGrid_Y=23,
                      TimeStep=.05, AnalysisTime=max(120, args.duration+40),
                      UsableTime='"ALL"', HubHt=101, GridHeight=200, GridWidth=220,
                      IECturbc=args.ti*100, URef=mean_wind, RefHt=90,
                      WindProfileType='"PL"', PLExp=.2, ScaleIEC=1))
    turbsim = Path('/opt/anaconda3/envs/wfrl-mac/bin/turbsim')
    execute(turbsim, ['random.inp'], farm, run/'turbsim.log')
    from openfast_toolbox.io import TurbSimFile
    wind = TurbSimFile(str(farm/'random.bts'))
    times = wind['t']
    hub = np.array([np.interp(90, wind['z'], u) for u in wind['u'][0, :, len(wind['y'])//2]])
    # Preserve spatial/temporal correlations while correcting short realization
    # mean and background TI at the actual 90 m turbine hub (grid center=101 m).
    mean = wind['u'][0].mean(axis=0)
    factor = mean_wind*args.ti/hub.std()
    wind['u'][0] = (wind['u'][0]-mean[None])*factor + mean_wind*(wind['z'][None,None,:]/90)**.2
    hub = np.array([np.interp(90, wind['z'], u) for u in wind['u'][0, :, len(wind['y'])//2]])
    # InflowWind starts the non-periodic box half a grid-width upstream of x=0.
    offset = float((wind['y'][-1]-wind['y'][0])/2/wind['uRef'])
    gust, events = pulses(times-offset, args.seed+1, args.duration,(args.gust_min,args.gust_max))
    wind['u'][0] += gust[:,None,None]
    wind.write(str(farm/'random-gust.bts'))
    reread = TurbSimFile(str(farm/'random-gust.bts'))
    quant_error = float(np.max(np.abs(reread['u']-wind['u'])))
    final_hub = np.array([np.interp(90, reread['z'], u) for u in reread['u'][0,:,len(wind['y'])//2]])
    clip = (times-offset >= 18) & (times-offset <= args.duration)
    clip_stats = dict(mean_mps=float(final_hub[clip].mean()),
                      ti_including_gusts=float(final_hub[clip].std()/final_hub[clip].mean()),
                      min_mps=float(final_hub[clip].min()), max_mps=float(final_hub[clip].max()))
    replace(farm/'InflowWind.dat', dict(WindType=3, FileName_BTS='"random-gust.bts"'))
    replace(farm/'Case.fstf', dict(DT_Low=.5, Z0_Low=1))
    lines=(farm/'Case.fstf').read_text().splitlines()
    for i,line in enumerate(lines):
        if '"FFTest_WT1.fst"' in line:
            cells=line.split(); cells[6]='1'; lines[i]='\t'.join(cells)
    (farm/'Case.fstf').write_text('\n'.join(lines)+'\n')
    cfg=json.loads((run/'run_config.json').read_text())
    cfg['wind_profile']=dict(kind='TurbSim_IECKAI_plus_seeded_gusts', mean_wind_mps=mean_wind,
        target_ti=args.ti, seed=args.seed, gust_seed=args.seed+1, events=events,
        gust_increment_range_mps=[args.gust_min,args.gust_max], gust_peak_envelope_mps=[mean_wind+args.gust_min,mean_wind+args.gust_max], gust_duration_range_s=[2,5],
        background_hub_mean_mps=float(hub.mean()), background_hub_ti=float(hub.std()/hub.mean()),
        background_scale_factor=float(factor), bts_quantization_max_mps=quant_error,
        bts_time_offset_at_x0_s=offset, full_field_speed_range_mps=[float(wind['u'][0].min()),float(wind['u'][0].max())],
        clip_hub_statistics=clip_stats, grid_spacing_m=[10,10], evidence_boundary='stochastic Kaimal background plus prescribed seeded coherent pulses; no IEC certification, field measurement or MAPPO; fixed rpm and pitch; gust envelope is not instantaneous cap')
    cfg['inputs']=[dict(path=str(f.relative_to(run)),sha256=hashlib.sha256(f.read_bytes()).hexdigest())
        for f in sorted(run.rglob('*')) if f.is_file() and f.name not in ('run_config.json','turbsim.log')]
    (run/'run_config.json').write_text(json.dumps(cfg,indent=2))
    exe=Path('/opt/anaconda3/envs/wfrl-mac/bin/FAST.Farm')
    status=dict(status='PREPARED_ONLY',exit_code=None,executable=str(exe),sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    try:
        if args.run:
            execute(exe,['Case.fstf'],farm,run/'solver.log')
            status.update(status='COMPLETE',exit_code=0)
    except Exception as exc:
        status.update(status='FAILED',exit_code=1,error=str(exc))
        raise
    finally:
        (run/'exit_status.json').write_text(json.dumps(status,indent=2))
    print(json.dumps(cfg['wind_profile'],indent=2),flush=True)


if __name__=='__main__':
    main()
