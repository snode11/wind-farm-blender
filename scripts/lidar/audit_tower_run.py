"""Audit completed flexible-tower source channels without altering the raw probe."""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from scripts.experiments.calib_blade_flex import read_fast_out


def audit(probe, log, output):
    probe,log,output=map(Path,(probe,log,output))
    report=json.loads(probe.read_text())
    if report['status']!='CONTROL_PROBE_COMPLETE' or report.get('tower')!='flexible':
        raise ValueError('Expected completed flexible tower run')
    if 'FAST.Farm terminated normally.' not in log.read_text():
        raise ValueError('Solver did not terminate normally')
    farm=Path(report['case_dir'])/'FarmInputs'
    report['tower_checks']=[];report['source_outputs']={}
    for tid in ('T1','T2','T3'):
        path=farm/f'Case.{tid}.out';c=read_fast_out(path)
        mask=(c['Time']>=117)&(c['Time']<=177)
        if np.count_nonzero(mask)!=2401: raise ValueError('Incomplete review segment')
        if not all(np.isfinite(v).all() for v in c.values()): raise ValueError('Nonfinite source output')
        displacement=np.column_stack([c[k][mask] for k in ('YawBrTDxt','YawBrTDyt','YawBrTDzt')])
        if np.max(np.linalg.norm(displacement,axis=1))<.001: raise ValueError('No measured tower motion')
        record=dict(turbine_id=tid,displacement_min_m=displacement.min(0).tolist(),
            displacement_max_m=displacement.max(0).tolist(),tower_top_max_displacement_m=float(np.linalg.norm(displacement,axis=1).max()),
            rpm_range=[float(c['RotSpeed'][mask].min()),float(c['RotSpeed'][mask].max())])
        for b in (1,2,3):
            pitch=c[f'BlPitch{b}'][mask]
            if pitch.min()<-.05 or pitch.max()>90.05: raise ValueError('Pitch validation failed')
        report['tower_checks'].append(record)
        report['source_outputs'][tid]=hashlib.sha256(path.read_bytes()).hexdigest()
    for f in farm.glob('*ElastoDyn*.dat'):
        fields={words[1]:words[0] for line in f.read_text().splitlines() if len(words:=line.split())>=2}
        for key in ('TwFADOF1','TwFADOF2','TwSSDOF1','TwSSDOF2'):
            if fields[key].lower()!='true': raise ValueError('Tower mode disabled: '+key)
    report['input_hashes']={str(f.relative_to(farm)):hashlib.sha256(f.read_bytes()).hexdigest()
                            for f in farm.iterdir() if f.is_file() and f.suffix in ('.fst','.fstf','.dat','.bts')}
    report.update(original_probe_sha256=hashlib.sha256(probe.read_bytes()).hexdigest(),
        solver_log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),segment=dict(start_s=117.,end_s=177.))
    output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['tower_checks'],indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('probe');p.add_argument('log');p.add_argument('output');a=p.parse_args()
    audit(a.probe,a.log,a.output)
