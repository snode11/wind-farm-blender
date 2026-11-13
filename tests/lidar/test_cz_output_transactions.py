"""Failed report/export writes preserve complete previous evidence."""
import importlib
import json
from pathlib import Path
import sys

import pytest

from scripts.lidar import postprocess_dual_beam as postprocess
from scripts.lidar import export_dual_beam_validation_source as exporter
from scripts.lidar import run_dual_beam_validation as runner
from scripts.lidar import verify_dual_beam_source_provenance as provenance
from test_dual_beam_source_provenance import raw_source
from test_dual_beam_validation_source import synthetic_run
from test_dual_beam_validation_run import inputs
from types import SimpleNamespace
import numpy as np


@pytest.mark.parametrize('module,function', [
    ('postprocess_dual_beam', 'write_json'),
    ('stress_dual_beam_pointing', 'write_json'),
    ('audit_dual_beam_observability', 'write_json'),
    ('export_dual_beam_validation_source', 'save_json'),
    ('run_dual_beam_validation', 'save_json'),
    ('package_prebend_probe', 'save_json'),
])
def test_json_write_failure_preserves_old_file_and_cleans_temporary(tmp_path, monkeypatch, module, function):
    target = tmp_path / 'report.json'
    target.write_text('{"complete": true}\n')
    old = target.read_bytes()
    original = Path.write_text
    attempted = []

    def disk_full(path, text, *args, **kwargs):
        attempted.append(path)
        original(path, text[:5], *args, **kwargs)
        raise OSError('injected disk full')

    monkeypatch.setattr(Path, 'write_text', disk_full)
    module = importlib.import_module('scripts.lidar.' + module)
    with pytest.raises(OSError, match='disk full'):
        getattr(module, function)(target, {'complete': False})
    assert target.read_bytes() == old
    assert list(tmp_path.iterdir()) == [target]
    assert all(not path.exists() for path in attempted if path != target)


def test_postprocess_missing_source_leaves_no_output(tmp_path, monkeypatch):
    output = tmp_path / 'result'
    calibration = postprocess.ROOT / 'configs/lidar/dual-beam-diagnostic.json'
    monkeypatch.setattr(sys, 'argv', ['postprocess', '--calibration', str(calibration),
        '--source', str(tmp_path / 'missing'), '--output', str(output)])
    with pytest.raises(FileNotFoundError):
        postprocess.main()
    assert not output.exists()


def test_provenance_report_pair_is_not_published_when_markdown_fails(raw_source, monkeypatch):
    root, source, _, _ = raw_source
    output = root / 'report'
    def fail(_):
        raise RuntimeError('injected markdown failure')
    monkeypatch.setattr(provenance, 'markdown_report', fail)
    with pytest.raises(RuntimeError, match='markdown failure'):
        provenance.main(['--source', str(source), '--repo-root', str(root), '--output', str(output)])
    assert not output.exists()


def test_export_npz_failure_leaves_no_partial_source(synthetic_run, monkeypatch):
    run, reference, output = synthetic_run
    original = exporter.np.savez_compressed
    calls = []
    def disk_full(path, **arrays):
        calls.append(path)
        if len(calls) == 2:
            Path(path).write_bytes(b'partial zip')
            raise OSError('injected npz disk full')
        return original(path, **arrays)
    monkeypatch.setattr(exporter.np, 'savez_compressed', disk_full)
    with pytest.raises(OSError, match='npz disk full'):
        exporter.export(run, reference, output)
    assert not output.exists()
    assert all(not Path(path).exists() for path in calls)


def test_prepare_copy_failure_leaves_no_prepared_run(tmp_path, inputs, monkeypatch):
    template, reference, executable = inputs
    output = tmp_path / 'run'
    original = runner.shutil.copy2
    calls = []
    def disk_full(source, destination):
        calls.append(destination)
        original(source, destination)
        if len(calls) == 2:
            raise OSError('injected copy failure')
    monkeypatch.setattr(runner.shutil, 'copy2', disk_full)
    with pytest.raises(OSError, match='copy failure'):
        runner.prepare(output, template=template, reference=reference, executable=executable)
    assert not output.exists()
    assert all(not Path(path).exists() for path in calls)


