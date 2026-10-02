"""The isolated solver runner preserves inputs and verifies lossless raw evidence."""
import hashlib
import builtins
import json
import lzma
from pathlib import Path
import sys

import pytest

from scripts.lidar import run_dual_beam_validation as runner


@pytest.fixture
def inputs(tmp_path):
    template, reference = tmp_path / 'template', tmp_path / 'reference'
    for root in (template, reference):
        (root / 'FarmInputs').mkdir(parents=True)
        (root / '5MW_Baseline/Airfoils').mkdir(parents=True)
        for name in ('NRELOffshrBsline5MW_BeamDyn.dat', 'NRELOffshrBsline5MW_BeamDyn_Blade.dat'):
            (root / '5MW_Baseline' / name).write_text('matching physical BeamDyn input\n')
        (root / '5MW_Baseline/AD.dat').write_text('aerodynamic input\n')
        (root / '5MW_Baseline/Airfoils/test_coords.txt').write_text('0 0\n1 0\n')
        (root / 'probe-config.json').write_text(json.dumps(dict(
            structural_module='BeamDyn', unloaded=root == reference,
            reference_curve=[[0, 0, 0, 0], [-1, 0, 61.5, 0]],
            model='NREL 5MW derivative', structural_source={})))
    fst_keys = ('TMax', 'DT', 'CompElast', 'CompServo', 'CompAero', 'CompInflow',
                'Gravity', 'WrVTK', 'VTK_type', 'VTK_fields', 'VTK_fps', 'DT_Out',
                'TStart', 'OutFileFmt', 'OutFmt', 'SumPrint',
                'BDBldFile(1)', 'BDBldFile(2)', 'BDBldFile(3)')
    (template / 'FarmInputs/FFTest_WT1.fst').write_text(''.join(f'0 {k} - field\n' for k in fst_keys))
    ed_keys = ('GenDOF', 'DrTrDOF', 'YawDOF', 'RotSpeed', 'Azimuth', 'NacYaw',
               'FlapDOF1', 'FlapDOF2', 'EdgeDOF', 'TwFADOF1', 'TwFADOF2',
               'TwSSDOF1', 'TwSSDOF2', 'BlPitch(1)', 'BlPitch(2)', 'BlPitch(3)')
    (template / 'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat').write_text(
        ''.join(f'0 {k} - field\n' for k in ed_keys))
    (template / 'FarmInputs/InflowWind.dat').write_text('1 WindType\n8 HWindSpeed\n0 PLExp\n')
    (template / 'FarmInputs/old.out').write_text('old solver output')
    (template / '5MW_Baseline/old.sum').write_text('old summary')
    exe = tmp_path / 'fake-openfast'
    exe.write_text(f'#!{sys.executable}\nprint("unused")\n')
    exe.chmod(0o755)
    return template, reference, exe


def prepare(tmp_path, inputs, **kwargs):
    template, reference, exe = inputs
    return runner.prepare(tmp_path / 'run', template=template, reference=reference,
                          executable=exe, **kwargs)


@pytest.mark.parametrize('kwargs', [dict(wind=float('nan')), dict(wind=0),
    dict(duration=18), dict(fps=0), dict(dt=.008), dict(fps=80, dt=.003),
    dict(rpm=0), dict(pitch=-1), dict(azimuth=360), dict(duration=26.001)])
def test_invalid_physics_never_creates_output(tmp_path, inputs, kwargs):
    with pytest.raises(ValueError):
        prepare(tmp_path, inputs, **kwargs)
    assert not (tmp_path / 'run').exists()


def test_prepare_records_actual_inputs_and_preserves_template(tmp_path, inputs):
    template, reference, exe = inputs
    before = {str(p.relative_to(template)): runner.digest(p) for p in template.rglob('*') if p.is_file()}
    run = prepare(tmp_path, inputs, duration=26, fps=80, dt=.003125)
    config = json.loads((run / 'run-config.json').read_text())
    assert config['evaluation_window_s'] == [18, 26]
    assert config['fps'] == 80 and config['dt_s'] == .003125
    assert config['reference_config_sha256'] == runner.digest(reference / 'probe-config.json')
    assert config['solver_sha256'] == runner.digest(exe)
    assert not (run / 'FarmInputs/old.out').exists()
    assert not (run / '5MW_Baseline/old.sum').exists()
    assert '0    CompServo' in (run / 'FarmInputs/FFTest_WT1.fst').read_text()
    assert 'True    TwFADOF1' in (run / 'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat').read_text()
    for row in config['inputs']:
        assert row['sha256'] == runner.digest(run / row['path'])
    assert before == {str(p.relative_to(template)): runner.digest(p) for p in template.rglob('*') if p.is_file()}
    with pytest.raises(FileExistsError):
        runner.prepare(run, template=template, reference=reference, executable=exe)


