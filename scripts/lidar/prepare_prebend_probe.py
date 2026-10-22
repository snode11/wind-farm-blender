"""Prepare isolated matched A/B/C OpenFAST interface probes, never touch A's farm.

The reference quadratic is Jonkman's 2016 NREL 5MW upwind-precurve example.
This is an interface experiment (prescribed 9 rpm, 8 m/s), not the MAPPO run.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

from scripts.lidar.run_physics import replace

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / 'evidence/prebend-20260918/reference'
SOURCE = ROOT / '__simul__/fastfarm/FastFarm__208s__3T_1789560533.018898'


def prepare(destination, variant, duration=6., unloaded=False, dt=.00625):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    farm = destination / 'FarmInputs'
    farm.mkdir()
    shutil.copytree(SOURCE / '5MW_Baseline', destination / '5MW_Baseline',
                    ignore=shutil.ignore_patterns('ServoData'))
    for name in ('FFTest_WT1.fst', 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat', 'InflowWind.dat'):
        shutil.copyfile(SOURCE / 'FarmInputs' / name, farm / name)
    bd = variant in ('B', 'C')
    prebend = variant == 'C'
    fst = farm / 'FFTest_WT1.fst'
    values = dict(TMax=duration, DT=dt, CompElast=2 if bd else 1, CompServo=0,
                  CompAero=2, CompInflow=0 if unloaded else 1, Gravity=0 if unloaded else 9.80665,
                  WrVTK=2, VTK_fps=40, DT_Out=.025, SumPrint='True')
    if bd:
        for b in (1, 2, 3):
            values[f'BDBldFile({b})'] = '"../5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat"'
        for name in ('NRELOffshrBsline5MW_BeamDyn.dat', 'NRELOffshrBsline5MW_BeamDyn_Blade.dat'):
            shutil.copyfile(REFERENCE / name, destination / '5MW_Baseline' / name)
    replace(fst, values)
    ed = farm / 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat'
    replace(ed, dict(GenDOF='False', DrTrDOF='False', YawDOF='False', RotSpeed=0 if unloaded else 9,
                     FlapDOF1='False' if bd else 'True', FlapDOF2='False' if bd else 'True',
                     EdgeDOF='False' if bd else 'True'))
    replace(farm / 'InflowWind.dat', dict(WindType=1, HWindSpeed=8, PLExp=0))
    curve = []
    if bd:
        path = destination / '5MW_Baseline/NRELOffshrBsline5MW_BeamDyn.dat'
        lines = path.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if 'kp_xr' in line) + 2
        for i in range(start, start+49):
            row = [float(v) for v in lines[i].split()[:4]]
            row[0] = -(row[2]/61.5)**2 if prebend else 0.
            curve.append(row)
            lines[i] = ' '.join(f'{v:.10E}' for v in row)
        path.write_text('\n'.join(lines)+'\n')
    if prebend:
        path = destination / '5MW_Baseline/NRELOffshrBsline5MW_AeroDyn_blade.dat'
        lines = path.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if 'BlSpn' in line) + 2
        for i in range(start, start+19):
            fields = lines[i].split()
            fields[1] = f'{float(fields[1]) - (float(fields[0])/61.5)**2:.10E}'
            lines[i] = ' '.join(fields)
        path.write_text('\n'.join(lines)+'\n')
    inputs = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in destination.rglob('*') if p.is_file()}
    config = dict(variant=variant, unloaded=unloaded, dt_s=dt, duration_s=duration,
                  model='NREL 5MW upwind prebend derivative' if prebend else 'NREL 5MW straight reference',
                  structural_module='BeamDyn' if bd else 'ElastoDyn',
                  reference_curve=curve, reference_columns=['x_m', 'y_m', 'z_m', 'twist_deg'],
                  prebend_definition='x=-(z/61.5)^2 m; upwind; added to existing AeroDyn AC offsets',
                  precone_deg=-2.5, source_case=str(SOURCE), inputs=inputs,
                  scope='Single-turbine fixed rpm and steady wind interface test; not matched to MAPPO A segment',
                  structural_source=json.loads((REFERENCE/'sources.json').read_text()))
    (destination/'probe-config.json').write_text(json.dumps(config, indent=2))
    return destination


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination', type=Path)
    p.add_argument('--variant', choices=['A','B','C'], required=True)
    p.add_argument('--duration', type=float, default=6.)
    p.add_argument('--dt', type=float, default=.00625)
    p.add_argument('--unloaded', action='store_true')
    a = p.parse_args()
    print(prepare(a.destination, a.variant, a.duration, a.unloaded, a.dt))