@pytest.mark.parametrize('name', ['compare_dual_beam_methods', 'diagnose_dual_beam_tls_residuals'])
def test_analysis_report_failure_publishes_no_directory(tmp_path, monkeypatch, name):
    module = importlib.import_module('scripts.lidar.' + name)
    package = tmp_path / 'package'
    package.mkdir()
    for file in ('manifest.json', 'calibration.json', 'results.json'):
        (package / file).write_text('{}')
    monkeypatch.setattr(module, 'load_verified', lambda _: {'path': package})
    def fail(_):
        raise RuntimeError('injected report failure')
    monkeypatch.setattr(module, 'markdown_report', fail)
    output = tmp_path / 'report'
    if name == 'compare_dual_beam_methods':
        monkeypatch.setattr(module, 'compare_packages', lambda *args: {})
        arguments = ['--baseline', str(package), '--candidate', str(package)]
    else:
        monkeypatch.setattr(module, 'diagnose_packages', lambda *args: {})
        arguments = ['--package', str(package)]
    with pytest.raises(RuntimeError, match='report failure'):
        module.main(arguments + ['--output', str(output)])
    assert not output.exists()


@pytest.mark.parametrize('name', ['audit_dual_beam_observability', 'stress_dual_beam_pointing'])
def test_declared_experiment_failure_leaves_no_partial_result(tmp_path, monkeypatch, name):
    module = importlib.import_module('scripts.lidar.' + name)
    output = tmp_path / 'report'
    def fail(*args):
        raise RuntimeError('injected experiment failure')
    if name == 'audit_dual_beam_observability':
        model = SimpleNamespace(scalars={'HubRad': 1.5}, tip_local=np.array([-1., 0., 63.]))
        monkeypatch.setattr(module, 'load_independent_model', lambda *args: (model, {}))
        monkeypatch.setattr(module, 'audit_case', fail)
        arguments = (tmp_path / 'source', tmp_path / 'calibration', output)
    else:
        package = tmp_path / 'package'; package.mkdir()
        (package / 'manifest.json').write_text('{}')
        config = json.loads((postprocess.ROOT / 'configs/lidar/dual-beam-diagnostic.json').read_text())
        verified = dict(path=package, sources={'T1': object()}, overlay=dict(config=config,
            manifest=dict(source_hashes={}, source_fps=40)))
        monkeypatch.setattr(module, 'load_verified', lambda _: verified)
        monkeypatch.setattr(module, 'freeze_windows', lambda *args: {'T1': []})
        monkeypatch.setattr(module, 'structural_reference_points', fail)
        arguments = (package, output)
    with pytest.raises(RuntimeError, match='experiment failure'):
        module.run(*arguments)
    assert not output.exists()


def test_archive_inventory_failure_rolls_back_unindexed_compression(tmp_path, monkeypatch):
    from test_dual_beam_validation_run import XML
    folder = tmp_path / 'FarmInputs/vtk'; folder.mkdir(parents=True)
    path = folder / 'FFTest_WT1.Blade1Surface.0.vtp'; path.write_bytes(XML)
    archiver = runner.SurfaceArchiver(tmp_path)
    archiver.write_inventory(status='RUNNING')
    inventory = tmp_path / 'compressed-surfaces.json'
    old = inventory.read_bytes()
    original = archiver.write_inventory
    def disk_full(**kwargs):
        raise OSError('injected inventory failure')
    monkeypatch.setattr(archiver, 'write_inventory', disk_full)
    archiver.scan()
    with pytest.raises(OSError, match='inventory failure'):
        archiver.scan()
    assert path.read_bytes() == XML
    assert inventory.read_bytes() == old
    assert not archiver.surfaces
    assert list(folder.iterdir()) == [path]
    monkeypatch.setattr(archiver, 'write_inventory', original)
    archiver.scan()
    assert len(archiver.surfaces) == 1 and not path.exists()


