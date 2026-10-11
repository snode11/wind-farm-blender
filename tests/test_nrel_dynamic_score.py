import copy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scripts.nrel_dynamic_score import (
    digest, exact_source_indices, mechanical_truth, run, scalar_metrics,
    source_turbine_index, validate_source_links, verify_estimator_freeze,
)


def test_mechanical_material_tip_displacement_uses_same_pitched_axes():
    scalars = {'ShftTilt': 0., 'OverHang': -5., 'TowerHt': 90., 'Twr2Shft': 0., 'PreCone(1)': 0.}
    pose = [0., 0., 0., 90., 0., 0.]
    nacelle = np.c_[np.eye(3), [3., 4., 5.]]
    p, c, n = mechanical_truth(scalars, pose, nacelle, [2., 0., 0.], [-1., 0., 63.])
    assert np.allclose(c, [-2., 4., 95.])
    assert np.allclose(p, [-2., 3., 158.])
    assert np.array_equal(n, [1., 0., 0.])


def test_reflection_and_missing_metrics_are_rejected():
    s = {'ShftTilt': 0., 'OverHang': 0., 'TowerHt': 0., 'Twr2Shft': 0., 'PreCone(1)': 0.}
    with pytest.raises(ValueError, match='proper rigid'):
        mechanical_truth(s, [0.]*6, np.c_[np.diag([-1.,1.,1.]), [0.]*3], [0.]*3, [0.,0.,63.])
    with pytest.raises(ValueError):
        scalar_metrics([])
    result = scalar_metrics([-2., 2.])
    assert result['mean'] == 0.
    assert result['rmse'] == 2.
    assert result['mean_absolute'] == 2.


def test_exact_source_match_handles_roundoff_above_saved_sample():
    times = np.arange(4800, 5001) / 40
    targets = [120.025 + frame / 20 for frame in (42, 43, 44, 45)]
    assert targets[1] > times[87]  # Original searchsorted selected sample 88.
    assert exact_source_indices(times, targets).tolist() == [85, 87, 89, 91]


@pytest.mark.parametrize('times,targets', [
    ([0., 1.], [.5]), ([0., 1.], [2.]), ([0., 0.], [0.]),
    ([1., 0.], [0.]), ([0., np.inf], [0.]), ([0., 1.], [np.nan]),
    ([0., 5e-9], [2e-9]),
])
def test_source_match_rejects_missing_ambiguous_or_invalid_times(times, targets):
    with pytest.raises(ValueError):
        exact_source_indices(times, targets)


def static_definitions():
    signatures = {name: name + '-hash' for name in ('geometry.npz', 'tower-motion.npz', 'blade-reference.json')}
    manifest = {'schema': 'wfrl.farm-flex-review.v3', 'status': 'REVIEW_ONLY',
                'structural_module': 'BeamDyn', 'tower_model': 'elastodyn-flexible',
                'includes_rigid_motion': True, 'model': 'synthetic-prebend',
                'turbine_ids': ['T2', 'T1'], 'files': signatures}
    scalars = {'TipRad': 63., 'HubRad': 1.5, 'OverHang': -5., 'TowerHt': 90.,
               'Twr2Shft': 0., 'ShftTilt': 0., 'PreCone(1)': -2.5}
    channels = {'schema': 'wfrl.tip-deflection.t1.v3', 'unit': 'm', 'turbine_id': 'T1',
                'reference': 'BeamDyn structural tip; independent unloaded prebend',
                'frame': 'BeamDyn pitched root xyz', 'scalars': scalars,
                'geometry_sha256': signatures['geometry.npz'],
                'tower_motion_sha256': signatures['tower-motion.npz'],
                'reference_sha256': signatures['blade-reference.json']}
    z = np.linspace(0., 61.5, 49)
    reference = {'schema': 'wfrl.blade-reference.v1', 'structural_module': 'BeamDyn',
                 'model': manifest['model'], 'tip_local_m': [-1., 0., 63.],
                 'definition': 'x=-(z/61.5)^2; y=0; metres; z from blade root',
                 'reference_state': 'independent zero-load stationary solve', 'precone_deg': -2.5,
                 'curve': np.c_[-(z / 61.5)**2, z * 0., z, z * 0.].tolist()}
    return manifest, channels, reference


