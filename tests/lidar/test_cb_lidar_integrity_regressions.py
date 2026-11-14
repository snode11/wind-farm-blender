"""Contract and failure-path checks using synthetic geometry and temporary packages."""
from io import BytesIO
import json
from pathlib import Path
import sys

import numpy as np
import pytest

import test_dual_beam as pair_fixtures
import test_dual_beam_portable as portable_fixtures
import test_dual_beam_replay as dual_fixtures
import test_moving_tower as tower_fixtures
import test_replay as legacy_fixtures
from wfrl.lidar import dual_beam_package, sampling
from wfrl.lidar.dual_beam_replay import DualBeamReader, digest, precompute, resolve_package
from wfrl.lidar.evidence import validate_numerical_evidence
from wfrl.lidar.moving_tower import horizontal_clearance, separated_from_tower
from wfrl.lidar.physics import collision_excluded, first_hit, read_surface, tip_reference, truth_clearance
from wfrl.lidar.replay import ReplayPackage, _validate, publish_package


def grid(times=(0., 1.)):
    return dict(motion=[{'time_s': t} for t in times], measurements=[dict(
        time_s=.5, expected=True, passage_id='p1',
        beams={'B2': dict(valid=True, error_m=.1)})])


def sampling_cli(tmp_path, monkeypatch):
    for name, fps in (('base', 10), ('refined', 20)):
        run = tmp_path / name
        run.mkdir()
        (run / 'processed.json').write_text(json.dumps(grid()), encoding='utf-8')
        (run / 'run_config.json').write_text(json.dumps(dict(fps=fps)), encoding='utf-8')
    output = tmp_path / 'comparison.json'
    monkeypatch.setattr(sys, 'argv', ['sampling', str(tmp_path / 'base'),
                                     str(tmp_path / 'refined'), str(output)])
    return output


@pytest.mark.parametrize('existing', [False, True])
def test_sampling_write_failure_preserves_destination_and_cleans_staging(tmp_path, monkeypatch, existing):
    output = sampling_cli(tmp_path, monkeypatch)
    original = b'{"complete": true}\n'
    if existing:
        output.write_bytes(original)
    before = set(tmp_path.iterdir())

    def fail(path, *args, **kwargs):
        path.write_bytes(b'{"partial":')
        raise OSError('injected short write')

    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(OSError, match='short write'):
        sampling.main()
    assert output.read_bytes() == original if existing else not output.exists()
    assert set(tmp_path.iterdir()) == before


def test_sampling_success_replaces_complete_json(tmp_path, monkeypatch):
    output = sampling_cli(tmp_path, monkeypatch)
    output.write_text('old result', encoding='utf-8')
    sampling.main()
    assert json.loads(output.read_text())['verdict'] == 'NUMERICAL_COMPARISON_ONLY'
    assert {p.name for p in tmp_path.iterdir()} == {'base', 'refined', 'comparison.json'}


@pytest.mark.parametrize('stage', ['copy', 'manifest', 'validate'])
def test_portable_failure_never_publishes_partial_directory(tmp_path, monkeypatch, stage):
    package, source = portable_fixtures.fixture_package(tmp_path)
    originals = {p: p.read_bytes() for parent in (package, source) for p in parent.iterdir()}
    output = tmp_path / 'portable'
    resolve = dual_beam_package.resolve_package

    def fail(*args, **kwargs):
        raise OSError('injected portable failure')

    with monkeypatch.context() as fault:
        if stage == 'copy':
            fault.setattr(dual_beam_package.shutil, 'copy2', fail)
        elif stage == 'manifest':
            fault.setattr(Path, 'write_text', fail)
        else:
            def resolve_with_failure(path):
                return resolve(path) if Path(path) == package else fail()
            fault.setattr(dual_beam_package, 'resolve_package', resolve_with_failure)
        with pytest.raises(OSError, match='portable failure'):
            dual_beam_package.portable_copy(package, output)
    assert not output.exists()
    assert set(tmp_path.iterdir()) == {package, source}
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert dual_beam_package.portable_copy(package, output) == output


