"""Predeclared finite-budget coordinate comparison; no truth imports or reads.

Run `fit` inside the saved OS isolation policy. Both meshes are signed before
`nonblind` is allowed. Historical optimizer and observation files are untouched.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch

from .dataset import write_json
from .greville_model import GrevilleBladeModel
from .model import BladeModel, write_ply
from .optimize import OptimizationConfig, _image_loss, _prepare, _residuals


BRANCHES = ['original_raw', 'physical_reference_center', 'greville_normalized']


class PhysicalReferenceBladeModel(BladeModel):
    """Cfree/5 is independent; retain original chord raw/tanh coordinate."""

    def control_values(self):
        c = torch.cat((self.center_raw.new_zeros((1, 2)), 5 * self.center_raw), dim=0)
        return c, .35 * torch.tanh(self.chord_raw)

    def metadata(self):
        result = super().metadata()
        result['optimization_coordinates'] = {'center_raw':'Cfree / 5; no center tanh',
                                               'chord_raw':'ell=.35*tanh(chord_raw)'}
        return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_signatures(signatures):
    changed = [p for p, sha in signatures.items() if digest(p) != sha]
    if changed:
        raise RuntimeError(f'Frozen inputs/code changed: {changed}')


def feasible(model):
    if isinstance(model, GrevilleBladeModel):
        return model.check_feasible()
    c, ell = model.control_values()
    return bool(torch.isfinite(c).all() and torch.isfinite(ell).all()
                and (c[1:].abs() < 5).all() and (ell.abs() < .35).all())


def feasible_step(model, before, maximum_halvings=30):
    """Restrict proposed coordinate displacement without looking at image loss."""
    parameters = list(model.parameters())
    proposed = [p.detach().clone() for p in parameters]
    for halvings in range(maximum_halvings + 1):
        if halvings:
            with torch.no_grad():
                for p, old, end in zip(parameters, before, proposed):
                    p.copy_(old + (end - old) * (2.0 ** -halvings))
        if feasible(model):
            return halvings
    with torch.no_grad():
        for p, old in zip(parameters, before):
            p.copy_(old)
    raise RuntimeError("No feasible coordinate step within predeclared budget")


def fit_coordinate(initial, observations, config, image_weight):
    model = copy.deepcopy(initial)
    optimizer = torch.optim.Adam(model.parameters(), lr=config.learning_rate)
    history, started = [], time.perf_counter()
    for stage, (size, iterations) in enumerate(zip(config.resolutions, config.iterations_per_stage)):
        fitting = _prepare(observations, size, config.device, require_contours=True)
        assert len(fitting) == 6 and all(o['split'] == 'fit' for o in fitting)
        for iteration in range(iterations):
            optimizer.zero_grad(set_to_none=True)
            values = model.regularization()
            regularization = (config.relative_change_weight * values['relative_parameter_change']
                              + config.smoothness_weight * values['span_smoothness'])
            regularization.backward()
            image_sum = 0.0
            if image_weight:
                for begin in range(0, len(fitting), config.batch_size):
                    batch_loss = next(model.parameters()).sum() * 0
                    for observation in fitting[begin:begin + config.batch_size]:
                        loss, terms = _image_loss(model, observation, size, config)
                        if not float(terms['contour_terms_available']):
                            raise RuntimeError('Missing fitting contour')
                        batch_loss = batch_loss + loss / len(fitting)
                    image_sum += float(batch_loss.detach())
                    (image_weight * batch_loss).backward()
            parameters = list(model.parameters())
            if any(p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in parameters):
                raise RuntimeError('Absent or nonfinite gradients')
            gradient_norm = float(torch.nn.utils.clip_grad_norm_(parameters, 10.0))
            before = [p.detach().clone() for p in parameters]
            optimizer.step()
            halvings = feasible_step(model, before)
            c, ell = model.control_values()
            history.append({'stage': stage, 'iteration': iteration, 'resolution': list(size),
                            'image_loss_pre_step': image_sum,
                            'regularization_pre_step': float(regularization.detach()),
                            'total_loss_pre_step': image_weight * image_sum + float(regularization.detach()),
                            'gradient_norm_pre_clip': gradient_norm, 'feasibility_halvings': halvings,
                            'center_bound_margin_m': float((5 - c[1:].abs()).min().detach()),
                            'chord_log_bound_margin': float((.35 - ell.abs()).min().detach())})
    return model, history, time.perf_counter() - started


def total_objective(model, prepared, size, config):
    values = model.regularization()
    result = config.relative_change_weight * values['relative_parameter_change']
    result = result + config.smoothness_weight * values['span_smoothness']
    for obs in prepared:
        loss, terms = _image_loss(model, obs, size, config)
        if not float(terms['contour_terms_available']):
            raise RuntimeError('Missing contour in preflight')
        result = result + config.image_weight * loss / len(prepared)
    return result


def objective_preflight(initial, observations, config):
    """Actual six-image objective and reverse-mode chain rule at initialization."""
    rows = []
    for size, branch in [(s, b) for s in config.resolutions for b in ['physical_reference_center','greville_normalized']]:
        old = copy.deepcopy(initial)
        if branch == 'greville_normalized':
            new = GrevilleBladeModel(old)
        else:
            new = PhysicalReferenceBladeModel(config.span_samples, config.ring_samples,
                                              config.control_stations, interpolation='bspline')
        prepared = _prepare(observations, size, config.device, require_contours=True)
        lhs, rhs = total_objective(old, prepared, size, config), total_objective(new, prepared, size, config)
        old_grad = torch.autograd.grad(lhs, tuple(old.parameters()))
        new_grad = torch.autograd.grad(rhs, tuple(new.parameters()))
        c, ell = old.control_values()
        mapped = (new.coordinates_from_controls(c, ell) if branch == 'greville_normalized'
                  else (c[1:] / 5, old.chord_raw))
        pulled = torch.autograd.grad(mapped, tuple(old.parameters()), grad_outputs=new_grad)
        expected = torch.cat([v.reshape(-1) for v in old_grad])
        actual = torch.cat([v.reshape(-1) for v in pulled])
        absolute = float((expected - actual).abs().max())
        relative = float(torch.linalg.vector_norm(expected - actual) / torch.linalg.vector_norm(expected).clamp_min(1e-8))
        terms_rows = []
        with torch.no_grad():
            for obs in prepared:
                _, t1 = _image_loss(old, obs, size, config)
                _, t2 = _image_loss(new, obs, size, config)
                terms_rows.append({'camera_id':obs['camera_id'],'frame_id':obs['frame_id'],
                                   'original':{k:float(v) for k,v in t1.items()},
                                   'transformed':{k:float(v) for k,v in t2.items()},
                                   'max_abs_difference':max(abs(float(t1[k]-t2[k])) for k in t1)})
        row = {'coordinate_branch':branch, 'resolution': list(size), 'vertex_max_abs_m': float((old() - new()).abs().max().detach()),
               'original_objective': float(lhs.detach()), 'greville_objective': float(rhs.detach()),
               'objective_abs_difference': abs(float(lhs.detach() - rhs.detach())),
               'pullback_gradient_max_abs': absolute, 'pullback_gradient_relative_l2': relative,
               'per_observation_terms':terms_rows}
        row['passed'] = (row['vertex_max_abs_m'] < 2e-6 and row['objective_abs_difference'] < 1e-6
                         and relative < 2e-4 and max(t['max_abs_difference'] for t in terms_rows) < 1e-6)
        rows.append(row)
    return {'rows': rows, 'passed': all(x['passed'] for x in rows),
            'scope': 'Initialization at all production resolutions; unit tests cover other feasible states. Numerical tolerances are not measurement accuracy.'}


def compare(observations, output_dir, reference_state, config):
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'model_freeze.json').exists() or (output / 'original_raw').exists():
        raise FileExistsError('Refuse to overwrite an experiment')
    config = OptimizationConfig(**config)
    if config.image_objective != 'projected_contour' or config.model_interpolation != 'bspline':
        raise ValueError('P4 is restricted to the frozen contour/B-spline experiment')
    torch.manual_seed(config.seed)
    torch.set_num_threads(config.threads)
    fitting = [o for o in observations if o['split'] == 'fit']
    if {(o['camera_id'], o['frame_id']) for o in fitting} != {(c, f) for c in ['C1','C2','C3'] for f in [42,43]}:
        raise ValueError('Unexpected fit set')
    source = Path(__file__).parent
    signatures = {str(p.resolve()): digest(p) for p in sorted(source.glob('*.py'))}
    runner = source.parents[1] / 'scripts/nrel_single_blade.py'
    signatures[str(runner.resolve())] = digest(runner)
    write_json(output / 'implementation_at_launch.json', signatures)
    entry_signatures = json.loads((output / 'fit_entry_inputs.json').read_text())
    verify_signatures(entry_signatures)
    initial = BladeModel(config.span_samples, config.ring_samples, config.control_stations,
                         interpolation=config.model_interpolation)
    preflight = objective_preflight(initial, fitting, config)
    write_json(output / 'objective_preflight.json', preflight)
    if not preflight['passed']:
        raise RuntimeError('Geometry/objective/gradient preflight failed; no fit launched')
    sidecar = {**reference_state, 'target':'T1/B1', 'coordinate_frame':'blade_root_local', 'units':'m',
               'surface_scope':'entire_evaluated_blade_surface', 'reference_shape':'static_near_t_ref',
               'deformation_assistance':'none'}
    summary = {}
    physical = PhysicalReferenceBladeModel(config.span_samples, config.ring_samples,
                                           config.control_stations, interpolation=config.model_interpolation)
    for name, start in [('original_raw', initial), ('physical_reference_center', physical),
                        ('greville_normalized', GrevilleBladeModel(initial))]:
        dest = output / name
        dest.mkdir()
        prior, prior_history, prior_s = fit_coordinate(start, fitting, config, 0.0)
        final, history, wall_s = fit_coordinate(start, fitting, config, config.image_weight)
        models = {'initial_template':start, 'prior_only':prior, 'T1_B1':final}
        for kind, model in models.items():
            write_ply(dest / f'{kind}.ply', model().detach().numpy(), model.faces.numpy())
            np.savez_compressed(dest / f'{kind}_exact.npz', vertices=model().detach().numpy(), faces=model.faces.numpy())
            write_json(dest / f'{kind}.json', {**sidecar, **model.metadata(), 'model_name':kind})
        write_json(dest / 'model_state.json', {**sidecar, 'models':{k:m.metadata() for k,m in models.items()}})
        prepared = _prepare(fitting, config.resolutions[-1], config.device, require_contours=True)
        residuals = {k:_residuals(m, prepared, config.resolutions[-1], config) for k,m in models.items()}
        final_objective = float(total_objective(final, prepared, config.resolutions[-1], config).detach())
        write_json(dest / 'optimization.json', {'config':asdict(config), 'coordinate_branch':name,
                   'prior_only_history':prior_history, 'video_history':history, 'fit_residuals':residuals,
                   'final_640_total_objective':final_objective,
                   'comparison_provenance':{'same_initial_parameters':True,'same_optimizer':True,
                       'same_stage_schedule':True,'same_regularization':True,'same_budget':True,
                       'only_image_weights_differ':True,'prior_only_image_weight':0.0,'final_image_weight':1.0},
                   'provenance_scope':'Within each coordinate branch: video versus prior-only. Across branches optimizer coordinates differ.',
                   'nonblind_status':'NOT_EVALUATED_BEFORE_MODEL_FREEZE',
                   'nonblind_preverification':'All 12 historical observations and RGB hashes prevalidated by existing fit_bundle; only 6 fit observations enter objective or gradients.'})
        write_json(dest / 'surface_evidence.json', {'target':'T1/B1','units':'m','coordinate_frame':'blade_root_local',
                   'regions':[{'name':n,'span_bounds_m':z,'status':'prior_only'} for n,z in
                               [('root',[0,20.5]),('middle',[20.5,41]),('tip',[41,61.5])]],
                   'full_surface_recovery':False,'backside_and_thickness':'prior_only'})
        summary[name] = {'video_fit_wall_s':wall_s,'prior_only_wall_s':prior_s,'steps':len(history),
                         'image_forward_backward_evaluations':len(history)*len(fitting),
                         'feasibility_reduced_steps':sum(h['feasibility_halvings'] > 0 for h in history),
                         'min_center_bound_margin_m':min(h['center_bound_margin_m'] for h in history),
                         'min_chord_log_bound_margin':min(h['chord_log_bound_margin'] for h in history),
                         'final_640_total_objective':final_objective}
        print(json.dumps({name:summary[name]}), flush=True)
    verify_signatures(signatures)
    verify_signatures(entry_signatures)
    paths = [p for name in summary for p in (output/name).glob('*') if p.is_file()]
    paths += [output / name for name in ['implementation_at_launch.json','fit_entry_inputs.json','objective_preflight.json']]
    write_json(output / 'model_freeze.json', {'utc':datetime.now(timezone.utc).isoformat(),
               'status':'ALL_BRANCHES_FROZEN_BEFORE_NONBLIND_AND_TRUTH_EVALUATION',
               'sha256':{str(p.relative_to(output)):digest(p) for p in paths},
               'inputs_and_source_unchanged':True,
               'further_tuning':False, 'selected_formal_replacement':None})
    write_json(output / 'fit_summary.json', summary)
    return {'status':'P4_COORDINATE_MODELS_FROZEN','branches':summary}


def nonblind(source, output):
    freeze = json.loads((output/'model_freeze.json').read_text())
    if any(digest(output/p) != sha for p,sha in freeze['sha256'].items()):
        raise RuntimeError('Frozen fit artifacts changed')
    verify_signatures(json.loads((output/'fit_entry_inputs.json').read_text()))
    verify_signatures(json.loads((output/'implementation_at_launch.json').read_text()))
    observations = json.loads((source/'observations/observations.json').read_text())
    config = OptimizationConfig(**json.loads((source/'reconstruction/frozen_config.json').read_text()))
    prepared = _prepare([o for o in observations if o['frame_id'] in [44,45]], config.resolutions[-1], 'cpu', True)
    results = {}
    for name in BRANCHES:
        arrays = np.load(output/name/'T1_B1_exact.npz', allow_pickle=False)
        class FrozenMesh(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.register_buffer('vertices', torch.tensor(arrays['vertices']))
                self.register_buffer('faces', torch.tensor(arrays['faces']))
            def forward(self):
                return self.vertices
        model = FrozenMesh()
        results[name] = _residuals(model, prepared, config.resolutions[-1], config)
        for row in results[name]:
            row['split'] = 'nonblind_diagnostic'
    write_json(output/'nonblind_residuals.json', {'status':'NONBLIND_DIAGNOSTIC_ONLY','results':results})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fit','nonblind'])
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.mode == 'fit':
        from scripts.nrel_single_blade import fit_bundle
        args.output.mkdir(parents=True, exist_ok=True)
        entry = args.output/'fit_entry_inputs.json'
        if entry.exists():
            raise FileExistsError('Refuse to overwrite input freeze; use a new fit output')
        files = list((args.source/'observations').glob('*')) + list(args.input.glob('*'))
        files += [args.source/'reconstruction/frozen_config.json',
                  args.output.parent/'protocol.json', args.output.parent/'protocol_addendum.json']
        write_json(entry, {str(p.resolve()):digest(p) for p in files if p.is_file()})
        with patch('wfrl.nrel_reconstruction.optimize.reconstruct', compare):
            result = fit_bundle(args.input, args.source/'observations', args.output,
                                args.source/'reconstruction/frozen_config.json')
        print(json.dumps(result))
    else:
        nonblind(args.source, args.output)


if __name__ == '__main__':
    main()