def test_source_static_contract_selects_t1_and_validates_links():
    manifest, channels, reference = static_definitions()
    assert source_turbine_index(manifest) == 1
    assert np.array_equal(validate_source_links(manifest, channels, reference), [-1., 0., 63.])


@pytest.mark.parametrize('key,value', [
    ('geometry_sha256', 'wrong'), ('tower_motion_sha256', 'wrong'),
    ('reference_sha256', 'wrong'), ('turbine_id', 'T2'), ('frame', 'unpitched'),
])
def test_source_channel_links_fail_closed(key, value):
    manifest, channels, reference = static_definitions()
    channels[key] = value
    with pytest.raises(ValueError, match='channel definition or source links'):
        validate_source_links(manifest, channels, reference)


@pytest.mark.parametrize('key,value', [
    ('schema', 'wfrl.farm-flex-review.v2'), ('structural_module', 'ElastoDyn'),
    ('status', 'READY'), ('turbine_ids', ['T1', 'T1']), ('turbine_ids', ['T2']),
])
def test_source_identity_fail_closed(key, value):
    manifest, _, _ = static_definitions()
    manifest[key] = value
    with pytest.raises(ValueError, match='source schema or turbine identity'):
        source_turbine_index(manifest)


@pytest.mark.parametrize('key,value', [
    ('schema', 'wrong'), ('model', 'other'), ('reference_state', 'loaded'),
    ('tip_local_m', [-1., 0., 61.5]), ('precone_deg', 0.),
])
def test_reference_definition_fail_closed(key, value):
    manifest, channels, reference = static_definitions()
    reference[key] = value
    with pytest.raises(ValueError, match='unloaded structural reference'):
        validate_source_links(manifest, channels, reference)


def write_json(path, value):
    path.write_text(json.dumps(value))


def frozen_package(tmp_path):
    package = tmp_path / 'package'
    package.mkdir()
    sequence = {'schema': 'nrel-dynamic-review-sequence.v1', 'frames': [42, 43, 44, 45]}
    write_json(package / 'sequence.json', sequence)
    manifest = {'schema': 'nrel-dynamic-review-package.v1', 'status': 'REVIEW_ONLY',
                'target': 'T1/B1', 'files': {'sequence.json': digest(package / 'sequence.json')}}
    write_json(package / 'manifest.json', manifest)
    signatures = {str(package / name): digest(package / name) for name in ('manifest.json', 'sequence.json')}
    freeze = {'schema': 'nrel-dynamic-estimator-freeze.v1', 'status': 'FROZEN_BEFORE_INDEPENDENT_SCORE',
              'truth_read': False, 'independent_score_read': False, 'package_path': str(package),
              'manifest_sha256': signatures[str(package / 'manifest.json')], 'files': signatures}
    freeze_path = tmp_path / 'estimator_freeze.json'
    write_json(freeze_path, freeze)
    return package, manifest, signatures, freeze, freeze_path


@pytest.mark.parametrize('key,value', [
    ('schema', 'nrel-a03-post-estimate-score-inputs.v1'), ('status', 'SCORED'),
    ('truth_read', True), ('truth_read', 0), ('independent_score_read', True),
    ('package_path', '/different/package'), ('manifest_sha256', 'wrong'), ('files', {}),
])
def test_invalid_freeze_rejected_before_any_source_read(tmp_path, monkeypatch, key, value):
    package, _, _, freeze, freeze_path = frozen_package(tmp_path)
    freeze[key] = value
    write_json(freeze_path, freeze)
    source = tmp_path / 'forbidden-source'
    original_text, original_bytes = Path.read_text, Path.read_bytes

    def guard(original):
        def checked(path, *args, **kwargs):
            if source in path.parents:
                pytest.fail('Invalid freeze opened independent source truth')
            return original(path, *args, **kwargs)
        return checked

    monkeypatch.setattr(Path, 'read_text', guard(original_text))
    monkeypatch.setattr(Path, 'read_bytes', guard(original_bytes))
    with pytest.raises(ValueError, match='estimator freeze|Estimator freeze'):
        run(SimpleNamespace(package=package, source=source, freeze=freeze_path, output=tmp_path / 'score'))
    assert not (tmp_path / 'score').exists()