@pytest.mark.parametrize('base_fps,refined_fps', [(float('nan'), 20), (10, float('inf')),
                                                (True, 20), (10, float('nan'))])
def test_sampling_rejects_nonfinite_or_boolean_rates(base_fps, refined_fps):
    with pytest.raises(ValueError):
        sampling.compare_grids(grid(), grid(), base_fps, refined_fps)


@pytest.mark.parametrize('times', [(0., .7, .3, 1.), (0., .5, .5, 1.),
                                   (0., float('nan'), 1.)])
def test_sampling_rejects_invalid_motion_grid(times):
    with pytest.raises(ValueError):
        sampling.compare_grids(grid(times), grid(), 10, 20)


def test_sampling_rejects_measurement_outside_compared_window():
    data = grid()
    data['measurements'][0]['time_s'] = 2.
    with pytest.raises(ValueError):
        sampling.compare_grids(data, grid(), 10, 20)


@pytest.mark.parametrize('error', [float('nan'), float('inf'), True])
def test_sampling_valid_errors_must_be_finite_numbers(error):
    data = grid()
    data['measurements'][0]['beams']['B2']['error_m'] = error
    with pytest.raises(ValueError):
        sampling.compare_grids(data, grid(), 10, 20)


@pytest.mark.parametrize('mutation', ['count', 'missed', 'duration', 'time', 'comparison'])
def test_numerical_evidence_rejects_inconsistent_passage_details(mutation):
    evidence = legacy_fixtures.numerical_evidence()
    passage = evidence['sampling']['base']['passages']['p1']
    if mutation == 'count':
        passage['valid_samples'] = 0
    elif mutation == 'missed':
        passage['missed'] = True
    elif mutation == 'duration':
        passage['valid_duration_s'] = 123.
    elif mutation == 'time':
        passage['first_valid_time_s'] = -1.
    else:
        evidence['sampling']['passage_comparison']['p1']['validity_class_changed'] = True
    with pytest.raises(ValueError, match='passage'):
        validate_numerical_evidence(evidence, evidence['run_id'])


def surface_xml(connectivity='0 1 2 3', offsets='4', counts=''):
    return BytesIO(('<VTKFile><PolyData><Piece ' + counts + '><Points><DataArray>'
        '0 0 0  1 0 0  1 1 0  0 1 0</DataArray></Points><Polys>'
        '<DataArray Name="connectivity">' + connectivity + '</DataArray>'
        '<DataArray Name="offsets">' + offsets + '</DataArray>'
        '</Polys></Piece></PolyData></VTKFile>').encode())


@pytest.mark.parametrize('connectivity,offsets,counts', [
    ('0 1 2 3 0 1 2 3', '4', ''), ('0 1 2', '4', ''),
    ('0 1 2 3', '3 2 4', ''), ('0 1 2 -1', '4', ''),
    ('0 1 2 4', '4', ''), ('0 1 2 3', '4', 'NumberOfPoints="5"'),
    ('0 1 2 3', '4', 'NumberOfPolys="2"'), ('', '', ''),
])
def test_surface_rejects_truncated_or_inconsistent_topology(connectivity, offsets, counts):
    with pytest.raises(ValueError):
        read_surface(surface_xml(connectivity, offsets, counts))


def test_surface_quad_triangulation_preserves_vertex_order():
    points, triangles = read_surface(surface_xml(counts='NumberOfPoints="4" NumberOfPolys="1"'))
    assert points.shape == (4, 3)
    np.testing.assert_array_equal(triangles, [[0, 1, 2], [0, 2, 3]])


@pytest.mark.parametrize('target', ['origin', 'points', 'direction_scale'])
def test_first_hit_rejects_invalid_geometry_instead_of_missing_return(target):
    origin, direction = np.zeros(3), np.array([0., 0., 1.])
    points = np.array([[-1., -1., 2.], [1., -1., 2.], [0., 1., 2.]])
    if target == 'origin':
        origin[0] = float('nan')
    elif target == 'points':
        points[0, 0] = float('nan')
    else:
        direction[2] += 1e-6
    with pytest.raises(ValueError):
        first_hit(origin, direction, points, np.array([[0, 1, 2]]))


