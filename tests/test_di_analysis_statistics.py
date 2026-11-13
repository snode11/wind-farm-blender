"""Analysis rejects incomplete measurements and unmatched comparison inputs."""

import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.analysis import blade_del, compare_eval, compare_pinned_seeds as pinned
from scripts.analysis import compare_static_dynamic as static
from scripts.analysis import eval_visualize_ppo as visualize, export_blade_response as response


def history(backend='Floris', seed=0, recurrent=False):
    config = dict(env_id=f'Dec_Turb3_Row1_{backend}', seed=seed, recurrent=recurrent,
                  iters=5, n_steps=8, wind_sched='pin:8', wind_direction=270., lr=.001)
    return dict(env_id=config['env_id'], hist=dict(
        cfg=config, reward=[1.] * 5, power_mw=[2.] * 5,
        power_norm=[3.] * 5, explained_var=[.5] * 5))


def write_history(tmp_path, data, *, speed='8', seed=None, suffix=''):
    cfg = data['hist']['cfg']
    backend = cfg['env_id'].rsplit('_', 1)[1]
    net = 'gru-mappo' if cfg['recurrent'] else 'mappo'
    seed = cfg['seed'] if seed is None else seed
    path = tmp_path / f'fastfarm_Dec_Turb3_Row1_{backend}_{net}_s{seed}_u{speed}{suffix}_hist.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


@pytest.mark.parametrize('damage', ['empty', 'nan', 'inf', 'missing', 'short', 'incomplete',
                                   'no-config', 'overflow'])
@pytest.mark.parametrize('reader', ['pinned', 'static'])
def test_history_rejects_invalid_samples_with_path(tmp_path, monkeypatch, damage, reader):
    data = history()
    hist = data['hist']
    if damage == 'empty':
        hist['power_mw'] = []
    elif damage in {'nan', 'inf'}:
        hist['power_mw'][0] = float(damage)
    elif damage == 'missing':
        del hist['power_mw']
    elif damage == 'short':
        hist['power_mw'].pop()
    elif damage == 'incomplete':
        hist['run'] = {'status': 'running'}
    elif damage == 'no-config':
        del hist['cfg']
    elif damage == 'overflow':
        hist['power_mw'] = [1e308] * 5
    path = tmp_path / 'fastfarm_Dec_Turb3_Row1_Floris_mappo_s0_u8_hist.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(pinned.paths, 'RUNS', str(tmp_path))
    with pytest.raises(ValueError, match=str(path)):
        pinned.load() if reader == 'pinned' else static._load(path)


@pytest.mark.parametrize('damage', ['wind', 'filename-seed', 'duplicate-seed', 'seed-set', 'config'])
def test_pinned_comparison_refuses_mismatched_runs(tmp_path, monkeypatch, damage):
    first = history()
    path = write_history(tmp_path, first)
    if damage == 'wind':
        path = write_history(tmp_path, first, speed='9')
    elif damage == 'filename-seed':
        path = write_history(tmp_path, first, seed=1)
    elif damage == 'duplicate-seed':
        path = write_history(tmp_path, first, speed='8.0')
    else:
        second = history('Fastfarm', seed=1 if damage == 'seed-set' else 0)
        if damage == 'config':
            second['hist']['cfg']['lr'] = .02
        path = write_history(tmp_path, second)
    monkeypatch.setattr(pinned.paths, 'RUNS', str(tmp_path))
    with pytest.raises(ValueError) as error:
        pinned.load()
    assert str(path) in str(error.value)


def test_pinned_single_seed_does_not_claim_backend_effect(tmp_path, monkeypatch, capsys):
    write_history(tmp_path, history())
    second = history('Fastfarm')
    second['hist']['power_mw'] = [4.] * 5
    write_history(tmp_path, second)
    monkeypatch.setattr(pinned.paths, 'RUNS', str(tmp_path))
    monkeypatch.setattr(pinned.paths, 'FIGS', str(tmp_path))
    pinned.main()
    output = capsys.readouterr().out
    assert '差异可谈' not in output
    assert 'seed 不足' in output


