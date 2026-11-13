"""Boundary cases for saved-grid audit conclusions and source identities."""
from copy import deepcopy
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.lidar import audit_dual_beam_mount as mount
from scripts.lidar import compare_dual_beam_methods as comparison
from scripts.lidar import diagnose_dual_beam_tls_residuals as diagnostic
from scripts.lidar import export_dual_beam_validation_source as exporter
from scripts.lidar import verify_dual_beam_isolation as isolation
from scripts.lidar import verify_dual_beam_source_provenance as provenance
from scripts.lidar import compare_dual_beam_time_refinement as temporal
from test_dual_beam_isolation import fixture_source_and_rows
from test_dual_beam_source_provenance import raw_source
from test_dual_beam_validation_source import synthetic_run
from test_dual_beam_time_refinement import make_runs


@pytest.mark.parametrize('historical', [float('nan'), float('inf'), -float('inf')])
def test_historical_nonfinite_reference_cannot_pass_reproduction(historical):
    report = comparison.numerical_reproduction({'clearance': 1.}, {'clearance': historical})
    assert not report['passed']
    json.dumps(report, allow_nan=False)


def test_empty_isolation_inputs_cannot_report_pass():
    assert isolation.verify_packages([])['status'] == 'FAIL'


def test_empty_isolation_grid_cannot_report_pass():
    source, data, config = fixture_source_and_rows('hub-tls.v1')
    source.times = np.array([])
    data['samples'] = []
    with pytest.raises(ValueError, match='empty|nonempty|non-empty'):
        isolation.verify_turbine(source, data, config)


def test_mount_without_poses_has_unresolved_separation():
    source = SimpleNamespace(times=[], turbine_id='T1',
        blade_triangles=np.array([[0, 1, 2]]), blade_reference=np.zeros((3, 2, 3, 3)))
    report = mount.audit_source(source, np.array([[0., 0., 90.]]), .6, progress=False)
    assert report['mounts'][0]['poses_audited'] == 0
    assert report['mounts'][0]['saved_pose_envelope_separation'] == 'unresolved'


@pytest.mark.parametrize('name', ['source-run.json', 'telemetry.json', 'deflection-t1.json',
    'blade-reference.json', 'source-surfaces.json', 'reference-surfaces.npz',
    'geometry.npz', 'tower-motion.npz', 'data.json'])
def test_provenance_rejects_consumed_file_missing_from_inventory(raw_source, name):
    root, source, _, _ = raw_source
    path = source / 'manifest.json'
    manifest = json.loads(path.read_text())
    del manifest['files'][name]
    path.write_text(json.dumps(manifest))
    report = provenance.verify_provenance(source, root)
    assert not report['passed'], name
    assert any(name in failure for failure in report['failures'])


def test_export_raw_hash_identifies_the_bytes_parsed(synthetic_run, monkeypatch):
    run, reference, output = synthetic_run
    raw_path = run / 'FarmInputs/FFTest_WT1.out'
    original_hash = exporter.digest(raw_path)
    original_reader = exporter.read_output

    def changed_after_read(path):
        result = original_reader(path)
        raw_path.write_bytes(raw_path.read_bytes() + b'\n')
        return result

    monkeypatch.setattr(exporter, 'read_output', changed_after_read)
    exporter.export(run, reference, output)
    saved = json.loads((output / 'source-run.json').read_text())
    assert saved['raw_output']['sha256'] == original_hash
    assert saved['raw_output']['sha256'] != exporter.digest(raw_path)


def test_comparison_markdown_preserves_unknown_empty_coverage():
    empty = comparison.statistics([])
    cov = comparison.coverage([], .025)
    report = dict(metrics=dict(common=dict(baseline=empty, candidate=empty), worsened_sample_count=0),
        coverage=dict(pooled=dict(baseline=cov, candidate=cov)),
        passage_minimum_statistics=dict(baseline=empty, candidate=empty),
        integrity=dict(max_independent_scoring_residual_m=0.))
    text = comparison.markdown_report(report)
    assert '|0/0|unknown|' in text