@pytest.mark.parametrize('value', [float('nan'), float('inf')])
@pytest.mark.parametrize('model', ['rigid', 'moving'])
def test_clearance_rejects_nonfinite_horizontal_coordinates(value, model):
    tip = [value, 0., 5.]
    points, triangles = tower_fixtures.prism()
    with pytest.raises(ValueError):
        if model == 'rigid':
            truth_clearance(tip)
        else:
            horizontal_clearance(tip, points, triangles)


def test_empty_tip_surface_has_no_numeric_reference():
    with pytest.raises(ValueError):
        tip_reference(np.empty((0, 3)), 19)


@pytest.mark.parametrize('target', ['blade', 'tower'])
def test_nonfinite_surfaces_cannot_certify_tower_separation(target):
    tower, tower_triangles = tower_fixtures.prism()
    blade = np.array([[-5., 0., 4.], [-5., 1., 6.], [-4., 0., 5.]])
    (blade if target == 'blade' else tower)[0, 2] = float('nan')
    assert not separated_from_tower(blade, np.array([[0, 1, 2]]), tower, tower_triangles)


@pytest.mark.parametrize('target', ['blade', 'tower'])
def test_empty_surfaces_cannot_certify_tower_separation(target):
    tower, tower_triangles = tower_fixtures.prism()
    blade = np.array([[-5., 0., 4.], [-5., 1., 6.], [-4., 0., 5.]])
    blade_triangles = np.array([[0, 1, 2]])
    if target == 'blade':
        blade_triangles = np.empty((0, 3), dtype=int)
    else:
        tower_triangles = np.empty((0, 3), dtype=int)
    assert not separated_from_tower(blade, blade_triangles, tower, tower_triangles)


def test_empty_blade_surface_cannot_certify_rigid_tower_separation():
    assert not collision_excluded(np.empty((0, 3)), np.empty((0, 3), dtype=int))


def test_horizontal_section_shared_ring_keeps_penetration_sign():
    points, triangles = tower_fixtures.prism()
    lower, upper = points.copy(), points.copy()
    lower[4:, 2] = 5.
    upper[:4, 2] = 5.
    points = np.vstack((lower, upper))
    triangles = np.vstack((triangles, triangles + 8))
    for height in (4.9, 5., 5.1):
        distance, wall = horizontal_clearance([0., 0., height], points, triangles)
        assert distance == pytest.approx(-1.)
        assert wall[2] == pytest.approx(height)


def refresh_dual_prefix(overlay):
    result = overlay['results']['T1']
    result['cumulative'], result['events'] = precompute(result['samples'], overlay['config'])


@pytest.mark.parametrize('target,value', [('pair_valid', 1), ('observed', 1), ('beam_valid', 1),
                                         ('blade_id', True), ('blade_id', 4)])
def test_dual_reader_rejects_false_discrete_contract_even_with_matching_prefix(target, value):
    _, legacy, overlay = dual_fixtures.make_reader()
    row = overlay['results']['T1']['samples'][2]
    if target == 'pair_valid':
        row['reconstruction']['valid'] = value
    elif target == 'blade_id':
        for name in ('S2', 'S3'):
            row['observations'][name]['blade_id'] = value
        row['reconstruction']['blade_id'] = value
    else:
        row['observations']['S2']['observed' if target == 'observed' else 'valid'] = value
    refresh_dual_prefix(overlay)
    with pytest.raises(ValueError):
        DualBeamReader(legacy, overlay, 'T1')


@pytest.mark.parametrize('mutation', ['duplicate', 'unordered'])
def test_dual_reader_requires_strict_source_times(mutation):
    _, legacy, overlay = dual_fixtures.make_reader()
    rows = overlay['results']['T1']['samples']
    times = [1., 1., 1.05, 2.5] if mutation == 'duplicate' else [1., 1.05, 1.025, 2.5]
    for row, motion, time in zip(rows, legacy.package.motion, times):
        row['time_s'] = motion['time_s'] = time
        for observation in row['observations'].values():
            observation['time_s'] = time
    refresh_dual_prefix(overlay)
    with pytest.raises(ValueError):
        DualBeamReader(legacy, overlay, 'T1')


