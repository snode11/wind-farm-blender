"""P5: a frozen, two-arm RGB observation experiment using the original optimizer.

Only C1#42's trusted contour and candidate domain may change.  The historical
F/B/U labels, poses, model, loss and 100-step budget are identical.  This runner
does not import the truth evaluator or open score files.  Run ``fit`` inside the
declared OS isolation policy; ``nonblind`` requires both final mesh freezes.
"""
from __future__ import annotations

import argparse
import copy
import json
import resource
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch
from PIL import Image

from .dataset import write_json
from .greville_experiment import digest
from .model import BladeModel, write_ply
from .optimize import OptimizationConfig, _fit, _image_loss, _prepare, _residuals


BRANCHES = ['original_raw', 'revised_boundary']
ANNOTATION_SCHEMA = 'nrel-observation-boundary-review.v1'
PROTOCOL_SCHEMA = 'nrel-observation-experiment.v1'
CHANGE = {'camera_id': 'C1', 'frame_id': 42,
          'fields': ['contour_points_path', 'contour_valid_path']}
FROZEN_CONFIG = asdict(OptimizationConfig(
    model_interpolation='bspline', image_objective='projected_contour',
    resolutions=((160, 90), (320, 180), (640, 360)),
    iterations_per_stage=(40, 40, 20), batch_size=1,
    learning_rate=.012, image_weight=1., seed=20261002, threads=4))


def _json_value(value):
    """Normalize JSON list versus dataclass tuple spelling without float drift."""
    return json.loads(json.dumps(value, allow_nan=False))


def verify_signatures(signatures):
    changed = [str(path) for path, signature in signatures.items()
               if not Path(path).is_file() or digest(path) != signature]
    if changed:
        raise RuntimeError(f'Frozen inputs/code changed: {changed}')


def _image_array(path):
    with Image.open(path) as image:
        return np.array(image, copy=True)


def _path(base, value):
    p = Path(value)
    return (p if p.is_absolute() else base / p).resolve()


