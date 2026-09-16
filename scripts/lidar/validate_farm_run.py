"""Audit native outputs on the displayed interval; preserve original probe logs."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from scripts.experiments.calib_blade_flex import read_fast_out


def validate(probe,solver_log,output,start=117.,duration=60.,legacy_yaw_radians=False):
    probe,solver_log,output=Path(probe),Path(solver_log),Path(output)
    if output.exists():raise FileExistsError(output)
    log=solver_log.read_text(errors='replace')
    if 'FAST.Farm terminated normally.' not in log:raise ValueError('Solver did not finish')
    report=json.loads(probe.read_text());farm=Path(report['case_dir'])/'FarmInputs'
    report['original_probe_sha256']=hashlib.sha256(probe.read_bytes()).hexdigest()
    report['solver_log_sha256']=hashlib.sha256(solver_log.read_bytes()).hexdigest()
    report['source_outputs']={};report['physical_checks']=[]
    for tid in ('T1','T2','T3'):
        path=farm/f'Case.{tid}.out';c=read_fast_out(path)
        report['source_outputs'][tid]=hashlib.sha256(path.read_bytes()).hexdigest()
        selected=(c['Time']>=start)&(c['Time']<=start+duration)
        if not selected.any() or c['Time'][-1]<start+duration:raise ValueError('Incomplete display interval')
        rpm,pitch=c['RotSpeed'][selected],c['BlPitch1'][selected]
        # The pitch output can overshoot a bounded command during OpenFAST's
        # coupling/extrapolation. Bound tolerance by one physical actuator step,
        # 8 deg/s * 0.00625 s, rather than an unexplained 0.01 degree constant.
        pitch_tolerance=8*.00625
        row=dict(turbine_id=tid,rpm_range=[float(rpm.min()),float(rpm.max())],
            pitch_deg_range=[float(pitch.min()),float(pitch.max())],
            yaw_deg_range=[float(c['YawPzn'][selected].min()),float(c['YawPzn'][selected].max())],
            pitch_tolerance_deg=pitch_tolerance,finite=all(np.isfinite(v).all() for v in c.values()))
        row['passed']=bool(row['finite'] and rpm.min()>0 and rpm.max()<=12.2
                           and pitch.min()>=-pitch_tolerance and pitch.max()<=90+pitch_tolerance)
        report['physical_checks'].append(row)
    if legacy_yaw_radians:
        for row in report['records']:
            raw=row.pop('measured_yaw_deg',None)
            row['measured_yaw_rad']=raw
            row['measured_yaw_deg']=None if raw is None else np.degrees(raw).tolist()
        report['trace_unit_correction']='Original probe mislabeled raw MPI yaw radians as degrees; original file preserved'
    report['segment']=dict(start_s=start,end_s=start+duration)
    report['status']='CONTROL_PROBE_COMPLETE' if all(c['passed'] for c in report['physical_checks']) else 'FAILED'
    report.pop('error',None)
    output.write_text(json.dumps(report,indent=2,allow_nan=False))
    if report['status']=='FAILED':raise ValueError('Native interval audit failed')
    print(json.dumps(report['physical_checks']),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('probe');p.add_argument('solver_log');p.add_argument('output')
    p.add_argument('--legacy-yaw-radians',action='store_true')
    a=p.parse_args();validate(a.probe,a.solver_log,a.output,legacy_yaw_radians=a.legacy_yaw_radians)