@pytest.mark.parametrize('damage', ['seed', 'config', 'missing-gru'])
def test_static_comparison_refuses_mismatches_before_drawing(tmp_path, monkeypatch, damage):
    first = write_history(tmp_path, history())
    data = history('Fastfarm')
    if damage == 'seed':
        data['hist']['cfg']['seed'] = 1
    elif damage == 'config':
        data['hist']['cfg']['lr'] = .02
    second = write_history(tmp_path, data)
    output = tmp_path / 'comparison.png'
    argv = ['compare', '--static', str(first), '--dynamic', str(second), '--out', str(output)]
    bad_path = second
    if damage == 'missing-gru':
        bad_path = tmp_path / 'absent.json'
        argv += ['--dynamic-gru', str(bad_path)]
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(ValueError, match=str(bad_path)):
        static.main()
    assert not output.exists()


@pytest.mark.parametrize('values', [[], [1., np.nan], [np.inf, 2.], [[1., 2.]], [1e308] * 3])
def test_episode_summary_rejects_invalid_statistics(values):
    with pytest.raises(ValueError):
        compare_eval.summarize('fixture', np.asarray(values))


@pytest.mark.parametrize('count', [0, -1, 1])
def test_comparison_requires_multiple_episodes_before_loading_models(monkeypatch, count):
    called = []
    monkeypatch.setattr(compare_eval, 'eval_ippo', lambda n: called.append(n) or np.ones(n))
    monkeypatch.setattr(compare_eval, 'eval_central', lambda n: np.ones(n))
    monkeypatch.setattr(sys, 'argv', ['compare', '--episodes', str(count)])
    with pytest.raises(SystemExit):
        compare_eval.main()
    assert called == []


def test_comparison_zero_variance_does_not_fabricate_significance(monkeypatch, capsys):
    monkeypatch.setattr(compare_eval, 'eval_ippo', lambda n: np.ones(n))
    monkeypatch.setattr(compare_eval, 'eval_central', lambda n: np.full(n, 2.))
    monkeypatch.setattr(sys, 'argv', ['compare', '--episodes', '2'])
    compare_eval.main()
    assert '显著更优' not in capsys.readouterr().out


@pytest.mark.parametrize('failure', [None, 'checkpoint', 'step'])
def test_central_evaluation_closes_sampler_environment(monkeypatch, failure):
    closed = []

    def fail():
        raise RuntimeError('evaluation failed')

    sampler = SimpleNamespace(obs_dim=1, act_dim=1, par=SimpleNamespace(close=lambda: closed.append(1)),
                              reset=lambda: np.zeros((1, 1)),
                              step=lambda a: fail() if failure == 'step' else (np.zeros((1, 1)), 2., True))
    monkeypatch.setattr(compare_eval.MC, 'Sampler', lambda: sampler)
    actor = SimpleNamespace(load_state_dict=lambda value: None, mu=lambda value: value)
    monkeypatch.setattr(compare_eval.MC, 'Actor', lambda *args: actor)
    monkeypatch.setattr(compare_eval.torch, 'load', lambda *a, **k: fail() if failure == 'checkpoint'
                        else dict(actor={}, obs_mean=np.zeros(1), obs_var=np.ones(1)))
    if failure:
        with pytest.raises(RuntimeError, match='evaluation failed'):
            compare_eval.eval_central(1)
    else:
        np.testing.assert_array_equal(compare_eval.eval_central(1), [2.])
    assert closed == [1]


@pytest.mark.parametrize('value', [np.nan, np.inf])
def test_ippo_evaluation_rejects_invalid_reward_and_closes(monkeypatch, value):
    closed = []
    env = SimpleNamespace(num_envs=1, reset=lambda: {}, close=lambda: closed.append(1),
                          step=lambda a: ({}, np.array([value]), np.array([True]), {}))
    model = SimpleNamespace(predict=lambda *a, **k: (None, None))
    monkeypatch.setattr(compare_eval.T, 'load_evaluation_pair', lambda: (model, env))
    with pytest.raises(ValueError, match='finite'):
        compare_eval.eval_ippo(1)
    assert closed == [1]


