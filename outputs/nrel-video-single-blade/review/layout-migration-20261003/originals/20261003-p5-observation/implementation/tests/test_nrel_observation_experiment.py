"""Frozen-observation contracts, without truth assets or expensive mesh fitting."""
import copy
import json
from dataclasses import asdict

import numpy as np
import pytest
import torch
from PIL import Image

from wfrl.nrel_reconstruction import observation_experiment as experiment
from wfrl.nrel_reconstruction import optimize


def observation_case(tmp_path):
    source = tmp_path / 'source'
    obs = source / 'observations'
    obs.mkdir(parents=True)
    annotation_dir = tmp_path / 'annotation'
    annotation_dir.mkdir()
    labels = np.zeros((10, 16), dtype=np.uint8)
    labels[2:4, 2:4] = 1
    old = np.zeros_like(labels)
    old[1:4, 1:4] = 255
    support = np.zeros_like(labels)
    support[4:7, 6:9] = 255
    for path, array in [(obs / 'FBU.png', labels), (obs / 'valid.png', old),
                        (annotation_dir / 'valid.png', old | support),
                        (annotation_dir / 'support.png', support)]:
        Image.fromarray(array).save(path)
    points = np.array([[1., 1.], [2., 2.]], dtype=np.float32)
    np.save(obs / 'points.npy', points)
    np.save(annotation_dir / 'points.npy', np.vstack([points, [[7., 5.]]]).astype(np.float32))
    observations = []
    for camera in ('C1', 'C2', 'C3'):
        for frame in (42, 43, 44, 45):
            observations.append({'camera_id': camera, 'frame_id': frame, 'split': 'fit' if frame < 44 else 'heldout',
                'sim_time_s': 1. + frame, 'K': [[20., 0., 7.5], [0., 20., 4.5], [0., 0., 1.]],
                'T_camera_cv_from_world': np.eye(4).tolist(), 'T_world_from_blade_root': np.eye(4).tolist(),
                'labels_path': str(obs / 'FBU.png'), 'contour_points_path': str(obs / 'points.npy'),
                'contour_valid_path': str(obs / 'valid.png'), 'contour_uncertainty_px': 3.})
    experiment.write_json(obs / 'observations.json', observations)
    experiment.write_json(obs / 'annotations.json', {'annotations': [
        {'camera_id': 'C1', 'frame_id': 42, 'rgb_sha256': '0' * 64}]})
    override = {'camera_id': 'C1', 'frame_id': 42, 'contour_points_path': str(annotation_dir / 'points.npy'),
        'contour_valid_path': str(annotation_dir / 'valid.png'),
        'reviewed_boundary_mask_path': str(annotation_dir / 'support.png'), 'contour_uncertainty_px': 3.}
    annotation = {'schema': experiment.ANNOTATION_SCHEMA, 'frozen_utc': '2026-10-03T00:00:00Z',
        'override': override, 'sha256': {str(p): experiment.digest(p) for p in annotation_dir.iterdir()},
        'provenance': {'source_labels_sha256': experiment.digest(obs / 'FBU.png'),
            'source_contour_points_sha256': experiment.digest(obs / 'points.npy'),
            'source_contour_valid_sha256': experiment.digest(obs / 'valid.png'),
            'decoded_rgb_pixel_sha256': '0' * 64}}
    annotation_path = annotation_dir / 'annotation.json'
    experiment.write_json(annotation_path, annotation)
    return source, observations, annotation_path, annotation


def protocol_case(tmp_path):
    source, observations, annotation_path, annotation = observation_case(tmp_path)
    (source / 'reconstruction').mkdir()
    config = source / 'reconstruction/frozen_config.json'
    experiment.write_json(config, experiment.FROZEN_CONFIG)
    input_dir = tmp_path / 'input'
    input_dir.mkdir()
    experiment.write_json(input_dir / 'dataset.json', {'test': 'signed mock bundle'})
    signatures = {str(p): experiment.digest(p) for root in (source / 'observations', input_dir)
                  for p in root.iterdir() if p.is_file()}
    signatures[str(config)] = experiment.digest(config)
    protocol = {'schema': experiment.PROTOCOL_SCHEMA, 'branches': experiment.BRANCHES,
        'source_directory': str(source), 'input_directory': str(input_dir), 'annotation_path': str(annotation_path),
        'source_observations_sha256': experiment.digest(source / 'observations/observations.json'),
        'input_manifest_sha256': experiment.digest(input_dir / 'dataset.json'),
        'config_sha256': experiment.digest(config), 'annotation_sha256': experiment.digest(annotation_path),
        'only_observation_change': experiment.CHANGE, 'shared_prior_only': True, 'sha256': signatures}
    protocol_path = tmp_path / 'protocol.json'
    experiment.write_json(protocol_path, protocol)
    return source, input_dir, observations, annotation_path, annotation, protocol_path, protocol


