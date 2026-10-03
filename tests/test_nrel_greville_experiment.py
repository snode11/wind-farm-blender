import copy

import pytest
import torch

from wfrl.nrel_reconstruction import greville_experiment as experiment
from wfrl.nrel_reconstruction import optimize
from wfrl.nrel_reconstruction.model import BladeModel


def test_shared_loop_reproduces_original_adam_and_budget(monkeypatch):
    observations = [{'split':'fit'} for _ in range(6)]
    calls = []
    def image_loss(model, obs, size, config):
        calls.append(tuple(size))
        return (model()[:, :2] - .1).square().mean(), {'contour_terms_available':1.0}
    def prepare(items, *args, **kwargs):
        return items
    for module in [experiment, optimize]:
        monkeypatch.setattr(module, '_image_loss', image_loss)
        monkeypatch.setattr(module, '_prepare', prepare)
    cfg = optimize.OptimizationConfig(model_interpolation='bspline', image_objective='projected_contour',
                                     resolutions=((160,90),(320,180)), iterations_per_stage=(2,3),
                                     batch_size=1, learning_rate=.012)
    initial = BladeModel(interpolation='bspline')
    actual, history, _ = experiment.fit_coordinate(initial, observations, cfg, 1.0)
    assert len(calls) == 30 and calls.count((160,90)) == 12 and calls.count((320,180)) == 18
    expected, _, _ = optimize._fit(initial, observations, cfg, 1.0)
    assert torch.equal(actual(), expected())
    assert len(history) == 5 and all(h['feasibility_halvings'] == 0 for h in history)


def test_feasibility_halves_coordinate_displacement_without_clipping():
    model = experiment.PhysicalReferenceBladeModel(interpolation='bspline')
    before = [p.detach().clone() for p in model.parameters()]
    with torch.no_grad():
        model.center_raw.fill_(1.6)
        model.chord_raw.fill_(.4)
    assert experiment.feasible_step(model, before) == 1
    assert torch.allclose(model.center_raw, torch.full_like(model.center_raw, .8))
    assert torch.allclose(model.chord_raw, torch.full_like(model.chord_raw, .2))


def test_infeasible_nonfinite_proposal_aborts_and_restores():
    model = experiment.PhysicalReferenceBladeModel(interpolation='bspline')
    before = [p.detach().clone() for p in model.parameters()]
    with torch.no_grad():
        model.center_raw.fill_(float('nan'))
    with pytest.raises(RuntimeError):
        experiment.feasible_step(model, before, maximum_halvings=2)
    assert all(torch.equal(p, old) for p,old in zip(model.parameters(), before))


def test_frozen_input_tampering_is_detected(tmp_path):
    p = tmp_path/'observations.json'
    p.write_text('first')
    signatures = {str(p):experiment.digest(p)}
    experiment.verify_signatures(signatures)
    p.write_text('modified')
    with pytest.raises(RuntimeError, match='changed'):
        experiment.verify_signatures(signatures)