@pytest.mark.parametrize('name', ['audit_dual_beam_mount', 'search_dual_beam_mount'])
def test_exclusive_audit_serialization_failure_leaves_no_partial_file(tmp_path, monkeypatch, name):
    module = importlib.import_module('scripts.lidar.' + name)
    output = tmp_path / 'report.json'
    package = tmp_path / 'source'; package.mkdir()
    (package / 'manifest.json').write_text(json.dumps({'turbine_ids': ['T1'], 'source_fps': 40, 'segment': {}}))
    scalars = json.loads((postprocess.ROOT / 'blender_frontend/wfrl_blender/assets/nrel5mw_geometry.json').read_text())['scalars']
    monkeypatch.setattr(module, 'SourceGeometry', lambda *args: SimpleNamespace(source_hashes={'invalid': float('nan')}, scalars=scalars))
    arguments = ['audit', '--source', str(package), '--output', str(output)]
    if name == 'audit_dual_beam_mount':
        monkeypatch.setattr(module, 'audit_source', lambda *args: {'mounts': []})
        arguments += ['--calibration', str(postprocess.ROOT / 'configs/lidar/dual-beam-diagnostic.json')]
    else:
        monkeypatch.setattr(module, 'evaluate', lambda *args: ([], {'invalid': float('nan')}))
    monkeypatch.setattr(sys, 'argv', arguments)
    with pytest.raises(ValueError, match='JSON|range|nan'):
        module.main()
    assert not output.exists()


@pytest.mark.parametrize('name', ['export_farm_flex', 'export_prebend_probe'])
def test_source_export_failure_leaves_no_directory(tmp_path, name):
    module = importlib.import_module('scripts.lidar.' + name)
    output = tmp_path / 'output'
    if name == 'export_farm_flex':
        probe = tmp_path / 'probe.json'
        probe.write_text(json.dumps(dict(status='CONTROL_PROBE_COMPLETE', case_dir=str(tmp_path / 'missing'))))
        arguments = (probe, output)
    else:
        for label, unloaded in (('run', False), ('reference', True)):
            folder = tmp_path / label; folder.mkdir()
            (folder / 'probe-config.json').write_text(json.dumps(dict(structural_module='BeamDyn',
                reference_curve=[], unloaded=unloaded)))
        arguments = (tmp_path / 'run', tmp_path / 'reference', output)
    with pytest.raises((FileNotFoundError, ValueError)):
        module.export(*arguments)
    assert not output.exists()


def test_accuracy_missing_source_leaves_no_report(tmp_path, monkeypatch):
    from scripts.lidar import audit_dual_beam_accuracy as module
    package = tmp_path / 'package'; package.mkdir()
    (package / 'manifest.json').write_text('{}')
    output = tmp_path / 'report'
    monkeypatch.setattr(module, 'resolve_package', lambda _: (tmp_path / 'missing',
        dict(config={}, results={'T1': {}})))
    monkeypatch.setattr(sys, 'argv', ['accuracy', '--package', str(package), '--output', str(output)])
    with pytest.raises(FileNotFoundError):
        module.main()
    assert not output.exists()


def test_isolation_write_failure_preserves_existing_report(tmp_path, monkeypatch):
    from scripts.lidar import verify_dual_beam_isolation as module
    output = tmp_path / 'report.json'; output.write_text('{"old": true}\n')
    old = output.read_bytes()
    monkeypatch.setattr(module, 'verify_packages', lambda _: dict(status='FAIL', rows=0))
    monkeypatch.setattr(sys, 'argv', ['isolation', '--package', 'unused', '--output', str(output)])
    original = Path.write_text
    def disk_full(path, text, **kwargs):
        original(path, text[:5], **kwargs)
        raise OSError('injected write failure')
    monkeypatch.setattr(Path, 'write_text', disk_full)
    with pytest.raises(OSError, match='write failure'):
        module.main()
    assert output.read_bytes() == old