def build_observation_versions(observations, annotation_path):
    """Validate the signed local review and construct the two unchanged-fit arms."""
    annotation_path = Path(annotation_path).resolve()
    annotation = json.loads(annotation_path.read_text())
    if annotation.get('schema') != ANNOTATION_SCHEMA or not annotation.get('frozen_utc'):
        raise ValueError('Expected a frozen RGB boundary annotation schema')
    override = annotation.get('override', {})
    required = {'camera_id', 'frame_id', 'contour_points_path', 'contour_valid_path',
                'reviewed_boundary_mask_path', 'contour_uncertainty_px'}
    if set(override) != required or (override['camera_id'], override['frame_id']) != ('C1', 42):
        raise ValueError('Only C1#42 contour paths and reviewed support are permitted')
    signed = annotation.get('sha256', {})
    if not isinstance(signed, dict) or not signed:
        raise ValueError('Annotation must sign every declared artifact')
    signatures = {str(_path(annotation_path.parent, p)): sha for p, sha in signed.items()}
    verify_signatures(signatures)
    paths = {field: _path(annotation_path.parent, override[field]) for field in
             ('contour_points_path', 'contour_valid_path', 'reviewed_boundary_mask_path')}
    if any(str(p) not in signatures for p in paths.values()):
        raise ValueError('Annotation override/support file is not signed')
    if any(part in {'evaluation-only', 'internal-capture'} for p in paths.values() for part in p.parts):
        raise ValueError('RGB annotations may not read internal or evaluation data')
    original = copy.deepcopy(list(observations))
    pairs = [(o['camera_id'], o['frame_id']) for o in original]
    expected = {(c, f) for c in ('C1', 'C2', 'C3') for f in (42, 43, 44, 45)}
    if len(pairs) != len(set(pairs)) or set(pairs) != expected:
        raise ValueError('Expected exactly the 12 historical camera/frame observations')
    target = original[pairs.index(('C1', 42))]
    if target['split'] != 'fit' or override['contour_uncertainty_px'] != target['contour_uncertainty_px']:
        raise ValueError('Frozen split and contour uncertainty may not change')
    provenance = annotation.get('provenance', {})
    # These generated review artifacts are signed separately in provenance.
    # The native RGB path is informational: MP4 pixels are verified at entry.
    for name in ('rgb_extraction_protocol', 'build_script'):
        if f'{name}_path' in provenance:
            path = _path(annotation_path.parent, provenance[f'{name}_path'])
            if path.parent != annotation_path.parent or provenance.get(f'{name}_sha256') != digest(path):
                raise ValueError(f'Frozen RGB review artifact mismatch: {name}')
            signatures[str(path)] = provenance[f'{name}_sha256']
    for field, source_field in [('source_contour_points_sha256', 'contour_points_path'),
                                ('source_contour_valid_sha256', 'contour_valid_path'),
                                ('source_labels_sha256', 'labels_path')]:
        if provenance.get(field) != digest(target[source_field]):
            raise ValueError(f'Historical observation hash mismatch: {field}')
    old_points = np.load(target['contour_points_path'], allow_pickle=False)
    new_points = np.load(paths['contour_points_path'], allow_pickle=False)
    old_valid = _image_array(target['contour_valid_path'])
    new_valid = _image_array(paths['contour_valid_path'])
    support = _image_array(paths['reviewed_boundary_mask_path'])
    labels = _image_array(target['labels_path'])
    if (old_valid.shape != labels.shape or new_valid.shape != labels.shape or support.shape != labels.shape
            or old_valid.ndim != 2 or not np.isin(old_valid, [0, 255]).all()
            or not np.isin(new_valid, [0, 255]).all() or not np.isin(support, [0, 255]).all()):
        raise ValueError('Reviewed and historical domains must be native-size binary masks')
    old_domain, new_domain, reviewed = old_valid > 0, new_valid > 0, support > 0
    if not np.array_equal(new_domain, old_domain | reviewed):
        raise ValueError('Revised candidate domain must equal original domain union reviewed local support')
    if not (new_domain & ~old_domain).any():
        raise ValueError('Reviewed boundary must add a nonempty local candidate domain')
    if (new_points.ndim != 2 or new_points.shape[1] != 2 or new_points.dtype.kind not in 'fiu'
            or len(new_points) <= len(old_points) or not np.isfinite(new_points).all()
            or not np.array_equal(new_points[:len(old_points)], old_points)):
        raise ValueError('Revised contour must retain original points exactly and append finite RGB boundary points')
    height, width = labels.shape
    if (new_points[:, 0].min() < 0 or new_points[:, 0].max() >= width
            or new_points[:, 1].min() < 0 or new_points[:, 1].max() >= height):
        raise ValueError('Revised contour points outside the native image')
    rounded = np.rint(new_points).astype(np.int64)
    if not new_domain[rounded[:, 1], rounded[:, 0]].all():
        raise ValueError('Revised contour enters excluded candidate domain')
    appended = np.rint(new_points[len(old_points):]).astype(np.int64)
    if not reviewed[appended[:, 1], appended[:, 0]].all():
        raise ValueError('Appended RGB contour points must stay inside reviewed local support')
    if old_domain[appended[:, 1], appended[:, 0]].any():
        raise ValueError('Appended RGB contour points must be outside the original candidate domain')
    if len(np.unique(new_points, axis=0)) != len(new_points):
        raise ValueError('RGB contour coordinates must not duplicate original or appended points')
    revised = copy.deepcopy(original)
    revised_target = revised[pairs.index(('C1', 42))]
    for field in CHANGE['fields']:
        if Path(target[field]).resolve() == paths[field]:
            raise ValueError('Revised annotation must be separate from historical files')
        revised_target[field] = str(paths[field])
    verify_only_observation_change(original, revised)
    metadata = {'schema': annotation['schema'], 'annotation_path': str(annotation_path),
                'annotation_sha256': digest(annotation_path), 'override': CHANGE,
                'original_contour_points': len(old_points), 'revised_contour_points': len(new_points),
                'new_dense_rgb_points': len(new_points) - len(old_points),
                'dense_samples_are_independent_information': False,
                'max_observed_unchanged': 600,
                'sampling_share_note': 'Appending points changes the fixed linspace max600 sample allocation and spatial weighting.',
                'added_candidate_domain_pixels': int((new_domain & ~old_domain).sum()),
                'FBU_unchanged': True, 'uncertainty_native_px_unchanged': target['contour_uncertainty_px']}
    return {'original_raw': original, 'revised_boundary': revised}, metadata, signatures