def test_manifest_must_bind_sequence(tmp_path):
    package, manifest, signatures, freeze, _ = frozen_package(tmp_path)
    verify_estimator_freeze(freeze, package, manifest, signatures)
    del manifest['files']['sequence.json']
    with pytest.raises(ValueError, match='unbound sequence'):
        verify_estimator_freeze(freeze, package, manifest, signatures)


def test_scoring_synthetic_source_uses_t1_and_exact_sample_centers(tmp_path):
    package, package_manifest, _, freeze, freeze_path = frozen_package(tmp_path)
    source = tmp_path / 'synthetic-source'
    source.mkdir()
    manifest, channels, reference = static_definitions()
    times = np.arange(4800, 5001) / 40
    poses = np.zeros((len(times), 2, 6))
    poses[:, 0, 1] = 90.  # T2 must not be used for this T1 sidecar.
    nacelles = np.broadcast_to(np.c_[np.eye(3), [0., 0., 0.]], (len(times), 2, 3, 4)).copy()
    nacelles[:, 0, :, 3] = [100., 0., 0.]
    np.savez(source / 'geometry.npz', times=times, poses=poses)
    np.savez(source / 'tower-motion.npz', times=times, nacelle=nacelles)
    write_json(source / 'blade-reference.json', reference)
    channels.update(times=times.tolist(), poses=poses[:, 1].tolist(),
                    simulation=np.zeros((len(times), 3, 3)).tolist())
    for field, name in [('geometry_sha256', 'geometry.npz'), ('tower_motion_sha256', 'tower-motion.npz'),
                        ('reference_sha256', 'blade-reference.json')]:
        channels[field] = digest(source / name)
    write_json(source / 'deflection-t1.json', channels)
    write_json(source / 'interface-audit.json', {'max_component_error_m': .001})
    manifest['files'] = {name: digest(source / name) for name in (
        'geometry.npz', 'tower-motion.npz', 'deflection-t1.json', 'blade-reference.json', 'interface-audit.json')}
    write_json(source / 'manifest.json', manifest)
    point, center, normal = mechanical_truth(channels['scalars'], poses[0, 1], nacelles[0, 1],
                                              [0., 0., 0.], reference['tip_local_m'])
    records = [{'frame_id': f, 'sim_time_s': 120. + (f + .5) / 20, 'point_world_m': point.tolist(),
                'center_world_m': center.tolist(), 'normal_world_unit': normal.tolist(),
                'd_m': float(normal @ (point - center)), 'status': 'SYNTHETIC_TEST'} for f in (42, 43, 44, 45)]
    write_json(package / 'sequence.json', {'schema': 'nrel-dynamic-review-sequence.v1', 'frames': [42, 43, 44, 45],
               'groups': {group: {'records': copy.deepcopy(records)} for group in ('G1', 'G2', 'G3')}})
    package_manifest['files']['sequence.json'] = digest(package / 'sequence.json')
    write_json(package / 'manifest.json', package_manifest)
    freeze['files'] = {str(package / name): digest(package / name) for name in ('manifest.json', 'sequence.json')}
    freeze['manifest_sha256'] = digest(package / 'manifest.json')
    write_json(freeze_path, freeze)
    output = tmp_path / 'score'
    run(SimpleNamespace(package=package, source=source, freeze=freeze_path, output=output))
    score = json.loads((output / 'score.json').read_text())
    assert [row['sim_time_s'] for row in score['truth']] == [122.125, 122.175, 122.225, 122.275]
    assert all(group['point_norm_error_m']['max_absolute'] == 0. for group in score['groups'].values())
    assert json.loads((output / 'post_run_integrity.json').read_text())['unchanged']