def test_temporal_report_failure_leaves_no_partial_directory(tmp_path, monkeypatch):
    from scripts.lidar import compare_dual_beam_time_refinement as module
    source_path = tmp_path / 'source'; source_path.mkdir()
    (source_path / 'source-surfaces.json').write_text(json.dumps({'reference_surfaces': {'rest': 'a' * 64}}))
    source = SimpleNamespace(path=source_path, manifest={})
    package = dict(sources={'T1': source}, overlay=dict(manifest=dict(segment=dict(start_s=0., end_s=1.)), results={}))
    report = dict(source_fps=40, complete_passage_minima=[], passage_minimum_statistics={}, metrics={}, coverage={})
    bundle = dict(packages={'baseline': package, 'candidate': package}, report=report, sha256='b' * 64)
    monkeypatch.setattr(module, 'verify_run_inputs', lambda *args: ([dict(evaluation_window_s=[0., 1.], fps=40)] * 2,
        dict(run_config_hashes={'coarse': 'c' * 64, 'fine': 'd' * 64})))
    monkeypatch.setattr(module, 'load_comparison', lambda *args: bundle)
    monkeypatch.setattr(module, 'verify_source_link', lambda *args: {})
    monkeypatch.setattr(module, 'grid_records', lambda *args: {})
    monkeypatch.setattr(module, 'compare_grids', lambda *args: {})
    def fail(_):
        raise RuntimeError('injected temporal markdown failure')
    monkeypatch.setattr(module, 'markdown_report', fail)
    output = tmp_path / 'report'
    args = [value for name in ('coarse-run', 'fine-run', 'coarse-comparison', 'fine-comparison')
            for value in ('--' + name, 'unused')]
    with pytest.raises(RuntimeError, match='temporal markdown failure'):
        module.main(args + ['--output', str(output)])
    assert not output.exists()


def test_prebend_sidecar_failure_preserves_existing_files_and_manifest(synthetic_run, monkeypatch):
    from copy import deepcopy
    from scripts.lidar import export_prebend_sidecars as sidecars
    run, reference, output = synthetic_run
    manifest = exporter.export(run, reference, output)
    with np.load(output / 'geometry.npz') as archive:
        times, poses, transforms = (archive[name] for name in ('times', 'poses', 'transforms'))
    (run / 'FarmInputs/Case.T1.out').write_bytes((run / 'FarmInputs/FFTest_WT1.out').read_bytes())
    config = json.loads((run / 'probe-config.json').read_text())
    config['structural_source'] = {}
    report = dict(case_dir=str(run), unloaded_reference=str(reference), blade_config=config)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    original_manifest = deepcopy(manifest)
    def failed_surface(_):
        raise ValueError('injected invalid reference surface')
    monkeypatch.setattr(sidecars, 'read_surface', failed_surface)
    with pytest.raises(ValueError, match='invalid reference surface'):
        sidecars.supplement(output, report, manifest, times, poses, transforms)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert manifest == original_manifest


def test_prebend_package_failure_leaves_no_output(tmp_path):
    from scripts.lidar import package_prebend_probe as packager
    source = tmp_path / 'source'; source.mkdir()
    np.savez(source / 'probe.npz', times=[0., 1.], poses=np.zeros((2, 6)))
    (source / 'manifest.json').write_text(json.dumps({'files': {}}))
    output = tmp_path / 'result'
    with pytest.raises(KeyError, match='transforms'):
        packager.package(source, output)
    assert not output.exists()


@pytest.mark.parametrize('module', ['audit_prebend_baseline', 'audit_prebend_package', 'audit_scan_candidate'])
def test_legacy_audit_missing_source_leaves_no_output(tmp_path, monkeypatch, module):
    if module == 'audit_scan_candidate':
        monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(Vector=None))
        monkeypatch.setitem(sys.modules, 'mathutils.bvhtree', SimpleNamespace(BVHTree=None))
    audit = importlib.import_module('scripts.lidar.' + module)
    output = tmp_path / 'report'
    with pytest.raises(FileNotFoundError):
        audit.audit(tmp_path / 'missing', output)
    assert not output.exists()


@pytest.mark.parametrize('module', ['audit_tower_run', 'validate_farm_run', 'compare_tower_numerics',
                                  'export_farm_flex', 'validate_tower_package'])