def test_residual_markdown_handles_no_valid_pairs():
    report = dict(pooled=diagnostic.summarize_samples([]), diagnosed_pairs=0,
        valid_pair_counts={'T1': 0}, groups={}, tail_threshold_abs_error_m=None,
        tail_sample_count=0, candidate_worse_sample_count=None, worst_absolute_samples=[])
    text = diagnostic.markdown_report(report)
    assert '0 / 0' in text
    assert 'MAE —' in text


def test_provenance_parses_the_output_snapshot_that_was_hashed(raw_source, monkeypatch):
    root, source, case, _ = raw_source
    output = case / 'FarmInputs/Case.T1.out'
    original_reader = provenance.read_output
    def changed_before_parse(path):
        output.write_text(output.read_text().replace('(kW)', '(W)'))
        return original_reader(path)
    monkeypatch.setattr(provenance, 'read_output', changed_before_parse)
    report = provenance.verify_provenance(source, root)
    assert report['passed'], report['failures']
    assert report['numeric_checks']['T1']['telemetry_max_differences']['power'] == 0.


def test_provenance_failed_numeric_check_has_unknown_maximum(raw_source):
    root, source, case, _ = raw_source
    from test_dual_beam_source_provenance import update_output_hash
    for tid in ('T1', 'T2', 'T3'):
        output = case / f'FarmInputs/Case.{tid}.out'
        output.write_text(output.read_text().replace('(kW)', '(W)'))
        update_output_hash(source, tid, output)
    report = provenance.verify_provenance(source, root)
    assert not report['passed']
    assert report['max_tip_component_difference_m'] is None


def test_provenance_parses_the_npz_snapshot_that_was_hashed(raw_source, monkeypatch):
    root, source, _, _ = raw_source
    original_load = np.load
    calls = 0
    def changed_before_parse(path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            geometry_path = source / 'geometry.npz'
            with original_load(geometry_path) as archive:
                values = {name: archive[name] for name in archive.files}
            values['transforms'][1, 0, 0, -1, 0, 3] += .01
            np.savez_compressed(geometry_path, **values)
        return original_load(path, **kwargs)
    monkeypatch.setattr(provenance.np, 'load', changed_before_parse)
    report = provenance.verify_provenance(source, root)
    assert report['passed'], report['failures']
    assert report['max_tip_component_difference_m'] < 1e-12


def test_time_refinement_requires_actual_temporal_input(tmp_path):
    runs = make_runs(tmp_path)
    for run in runs:
        path = run / 'run-config.json'
        config = json.loads(path.read_text())
        config['inputs'] = [item for item in config['inputs'] if not item['path'].endswith('.fst')]
        path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='FST|fst|temporal input'):
        temporal.verify_run_inputs(*runs)


@pytest.mark.parametrize('corruption', ['missing', 'duplicate', 'wrong_error', 'wrong_validity'])
def test_time_refinement_rejects_corrupt_fixed_window_records(corruption):
    pair = dict(valid=True, blade_id=1, reason='valid', clearance_estimate=8.)
    row = dict(time_s=1., expected_blade_id=1, passage_id='p', reconstruction=pair,
        evaluation=dict(clearance_reference_m=7.), observations={'S1': {}}, s1_observation_state='unknown')
    method = dict(fresh_valid=True, reason=None, estimate_clearance_m=8., error_m=1.)
    window = dict(turbine_id='T1', time_s=1., expected_blade_id=1, passage_id='p',
        boundary_truncated=False, reference_clearance_m=7., reference_reason='valid',
        baseline=deepcopy(method), candidate=deepcopy(method))
    windows = [window]
    if corruption == 'missing':
        windows = []
    elif corruption == 'duplicate':
        windows.append(deepcopy(window))
    elif corruption == 'wrong_error':
        window['candidate']['error_m'] = 0.
    else:
        window['candidate']['fresh_valid'] = False
    package = dict(overlay=dict(results={'T1': dict(samples=[row], cumulative=[{'alarm': {}}])}))
    bundle = dict(report=dict(fixed_window_samples=windows),
        packages=dict(baseline=deepcopy(package), candidate=package))
    with pytest.raises(ValueError, match='window|Window'):
        temporal.grid_records(bundle)


