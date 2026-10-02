"""Raw-file corruption, unit and relocation checks for the provenance audit."""
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from scripts.lidar import verify_dual_beam_source_provenance as audit


def save_json(path, payload):
    path.write_text(json.dumps(payload))


def write_surface(path, points):
    values = ' '.join(str(float(value)) for value in np.asarray(points).ravel())
    path.write_text('<VTKFile><PolyData><Piece><Points><DataArray>' + values
                    + '</DataArray></Points><Polys><DataArray Name="connectivity">0 1 2</DataArray>'
                    + '<DataArray Name="offsets">3</DataArray></Polys></Piece></PolyData></VTKFile>')


def refresh_inventory(source):
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest['files'] = {path.name: audit.digest(path) for path in source.iterdir()
                         if path.name != 'manifest.json'}
    save_json(source / 'manifest.json', manifest)


def update_output_hash(source, tid, output):
    telemetry = json.loads((source / 'telemetry.json').read_text())
    checksum = audit.digest(output)
    telemetry['sources'][tid]['sha256'] = checksum
    save_json(source / 'telemetry.json', telemetry)
    if tid == 'T1':
        sidecar = json.loads((source / 'deflection-t1.json').read_text())
        sidecar['sources']['T1']['sha256'] = checksum
        save_json(source / 'deflection-t1.json', sidecar)
    refresh_inventory(source)


@pytest.fixture
def raw_source(tmp_path):
    case = tmp_path / '__simul__/fastfarm/synthetic-independent-fixture'
    farm = case / 'FarmInputs'
    farm.mkdir(parents=True)
    blade_folder = case / '5MW_Baseline'
    servo_folder = blade_folder / 'ServoData'
    servo_folder.mkdir(parents=True)
    rest = tmp_path / 'results/prebend-fixture/C-rest'
    vtk = rest / 'FarmInputs/vtk'
    vtk.mkdir(parents=True)
    source = tmp_path / 'source'
    source.mkdir()
    times = np.array([117., 117.025])
    poses = np.zeros((2, 3, 6), np.float32)
    poses[:, :, 2] = 9.
    identity = np.column_stack((np.eye(3), np.zeros(3)))
    transforms = np.broadcast_to(identity, (2, 3, 3, 19, 3, 4)).copy()
    nacelles = np.broadcast_to(identity, (2, 3, 3, 4)).copy()
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    triangles = np.array([[0, 1, 2]])
    for name in ['Blade1', 'Blade2', 'Blade3', 'Tower']:
        write_surface(vtk / f'FFTest_WT1.{name}Surface.0.vtp', points)
    np.savez_compressed(source / 'reference-surfaces.npz', blades=np.tile(points, (3, 1, 1)),
                        triangles=triangles, tower=points, tower_triangles=triangles)
    np.savez_compressed(source / 'geometry.npz', times=times, poses=poses, transforms=transforms)
    np.savez_compressed(source / 'tower-motion.npz', times=times, nacelle=nacelles)
    telemetry = dict(times=times.tolist(), sources={}, turbines={})
    payload, input_hashes = {}, {}
    tip_channels = [f'B{blade}TipTD{axis}r' for blade in (1, 2, 3) for axis in 'xyz']
    names = ['Time', 'YawPzn', 'Azimuth', 'RotSpeed', 'BlPitch1', 'BlPitch2', 'BlPitch3',
             *tip_channels, 'GenPwr', 'GenTq']
    units = ['(s)', '(deg)', '(deg)', '(rpm)', '(deg)', '(deg)', '(deg)',
             *['(m)'] * 9, '(kW)', '(kN-m)']
    output_lines = ['Synthetic fixture; not research evidence', ' '.join(names), ' '.join(units)]
    for time in times:
        output_lines.append(' '.join(str(value) for value in [time, 0., 0., 9., 0., 0., 0.,
                                                              *[0.] * 9, 1000., 2.]))
    for tid in ['T1', 'T2', 'T3']:
        output = farm / f'Case.{tid}.out'
        output.write_text('\n'.join(output_lines) + '\n')
        fst = farm / f'FFTest_WT{tid[1:]}.fst'
        fst.write_text(f'"Servo{tid}.dat" ServoFile\n')
        input_hashes[fst.name] = audit.digest(fst)
        (farm / f'Servo{tid}.dat').write_text(f'"../5MW_Baseline/ServoData/DISCON_{tid}.dll" DLL_FileName\n')
        (servo_folder / f'DISCON_{tid}.dll').write_bytes(b'controller fixture')
        telemetry['sources'][tid] = dict(path=str(output), sha256=audit.digest(output))
        telemetry['turbines'][tid] = dict(power=dict(source_channel='GenPwr', unit='MW', values=[1., 1.]),
                                          torque=dict(source_channel='GenTq', unit='N m', values=[2000., 2000.]))
        payload[tid] = dict(motion=[dict(time_s=float(time), yaw_deg=0., azimuth_deg=0., rotor_speed_rpm=9.,
                                        pitch_deg=[0., 0., 0.]) for time in times])
    wind = farm / 'replay-wind.bts'
    wind.write_bytes(b'wind fixture')
    (farm / 'InflowWind.dat').write_text('"replay-wind.bts" FileName_BTS\n')
    beam_hashes = {}
    for name in ['BeamDyn.dat', 'BeamDyn_Blade.dat']:
        path = blade_folder / name
        path.write_text('BeamDyn synthetic fixture')
        beam_hashes[name] = audit.digest(path)
    save_json(source / 'source-run.json', dict(case_dir=str(case.relative_to(tmp_path)),
              unloaded_reference=str(rest), input_hashes=input_hashes, blade_input_hashes=beam_hashes,
              wind_sha256=audit.digest(wind), controller_sha256=audit.digest(servo_folder / 'DISCON_T1.dll')))
    save_json(source / 'source-surfaces.json', {str(vtk / 'FFTest_WT1.Blade1Surface.0.vtp'):
              audit.digest(vtk / 'FFTest_WT1.Blade1Surface.0.vtp'),
              'vtk/Case.T1.Blade1Surface.04680.vtp': 'a' * 64})
    save_json(source / 'data.json', payload)
    save_json(source / 'telemetry.json', telemetry)
    save_json(source / 'blade-reference.json', dict(tip_local_m=[-1., 0., 63.]))
    save_json(source / 'deflection-t1.json', dict(times=times.tolist(), simulation=np.zeros((2, 3, 3)).tolist(),
              scalars=dict(ShftTilt=0., OverHang=0., TowerHt=100., Twr2Shft=0., **{'PreCone(1)': 0.}),
              sources={'T1': telemetry['sources']['T1']}))
    save_json(source / 'manifest.json', dict(schema='wfrl.farm-flex-review.v3', status='REVIEW_ONLY',
              structural_module='BeamDyn', includes_rigid_motion=True, tower_model='elastodyn-flexible',
              turbine_ids=['T1', 'T2', 'T3'], source_fps=40,
              segment=dict(start_s=117., end_s=117.025), files={}))
    refresh_inventory(source)
    return tmp_path, source, case, rest