def test_legacy_report_write_failure_preserves_old_result(tmp_path, monkeypatch, module):
    audit = importlib.import_module('scripts.lidar.' + module)
    output = tmp_path / 'report.json'
    old = b'{"old": true}\n'
    output.write_bytes(old)
    probe = tmp_path / 'probe.json'; log = tmp_path / 'solver.log'
    farm = tmp_path / 'FarmInputs'; farm.mkdir()
    probe.write_text(json.dumps(dict(status='CONTROL_PROBE_COMPLETE', tower='flexible', case_dir=str(tmp_path))))
    log.write_text('FAST.Farm terminated normally.')
    if module in ('audit_tower_run', 'validate_farm_run'):
        times = np.linspace(117., 177., 2401)
        columns = {'Time': times, 'RotSpeed': np.full(len(times), 9.), 'YawPzn': np.zeros(len(times))}
        for name in ('YawBrTDxt', 'YawBrTDyt', 'YawBrTDzt', 'BlPitch1', 'BlPitch2', 'BlPitch3'):
            columns[name] = np.ones(len(times))
        for tid in ('T1', 'T2', 'T3'):
            (farm / f'Case.{tid}.out').write_text('fixture')
        monkeypatch.setattr(audit, 'read_fast_out', lambda _: columns)
        if module == 'audit_tower_run':
            call = lambda: audit.audit(probe, log, output)
        else:
            output.unlink()
            call = lambda: audit.validate(probe, log, output)
    elif module == 'compare_tower_numerics':
        monkeypatch.setattr(audit, 'compare_runs', lambda *args: dict(passed=True, turbines=[
            dict(turbine='T1', tower_vector_rms_m=0., blade_component_rms_max_m=0.)]))
        monkeypatch.setattr(sys, 'argv', ['compare', *['unused'] * 4, '--output', str(output)])
        call = audit.main
    elif module == 'export_farm_flex':
        output.rename(tmp_path / 'manifest.json'); output = tmp_path / 'manifest.json'
        old = json.dumps(dict(wind_sha256='wind', controller_sha256='controller')).encode()
        output.write_bytes(old)
        probe.write_text(json.dumps(dict(inputs=[dict(path='FarmInputs/random-gust.bts', sha256='wind')], wind_profile={})))
        log.write_text(json.dumps(dict(binary_sha256='controller')))
        call = lambda: audit.add_provenance(tmp_path, probe, log)
    else:
        transform = np.column_stack((np.eye(3), np.zeros(3)))
        transforms = np.broadcast_to(transform, (1, 1, 3, 1, 3, 4))
        monkeypatch.setattr(audit, 'read_package', lambda _: ({}, [0.], transforms, [], []))
        monkeypatch.setattr(audit, 'read_comparison', lambda *args: dict(scalars={'TipRad': 63.},
            poses=[[0.] * 6], nacelle=[transform], simulation=np.zeros((1, 3, 3))))
        monkeypatch.setattr(audit, 'read_tower', lambda *args: {'heights': [0., 1.]})
        monkeypatch.setattr(audit, 'rigid_frame', lambda *args: (np.zeros(3), np.eye(3), np.eye(3)))
        monkeypatch.setattr(audit, 'compare', lambda *args: {'error': [0., 0., 0.]})
        (tmp_path / 'data.json').write_text('{}')
        call = lambda: audit.validate(tmp_path, output)
    original = Path.write_text
    def disk_full(path, text, *args, **kwargs):
        original(path, text[:5], *args, **kwargs)
        raise OSError('injected report disk full')
    monkeypatch.setattr(Path, 'write_text', disk_full)
    with pytest.raises(OSError, match='report disk full'):
        call()
    if module == 'validate_farm_run':
        assert not output.exists()
    else:
        assert output.read_bytes() == old


def test_updated_files_rolls_back_partial_commit(tmp_path, monkeypatch):
    from scripts.lidar._atomic_output import updated_files
    (tmp_path / 'a.json').write_text('old a')
    (tmp_path / 'b.json').write_text('old b')
    original = Path.replace
    def fail_last(path, target):
        if path.name == 'c.json':
            raise OSError('injected publish failure')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', fail_last)
    with pytest.raises(OSError, match='publish failure'):
        with updated_files(tmp_path) as output:
            for name in ('a.json', 'b.json', 'c.json'):
                (output / name).write_text('new ' + name)
    assert {p.name: p.read_text() for p in tmp_path.iterdir()} == {'a.json': 'old a', 'b.json': 'old b'}