@pytest.mark.parametrize('field', ['fixed_window_samples', 'complete_passage_minima', 'metrics',
    'coverage', 'passage_minimum_statistics'])
def test_time_refinement_recomputes_consumed_comparison_evidence(tmp_path, monkeypatch, field):
    packages = {}
    for name in temporal.METHODS:
        path = tmp_path / name; path.mkdir()
        (path / 'manifest.json').write_text('{}')
        packages[name] = dict(path=path, overlay=dict(manifest=dict(source_hashes={}, source_fps=40)))
    fields = ('fixed_window_samples', 'complete_passage_minima', 'metrics', 'coverage', 'passage_minimum_statistics')
    actual = {name: {'checked': 1.} for name in fields}
    report = dict(actual, schema='wfrl.dual-beam-method-comparison.v1',
        baseline_method='hub-axis.v1', candidate_method='hub-tls.v1', source_hashes={}, source_fps=40,
        integrity=dict(reader_validation_passed=True, raw_observations_and_s1_identical=True),
        baseline_package=str(packages['baseline']['path']), candidate_package=str(packages['candidate']['path']),
        package_manifest_hashes={name: temporal.digest(p['path'] / 'manifest.json') for name, p in packages.items()})
    report[field] = {'checked': 99.}
    path = tmp_path / 'comparison.json'; path.write_text(json.dumps(report))
    monkeypatch.setattr(temporal, 'load_verified', lambda path: packages[path.name])
    monkeypatch.setattr(comparison, 'compare_packages', lambda *args: actual)
    with pytest.raises(ValueError, match='comparison|Comparison'):
        temporal.load_comparison(path)


@pytest.mark.parametrize('channel,unit', [('GenPwr', '(W)'), ('GenTq', '(N-m)')])
def test_prebend_telemetry_rejects_incompatible_raw_units(synthetic_run, channel, unit):
    from scripts.lidar import export_prebend_sidecars as sidecars
    run, reference, output = synthetic_run
    manifest = exporter.export(run, reference, output)
    with np.load(output / 'geometry.npz') as archive:
        times, poses, transforms = (archive[name] for name in ('times', 'poses', 'transforms'))
    lines = (run / 'FarmInputs/FFTest_WT1.out').read_text().splitlines()[1:]
    lines[0] += ' ' + channel
    lines[1] += ' ' + unit
    lines[2:] = [line + ' 1000' for line in lines[2:]]
    (run / 'FarmInputs/Case.T1.out').write_text('\n'.join(['fixture'] * 6 + lines) + '\n')
    config = json.loads((run / 'probe-config.json').read_text())
    config['structural_source'] = {}
    report = dict(case_dir=str(run), unloaded_reference=str(reference), blade_config=config)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    original_manifest = deepcopy(manifest)
    with pytest.raises(ValueError, match='telemetry unit'):
        sidecars.supplement(output, report, manifest, times, poses, transforms)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert manifest == original_manifest


def test_observability_uses_the_hashed_reference_snapshot(tmp_path, monkeypatch):
    from scripts.lidar import audit_dual_beam_observability as observability
    from test_dual_beam_observability import test_loader_never_accesses_dynamic_archives_or_source_geometry
    with monkeypatch.context() as setup:
        test_loader_never_accesses_dynamic_archives_or_source_geometry(tmp_path, setup)
    reference = tmp_path / 'source/reference-surfaces.npz'
    original_load = np.load
    def changed_before_parse(path, **kwargs):
        with original_load(reference) as archive:
            values = {name: archive[name] for name in archive.files}
        values['blades'] += 1000.
        np.savez(reference, **values)
        return original_load(path, **kwargs)
    monkeypatch.setattr(observability.np, 'load', changed_before_parse)
    model, _ = observability.load_independent_model(tmp_path / 'source', tmp_path / 'calibration.json')
    assert np.max(abs(model.local_blades)) <= 63.000001