def test_full_raw_values_match_and_missing_loaded_vtp_remains_an_evidence_gap(raw_source):
    root, source, case, rest = raw_source
    report = audit.verify_provenance(source, root)
    assert report['passed'], report['failures']
    assert report['status'] == 'SAME_RUN_VERIFIED_WITH_EVIDENCE_GAPS'
    assert report['saved_samples_checked'] == 6
    assert report['max_tip_component_difference_m'] < 1e-12
    assert report['raw_surface_hashes']['counts']['relative_loaded_missing'] == 1
    assert report['raw_surface_hashes']['counts']['absolute_reference_matched'] == 1
    assert len(report['reference_geometry']) == 4
    for check in report['numeric_checks'].values():
        assert check['saved_samples_checked'] == 2
        assert check['max_saved_motion_difference'] == 0.
        assert check['telemetry_max_differences'] == {'power': 0., 'torque': 0.}
        assert check['spotchecks'][0]['time_s'] == 117.
        assert check['spotchecks'][0]['power_mw'] == 1.
        assert check['spotchecks'][0]['torque_n_m'] == 2000.
    assert report['independent_complete_dataset']['status'] == 'NOT_SUPPLIED'
    assert report['space_time_convergence']['status'] == 'NOT_COMPLETED'
    assert all(item['repository_relative_path'] and Path(item['resolved_path']).is_file()
               for item in report['portable_copy_inventory'])