def test_window_report_pair_failure_preserves_old_results(tmp_path, monkeypatch):
    from scripts.lidar import audit_measurement_window as audit
    source = tmp_path / 'source'; source.mkdir()
    (source / 'manifest.json').write_text(json.dumps(dict(files={}, turbine_ids=[])))
    (source / 'source-run.json').write_text(json.dumps(dict(case_dir=str(tmp_path))))
    (source / 'source-surfaces.json').write_text('{}')
    np.savez(source / 'geometry.npz', times=[], transforms=[], poses=[])
    np.savez(source / 'tower-motion.npz', transforms=[], nacelle=[], heights=[])
    output = tmp_path / 'report'; output.mkdir()
    for name in ('window-summary.json', 'window-rows.json'):
        (output / name).write_text('old ' + name)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    original = Path.write_text
    def fail_rows(path, text, *args, **kwargs):
        if path.name == 'window-rows.json':
            original(path, text[:1], *args, **kwargs)
            raise OSError('injected rows failure')
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_rows)
    with pytest.raises(OSError, match='rows failure'):
        audit.audit(source, output)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


def test_physics_processing_pair_failure_preserves_prior_hashes(tmp_path, monkeypatch):
    from scripts.lidar import process_physics as process
    (tmp_path / 'run_config.json').write_text(json.dumps(dict(fps=40., startup_discard_s=1.,
        duration_s=1., span_refined=False)))
    (tmp_path / 'exit_status.json').write_text('{"exit_code": 0}')
    (tmp_path / 'solver.log').write_text('FAST.Farm terminated normally.')
    farm = tmp_path / 'FarmInputs'; vtk = farm / 'vtk'; vtk.mkdir(parents=True)
    (farm / 'Case.T1.out').write_text('Time\tAzimuth RotSpeed YawPzn BlPitch1\n(s) (deg) (rpm) (deg) (deg)\n0 0 9 0 0\n0.025 0 9 0 0\n')
    for blade in (1, 2, 3):
        for frame in (0, 1):
            (vtk / f'Case.T1.Blade{blade}Surface.{frame}.vtp').write_text('unused surface')
    prior = tmp_path / 'surface_hashes.json'; prior.write_text('old hashes')
    original = Path.write_text
    def fail_processed(path, text, *args, **kwargs):
        if path.name == 'processed.json':
            original(path, text[:1], *args, **kwargs)
            raise OSError('injected processed failure')
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(Path, 'write_text', fail_processed)
    with pytest.raises(OSError, match='processed failure'):
        process.process(tmp_path)
    assert prior.read_text() == 'old hashes'
    assert not (tmp_path / 'processed.json').exists()


def test_physics_validation_write_failure_leaves_no_partial_report(tmp_path, monkeypatch):
    from scripts.lidar import validate_physics as validation
    from wfrl.lidar import sampling
    paths = []
    for name, refined, dt, fps in [('base', False, .01, 40), ('space', True, .01, 40), ('time', True, .005, 80)]:
        path = tmp_path / name; path.mkdir(); paths.append(str(path))
        (path / 'run_config.json').write_text(json.dumps(dict(wind_mps=8., duration_s=1.,
            startup_discard_s=0., span_refined=refined, dt_s=dt, fps=fps)))
        (path / 'exit_status.json').write_text('{"exit_code": 0}')
        (path / 'solver.log').write_text('FAST.Farm terminated normally.')
        (path / 'processed.json').write_text('{}')
    monkeypatch.setattr(validation, 'analytic_evidence', lambda: {})
    monkeypatch.setattr(validation, 'compare', lambda *args: dict(max_truth_difference_m=0.))
    monkeypatch.setattr(sampling, 'compare_grids', lambda *args: {})
    output = tmp_path / 'report.json'
    monkeypatch.setattr(sys, 'argv', ['validate', *paths, str(output)])
    original = Path.write_text
    def disk_full(path, text, *args, **kwargs):
        original(path, text[:1], *args, **kwargs)
        raise OSError('injected physics disk full')
    monkeypatch.setattr(Path, 'write_text', disk_full)
    with pytest.raises(OSError, match='physics disk full'):
        validation.main()
    assert not output.exists()
