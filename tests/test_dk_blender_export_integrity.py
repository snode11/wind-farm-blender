"""Host regressions for geometry and same-run sidecar export contracts."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from scripts.blender import export_demo_telemetry as telemetry
from scripts.blender import export_geometry as geometry
from scripts.blender import export_tip_deflection as deflection


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'projects/nrel5mw-reference/template'
ASSET = ROOT / 'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob('*') if p.is_file()}


def inputs(tmp_path, *, source_times=(2., 3., 4.), times=None, units_override=None):
    package, case = tmp_path / 'package', tmp_path / 'case'
    package.mkdir()
    farm = case / 'FarmInputs'
    farm.mkdir(parents=True)
    source_times = np.asarray(source_times, dtype=float)
    poses = np.tile([11., 0., 9., 1., 2., 3.], (len(source_times), 1))
    names = ['Time', 'YawPzn', 'Azimuth', 'RotSpeed', 'BlPitch1', 'BlPitch2', 'BlPitch3',
             'GenPwr', 'GenTq', 'RootMyc1', 'BlPitchC1']
    units = ['(s)', '(deg)', '(deg)', '(rpm)', '(deg)', '(deg)', '(deg)',
             '(kW)', '(kN-m)', '(kN-m)', '(deg)']
    names += [f'{prefix}{b}' for b in (1, 2, 3) for prefix in ('OoPDefl', 'IPDefl')]
    units += ['(m)'] * 6
    for key, unit in (units_override or {}).items():
        units[names.index(key)] = unit
    rows = np.column_stack([source_times, poses,
                            np.tile([2000., 100., -50., 1.], (len(poses), 1)),
                            np.tile(np.arange(6) / 10, (len(poses), 1))])
    header = ['Synthetic output', 'Synthetic OpenFAST', '', '', '', '',
              ' '.join(names), ' '.join(units)]
    output = farm / 'Case.T1.out'
    np.savetxt(output, rows, header='\n'.join(header), comments='', encoding='utf-8')
    times = source_times.copy() if times is None else np.asarray(times, dtype=float)
    np.savez(package / 'geometry.npz', times=times,
             poses=np.tile(poses[0], (len(times), 1, 1)))
    (package / 'telemetry.json').write_text(json.dumps({
        'sources': {'T1': {'sha256': digest(output)}}}), encoding='utf-8')
    (package / 'deflection-t1.json').write_text('{"saved": "old result"}\n', encoding='utf-8')
    manifest = {'turbine_ids': ['T1'], 'files': {
        name: digest(package / name) for name in
        ('geometry.npz', 'telemetry.json', 'deflection-t1.json')}}
    (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    scalars = json.loads(ASSET.read_text(encoding='utf-8'))['scalars']
    ed = dict.fromkeys(('TwFADOF1', 'TwFADOF2', 'TwSSDOF1', 'TwSSDOF2', 'TeetDOF',
                       'PtfmSgDOF', 'PtfmSwDOF', 'PtfmHvDOF', 'PtfmRDOF',
                       'PtfmPDOF', 'PtfmYDOF', 'Furling'), 'False')
    ed.update(dict.fromkeys(('OoPDefl', 'IPDefl', 'Azimuth', 'NacYaw', 'AzimB1Up',
                            'PtfmRefzt', 'BlPitch(1)', 'BlPitch(2)', 'BlPitch(3)'), 0.))
    ed.update(scalars)
    ed.update(NumBl=3)
    ed.update({f'PreCone({b})': scalars['PreCone(1)'] for b in (2, 3)})
    (farm / 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat').write_text(
        ''.join(f'{value} {key}\n' for key, value in ed.items()), encoding='utf-8')
    return package, case, ASSET


def run_export(kind, args):
    return telemetry.export(*args[:2]) if kind == 'telemetry' else deflection.export(*args)


@pytest.mark.parametrize('token', ['NaN', 'Inf', 'None', '1e999'])
def test_geometry_table_rejects_missing_or_nonfinite_columns(token):
    lines = ['BlSpn BlCrvAC BlSwpAC BlCrvAng BlTwist BlChord BlAFID BlCb',
             '(m) (m) (m) (deg) (deg) (m) (-) (-)', f'0 {token} 0 0 13 3.5 1 0']
    with pytest.raises(ValueError, match='(?i)(finite|invalid|numeric)'):
        geometry.table(lines, 'BlSpn', 7, 1)


class BrokenWriter:
    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, text):
        self.stream.write(text[:11])
        raise OSError('injected partial write')


def test_geometry_partial_write_preserves_old_result(tmp_path, monkeypatch):
    output = tmp_path / 'geometry.json'
    output.write_bytes(b'old geometry\n')
    before = snapshot(tmp_path)
    real_open, real_fdopen = io.open, os.fdopen

    def broken_open(file, mode='r', *args, **kwargs):
        stream = real_open(file, mode, *args, **kwargs)
        return BrokenWriter(stream) if mode == 'w' else stream

    def broken_fdopen(fd, mode='r', *args, **kwargs):
        stream = real_fdopen(fd, mode, *args, **kwargs)
        return BrokenWriter(stream) if mode == 'w' else stream

    monkeypatch.setattr(io, 'open', broken_open)
    monkeypatch.setattr(os, 'fdopen', broken_fdopen)
    with pytest.raises(OSError, match='injected partial write'):
        geometry.export(TEMPLATE, output)
    assert snapshot(tmp_path) == before


def test_geometry_normal_export_keeps_packaged_bytes_and_hashes(tmp_path):
    output = tmp_path / 'geometry.json'
    expected = json.loads(ASSET.read_text(encoding='utf-8'))
    geometry.export(TEMPLATE, output, source=expected['source'])
    assert output.read_bytes() == ASSET.read_bytes()
    for name, recorded in expected['source_sha256'].items():
        assert digest(TEMPLATE / name) == recorded
    assert digest(ROOT / 'wfrl/turbines/nrel5mw.yaml') == expected['shell_source_sha256']


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
def test_sidecar_manifest_serialization_failure_preserves_package(tmp_path, monkeypatch, kind):
    args = inputs(tmp_path)
    before = snapshot(args[0])
    real_dumps = json.dumps

    def fail_manifest(value, *a, **kw):
        if isinstance(value, dict) and 'files' in value:
            raise ValueError('injected manifest serialization failure')
        return real_dumps(value, *a, **kw)

    monkeypatch.setattr(json, 'dumps', fail_manifest)
    with pytest.raises(ValueError, match='injected manifest serialization failure'):
        run_export(kind, args)
    assert snapshot(args[0]) == before


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
@pytest.mark.parametrize('existing', [False, True])
def test_sidecar_manifest_replace_failure_rolls_back_package(tmp_path, monkeypatch, kind, existing):
    args = inputs(tmp_path)
    name = 'telemetry.json' if kind == 'telemetry' else 'deflection-t1.json'
    if not existing:
        manifest_path = args[0] / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        del manifest['files'][name]
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        # Deflection needs telemetry provenance, but its own target may be absent.
        (args[0] / name).unlink()
    before = snapshot(args[0])
    real_replace = os.replace

    def fail_manifest(source, target):
        if Path(target) == args[0] / 'manifest.json':
            raise OSError('injected manifest replace failure')
        return real_replace(source, target)

    monkeypatch.setattr(os, 'replace', fail_manifest)
    with pytest.raises(OSError, match='injected manifest replace failure'):
        run_export(kind, args)
    assert snapshot(args[0]) == before


def test_telemetry_rejects_geometry_with_wrong_digest(tmp_path):
    args = inputs(tmp_path)
    with (args[0] / 'geometry.npz').open('ab') as stream:
        stream.write(b'extra bytes')
    before = snapshot(args[0])
    with pytest.raises(ValueError, match='(?i)(integrity|checksum|digest)'):
        telemetry.export(*args[:2])
    assert snapshot(args[0]) == before


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
def test_export_accepts_one_source_sample(tmp_path, kind):
    args = inputs(tmp_path, source_times=(2.,))
    run_export(kind, args)
    name = 'telemetry.json' if kind == 'telemetry' else 'deflection-t1.json'
    payload = json.loads((args[0] / name).read_text(encoding='utf-8'))
    assert payload['times'] == [2.]
    manifest = json.loads((args[0] / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['files'][name] == digest(args[0] / name)


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
@pytest.mark.parametrize('unit', [{'Time': '(ms)'}, {'YawPzn': '(rad)'}, {'RotSpeed': '(rad/s)'}])
def test_export_rejects_incompatible_time_and_pose_units(tmp_path, kind, unit):
    args = inputs(tmp_path, units_override=unit)
    before = snapshot(args[0])
    with pytest.raises(ValueError, match='(?i)unit'):
        run_export(kind, args)
    assert snapshot(args[0]) == before


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
@pytest.mark.parametrize('source_times', [(2., 2., 4.), (2., 4., 3.), (2., float('inf'), 4.)])
def test_export_rejects_ambiguous_source_time_axis(tmp_path, kind, source_times):
    args = inputs(tmp_path, source_times=source_times, times=[2.])
    before = snapshot(args[0])
    with pytest.raises(ValueError, match='(?i)time'):
        run_export(kind, args)
    assert snapshot(args[0]) == before


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
def test_export_rejects_empty_geometry_time_axis(tmp_path, kind):
    args = inputs(tmp_path, times=[])
    before = snapshot(args[0])
    with pytest.raises(ValueError, match='(?i)time'):
        run_export(kind, args)
    assert snapshot(args[0]) == before


def test_telemetry_outside_source_time_reports_range(tmp_path):
    args = inputs(tmp_path, times=[4.5])
    with pytest.raises(ValueError, match='outside.*range'):
        telemetry.export(*args[:2])


def test_deflection_uses_t1_manifest_slot(tmp_path):
    args = inputs(tmp_path)
    with np.load(args[0] / 'geometry.npz') as archive:
        times, poses = archive['times'], archive['poses']
    reordered = np.concatenate((poses + 20., poses), axis=1)
    np.savez(args[0] / 'geometry.npz', times=times, poses=reordered)
    manifest_path = args[0] / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['turbine_ids'] = ['T2', 'T1']
    manifest['files']['geometry.npz'] = digest(args[0] / 'geometry.npz')
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    result = deflection.export(*args)
    np.testing.assert_array_equal(result['poses'], poses[:, 0])
    assert result['frame'] == 'ElastoDyn unpitched coned xc,yc,zc'
    assert result['unit'] == 'm'
    assert np.asarray(result['simulation']).shape == (3, 3, 2)


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
@pytest.mark.parametrize('bad_pose', ['short', 'nonfinite'])
def test_export_rejects_invalid_geometry_pose_array(tmp_path, kind, bad_pose):
    args = inputs(tmp_path)
    with np.load(args[0] / 'geometry.npz') as archive:
        times, poses = archive['times'], archive['poses']
    if bad_pose == 'short':
        poses = poses[:1]
    else:
        poses[:, :, 1] = np.inf
    np.savez(args[0] / 'geometry.npz', times=times, poses=poses)
    manifest_path = args[0] / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']['geometry.npz'] = digest(args[0] / 'geometry.npz')
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    before = snapshot(args[0])
    with pytest.raises(ValueError, match='(?i)(geometry|pose)'):
        run_export(kind, args)
    assert snapshot(args[0]) == before


def test_geometry_export_accepts_external_template_path(tmp_path):
    external = tmp_path / 'template'
    external.symlink_to(TEMPLATE, target_is_directory=True)
    output = tmp_path / 'geometry.json'
    geometry.export(external, output)
    result = json.loads(output.read_text(encoding='utf-8'))
    assert str(external) in result['source']
    assert result['scalars'] == json.loads(ASSET.read_text(encoding='utf-8'))['scalars']


def test_geometry_rejects_missing_airfoil_coordinate_without_shifting_columns(tmp_path, monkeypatch):
    output = tmp_path / 'geometry.json'
    output.write_bytes(b'old geometry')
    read_bytes = Path.read_bytes

    def damaged_coordinates(path):
        raw = read_bytes(path)
        if path.name == 'Cylinder1_coords.txt':
            return raw.replace(b'1.000000  0.000000', b'NaN 0.0 42', 1)
        return raw

    monkeypatch.setattr(Path, 'read_bytes', damaged_coordinates)
    with pytest.raises(ValueError, match='(?i)(finite|coordinate)'):
        geometry.export(TEMPLATE, output)
    assert output.read_bytes() == b'old geometry'


@pytest.mark.parametrize('kind', ['telemetry', 'deflection'])
@pytest.mark.parametrize('token', ['NaN', 'Inf', 'None'])
def test_export_never_replaces_missing_source_values_with_zero(tmp_path, kind, token):
    args = inputs(tmp_path)
    output = args[1] / 'FarmInputs/Case.T1.out'
    lines = output.read_text(encoding='utf-8').splitlines()
    key = 'GenPwr' if kind == 'telemetry' else 'OoPDefl1'
    fields = lines[8].split()
    fields[lines[6].split().index(key)] = token
    lines[8] = ' '.join(fields)
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    telemetry_path = args[0] / 'telemetry.json'
    telemetry_path.write_text(json.dumps({'sources': {'T1': {'sha256': digest(output)}}}), encoding='utf-8')
    manifest_path = args[0] / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']['telemetry.json'] = digest(telemetry_path)
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    before = snapshot(args[0])
    with pytest.raises(ValueError):
        run_export(kind, args)
    assert snapshot(args[0]) == before


@pytest.mark.parametrize(('name', 'arguments'), [
    ('export_geometry', []), ('export_demo_telemetry', ['package', 'case']),
    ('export_tip_deflection', ['package', 'case', 'geometry.json']),
])
def test_export_cli_invalid_arguments_exit_nonzero(tmp_path, name, arguments):
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/blender' / f'{name}.py'),
                             *arguments, '--invalid-option'], cwd=tmp_path,
                            capture_output=True, text=True, encoding='utf-8', check=False)
    assert result.returncode == 2
    assert 'unrecognized arguments: --invalid-option' in result.stderr
    assert not list(tmp_path.iterdir())