def test_dual_manifest_turbines_must_match_source(tmp_path):
    package, _ = portable_fixtures.fixture_package(tmp_path)
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['turbine_ids'] = ['unrelated-turbine']
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='turbine'):
        resolve_package(package)


def test_dual_manifest_turbine_order_does_not_change_identity(tmp_path):
    package, _ = portable_fixtures.fixture_package(tmp_path)
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['turbine_ids'] = ['T3', 'T2', 'T1']
    path.write_text(json.dumps(manifest), encoding='utf-8')
    _, overlay = resolve_package(package)
    assert set(overlay['results']) == set(manifest['turbine_ids'])


def test_dual_hold_duration_is_not_a_boolean(tmp_path):
    package, _ = portable_fixtures.fixture_package(tmp_path)
    calibration = package / 'calibration.json'
    config = json.loads(calibration.read_text())
    config['measurement_hold_s'] = True
    calibration.write_text(json.dumps(config), encoding='utf-8')
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['files']['calibration.json'] = digest(calibration)
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='calibration'):
        resolve_package(package)


@pytest.mark.parametrize('field,value', [('blade_id', True), ('observed', 1), ('valid', 1)])
def test_reconstruction_rejects_invalid_discrete_observations(field, value):
    a, b = pair_fixtures.echo((-8, 0, 70)), pair_fixtures.echo((-8, 0, 40))
    a[field] = b[field] = value
    result = pair_fixtures.pair(a, b)
    assert not result['valid']
    assert result['clearance_estimate'] is None


def test_legacy_error_consistency_uses_absolute_roundoff_only():
    manifest, motion, rows = legacy_fixtures.PackageTests().fixture()
    rows[0]['beams']['B2'].update(estimate_m=1005., error_m=1000.0000001)
    with pytest.raises(ValueError, match='signed error'):
        _validate(manifest, motion, rows)


def test_legacy_manifest_cannot_declare_unverified_missing_payload(tmp_path):
    manifest, motion, rows = legacy_fixtures.PackageTests().fixture()
    package = publish_package(tmp_path / 'package', manifest, motion, rows, 'synthetic fixture')
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['files']['missing.json'] = '0' * 64
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='inventory'):
        ReplayPackage.load(package)


def test_publication_failure_record_does_not_mask_original_error(tmp_path, monkeypatch):
    manifest, motion, rows = legacy_fixtures.PackageTests().fixture()
    original = Path.write_text
    error = OSError('original measurement write failure')

    def fail(path, *args, **kwargs):
        if path.name == 'measurements.json':
            raise error
        if path.name == 'failure.json':
            raise OSError('failure record write failure')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail)
    with pytest.raises(OSError, match='original measurement') as caught:
        publish_package(tmp_path / 'result', manifest, motion, rows, 'synthetic fixture')
    assert caught.value is error
    assert not (tmp_path / 'result').exists()
    retained, = tmp_path.glob('.lidar-*')
    assert (retained / 'motion.json').is_file()


def test_dual_missing_frame_is_rejected_even_with_matching_prefix():
    _, legacy, overlay = dual_fixtures.make_reader()
    del overlay['results']['T1']['samples'][1]
    refresh_dual_prefix(overlay)
    with pytest.raises(ValueError, match='full source grid'):
        DualBeamReader(legacy, overlay, 'T1')


def test_rehashed_truncated_json_cannot_load(tmp_path):
    manifest, motion, rows = legacy_fixtures.PackageTests().fixture()
    package = publish_package(tmp_path / 'package', manifest, motion, rows, 'synthetic fixture')
    payload = package / 'motion.json'
    payload.write_bytes(payload.read_bytes()[:-1])
    path = package / 'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['files']['motion.json'] = digest(payload)
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        ReplayPackage.load(package)


def test_parallel_ray_is_missing_and_does_not_become_zero_range():
    points = np.array([[-1., -1., 2.], [1., -1., 2.], [0., 1., 2.]])
    assert first_hit([0., 0., 0.], [1., 0., 0.], points, np.array([[0, 1, 2]])) is None