def verify_only_observation_change(original, revised):
    if len(original) != len(revised):
        raise ValueError('Observation arms differ in length')
    for before, after in zip(original, revised):
        if set(before) != set(after):
            raise ValueError('Observation field set changed')
        changed = {key for key in before if _json_value(before[key]) != _json_value(after[key])}
        expected = set(CHANGE['fields']) if (before['camera_id'], before['frame_id']) == ('C1', 42) else set()
        if changed != expected:
            raise ValueError(f'Unexpected observation change: {before["camera_id"]}#{before["frame_id"]}: {sorted(changed)}')


def validate_protocol(protocol_path, source, input_dir, annotation_path):
    source, input_dir, annotation_path = [Path(p).resolve() for p in (source, input_dir, annotation_path)]
    protocol = json.loads(Path(protocol_path).read_text())
    identities = {'schema': PROTOCOL_SCHEMA, 'branches': BRANCHES,
                  'source_directory': str(source), 'input_directory': str(input_dir),
                  'annotation_path': str(annotation_path),
                  'source_observations_sha256': digest(source / 'observations/observations.json'),
                  'input_manifest_sha256': digest(input_dir / 'dataset.json'),
                  'config_sha256': digest(source / 'reconstruction/frozen_config.json'),
                  'annotation_sha256': digest(annotation_path),
                  'only_observation_change': CHANGE, 'shared_prior_only': True}
    for key, expected in identities.items():
        if protocol.get(key) != expected:
            raise ValueError(f'Frozen protocol identity mismatch: {key}')
    signatures = protocol.get('sha256', {})
    required = [p for root in (source / 'observations', input_dir) for p in root.iterdir() if p.is_file()]
    required += [source / 'reconstruction/frozen_config.json']
    if not isinstance(signatures, dict) or any(str(p.resolve()) not in signatures for p in required):
        raise ValueError('Protocol must sign every historical observation, input and config file')
    verify_signatures(signatures)
    config = OptimizationConfig(**json.loads((source / 'reconstruction/frozen_config.json').read_text()))
    if _json_value(asdict(config)) != _json_value(FROZEN_CONFIG):
        raise ValueError('P5 requires the unchanged historical v2 model/loss/optimizer/100-step config')
    return protocol, config, signatures


def cross_residuals(models, versions, config):
    """Each fixed model is evaluated under both observation targets, explicitly labeled."""
    results = {}
    for observation_version in BRANCHES:
        fitting = [o for o in versions[observation_version] if o['split'] == 'fit']
        prepared = _prepare(fitting, config.resolutions[-1], config.device, require_contours=True)
        results[observation_version] = {}
        for model_name, model in models.items():
            rows = _residuals(model, prepared, config.resolutions[-1], config)
            for row in rows:
                row['fitted_model'] = model_name
                row['observation_version'] = observation_version
                row['residual_scope'] = 'fit_images_under_named_observation_version'
            results[observation_version][model_name] = rows
    return {'comparison_contract': 'Compare fitted models within the same observation_version; training-target losses across versions are not a common-target comparison.',
            'results': results}