def test_only_c1_42_contour_and_domain_paths_change(tmp_path):
    _, observations, annotation_path, _ = observation_case(tmp_path)
    versions, metadata, _ = experiment.build_observation_versions(observations, annotation_path)
    assert versions['original_raw'] == observations
    experiment.verify_only_observation_change(versions['original_raw'], versions['revised_boundary'])
    revised = versions['revised_boundary'][0]
    assert revised['labels_path'] == observations[0]['labels_path']
    assert revised['K'] == observations[0]['K']
    assert metadata['FBU_unchanged'] and metadata['new_dense_rgb_points'] == 1
    changed = copy.deepcopy(versions['revised_boundary'])
    changed[1]['T_world_from_blade_root'][0][3] = .01
    with pytest.raises(ValueError, match='Unexpected observation change'):
        experiment.verify_only_observation_change(observations, changed)


def test_signed_annotation_mutation_and_original_fbu_mutation_rejected(tmp_path):
    source, observations, annotation_path, annotation = observation_case(tmp_path)
    target = annotation_path.parent / 'points.npy'
    target.write_bytes(target.read_bytes() + b'changed')
    with pytest.raises(RuntimeError, match='changed'):
        experiment.build_observation_versions(observations, annotation_path)
    annotation['sha256'][str(target)] = experiment.digest(target)
    experiment.write_json(annotation_path, annotation)
    (source / 'observations/FBU.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Historical observation hash mismatch'):
        experiment.build_observation_versions(observations, annotation_path)


def test_signed_domain_widening_outside_review_rejected(tmp_path):
    _, observations, annotation_path, annotation = observation_case(tmp_path)
    path = annotation_path.parent / 'valid.png'
    valid = experiment._image_array(path)
    valid[9, 15] = 255
    Image.fromarray(valid).save(path)
    annotation['sha256'][str(path)] = experiment.digest(path)
    experiment.write_json(annotation_path, annotation)
    with pytest.raises(ValueError, match='union reviewed local support'):
        experiment.build_observation_versions(observations, annotation_path)


@pytest.mark.parametrize('mutation', ['uncertainty', 'prefix', 'duplicate'])
def test_annotation_cannot_change_uncertainty_or_original_points(tmp_path, mutation):
    _, observations, annotation_path, annotation = observation_case(tmp_path)
    if mutation == 'uncertainty':
        annotation['override']['contour_uncertainty_px'] = 4.
    else:
        p = annotation_path.parent / 'points.npy'
        points = np.load(p)
        if mutation == 'prefix':
            points[0, 0] += .1
        else:
            points = np.vstack([points, points[-1]])
        np.save(p, points)
        annotation['sha256'][str(p)] = experiment.digest(p)
    experiment.write_json(annotation_path, annotation)
    with pytest.raises(ValueError):
        experiment.build_observation_versions(observations, annotation_path)


def test_protocol_rejects_changed_source_and_changed_optimizer_config(tmp_path):
    source, input_dir, _, annotation_path, _, protocol_path, protocol = protocol_case(tmp_path)
    experiment.validate_protocol(protocol_path, source, input_dir, annotation_path)
    p = source / 'observations/valid.png'
    original = p.read_bytes()
    p.write_bytes(b'mutated')
    with pytest.raises(RuntimeError, match='changed'):
        experiment.validate_protocol(protocol_path, source, input_dir, annotation_path)
    p.write_bytes(original)
    config = source / 'reconstruction/frozen_config.json'
    changed = copy.deepcopy(experiment.FROZEN_CONFIG)
    changed['learning_rate'] = .013
    experiment.write_json(config, changed)
    protocol['config_sha256'] = experiment.digest(config)
    protocol['sha256'][str(config)] = experiment.digest(config)
    experiment.write_json(protocol_path, protocol)
    with pytest.raises(ValueError, match='unchanged historical'):
        experiment.validate_protocol(protocol_path, source, input_dir, annotation_path)