@pytest.mark.parametrize('damage', ['power-nan', 'power-shape', 'reward-inf', 'yaw-nan'])
def test_visualize_rollout_rejects_invalid_measurements(damage):
    obs, info, reward = {'yaw': [0., 0.]}, {'power': [1., 2.]}, 1.
    if damage == 'power-nan': info['power'][0] = np.nan
    elif damage == 'power-shape': info['power'].pop()
    elif damage == 'reward-inf': reward = np.inf
    elif damage == 'yaw-nan': obs['yaw'][0] = np.nan
    env = SimpleNamespace(n_turbines=2, reset=lambda **k: (obs, {}),
                          step=lambda a: (obs, reward, True, False, info))
    model = SimpleNamespace(predict=lambda *a, **k: (None, None))
    norm = SimpleNamespace(normalize_obs=lambda obs: obs)
    with pytest.raises(ValueError):
        visualize.rollout(env, model, norm)


def write_beamdyn(path, *, truncated=False):
    names = ['Time', *blade_del.CHANNELS, 'unused']
    units = ['(s)', *['(N-m)'] * 6, 'INVALID']
    rows = [' '.join(names), ' '.join(units), '0 1 1 1 1 1 1 ignored',
            '.025 2 2 2 2 2 2' + ('' if truncated else ' ignored')]
    path.write_text('\n' * 6 + '\n'.join(rows) + '\n', encoding='utf-8')


@pytest.mark.parametrize('reader', [blade_del.read_beamdyn, response.read_output])
@pytest.mark.parametrize('damage', ['truncated-row', 'short-header', 'bad-unit'])
def test_openfast_reader_errors_identify_source_path(tmp_path, reader, damage):
    path = tmp_path / 'Case.T1.out'
    write_beamdyn(path, truncated=damage == 'truncated-row')
    if damage == 'short-header':
        path.write_text('header only\n', encoding='utf-8')
    elif damage == 'bad-unit':
        path.write_text(path.read_text().replace('(s)', '(invalid)'), encoding='utf-8')
    with pytest.raises(ValueError, match=str(path)):
        reader(path)


@pytest.mark.parametrize('reader', [blade_del.read_beamdyn, response.read_output])
def test_openfast_readers_preserve_blank_line_compatibility(tmp_path, reader):
    path = tmp_path / 'Case.T1.out'
    write_beamdyn(path)
    lines = path.read_text().splitlines()
    path.write_text('\n'.join(lines[:8] + ['', lines[8], '', lines[9], '', '']), encoding='utf-8')
    result = reader(path)
    times = result[0] if reader is blade_del.read_beamdyn else result[0]['Time']
    np.testing.assert_array_equal(times, [0., .025])


@pytest.mark.parametrize('duration,frequency', [(1e308, 1e308), (1e-300, 1e-300)])
def test_del_rejects_unrepresentable_equivalent_cycles(duration, frequency):
    cycles = blade_del.rainflow_four_point([0., 2., 0.])
    with pytest.raises(ValueError, match='equivalent'):
        blade_del.damage_equivalent_load(cycles, duration, equivalent_frequency_hz=frequency)


def test_valid_summaries_and_phase_offsets_keep_existing_values():
    assert compare_eval.summarize('fixture', np.array([1., 3.])) == (2., 1., 2)
    assert static._tail_mean([1., 2., 3., 4., 5., 6.]) == 4.
    azimuth = np.arange(0, 2520.5, .5)
    cycles, grid, curves = response.phase_sample(azimuth, np.tile([1., 2., 3.], (len(azimuth), 1)))
    assert len(cycles) == 6 and len(grid) == 72
    assert response.describe(curves.mean(axis=0))['blade_offsets'] == [-1., 0., 1.]