def observation_sampling_summary(versions, config):
    """Record the unchanged max600 limiter's share of the appended RGB points."""
    rows = []
    original_target = next(o for o in versions['original_raw'] if
                           (o['camera_id'], o['frame_id']) == ('C1', 42))
    original_count = len(np.load(original_target['contour_points_path'], allow_pickle=False))
    for version in BRANCHES:
        fitting = [o for o in versions[version] if o['split'] == 'fit']
        for size in config.resolutions:
            prepared = _prepare(fitting, size, config.device, require_contours=True)
            for observation in prepared:
                points = observation['contour_points']
                inside = (torch.isfinite(points).all(dim=-1) & (points[:, 0] >= 0)
                          & (points[:, 0] <= size[0] - 1) & (points[:, 1] >= 0)
                          & (points[:, 1] <= size[1] - 1))
                source_indices = torch.nonzero(inside, as_tuple=False).flatten()
                in_frame = len(source_indices)
                if in_frame > 600:
                    indices = torch.linspace(0, in_frame - 1, 600, device=points.device).round().long()
                    source_indices = source_indices[indices]
                revised_target = (version == 'revised_boundary' and
                                  (observation['camera_id'], observation['frame_id']) == ('C1', 42))
                rows.append({'observation_version': version, 'camera_id': observation['camera_id'],
                    'frame_id': observation['frame_id'], 'resolution': list(size),
                    'source_point_count': len(points), 'in_frame_point_count': in_frame,
                    'max_observed': 600, 'observed_count': len(source_indices),
                    'observed_sampling_truncated': in_frame > 600,
                    'sampled_new_rgb_point_count': int((source_indices >= original_count).sum()) if revised_target else 0})
    return {'geometry_status': 'FROZEN_BEFORE_THIS_DIAGNOSTIC', 'rows': rows,
            'meaning': 'Deterministic samples change spatial weighting; dense RGB points are not independent 3D information.'}


def _objective(model, observations, config):
    prepared = _prepare(observations, config.resolutions[-1], config.device, require_contours=True)
    with torch.no_grad():
        terms = model.regularization()
        regularization = (config.relative_change_weight * terms['relative_parameter_change']
                          + config.smoothness_weight * terms['span_smoothness'])
        image_mean = sum(float(_image_loss(model, o, config.resolutions[-1], config)[0]) for o in prepared) / len(prepared)
    return {'image_loss_mean': image_mean, 'regularization': float(regularization),
            'total_objective': image_mean * config.image_weight + float(regularization)}


def _save_model(destination, kind, model, sidecar):
    vertices = model().detach().cpu().numpy()
    faces = model.faces.detach().cpu().numpy()
    write_ply(destination / f'{kind}.ply', vertices, faces)
    np.savez_compressed(destination / f'{kind}_exact.npz', vertices=vertices, faces=faces,
                        center_raw=model.center_raw.detach().cpu().numpy(),
                        chord_raw=model.chord_raw.detach().cpu().numpy())
    write_json(destination / f'{kind}.json', {**sidecar, **model.metadata(), 'model_name': kind})