@pytest.mark.parametrize('kind', ['output', 'controller', 'beam', 'wind', 'reference'])
def test_retained_raw_hash_corruption_fails(raw_source, kind):
    root, source, case, rest = raw_source
    path = {'output': case / 'FarmInputs/Case.T2.out',
            'controller': case / '5MW_Baseline/ServoData/DISCON_T2.dll',
            'beam': case / '5MW_Baseline/BeamDyn.dat',
            'wind': case / 'FarmInputs/replay-wind.bts',
            'reference': rest / 'FarmInputs/vtk/FFTest_WT1.Blade1Surface.0.vtp'}[kind]
    path.write_bytes(path.read_bytes() + b' ')
    report = audit.verify_provenance(source, root)
    assert not report['passed']
    assert report['status'] == 'FAILED_INTEGRITY'
    assert any('hash mismatch' in message.lower() for message in report['failures'])


def test_original_unit_corruption_fails_even_with_updated_raw_hash(raw_source):
    root, source, case, _ = raw_source
    output = case / 'FarmInputs/Case.T1.out'
    output.write_text(output.read_text().replace('(kW)', '(W)'))
    update_output_hash(source, 'T1', output)
    report = audit.verify_provenance(source, root)
    assert not report['passed']
    assert 'Incorrect original unit for GenPwr' in report['numeric_checks']['T1']['error']


def test_all_timestamps_are_compared_not_only_spotchecks(raw_source):
    root, source, case, _ = raw_source
    output = case / 'FarmInputs/Case.T2.out'
    output.write_text(output.read_text().replace('117.025 ', '117.026 '))
    update_output_hash(source, 'T2', output)
    report = audit.verify_provenance(source, root)
    assert not report['passed']
    assert 'source timestamps' in report['numeric_checks']['T2']['error']


def test_terminal_transform_error_uses_existing_exporter_tolerance(raw_source):
    root, source, _, _ = raw_source
    path = source / 'geometry.npz'
    with np.load(path) as archive:
        geometry = {key: archive[key] for key in archive.files}
    geometry['transforms'][1, 1, 0, -1, 0, 3] += .006
    np.savez_compressed(path, **geometry)
    refresh_inventory(source)
    report = audit.verify_provenance(source, root)
    assert not report['passed']
    assert 'exceeds 0.005' in report['numeric_checks']['T2']['error']


def test_reference_geometry_difference_fails_after_surface_hash_is_updated(raw_source):
    root, source, _, rest = raw_source
    path = rest / 'FarmInputs/vtk/FFTest_WT1.Blade1Surface.0.vtp'
    points, _ = audit.read_surface(path)
    points[0, 0] += .001
    write_surface(path, points)
    hashes = json.loads((source / 'source-surfaces.json').read_text())
    hashes[str(path)] = audit.digest(path)
    save_json(source / 'source-surfaces.json', hashes)
    refresh_inventory(source)
    report = audit.verify_provenance(source, root)
    assert not report['passed']
    assert any('independent rest Blade1' in message for message in report['failures'])


def test_portable_root_preserves_recorded_paths_and_works_without_original_files(raw_source):
    root, source, case, rest = raw_source
    initial = audit.verify_provenance(source, root)
    relocated = root / 'portable-raw-evidence'
    for item in initial['portable_copy_inventory']:
        destination = relocated / item['repository_relative_path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item['resolved_path'], destination)
    shutil.rmtree(case)
    shutil.rmtree(rest)
    report = audit.verify_provenance(source, root, relocated)
    assert report['passed'], report['failures']
    assert report['saved_samples_checked'] == 6
    assert report['originating_case'] == str(case)
    assert report['resolved_originating_case'] == str(relocated / case.relative_to(root))
    assert all(Path(item['resolved_path']).is_relative_to(relocated)
               for item in report['portable_copy_inventory'])
    output = next(item for item in report['hash_checks'] if item['kind'] == 'originating_output')
    assert output['recorded_path'].startswith(str(case))
    assert output['resolved_path'].startswith(str(relocated))


def test_cli_writes_reviewable_json_and_markdown(raw_source):
    root, source, _, _ = raw_source
    output = root / 'provenance-report'
    assert audit.main(['--source', str(source), '--output', str(output), '--repo-root', str(root)]) == 0
    report = json.loads((output / 'provenance.json').read_text())
    assert report['saved_samples_checked'] == 6
    assert report['evidence_tool_sha256'] == audit.digest(Path(audit.__file__))
    text = (output / 'report.md').read_text()
    assert '同源来源核验' in text and '空间与时间收敛未完成' in text


def test_malformed_numeric_rows_are_rejected(tmp_path):
    path = tmp_path / 'broken.out'
    path.write_text('Time GenPwr\n(s) (kW)\n0 1\n.025\n')
    with pytest.raises(ValueError, match='incorrect column count'):
        audit.read_output(path)