def test_cross_residuals_label_both_targets_and_both_fitted_models(monkeypatch):
    def prepare(items, *args, **kwargs):
        return items
    def residuals(model, items, *args):
        return [{'camera_id': o['camera_id'], 'frame_id': o['frame_id'], 'split': o['split'],
                 'image_loss': model + o['target']} for o in items]
    monkeypatch.setattr(experiment, '_prepare', prepare)
    monkeypatch.setattr(experiment, '_residuals', residuals)
    versions = {v: [{'camera_id': 'C1', 'frame_id': 42, 'split': 'fit', 'target': index}]
                for index, v in enumerate(experiment.BRANCHES)}
    result = experiment.cross_residuals({'original_raw': 10, 'revised_boundary': 20}, versions,
                                        optimize.OptimizationConfig())
    for version in experiment.BRANCHES:
        for model in experiment.BRANCHES:
            row = result['results'][version][model][0]
            assert row['fitted_model'] == model and row['observation_version'] == version
    assert result['results']['original_raw']['original_raw'][0]['image_loss'] == 10
    assert result['results']['revised_boundary']['original_raw'][0]['image_loss'] == 11


def test_original_fit_function_and_100_step_config_are_used_without_reimplementation():
    assert experiment._fit is optimize._fit
    config = optimize.OptimizationConfig(**experiment.FROZEN_CONFIG)
    assert config.iterations_per_stage == (40, 40, 20)
    assert config.resolutions == ((160, 90), (320, 180), (640, 360))
    assert config.learning_rate == .012 and config.seed == 20261002


def test_runner_freezes_geometry_before_cross_residuals_and_rejects_overwrite(tmp_path, monkeypatch):
    import scripts.nrel_single_blade as cli
    source, input_dir, observations, annotation_path, _, protocol_path, _ = protocol_case(tmp_path)
    output = tmp_path / 'fit'
    def bundle_preflight(input_path, observation_path, output_path, config_path):
        value = optimize.reconstruct(observations, output_path, {'t_ref_sim_time_s': 43.},
                                     json.loads(config_path.read_text()))
        experiment.write_json(output_path / 'fit_decode_verification.json', {'selected_rgb_hashes_verified': True})
        return value
    calls = []
    def original_loop(initial, items, config, weight):
        calls.append((weight, len(items), config.learning_rate, sum(config.iterations_per_stage)))
        assert not (output / 'geometry_freeze.json').exists()
        return copy.deepcopy(initial), [{'iteration': i} for i in range(100)], .01
    def cross(models, versions, config):
        assert (output / 'geometry_freeze.json').is_file()
        assert all((output / arm / 'T1_B1_exact.npz').is_file() for arm in experiment.BRANCHES)
        assert not (output / 'model_freeze.json').exists()
        return {'results': {}}
    monkeypatch.setattr(cli, 'fit_bundle', bundle_preflight)
    monkeypatch.setattr(experiment, '_fit', original_loop)
    monkeypatch.setattr(experiment, '_objective', lambda *args: {'total_objective': 0.})
    monkeypatch.setattr(experiment, 'cross_residuals', cross)
    monkeypatch.setattr(experiment, 'observation_sampling_summary', lambda *args: {'rows': []})
    result = experiment.fit(source, input_dir, annotation_path, protocol_path, output)
    assert result['status'] == 'P5_OBSERVATION_MODELS_FROZEN'
    assert calls == [(0., 6, .012, 100), (1., 6, .012, 100), (1., 6, .012, 100)]
    freeze = json.loads((output / 'model_freeze.json').read_text())
    assert freeze['cross_residuals_after_geometry_freeze']
    assert 'cross_observation_residuals.json' in freeze['sha256']
    assert 'implementation_at_launch.json' in freeze['sha256']
    experiment.verify_signatures({str(output / name): sha for name, sha in freeze['sha256'].items()})
    with pytest.raises(FileExistsError, match='overwrite'):
        experiment.fit(source, input_dir, annotation_path, protocol_path, output)


def test_missing_signed_file_is_reported_as_input_mutation(tmp_path):
    p = tmp_path / 'frozen'
    p.write_text('first')
    signatures = {str(p): experiment.digest(p)}
    p.unlink()
    with pytest.raises(RuntimeError, match='changed'):
        experiment.verify_signatures(signatures)