def response_package(tmp_path, damage=None):
    times = np.arange(81) / 40.
    azimuth = np.arange(81) * 18.
    if damage == 'missing-start': times = times[1:]; azimuth = azimuth[1:]
    elif damage == 'nonfinite-time': times[0] = np.nan
    names, units = ['Time', 'Azimuth'], ['(s)', '(deg)']
    for suffix, expected, _ in response.CHANNELS.values():
        names.extend(f'B{b}{suffix}' for b in (1, 2, 3))
        units.extend([expected] * 3)
    raw = tmp_path / 'Case.T1.out'
    rows = [' '.join(names), ' '.join(units)]
    rows.extend(' '.join(map(str, [t, a, *([1.] * 27)])) for t, a in zip(times, azimuth))
    raw.write_text('\n' * 6 + '\n'.join(rows) + '\n', encoding='utf-8')
    use = np.isfinite(times)
    payloads = {
        'data.json': {'T1': {'motion': [dict(time_s=float(t), azimuth_deg=float(a))
                                      for t, a in zip(times[use], azimuth[use])]}},
        'telemetry.json': {'sources': {'T1': {'path': str(raw), 'sha256': response.digest(raw)}}},
        'deflection-t1.json': {'simulation': np.ones((int(use.sum()), 3, 3)).tolist()},
    }
    for name, data in payloads.items():
        (tmp_path / name).write_text(json.dumps(data), encoding='utf-8')
    (tmp_path / 'geometry.npz').write_bytes(b'geometry fixture')
    manifest = dict(schema='wfrl.farm-flex-review.v3', structural_module='BeamDyn',
                    segment=dict(start_s=0., end_s=2.), source_fps=40., turbine_ids=['T1'],
                    files={name: response.digest(tmp_path / name)
                           for name in [*payloads, 'geometry.npz']})
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return raw


@pytest.mark.parametrize('damage', ['missing-start', 'nonfinite-time'])
def test_response_rejects_incomplete_source_clock_with_path(tmp_path, damage):
    raw = response_package(tmp_path, damage)
    with pytest.raises(ValueError, match=str(raw)):
        response.build(tmp_path, tmp_path)


def test_response_valid_source_retains_units_and_phase_means(tmp_path):
    response_package(tmp_path)
    result, _ = response.build(tmp_path, tmp_path)
    for key, (_, _, unit) in response.CHANNELS.items():
        metric = result['turbines']['T1']['metrics'][key]
        assert metric['unit'] == unit
        np.testing.assert_array_equal(metric['phase_mean'], np.ones((3, 72)))


def test_stage3_rejects_overflow_across_valid_seed_gains(tmp_path, monkeypatch, capsys):
    from scripts.analysis import compare_stage3 as stage3
    from test_stage3_comparison import run_fixture
    from wfrl.training_integrity import experiment_identity

    runs = []
    for seed in range(2):
        for policy, power in [('zero', 1e-306), ('learn', 1.5)]:
            run = run_fixture(tmp_path, f'{policy}{seed}', policy, power=power)
            run['cfg']['seed'] = seed
            identity = run['hist']['run']
            identity['experiment_id'] = experiment_identity(run['cfg'], identity['source_identity'])
            runs.append(run)
    monkeypatch.setattr(stage3, 'load', lambda tag: runs)
    monkeypatch.setattr(stage3.paths, 'RUNS', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['compare', '--tag', 'fixture'])
    assert stage3.main() == 1
    assert 'finite' in capsys.readouterr().err
    assert not (tmp_path / 'stage3_fixture_curves.png').exists()


@pytest.mark.parametrize('fault', ['variance', 'zero-ratio'])
def test_pinned_rejects_unrepresentable_group_statistics(tmp_path, monkeypatch, fault):
    for backend in ('Floris', 'Fastfarm'):
        for seed in range(2):
            data = history(backend, seed)
            data['hist']['power_mw'] = ([-1e155 if seed == 0 else 1e155] * 5
                                       if fault == 'variance' else [0. if backend == 'Fastfarm' else 2.] * 5)
            write_history(tmp_path, data)
    monkeypatch.setattr(pinned.paths, 'RUNS', str(tmp_path))
    monkeypatch.setattr(pinned.paths, 'FIGS', str(tmp_path))
    with pytest.raises(ValueError):
        pinned.main()
    assert not (tmp_path / 'fig_pinned_seeds.png').exists()


