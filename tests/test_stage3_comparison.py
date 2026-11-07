"""Comparison selects independent completed runs with matching experiment inputs."""
import copy
import json
import sys

import pytest

from scripts.analysis import compare_stage3 as comparison
from scripts.train.train_fastfarm import experiment_config
from wfrl.training_integrity import experiment_identity, file_digest


def run_fixture(tmp_path, name, policy, source='source-a', power=100., **changes):
    config = experiment_config('fixture', 2 if policy == 'zero' else 3, 4,
                               policy=policy, seed=0, **changes)
    checkpoint = tmp_path / (name + '.pt')
    checkpoint.write_bytes(name.encode())
    run = dict(schema=1, run_id=name, source_identity=source, status='completed',
               experiment_id=experiment_identity(config, source), recovery_mode='fresh',
               parent_run_id=None, control_steps=config['iters'] * 4,
               checkpoint=str(checkpoint))
    data = dict(tag='fixture', hist=dict(cfg=config, run=run,
                                       power_norm=[power] * config['iters']),
                checkpoint_sha256=file_digest(checkpoint))
    path = tmp_path / (name + '_hist.json')
    path.write_text(json.dumps(data))
    return dict(path=str(path), tag=data['tag'], hist=data['hist'], cfg=config,
                checkpoint_sha256=data['checkpoint_sha256'])


def test_zero_gain_sources_cannot_be_combined_into_a_positive_gain(tmp_path, monkeypatch, capsys):
    run_fixture(tmp_path, 'z_zero', 'zero')
    run_fixture(tmp_path, 'a_learn', 'learn')
    run_fixture(tmp_path, 'a_zero', 'zero', source='source-b', power=200.)
    run_fixture(tmp_path, 'z_learn', 'learn', source='source-b', power=200.)
    monkeypatch.setattr(comparison.paths, 'RUNS', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['compare_stage3', '--tag', 'fixture', '--last', '1'])
    assert comparison.main() == 0
    output = capsys.readouterr().out
    assert '+100.00%' not in output
    assert output.count('→  +0.00%') == 2


def test_repeated_runs_require_explicit_pair_selection(tmp_path):
    zero = run_fixture(tmp_path, 'zero', 'zero')
    learn = run_fixture(tmp_path, 'learn', 'learn')
    repeated = run_fixture(tmp_path, 'repeat', 'learn')
    with pytest.raises(ValueError, match='ambiguous'):
        comparison.pair_runs([zero, learn, repeated])
    assert comparison.pair_runs([zero, learn, repeated], [('zero', 'learn')]) == [(zero, learn)]


@pytest.mark.parametrize('change', [dict(env_id='other'), dict(reward_alpha=.2),
                                   dict(lr=.02), dict(obs_duty=True), dict(seed=1),
                                   dict(scene={'backend': 'other'})])
def test_explicit_pair_rejects_any_common_config_mismatch(tmp_path, change):
    zero = run_fixture(tmp_path, 'zero', 'zero')
    learn = run_fixture(tmp_path, 'learn', 'learn')
    learn['cfg'].update(change)
    run = learn['hist']['run']
    run['experiment_id'] = experiment_identity(learn['cfg'], run['source_identity'])
    with pytest.raises(ValueError, match='configuration'):
        comparison.pair_runs([zero, learn], [('zero', 'learn')])


@pytest.mark.parametrize('damage', ['source', 'incomplete', 'recovery', 'identity', 'nan',
                                   'empty', 'zero-baseline', 'checkpoint', 'duplicate-id',
                                   'overflow-baseline', 'overflow-policy'])
def test_invalid_or_unrelated_artifacts_are_refused(tmp_path, damage):
    zero = run_fixture(tmp_path, 'zero', 'zero')
    learn = run_fixture(tmp_path, 'learn', 'learn')
    run = learn['hist']['run']
    runs = [zero, learn]
    if damage == 'source':
        run['source_identity'] = 'other'
        run['experiment_id'] = experiment_identity(learn['cfg'], 'other')
    elif damage == 'incomplete': run['status'] = 'running'
    elif damage == 'recovery': run['recovery_mode'] = 'new_episode_segment'
    elif damage == 'identity': learn['cfg']['gamma'] = .5
    elif damage == 'nan': learn['hist']['power_norm'][0] = float('nan')
    elif damage == 'empty': learn['hist']['power_norm'] = []
    elif damage == 'zero-baseline': zero['hist']['power_norm'] = [0., 0.]
    elif damage == 'checkpoint': (tmp_path / 'learn.pt').write_bytes(b'changed')
    elif damage == 'duplicate-id': runs.append(copy.deepcopy(learn))
    elif damage == 'overflow-baseline': zero['hist']['power_norm'] = [1e308, 1e308]
    elif damage == 'overflow-policy': learn['hist']['power_norm'] = [1e308] * 3
    with pytest.raises(ValueError):
        comparison.pair_runs(runs, [('zero', 'learn')])