def test_export_reference_surface_uses_frozen_inventory_bytes(synthetic_run, monkeypatch):
    run, reference, output = synthetic_run
    path = reference / 'FarmInputs/vtk/FFTest_WT1.Blade1Surface.0.vtp'
    expected = exporter.digest(path)
    original = exporter.read_surface
    changed = False
    def changed_before_parse(surface):
        nonlocal changed
        if not changed:
            path.write_text(path.read_text().replace('0.1', '0.2', 1))
            changed = True
        return original(surface)
    monkeypatch.setattr(exporter, 'read_surface', changed_before_parse)
    manifest = exporter.export(run, reference, output)
    saved = json.loads((output / 'source-surfaces.json').read_text())
    assert saved['reference_surfaces'][str(path)] == expected
    assert exporter.digest(path) != expected
    assert manifest['max_component_error_m'] < 1e-8


@pytest.mark.parametrize('name,field', [('run-config.json', 'run_config_sha256'),
    ('probe-config.json', 'probe_config_sha256'), ('compressed-surfaces.json', 'compressed_inventory_sha256'),
    ('exit-status.json', 'exit_status_sha256'), ('solver.log', 'solver_log_sha256'),
    ('reference-inventory.json', 'reference_inventory_sha256')])
def test_export_metadata_hashes_identify_consumed_bytes(synthetic_run, monkeypatch, name, field):
    run, reference, output = synthetic_run
    path = (reference if name == 'reference-inventory.json' else run) / name
    expected = exporter.digest(path)
    original = exporter.read_surface
    changed = False
    def changed_before_parse(surface):
        nonlocal changed
        if not changed:
            path.write_bytes(path.read_bytes() + b'\n')
            changed = True
        return original(surface)
    monkeypatch.setattr(exporter, 'read_surface', changed_before_parse)
    exporter.export(run, reference, output)
    source_run = json.loads((output / 'source-run.json').read_text())
    assert source_run[field] == expected
    assert exporter.digest(path) != expected


def test_provenance_accepts_verified_source_file_symlinks(raw_source):
    root, source, _, _ = raw_source
    path = source / 'geometry.npz'
    retained = root / 'retained-geometry.npz'
    path.rename(retained)
    path.symlink_to(retained)
    report = provenance.verify_provenance(source, root)
    assert report['passed'], report['failures']


@pytest.mark.parametrize('name,key', [('InflowWind.dat', 'FileName_BTS'),
    ('FFTest_WT1.fst', 'ServoFile'), ('ServoT1.dat', 'DLL_FileName')])
def test_provenance_input_pointers_use_the_hashed_snapshot(raw_source, monkeypatch, name, key):
    root, source, case, _ = raw_source
    path = case / 'FarmInputs' / name
    original = provenance.input_value
    changed = False
    def changed_before_parse(stream, parameter):
        nonlocal changed
        if parameter == key and not changed:
            path.write_text('broken input\n')
            changed = True
        return original(stream, parameter)
    monkeypatch.setattr(provenance, 'input_value', changed_before_parse)
    report = provenance.verify_provenance(source, root)
    assert changed
    assert report['passed'], report['failures']


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_baseline_metrics_reject_nonfinite_valid_error(value):
    from scripts.lidar.audit_prebend_baseline import metrics
    rows = [dict(expected=True, passage_id='p1', truth_m=1.,
                 beams={'B2': dict(valid=True, estimate_m=value)})]
    with pytest.raises(ValueError, match='[Ff]inite'):
        metrics(rows)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_tower_validation_rejects_nonfinite_component_error(tmp_path, monkeypatch, value):
    from scripts.lidar import validate_tower_package as tower
    transform = np.column_stack((np.eye(3), np.zeros(3)))
    transforms = np.broadcast_to(transform, (1, 1, 3, 1, 3, 4))
    monkeypatch.setattr(tower, 'read_package', lambda _: ({}, [0.], transforms, [], []))
    monkeypatch.setattr(tower, 'read_comparison', lambda *args: dict(scalars={'TipRad': 63.},
        poses=[[0.] * 6], nacelle=[transform], simulation=np.zeros((1, 3, 3))))
    monkeypatch.setattr(tower, 'read_tower', lambda *args: {'heights': [0., 1.]} )
    monkeypatch.setattr(tower, 'rigid_frame', lambda *args: (np.zeros(3), np.eye(3), np.eye(3)))
    monkeypatch.setattr(tower, 'compare', lambda *args: {'error': [value, 0., 0.]})
    (tmp_path / 'data.json').write_text('{}')
    with pytest.raises(ValueError, match='[Ff]inite'):
        tower.validate(tmp_path, tmp_path / 'report.json')
    assert not (tmp_path / 'report.json').exists()


