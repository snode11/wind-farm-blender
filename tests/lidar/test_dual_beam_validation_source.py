"""Synthetic mechanics fixtures, never physical validation evidence."""
import hashlib
import json
import lzma
from pathlib import Path
import shutil

import numpy as np
import pytest

from scripts.lidar import export_dual_beam_validation_source as exporter
from wfrl.camera_video.data import SourceGeometry, blade_root_frame


@pytest.mark.parametrize('fps', [40, 80])
def test_ascii_clock_recovers_actual_declared_grid(fps):
    exact = np.arange(fps+1)/fps
    rounded = np.array([float(f'{value:.4f}') for value in exact])
    recovered, audit = exporter.exact_output_grid({'Time': rounded}, fps, 1.)
    assert np.array_equal(recovered, exact)
    assert audit['max_ascii_time_error_s'] <= 5.1e-5
    assert audit['raw_sample_count'] == fps+1


@pytest.mark.parametrize('corruption', ['missing', 'duplicate', 'wrong_fps', 'shifted'])
def test_clock_rejects_missing_rows_and_wrong_sampling(corruption):
    times = np.arange(81)/80
    if corruption == 'missing':
        times = np.delete(times, 12)
    elif corruption == 'duplicate':
        times[12] = times[11]
    elif corruption == 'shifted':
        times += .001
    fps = 40 if corruption == 'wrong_fps' else 80
    with pytest.raises(ValueError):
        exporter.exact_output_grid({'Time': times}, fps, 1.)


def write_surface(path, points, triangles):
    path.parent.mkdir(parents=True, exist_ok=True)
    numbers = ' '.join(f'{v:.14g}' for v in np.asarray(points).ravel())
    connectivity = ' '.join(str(v) for v in np.asarray(triangles).ravel())
    offsets = ' '.join(str(v) for v in np.arange(1, len(triangles)+1)*3)
    path.write_text('<VTKFile><PolyData><Piece><Points><DataArray>'+numbers+
        '</DataArray></Points><Polys><DataArray Name="connectivity">'+connectivity+
        '</DataArray><DataArray Name="offsets">'+offsets+
        '</DataArray></Polys></Piece></PolyData></VTKFile>')


def compressed_entry(run, path):
    raw = path.read_bytes()
    data = lzma.compress(raw)
    compressed = path.with_suffix('.vtp.xz')
    compressed.write_bytes(data)
    return dict(path=str(path.relative_to(run)), compressed_path=str(compressed.relative_to(run)),
                sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw),
                compressed_sha256=hashlib.sha256(data).hexdigest(), compressed_bytes=len(data))