XML = b'<VTKFile><PolyData><Piece/></PolyData></VTKFile>\n'


def test_archiver_waits_for_stable_complete_xml_and_roundtrips(tmp_path):
    folder = tmp_path / 'FarmInputs/vtk'
    folder.mkdir(parents=True)
    path = folder / 'FFTest_WT1.Blade1Surface.0000.vtp'
    path.write_bytes(b'<VTKFile>')
    archiver = runner.SurfaceArchiver(tmp_path)
    archiver.scan()
    archiver.scan()
    assert path.exists() and not archiver.surfaces
    path.write_bytes(XML)
    archiver.scan()
    assert path.exists()
    archiver.scan()
    assert not path.exists()
    row = archiver.surfaces[0]
    assert row['path'] == 'FarmInputs/vtk/FFTest_WT1.Blade1Surface.0000.vtp'
    assert row['sha256'] == hashlib.sha256(XML).hexdigest()
    assert lzma.open(tmp_path / row['compressed_path'], 'rb').read() == XML
    assert row['compressed_sha256'] == runner.digest(tmp_path / row['compressed_path'])


def test_parallel_batch_failure_commits_other_verified_surfaces(tmp_path, monkeypatch):
    folder = tmp_path / 'FarmInputs/vtk'
    folder.mkdir(parents=True)
    good = folder / 'FFTest_WT1.Blade1Surface.0.vtp'
    bad = folder / 'FFTest_WT1.Blade2Surface.0.vtp'
    good.write_bytes(XML)
    bad.write_bytes(XML)
    archiver = runner.SurfaceArchiver(tmp_path)
    original = archiver._compress_surface
    def fail_one(candidate):
        if candidate[0] == bad:
            raise RuntimeError('simulated one-worker compression failure')
        return original(candidate)
    monkeypatch.setattr(archiver, '_compress_surface', fail_one)
    archiver.scan()
    with pytest.raises(RuntimeError, match='one-worker'):
        archiver.scan()
    assert bad.read_bytes() == XML
    assert not good.exists()
    assert lzma.open(good.with_name(good.name + '.xz'), 'rb').read() == XML
    inventory = json.loads((tmp_path / 'compressed-surfaces.json').read_text())
    assert len(inventory['surfaces']) == 1
    assert not list(folder.glob('*.tmp'))


def write_solver(exe, *, fail=False, omit=None):
    script = f'''#!{sys.executable}
from pathlib import Path
import re
text=Path('FFTest_WT1.fst').read_text()
def value(k): return float(re.search(r'^\\s*(\\S+)\\s+'+re.escape(k)+r'(?=\\s)',text,re.M)[1])
duration,fps=value('TMax'),value('VTK_fps')
folder=Path('vtk');folder.mkdir()
xml={XML!r}
kinds={runner.SURFACE_KINDS!r}
if {fail!r}:
    (folder/'FFTest_WT1.Blade1Surface.0.vtp').write_bytes(xml)
    (folder/'FFTest_WT1.Blade2Surface.0.vtp').write_bytes(b'<VTKFile>')
    print('fake solver failure',flush=True)
    raise SystemExit(3)
for i in range(round(duration*fps)+1):
    for kind in kinds:
        if (kind,i)=={omit!r}: continue
        # Different padding at initialization must still map to the actual index.
        index=str(i) if i==0 else f'{{i:05d}}'
        (folder/f'FFTest_WT1.{{kind}}Surface.{{index}}.vtp').write_bytes(xml)
(folder/'FFTest_WT1.GroundSurface.vtp').write_bytes(xml)
with Path('FFTest_WT1.out').open('w') as stream:
    stream.write('Time RotSpeed\\n(s) (rpm)\\n')
    for i in range(round(duration*fps)+1): stream.write(f'{{i/fps:.4f}} 9\\n')
print('OpenFAST terminated normally.',flush=True)
'''
    exe.write_text(script)