@pytest.mark.parametrize('changed_source', [False, True])
def test_scan_audit_cannot_pass_empty_or_changed_source(tmp_path, monkeypatch, changed_source):
    import importlib
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules, 'mathutils', SimpleNamespace(Vector=None))
    monkeypatch.setitem(sys.modules, 'mathutils.bvhtree', SimpleNamespace(BVHTree=None))
    audit = importlib.import_module('scripts.lidar.audit_scan_candidate')
    monkeypatch.setattr(audit, 'ROOT', tmp_path)
    budget = tmp_path / 'evidence/prebend-20260918'; budget.mkdir(parents=True)
    (budget / 'acceptance-budget.json').write_text(json.dumps(dict(E95_m=1., Emax_m=1.,
        Vmin_sample_fraction=.9, Vmin_passage_fraction=.9)))
    source = tmp_path / 'source'; source.mkdir()
    payload = {'T1': {'measurements': []}} if changed_source else {}
    data = source / 'data.json'; data.write_text(json.dumps(payload))
    (source / 'manifest.json').write_text(json.dumps(dict(turbine_ids=list(payload),
        files={'data.json': provenance.digest(data)})))
    np.savez(source / 'geometry.npz', times=[], transforms=[])
    np.savez(source / 'tower-motion.npz', nacelle=[], transforms=[], heights=[0.])
    np.savez(source / 'reference-surfaces.npz', blades=np.zeros((3, 19, 1, 3)),
        triangles=np.zeros((1, 3), dtype=int), tower=np.zeros((3, 3)), tower_triangles=np.zeros((1, 3), dtype=int))
    if changed_source:
        original = np.load
        def changed_after_hash(path, *args, **kwargs):
            data.write_text('{"changed": true}')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(audit.np, 'load', changed_after_hash)
        monkeypatch.setattr(audit, 'metrics', lambda _: dict(p95_m=0., max_abs_m=0.,
            valid_fraction=1., missed_passages=0, passages=1))
    output = tmp_path / 'report'
    audit.audit(source, output)
    report = json.loads((output / 'provenance.json').read_text())
    assert report['status'] == 'FAILED'
    assert report['source_unchanged'] is not changed_source


@pytest.mark.parametrize('kind', ['beam_curve', 'geometry_scalars'])
def test_export_solver_inputs_use_the_verified_snapshot(synthetic_run, monkeypatch, kind):
    run, reference, output = synthetic_run
    name = ('5MW_Baseline/' + exporter.BEAM_FILES[0] if kind == 'beam_curve' else
            'FarmInputs/NRELOffshrBsline5MW_Onshore_ElastoDyn_8mps.dat')
    path = run / name
    config_path = run / 'run-config.json'
    config = json.loads(config_path.read_text())
    config['inputs'] = [dict(path=name, sha256=exporter.digest(path), bytes=path.stat().st_size)]
    config_path.write_text(json.dumps(config))
    function = '_beam_curve' if kind == 'beam_curve' else '_input_values'
    original = getattr(exporter, function)
    calls = 0
    def changed_before_parse(stream):
        nonlocal calls
        calls += 1
        if calls == (1 if kind == 'beam_curve' else 3):
            path.write_text('broken input\n')
        return original(stream)
    monkeypatch.setattr(exporter, function, changed_before_parse)
    manifest = exporter.export(run, reference, output)
    assert manifest['max_component_error_m'] < 1e-8
    assert exporter.digest(path) != config['inputs'][0]['sha256']