def fit(source, input_dir, annotation_path, protocol_path, output, isolation_path=None):
    """Decode/verify all signed MP4 input, freeze, fit once per arm, then freeze meshes."""
    from scripts.nrel_single_blade import fit_bundle
    source, input_dir, annotation_path, protocol_path, output = [Path(p).resolve() for p in
        (source, input_dir, annotation_path, protocol_path, output)]
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Refuse to overwrite an observation experiment; use a fresh output directory')
    _, config, signatures = validate_protocol(protocol_path, source, input_dir, annotation_path)
    observations = json.loads((source / 'observations/observations.json').read_text())
    versions, review, annotation_signatures = build_observation_versions(observations, annotation_path)
    signatures = {**signatures, **annotation_signatures,
                  str(protocol_path): digest(protocol_path), str(annotation_path): digest(annotation_path)}
    for name in ('protocol.md', 'sampling_diagnostics.json'):
        declared_artifact = protocol_path.parent / name
        if declared_artifact.is_file():
            signatures[str(declared_artifact)] = digest(declared_artifact)
    if isolation_path is not None:
        p = Path(isolation_path).resolve()
        signatures[str(p)] = digest(p)
    source_code = Path(__file__).resolve().parent
    code = list(source_code.glob('*.py')) + [source_code.parents[1] / 'scripts/nrel_single_blade.py',
                                            source_code.parent / 'camera_video/media.py']
    code_signatures = {str(p.resolve()): digest(p) for p in code}
    signatures.update(code_signatures)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / 'fit_entry_inputs.json', signatures)
    write_json(output / 'implementation_at_launch.json', code_signatures)
    write_json(output / 'frozen_config.json', asdict(config))
    write_json(output / 'observation_versions.json', versions)
    write_json(output / 'annotation_validation.json', review)
    captured = {}
    def collect(items, destination, reference_state, frozen_config):
        if items != observations or _json_value(asdict(OptimizationConfig(**frozen_config))) != _json_value(asdict(config)):
            raise RuntimeError('Signed bundle preflight changed observations/config')
        captured['reference_state'] = reference_state
        return {'status': 'SIGNED_MP4_PREFLIGHT_COMPLETE_NO_OPTIMIZATION'}
    with patch('wfrl.nrel_reconstruction.optimize.reconstruct', collect):
        fit_bundle(input_dir, source / 'observations', output, source / 'reconstruction/frozen_config.json')
    rgb_evidence = json.loads((source / 'observations/annotations.json').read_text())
    target_evidence = next(a for a in rgb_evidence['annotations'] if
                           (a['camera_id'], a['frame_id']) == ('C1', 42))
    annotation = json.loads(annotation_path.read_text())
    if annotation['provenance'].get('decoded_rgb_pixel_sha256') != target_evidence['rgb_sha256']:
        raise ValueError('Reviewed RGB pixel signature differs from the actual MP4 decode')
    verify_signatures(signatures)
    launch_files = [output / name for name in ('fit_entry_inputs.json', 'implementation_at_launch.json', 'frozen_config.json',
                    'observation_versions.json', 'annotation_validation.json', 'fit_decode_verification.json')]
    write_json(output / 'launch_freeze.json', {
        'utc': datetime.now(timezone.utc).isoformat(), 'status': 'PROTOCOL_ANNOTATION_INPUT_CODE_FROZEN_BEFORE_FIT',
        'sha256': {p.name: digest(p) for p in launch_files}, 'source_sha256': signatures,
        'runtime': {'torch': torch.__version__, 'numpy': np.__version__},
        'isolation_policy_path': str(Path(isolation_path).resolve()) if isolation_path else None,
        'isolation_enforcement': 'external OS policy; this field does not prove enforcement',
        'truth_or_scores_read': False, 'nonblind_residuals_computed': False})
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.threads)
    initial = BladeModel(config.span_samples, config.ring_samples, config.control_stations,
                         interpolation=config.model_interpolation)
    if not all(bool((p == 0).all()) for p in initial.parameters()):
        raise RuntimeError('P5 requires identical zero raw controls')
    sidecar = {**captured['reference_state'], 'target': 'T1/B1', 'coordinate_frame': 'blade_root_local',
               'units': 'm', 'surface_scope': 'entire_evaluated_blade_surface',
               'reference_shape': 'static_near_t_ref', 'deformation_assistance': 'none'}
    fitting = {name: [o for o in items if o['split'] == 'fit'] for name, items in versions.items()}
    expected_fit = {(c, f) for c in ('C1', 'C2', 'C3') for f in (42, 43)}
    if any({(o['camera_id'], o['frame_id']) for o in items} != expected_fit for items in fitting.values()):
        raise ValueError('Only the six historical fitting images may enter optimization')
    prior, prior_history, prior_s = _fit(initial, fitting['original_raw'], config, 0.)
    verify_signatures(signatures)
    models, summaries = {}, {}
    for name in BRANCHES:
        verify_signatures(signatures)
        final, history, elapsed = _fit(initial, fitting[name], config, config.image_weight)
        verify_signatures(signatures)
        destination = output / name
        destination.mkdir()
        for kind, model in [('initial_template', initial), ('prior_only', prior), ('T1_B1', final)]:
            _save_model(destination, kind, model, sidecar)
        models[name] = final
        provenance = {'observation_version': name, 'coordinate_branch': 'original_raw',
                      'optimizer_implementation': 'wfrl.nrel_reconstruction.optimize._fit',
                      'only_between_arm_change': CHANGE, 'same_zero_initial_parameters': True,
                      'same_model_loss_weights_optimizer_budget': True, 'shared_prior_only': True,
                      'shared_prior_computed_with': 'original_raw; image_weight=0; no image gradients',
                      'same_initial_parameters': True, 'same_optimizer': True,
                      'same_stage_schedule': True, 'same_regularization': True,
                      'same_budget': True, 'only_image_weights_differ': True,
                      'provenance_scope': 'Within this arm video versus shared prior only; between image arms only C1#42 contour/domain paths differ.',
                      'prior_only_image_weight': 0., 'final_image_weight': config.image_weight}
        objective = _objective(final, fitting[name], config)
        write_json(destination / 'optimization.json', {'config': asdict(config), 'coordinate_branch': 'original_raw',
            'prior_only_history': prior_history, 'video_history': history, 'comparison_provenance': provenance,
            'final_objective_under_training_observation_version': objective,
            'fit_residual_comparison': '../cross_observation_residuals.json',
            'nonblind_status': 'NOT_EVALUATED_BEFORE_ALL_MODEL_FREEZE'})
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        write_json(destination / 'runtime_metrics.json', {'video_fit_wall_s': elapsed,
            'shared_prior_only_wall_s': prior_s, 'shared_prior_computed_once': True,
            'peak_process_rss_bytes': int(peak if sys.platform == 'darwin' else peak * 1024),
            'device': config.device, 'threads': config.threads, 'seed': config.seed,
            'torch_version': torch.__version__, 'numpy_version': np.__version__,
            'fitting_images': len(fitting[name]),
            'cache_policy': 'selected FBU labels, candidate domains and trusted contours; streaming RGB decode preflight'})
        write_json(destination / 'model_state.json', {**sidecar, 'models': {
            'initial_template': initial.metadata(), 'prior_only': prior.metadata(), 'T1_B1': final.metadata()}})
        write_json(destination / 'surface_evidence.json', {'target': 'T1/B1', 'units': 'm',
            'coordinate_frame': 'blade_root_local', 'regions': [
                {'name': n, 'span_bounds_m': z, 'status': 'prior_only'} for n, z in
                [('root', [0, 20.5]), ('middle', [20.5, 41]), ('tip', [41, 61.5])]],
            'full_surface_recovery': False, 'backside_and_thickness': 'prior_only'})
        summaries[name] = {'video_fit_wall_s': elapsed, 'shared_prior_only_wall_s': prior_s,
                           'steps': len(history), 'image_forward_backward_evaluations': len(history) * len(fitting[name]),
                           'final_objective_under_training_observation_version': objective}
        print(json.dumps({name: summaries[name]}), flush=True)
    geometry_files = [p for name in BRANCHES for p in (output / name).iterdir()
                      if p.suffix in {'.ply', '.npz'} or p.name in
                      {'T1_B1.json', 'prior_only.json', 'initial_template.json', 'model_state.json'}]
    geometry_signatures = {str(p.relative_to(output)): digest(p) for p in geometry_files}
    geometry_freeze = {'utc': datetime.now(timezone.utc).isoformat(),
        'status': 'BOTH_ARM_MESHES_AND_CONTROLS_FROZEN_BEFORE_CROSS_RESIDUALS_NONBLIND_AND_TRUTH',
        'sha256': geometry_signatures, 'further_tuning': False}
    write_json(output / 'geometry_freeze.json', geometry_freeze)
    verify_signatures({str(output / p): sha for p, sha in geometry_signatures.items()})
    write_json(output / 'cross_observation_residuals.json', cross_residuals(
        {**models, 'shared_prior_only': prior, 'initial_template': initial}, versions, config))
    write_json(output / 'postfreeze_observed_sampling.json', observation_sampling_summary(versions, config))
    verify_signatures({str(output / p): sha for p, sha in geometry_signatures.items()})
    write_json(output / 'fit_summary.json', {'branches': summaries, 'shared_prior_only': True,
        'shared_prior_compute_wall_s': prior_s, 'shared_prior_not_counted_twice': True})
    verify_signatures(signatures)
    frozen_files = [p for p in output.rglob('*') if p.is_file()]
    write_json(output / 'model_freeze.json', {
        'utc': datetime.now(timezone.utc).isoformat(),
        'status': 'ALL_BRANCHES_FROZEN_BEFORE_NONBLIND_AND_TRUTH_EVALUATION',
        'sha256': {str(p.relative_to(output)): digest(p) for p in frozen_files},
        'inputs_and_code_unchanged': True, 'inputs_and_source_unchanged': True,
        'geometry_freeze_utc': geometry_freeze['utc'], 'cross_residuals_after_geometry_freeze': True,
        'truth_or_scores_read': False,
        'further_tuning': False, 'selected_formal_replacement': None})
    return {'status': 'P5_OBSERVATION_MODELS_FROZEN', 'branches': summaries}