def test_fake_solver_complete_full_clock(tmp_path, inputs):
    write_solver(inputs[2])
    run = prepare(tmp_path, inputs, duration=19, fps=1)
    result = runner.execute(run, poll_seconds=.01)
    assert result['status'] == 'COMPLETE' and result['exit_code'] == 0
    assert json.loads((run / 'run-config.json').read_text())['status'] == 'SOLVER_COMPLETE'
    inventory = json.loads((run / 'compressed-surfaces.json').read_text())
    assert inventory['frame_audit']['frame_count'] == 20
    assert len(inventory['surfaces']) == 121
    assert not list((run / 'FarmInputs/vtk').glob('*.vtp'))


def test_fake_failure_retains_complete_and_partial_evidence(tmp_path, inputs):
    write_solver(inputs[2], fail=True)
    run = prepare(tmp_path, inputs)
    with pytest.raises(RuntimeError, match='code 3'):
        runner.execute(run, poll_seconds=.01)
    status = json.loads((run / 'exit-status.json').read_text())
    assert status['status'] == 'FAILED' and status['exit_code'] == 3
    assert (run / 'FarmInputs/vtk/FFTest_WT1.Blade1Surface.0.vtp.xz').exists()
    assert (run / 'FarmInputs/vtk/FFTest_WT1.Blade2Surface.0.vtp').read_bytes() == b'<VTKFile>'
    assert 'fake solver failure' in (run / 'solver.log').read_text()
    assert json.loads((run / 'run-config.json').read_text())['status'] == 'FAILED'


def test_solver_zero_exit_missing_frame_stays_failed(tmp_path, inputs):
    write_solver(inputs[2], omit=('Hub', 19))
    run = prepare(tmp_path, inputs, duration=19, fps=1)
    with pytest.raises(ValueError, match='Incomplete full-run surface clock'):
        runner.execute(run, poll_seconds=.01)
    assert json.loads((run / 'exit-status.json').read_text())['status'] == 'FAILED'
    assert json.loads((run / 'run-config.json').read_text())['status'] == 'FAILED'


def test_exhausted_shared_budget_does_not_launch_solver(tmp_path, inputs):
    run = prepare(tmp_path, inputs)
    with pytest.raises(RuntimeError, match='before solver launch'):
        runner.execute(run, budget_bytes=1, budget_root=tmp_path)
    assert not (run / 'solver.log').exists()
    assert json.loads((run / 'exit-status.json').read_text())['exit_code'] is None


def test_clock_audit_has_no_training_or_numpy_import(tmp_path, monkeypatch):
    folder = tmp_path / 'FarmInputs'
    folder.mkdir()
    (folder / 'FFTest_WT1.out').write_text('Time RotSpeed\n(s) (rpm)\n0 9\n1 9\n')
    surfaces = [dict(path=f'FarmInputs/vtk/FFTest_WT1.{kind}Surface.{frame}.vtp')
                for frame in (0, 1) for kind in runner.SURFACE_KINDS]
    surfaces.append(dict(path='FarmInputs/vtk/FFTest_WT1.GroundSurface.vtp'))
    original = builtins.__import__
    def forbid_training(name, *args, **kwargs):
        if name.startswith(('numpy', 'scipy', 'wfrl', 'scripts.experiments')):
            raise ImportError('training environment intentionally unavailable')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', forbid_training)
    audit = runner.audit_frames(tmp_path, surfaces, dict(duration_s=1, fps=1))
    assert audit['status'] == 'COMPLETE'
    assert audit['max_printed_time_error_s'] == 0


@pytest.mark.parametrize('text', [
    'Time Value\n(s) (m)\n0 1\nTime Value\n(s) (m)\n1 2\n',
    'Time Value\n(s)\n0 1\n1 2\n',
    'Time Value\n(s) (m)\n0 1\n1\n',
    'Time Value\n(s) (m)\n0 1\n1 NaN\n',
    'Time Value\n(s) (m)\n0 1\n0 2\n',
    'Time Value\n(s) (m)\n0 1\n1 invalid\n',
])
def test_strict_clock_parser_retains_malformed_row_failures(tmp_path, text):
    path = tmp_path / 'native.out'
    path.write_text(text)
    with pytest.raises(ValueError):
        runner.read_output_clock(path)
