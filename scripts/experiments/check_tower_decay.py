"""Measure unloaded tower frequencies with standalone OpenFAST, without MPI coupling."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess

import numpy as np
from scipy.signal import find_peaks

from scripts.experiments.audit_fastfarm_physics import prepare_diagnostics, read_native


def decay_frequency(time, displacement):
    """Average positive-peak spacing over 10--100 s of the free response."""
    mask = (time >= 10.) & (time <= 100.)
    t, v = time[mask], displacement[mask]
    if len(t) < 3:
        raise ValueError('Free decay must cover the 10--100 s analysis window')
    dt = float(np.median(np.diff(t)))
    peaks, _ = find_peaks(v, distance=round(2./dt), prominence=.001)
    if len(peaks) < 5:
        raise ValueError('Insufficient tower decay peaks')
    return dict(frequency_hz=float(1/np.diff(t[peaks]).mean()), peaks=len(peaks),
                first_peak_s=float(t[peaks[0]]), last_peak_s=float(t[peaks[-1]]))


def main():
    from wfcrl.simul_utils import FASTInputFile, create_ff_case
    from wfrl.solver_outputs import prepare_case
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='new scratch directory')
    parser.add_argument('--openfast', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=630)
    parser.add_argument('--timeout', type=float, default=180.)
    args = parser.parse_args()
    if args.seconds < 120 or args.seconds % 3 or not np.isfinite(args.timeout) or args.timeout <= 0:
        parser.error('requires seconds >= 120 divisible by 3, and finite positive timeout')
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    farm = Path(create_ff_case(dict(num_turbines=1, xcoords=[0.], ycoords=[0.],
        max_iter=args.seconds//3, dt=3, speed=8., wind_time_series=None,
        physics={'tower_flex': True}), str(root)))
    prepare_case(farm, {'tower_flex': True}, tower_outputs=True)
    prepare_diagnostics(farm, uniform_wind=True, output_dt=.025)
    fst_path = farm.with_name('FFTest_WT1.fst')
    fst = FASTInputFile(str(fst_path))
    for key, value in dict(CompAero=0, CompServo=0, TMax=args.seconds).items():
        fst[key] = value
    fst.write(str(fst_path))
    ed_path = fst_path.parent / fst['EDFile'].strip('"')
    ed = FASTInputFile(str(ed_path))
    for key, value in dict(GenDOF=False, DrTrDOF=False, RotSpeed=0., TTDspFA=.1, TTDspSS=.1).items():
        ed[key] = value
    ed.write(str(ed_path))
    farm.with_suffix('.physics.json').unlink(missing_ok=True)
    hashes = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in root.rglob('*') if p.is_file()}
    log = root / 'openfast.log'
    with log.open('wb') as stream:
        child = subprocess.Popen([str(args.openfast.resolve()), str(fst_path)], cwd=root,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
            env={**os.environ, 'OMP_NUM_THREADS': '1'})
        try:
            code = child.wait(timeout=args.timeout)
        except BaseException:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5.)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
            raise
    if code or 'OpenFAST terminated normally.' not in log.read_text(encoding='utf-8'):
        raise RuntimeError(f'OpenFAST did not complete: {log}')
    series, _ = read_native(fst_path.with_suffix('.out'), args.seconds)
    result = dict(seconds=args.seconds, inputs_sha256=hashes,
                  solver_sha256=hashlib.sha256(args.openfast.read_bytes()).hexdigest(),
                  completion='native process exited; normal termination marker confirmed',
                  frequencies={name: decay_frequency(series['Time'], series[name])
                               for name in ('TTDspFA', 'TTDspSS')})
    (root/'decay.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
