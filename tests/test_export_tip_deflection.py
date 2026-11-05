"""Host checks for solver-time selection in the tip-deflection exporter."""
import hashlib
import json

import numpy as np
import pytest

from scripts.blender.export_tip_deflection import export


SOURCE_TIMES = np.array([2., 3., 4.])
SOURCE_POSES = np.array([[11., 0., 9., 1., 2., 3.],
                         [12., 90., 10., 2., 3., 4.],
                         [13., 180., 11., 3., 4., 5.]])
SOURCE_DEFLECTION = np.arange(18, dtype=float).reshape(3, 3, 2) / 10


def export_inputs(tmp_path, times):
    package = tmp_path / 'package'
    package.mkdir()
    case = tmp_path / 'case'
    farm = case / 'FarmInputs'
    farm.mkdir(parents=True)
    times = np.asarray(times)
    pose_rows = np.abs(times[:, None] - SOURCE_TIMES).argmin(axis=1)
    np.savez(package / 'geometry.npz', times=times,
             poses=SOURCE_POSES[pose_rows, None, :])

    names = ['Time', 'YawPzn', 'Azimuth', 'RotSpeed',
             'BlPitch1', 'BlPitch2', 'BlPitch3']
    names += [f'{prefix}{blade}' for blade in (1, 2, 3)
              for prefix in ('OoPDefl', 'IPDefl')]
    units = ['(s)', '(deg)', '(deg)', '(rpm)', '(deg)', '(deg)', '(deg)'] + ['(m)'] * 6
    header = ['Synthetic solver output', 'Synthetic OpenFAST', '', '', '', '',
              ' '.join(names), ' '.join(units)]
    output = farm / 'Case.T1.out'
    np.savetxt(output, np.column_stack([SOURCE_TIMES, SOURCE_POSES,
                                      SOURCE_DEFLECTION.reshape(3, 6)]),
               header='\n'.join(header), comments='', encoding='utf-8')
    telemetry = {'sources': {'T1': {'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}}}
    (package / 'telemetry.json').write_text(json.dumps(telemetry), encoding='utf-8')
    manifest = {'files': {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                          for name in ('geometry.npz', 'telemetry.json')}}
    (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    scalars = {'TipRad': 63., 'HubRad': 1.5, 'OverHang': -5., 'TowerHt': 87.6,
               'Twr2Shft': 1.9, 'ShftTilt': -5., 'PreCone(1)': 0.}
    inputs = dict.fromkeys(('TwFADOF1', 'TwFADOF2', 'TwSSDOF1', 'TwSSDOF2', 'TeetDOF',
                           'PtfmSgDOF', 'PtfmSwDOF', 'PtfmHvDOF', 'PtfmRDOF',
                           'PtfmPDOF', 'PtfmYDOF', 'Furling'), 'False')
    inputs.update(dict.fromkeys(('OoPDefl', 'IPDefl', 'Azimuth', 'NacYaw', 'AzimB1Up',
                                'PtfmRefzt', 'BlPitch(1)', 'BlPitch(2)', 'BlPitch(3)'), 0.))
    inputs.update(scalars)
    inputs.update({'NumBl': 3, 'PreCone(2)': 0., 'PreCone(3)': 0.})
    (farm / 'NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat').write_text(
        ''.join(f'{value} {name}\n' for name, value in inputs.items()), encoding='utf-8')
    geometry_asset = tmp_path / 'geometry.json'
    geometry_asset.write_text(json.dumps({'scalars': scalars}), encoding='utf-8')
    return package, case, geometry_asset


@pytest.mark.parametrize('requested_time', [1.5, 4.5,
    np.nextafter(2., -np.inf), np.nextafter(4., np.inf)],
    ids=['before-start', 'after-end', 'just-before-start', 'just-after-end'])
def test_export_rejects_time_outside_solver_range(tmp_path, requested_time):
    args = export_inputs(tmp_path, sorted([2., requested_time, 4.]))
    with pytest.raises(ValueError) as error:
        export(*args)
    message = str(error.value)
    assert f'{requested_time} s' in message
    assert '[2.0, 4.0] s' in message
    assert 'outside' in message and 'range' in message
    assert not (args[0] / 'deflection-t1.json').exists()


@pytest.mark.parametrize(('times', 'rows'), [
    ([2.], [0]),
    ([4.], [2]),
    ([2., 4.], [0, 2]),
    ([3.], [1]),
    ([3. - 5e-9], [1]),
], ids=['start', 'end', 'both-endpoints', 'interior', 'matching-tolerance'])
def test_export_preserves_solver_samples(tmp_path, times, rows):
    args = export_inputs(tmp_path, times)
    payload = export(*args)
    np.testing.assert_array_equal(payload['times'], times)
    np.testing.assert_array_equal(payload['poses'], SOURCE_POSES[rows])
    np.testing.assert_array_equal(payload['simulation'], SOURCE_DEFLECTION[rows])
    saved = json.loads((args[0] / 'deflection-t1.json').read_text(encoding='utf-8'))
    assert saved == payload


def test_export_rejects_unmatched_time_inside_solver_range(tmp_path):
    args = export_inputs(tmp_path, [2.5])
    with pytest.raises(ValueError, match='^Source time mismatch$'):
        export(*args)