@pytest.fixture
def synthetic_run(tmp_path):
    run, reference = tmp_path/'run', tmp_path/'rest'
    scalars = json.loads((exporter.ROOT/'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json').read_text())['scalars']
    curve = np.column_stack((-(np.linspace(0., 61.5, 49)/61.5)**2,
                             np.zeros(49), np.linspace(0., 61.5, 49), np.zeros(49)))
    for folder, unloaded in ((run, False), (reference, True)):
        (folder/'FarmInputs/vtk').mkdir(parents=True)
        (folder/'5MW_Baseline').mkdir()
        exporter.save_json(folder/'probe-config.json', dict(structural_module='BeamDyn', unloaded=unloaded,
            model='Synthetic NREL 5MW derivative fixture', reference_curve=curve.tolist()))
        (folder/'5MW_Baseline'/exporter.BEAM_FILES[0]).write_text('kp_xr kp_yr kp_zr initial_twist\n(m) (m) (m) (deg)\n'+
            '\n'.join(' '.join(f'{v:.14g}' for v in row) for row in curve))
        (folder/'5MW_Baseline'/exporter.BEAM_FILES[1]).write_text('Synthetic matching material file\n')
        (folder/'FarmInputs/FFTest_WT1.fst').write_text('0 Gravity\n0 CompInflow\n')
        (folder/'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat').write_text('0 RotSpeed\n'+
            '\n'.join(f'{value} {key}' for key, value in scalars.items() if isinstance(value, (int, float))))
    exporter.save_json(run/'run-config.json', dict(status='SOLVER_COMPLETE', duration_s=.05, fps=80, dt_s=.003125, wind_mps=12., rpm=0.,
        pitch_deg=0., azimuth_deg=0., evaluation_window_s=[0., .05], reference_path=str(reference)))
    exporter.save_json(run/'exit-status.json', dict(status='COMPLETE', exit_code=0))
    identity = np.column_stack((np.eye(3), np.zeros(3)))
    ring = np.array([[0., 0., 0.], [.1, 0., 0.], [.1, .1, 0.], [0., .1, 0.]])
    span = np.linspace(1.5, 63., 19)
    local = np.array([ring+[-((z-1.5)/61.5)**2, 0., z] for z in span])
    triangles = np.array([[s*4, s*4+1, s*4+2] for s in range(19)] +
                         [[s*4, s*4+2, s*4+3] for s in range(19)])
    surfaces = {}
    for blade in (1, 2, 3):
        hub, axes = blade_root_frame(scalars, [0.]*6, blade, identity)
        surfaces[f'Blade{blade}'] = ((hub+local@axes.T).reshape(-1, 3), triangles)
    support_triangles = np.array([[0, 1, 2], [0, 2, 3]])
    surfaces['Nacelle'] = (ring+[0, 0, 87.6], support_triangles)
    tower_ring = np.array([[-1., -1., 0.], [1., -1., 0.], [1., 1., 0.], [-1., 1., 0.]])
    surfaces['Tower'] = (np.concatenate((tower_ring, tower_ring+[0, 0, 87.6])),
                         np.array([[0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                                   [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]))
    entries = []
    for kind, (points, topology) in surfaces.items():
        write_surface(reference/f'FarmInputs/vtk/FFTest_WT1.{kind}Surface.0.vtp', points, topology)
        for frame in range(5):
            path = run/f'FarmInputs/vtk/FFTest_WT1.{kind}Surface.{frame:05d}.vtp'
            write_surface(path, points, topology)
            entries.append(compressed_entry(run, path))
            path.unlink()
    exporter.save_json(run/'compressed-surfaces.json', {'surfaces': entries})
    tip_channels = [f'B{blade}TipTD{axis}r' for blade in (1, 2, 3) for axis in 'xyz']
    names = ['Time', 'YawPzn', 'Azimuth', 'RotSpeed', 'BlPitch1', 'BlPitch2', 'BlPitch3', *tip_channels]
    units = ['(s)', '(deg)', '(deg)', '(rpm)', '(deg)', '(deg)', '(deg)', *['(m)']*9]
    lines = ['Synthetic fixture', ' '.join(names), ' '.join(units)]
    lines += [' '.join([f'{frame/80:.4f}', *['0']*(len(names)-1)]) for frame in range(5)]
    (run/'FarmInputs/FFTest_WT1.out').write_text('\n'.join(lines)+'\n')
    (run/'solver.log').write_text('Synthetic fixture only\nOpenFAST terminated normally.\n')
    inventory = {str(path.relative_to(reference)): dict(sha256=exporter.digest(path), bytes=path.stat().st_size)
                 for path in reference.rglob('*') if path.is_file()}
    exporter.save_json(reference/'reference-inventory.json', inventory)
    exporter.save_json(tmp_path/'protocol-declaration.json', dict(before_solver_execution=True,
        reference_inventory_sha256=exporter.digest(reference/'reference-inventory.json')))
    config = json.loads((run/'run-config.json').read_text())
    config['reference_config_sha256'] = exporter.digest(reference/'probe-config.json')
    exporter.save_json(run/'run-config.json', config)
    return run, reference, tmp_path/'source'


def test_xz_only_export_contract_works_with_actual_source_reader(synthetic_run):
    run, reference, output = synthetic_run
    manifest = exporter.export(run, reference, output, start=.0125, end=.05)
    source = SourceGeometry(output)
    assert np.array_equal(source.times, np.arange(1, 5)/80)
    assert source.transforms.shape == (4, 3, 19, 3, 4)
    assert manifest['source_fps'] == 80 and manifest['interface_only'] is False
    assert source.measurements == []
    assert manifest['max_component_error_m'] < 1e-8
    inventory = json.loads((output/'source-surfaces.json').read_text())
    assert len(inventory['surfaces']) == 20
    assert all('compressed_sha256' in row and 'bytes' in row for row in inventory['surfaces'])
    assert set(manifest['files']) == {'geometry.npz', 'tower-motion.npz', 'reference-surfaces.npz',
        'blade-reference.json', 'deflection-t1.json', 'telemetry.json', 'data.json',
        'source-surfaces.json', 'source-run.json', 'interface-audit.json'}
    assert manifest['run_config_sha256'] == exporter.digest(run/'run-config.json')


@pytest.mark.parametrize('which', ['compressed_sha256', 'sha256'])
def test_compressed_and_original_hashes_are_both_required(synthetic_run, which):
    run, _, _ = synthetic_run
    path = run/'compressed-surfaces.json'
    contents = json.loads(path.read_text())
    entry = next(entry for entry in contents['surfaces'] if '.Blade1Surface.00000.' in entry['path'])
    entry[which] = '0'*64
    exporter.save_json(path, contents)
    with pytest.raises(ValueError, match='integrity mismatch'):
        exporter.SurfaceInventory(run).surface('Blade1', 0)


def test_missing_actual_frame_is_rejected(synthetic_run):
    run, _, _ = synthetic_run
    path = run/'compressed-surfaces.json'
    contents = json.loads(path.read_text())
    contents['surfaces'] = [entry for entry in contents['surfaces'] if '.Blade1Surface.00000.' not in entry['path']]
    exporter.save_json(path, contents)
    with pytest.raises(ValueError, match='missing actual'):
        exporter.SurfaceInventory(run).surface('Blade1', 0)


def test_missing_raw_output_is_rejected(synthetic_run):
    run, reference, output = synthetic_run
    (run/'FarmInputs/FFTest_WT1.out').unlink()
    with pytest.raises(FileNotFoundError):
        exporter.export(run, reference, output)
    assert not output.exists()


def test_mismatched_reference_material_is_rejected(synthetic_run):
    run, reference, output = synthetic_run
    (reference/'5MW_Baseline'/exporter.BEAM_FILES[1]).write_text('Different structure\n')
    with pytest.raises(ValueError, match='reference file integrity mismatch'):
        exporter.export(run, reference, output)
    assert not output.exists()


def test_loaded_reference_is_rejected(synthetic_run):
    run, reference, output = synthetic_run
    path = reference/'probe-config.json'
    contents = json.loads(path.read_text()); contents['unloaded'] = False
    exporter.save_json(path, contents)
    with pytest.raises(ValueError, match='reference config differs'):
        exporter.export(run, reference, output)


def test_incomplete_solver_cannot_emit_complete_source(synthetic_run):
    run, reference, output = synthetic_run
    exporter.save_json(run/'exit-status.json', dict(status='FAILED', exit_code=1))
    with pytest.raises(ValueError, match='complete runner execution'):
        exporter.export(run, reference, output)
    assert not output.exists()


def test_raw_displacement_units_are_not_silently_rescaled(synthetic_run):
    run, reference, output = synthetic_run
    path = run/'FarmInputs/FFTest_WT1.out'
    path.write_text(path.read_text().replace('(m)', '(mm)', 1))
    with pytest.raises(ValueError, match='unexpected displacement unit'):
        exporter.export(run, reference, output)
    assert not output.exists()


def test_relocated_run_and_reference_export_from_immutable_original_declaration(synthetic_run):
    run, reference, output = synthetic_run
    relocated = run.parent/'relocated'
    relocated.mkdir()
    new_run, new_reference = relocated/'run', relocated/'rest'
    shutil.copytree(run, new_run); shutil.copytree(reference, new_reference)
    shutil.copy2(run.parent/'protocol-declaration.json', relocated/'protocol-declaration.json')
    original_config_hash = exporter.digest(run/'run-config.json')
    shutil.rmtree(run); shutil.rmtree(reference)
    new_output = relocated/'source'
    manifest = exporter.export(new_run, new_reference, new_output, start=.0125, end=.05)
    assert manifest['run_config_sha256'] == original_config_hash
    assert (new_output/manifest['run_package']).resolve() == new_run.resolve()
    assert (new_output/manifest['reference_package']).resolve() == new_reference.resolve()
    source = SourceGeometry(new_output)
    assert np.array_equal(source.times, np.arange(1, 5)/80)
    provenance = json.loads((new_output/'source-run.json').read_text())
    assert (new_output/provenance['raw_output']['path']).resolve().is_file()


def test_reference_surface_change_cannot_refresh_frozen_inventory(synthetic_run):
    run, reference, output = synthetic_run
    path = reference/'FarmInputs/vtk/FFTest_WT1.Blade1Surface.0.vtp'
    path.write_text(path.read_text().replace('0.1', '0.2', 1)+'\n')
    with pytest.raises(ValueError, match='reference file integrity mismatch'):
        exporter.export(run, reference, output)
    inventory_path = reference/'reference-inventory.json'
    inventory = json.loads(inventory_path.read_text())
    inventory[str(path.relative_to(reference))] = dict(sha256=exporter.digest(path), bytes=path.stat().st_size)
    exporter.save_json(inventory_path, inventory)
    with pytest.raises(ValueError, match='pre-solver hash'):
        exporter.export(run, reference, output)


def test_nacelle_channel_check_uses_tower_top_not_shaft_centre(synthetic_run):
    run, reference, output = synthetic_run
    angle = .01
    rotation = np.array([[np.cos(angle), 0., np.sin(angle)], [0., 1., 0.],
                         [-np.sin(angle), 0., np.cos(angle)]])
    inventory = json.loads((run/'compressed-surfaces.json').read_text())
    for entry in inventory['surfaces']:
        kind = Path(entry['path']).name.split('.')[1].removesuffix('Surface')
        original = reference/f'FarmInputs/vtk/FFTest_WT1.{kind}Surface.0.vtp'
        points, triangles = exporter.read_surface(original)
        plain = run/entry['path']
        write_surface(plain, points@rotation.T, triangles)
        entry.update(compressed_entry(run, plain)); plain.unlink()
    exporter.save_json(run/'compressed-surfaces.json', inventory)
    path = run/'FarmInputs/FFTest_WT1.out'
    lines = path.read_text().splitlines()
    lines[1] += ' YawBrTDxt YawBrTDyt YawBrTDzt'
    lines[2] += ' (m) (m) (m)'
    top = np.array([0., 0., 87.6])
    displacement = rotation@top-top
    for i in range(3, len(lines)):
        lines[i] += ' '+' '.join(str(value) for value in displacement)
    path.write_text('\n'.join(lines)+'\n')
    manifest = exporter.export(run, reference, output)
    report = json.loads((output/'interface-audit.json').read_text())
    assert report['max_nacelle_channel_error_m'] < 1e-8
    assert manifest['max_component_error_m'] < 1e-8