def nonblind(source, output):
    """Existing 44/45 observations are post-freeze, nonblind diagnostics only."""
    source, output = Path(source).resolve(), Path(output).resolve()
    destination = output / 'nonblind_residuals.json'
    if destination.exists():
        raise FileExistsError('Refuse to overwrite nonblind diagnostics')
    freeze = json.loads((output / 'model_freeze.json').read_text())
    if freeze.get('status') != 'ALL_BRANCHES_FROZEN_BEFORE_NONBLIND_AND_TRUTH_EVALUATION':
        raise ValueError('Both final models must be frozen before nonblind diagnostics')
    verify_signatures({str(output / p): sha for p, sha in freeze['sha256'].items()})
    signatures = json.loads((output / 'fit_entry_inputs.json').read_text())
    verify_signatures(signatures)
    if str(source / 'observations/observations.json') not in signatures:
        raise ValueError('Nonblind source differs from frozen historical source')
    observations = json.loads((source / 'observations/observations.json').read_text())
    config = OptimizationConfig(**json.loads((output / 'frozen_config.json').read_text()))
    prepared = _prepare([o for o in observations if o['frame_id'] in (44, 45)],
                        config.resolutions[-1], config.device, require_contours=True)
    results = {}
    for name in BRANCHES:
        with np.load(output / name / 'T1_B1_exact.npz', allow_pickle=False) as arrays:
            vertices, faces = np.array(arrays['vertices'], copy=True), np.array(arrays['faces'], copy=True)
        class FrozenMesh(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.register_buffer('vertices', torch.tensor(vertices))
                self.register_buffer('faces', torch.tensor(faces))
            def forward(self):
                return self.vertices
        results[name] = _residuals(FrozenMesh(), prepared, config.resolutions[-1], config)
        for row in results[name]:
            row.update({'split': 'nonblind_diagnostic', 'fitted_model': name,
                        'observation_version': 'historical_44_45_unchanged'})
    verify_signatures(signatures)
    write_json(destination, {'status': 'NONBLIND_DIAGNOSTIC_ONLY',
        'independent_validation': False, 'results': results})
    return {'status': 'NONBLIND_DIAGNOSTIC_ONLY', 'path': str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fit', 'nonblind'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--annotation', type=Path)
    parser.add_argument('--protocol', type=Path)
    parser.add_argument('--isolation', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'fit':
        if any(v is None for v in (args.input, args.annotation, args.protocol)):
            parser.error('fit requires --input, --annotation and --protocol')
        result = fit(args.source, args.input, args.annotation, args.protocol, args.output, args.isolation)
    else:
        result = nonblind(args.source, args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