@pytest.mark.parametrize('damage', ['truncated-json', 'empty-turbines', 'duplicate-turbines'])
def test_response_rejects_invalid_package_identity_with_path(tmp_path, damage):
    response_package(tmp_path)
    path = tmp_path / 'manifest.json'
    manifest = json.loads(path.read_bytes())
    if damage == 'truncated-json':
        path.write_text('{', encoding='utf-8')
    else:
        manifest['turbine_ids'] = [] if damage == 'empty-turbines' else ['T1', 'T1']
        path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match=str(tmp_path)):
        response.build(tmp_path, tmp_path)


@pytest.mark.parametrize('moment,duration,m', [(1e308, 1e-300, 10.), (1e-300, 1e300, 1.)])
def test_del_rejects_nonrepresentable_result(moment, duration, m):
    cycles = [blade_del.Cycle(moment, 0., 1., 0, 1)]
    with pytest.raises(ValueError, match='representable'):
        blade_del.damage_equivalent_load(cycles, duration, m)


def test_comparison_rejects_overflowing_test_statistic(monkeypatch):
    monkeypatch.setattr(compare_eval, 'eval_ippo', lambda n: np.full(n, 1e307))
    monkeypatch.setattr(compare_eval, 'eval_central', lambda n: np.array([-1e-150, 1e-150]))
    monkeypatch.setattr(sys, 'argv', ['compare', '--episodes', '2'])
    with pytest.raises(ValueError, match='finite'):
        compare_eval.main()


@pytest.mark.parametrize('fault', ['sum', 'mean'])
def test_visualize_rejects_overflowing_farm_statistics(fault):
    obs = {'yaw': [0., 0.]}
    powers = [1e308, 1e308] if fault == 'sum' else [5e307, 5e307]
    calls = []

    def step(action):
        calls.append(1)
        return obs, 0., len(calls) == 2, False, {'power': powers}

    env = SimpleNamespace(n_turbines=2, reset=lambda **k: (obs, {}), step=step)
    model = SimpleNamespace(predict=lambda *a, **k: (None, None))
    norm = SimpleNamespace(normalize_obs=lambda obs: obs)
    with pytest.raises(ValueError, match='finite'):
        visualize.rollout(env, model, norm)


@pytest.mark.parametrize('explicit', [False, True])
def test_stage3_pairing_error_identifies_history_paths(tmp_path, explicit):
    from scripts.analysis import compare_stage3 as stage3
    from test_stage3_comparison import run_fixture
    from wfrl.training_integrity import experiment_identity

    zero = run_fixture(tmp_path, 'zero', 'zero')
    learn = run_fixture(tmp_path, 'learn', 'learn')
    learn['cfg']['seed'] = 1
    identity = learn['hist']['run']
    identity['experiment_id'] = experiment_identity(learn['cfg'], identity['source_identity'])
    with pytest.raises(ValueError) as error:
        stage3.pair_runs([zero, learn], [('zero', 'learn')] if explicit else ())
    assert zero['path'] in str(error.value)


def test_pinned_matching_seed_curves_keep_existing_mean_and_range(tmp_path, monkeypatch):
    import matplotlib.pyplot as plt
    from matplotlib.figure import Figure

    for backend in ('Floris', 'Fastfarm'):
        for seed in range(3):
            data = history(backend, seed)
            data['hist']['power_mw'] = [1. + seed] * 5
            write_history(tmp_path, data)
    monkeypatch.setattr(pinned.paths, 'RUNS', str(tmp_path))
    monkeypatch.setattr(pinned.paths, 'FIGS', str(tmp_path))
    curves = []

    def save(fig, path, **kwargs):
        curves.extend(line.get_ydata().copy() for line in fig.axes[0].lines[:2])
        from pathlib import Path
        Path(path).write_bytes(b'image fixture')

    monkeypatch.setattr(Figure, 'savefig', save)
    figures = set(plt.get_fignums())
    pinned.main()
    np.testing.assert_array_equal(curves, np.full((2, 5), 2.))
    assert set(plt.get_fignums()) == figures
