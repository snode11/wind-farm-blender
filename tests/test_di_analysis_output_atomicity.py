"""Failed analysis writes preserve existing artifacts and release owned figures."""

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import matplotlib.pyplot as plt
from matplotlib.figure import Figure
import numpy as np
import pytest

from scripts.analysis import blade_del, compare_pinned_seeds as pinned, compare_stage3 as stage3
from scripts.analysis import compare_static_dynamic as static
from scripts.analysis import eval_visualize_ppo as visualize, export_blade_response as response
from test_di_analysis_statistics import history, write_history
from test_stage3_comparison import run_fixture


@pytest.fixture(autouse=True)
def close_test_figures():
    yield
    plt.close('all')


def test_del_json_serialization_failure_leaves_no_artifact(tmp_path, monkeypatch):
    output = tmp_path / 'results.json'
    monkeypatch.setattr(blade_del, 'build_report', lambda *a: {'valid': 1., 'invalid': np.nan})
    with pytest.raises(ValueError):
        blade_del.main(['--output', str(output)])
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_del_publish_does_not_overwrite_racing_result(tmp_path, monkeypatch):
    output = tmp_path / 'results.json'

    def build(*args):
        output.write_bytes(b'other result')
        return {'value': 1}

    monkeypatch.setattr(blade_del, 'build_report', build)
    with pytest.raises(FileExistsError):
        blade_del.main(['--output', str(output)])
    assert output.read_bytes() == b'other result'


@pytest.mark.parametrize('filename', ['results.json', 'manifest.json'])
def test_response_package_write_failure_leaves_no_partial_directory(tmp_path, monkeypatch, filename):
    output = tmp_path / 'response'
    monkeypatch.setattr(response, 'build', lambda *a: ({'schema': 'fixture'}, {}))
    write_bytes, write_text = Path.write_bytes, Path.write_text

    def bytes_write(path, data):
        if path.name == filename:
            write_bytes(path, b'partial')
            raise OSError('disk full')
        return write_bytes(path, data)

    def text_write(path, data, *args, **kwargs):
        if path.name == filename:
            write_text(path, 'partial', *args, **kwargs)
            raise OSError('disk full')
        return write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_bytes', bytes_write)
    monkeypatch.setattr(Path, 'write_text', text_write)
    with pytest.raises(OSError, match='disk full'):
        response.export(tmp_path, tmp_path, output)
    assert not output.exists()
    assert list(tmp_path.iterdir()) == []


def test_response_package_preserves_content_and_digest(tmp_path, monkeypatch):
    output = tmp_path / 'response'
    result = dict(schema='fixture', turbines={'T1': {'turns': []}})
    monkeypatch.setattr(response, 'build', lambda *a: (result, {'source': 'digest'}))
    assert response.export(tmp_path, tmp_path, output) == result
    raw = (output / 'results.json').read_bytes()
    assert raw == (json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n').encode()
    manifest = json.loads((output / 'manifest.json').read_bytes())
    assert manifest['files']['results.json'] == response.digest(output / 'results.json')
    with pytest.raises(FileExistsError):
        response.export(tmp_path, tmp_path, output)


def plot_invocation(name, tmp_path, monkeypatch):
    if name == 'timeseries':
        monkeypatch.setattr(visualize, 'OUT_DIR', str(tmp_path))
        return tmp_path / 'ppo_timeseries.png', lambda: visualize.plot_timeseries(
            np.zeros((2, 2)), np.ones((2, 2)), np.ones(2), 2)
    if name == 'wakefield':
        monkeypatch.setattr(visualize, 'OUT_DIR', str(tmp_path))
        monkeypatch.setattr(visualize.wakeviz, 'visualize_cut_plane', lambda *a, **k: None)
        monkeypatch.setattr(visualize.wakeviz, 'plot_turbines_with_fi', lambda *a, **k: None)
        fi = SimpleNamespace(calculate_horizontal_plane=lambda **k: None)
        env = SimpleNamespace(get_inner_env=lambda: SimpleNamespace(mdp=SimpleNamespace(interface=SimpleNamespace(fi=fi))))
        return tmp_path / 'wakefield_fixture.png', lambda: visualize.plot_wakefield(env, 'fixture')
    if name == 'stage3':
        run_fixture(tmp_path, 'zero', 'zero')
        run_fixture(tmp_path, 'learn', 'learn')
        monkeypatch.setattr(stage3.paths, 'RUNS', str(tmp_path))
        monkeypatch.setattr(sys, 'argv', ['compare', '--tag', 'fixture'])
        return tmp_path / 'stage3_fixture_curves.png', stage3.main
    if name == 'pinned':
        write_history(tmp_path, history())
        monkeypatch.setattr(pinned.paths, 'RUNS', str(tmp_path))
        monkeypatch.setattr(pinned.paths, 'FIGS', str(tmp_path))
        return tmp_path / 'fig_pinned_seeds.png', pinned.main
    first = write_history(tmp_path, history())
    second = write_history(tmp_path, history('Fastfarm'))
    output = tmp_path / 'comparison.png'
    monkeypatch.setattr(static, 'RUNS_DIR', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['compare', '--static', str(first), '--dynamic', str(second),
                                    '--out', str(output)])
    return output, static.main


@pytest.mark.parametrize('name', ['timeseries', 'wakefield', 'stage3', 'pinned', 'static'])
@pytest.mark.parametrize('existing', [False, True])
def test_plot_write_failure_preserves_output_and_closes_figure(tmp_path, monkeypatch, name, existing):
    output, run = plot_invocation(name, tmp_path, monkeypatch)
    if existing:
        output.write_bytes(b'previous complete image')
    before = {path.name for path in tmp_path.iterdir()}
    figures = set(plt.get_fignums())

    def fail_save(fig, target, *args, **kwargs):
        if hasattr(target, 'write'):
            target.write(b'partial image')
        else:
            Path(target).write_bytes(b'partial image')
        raise OSError('image encoding failed')

    monkeypatch.setattr(Figure, 'savefig', fail_save)
    with pytest.raises(OSError, match='image encoding failed'):
        run()
    assert (output.read_bytes() == b'previous complete image') if existing else not output.exists()
    assert {path.name for path in tmp_path.iterdir()} == before
    assert set(plt.get_fignums()) == figures


def setup_visualize_main(monkeypatch):
    monkeypatch.setattr(visualize, 'CheckpointPair', lambda *a: SimpleNamespace(require=lambda: ('model', 'norm')))
    monkeypatch.setattr(visualize, 'PPO', SimpleNamespace(load=lambda *a: object()))
    monkeypatch.setattr(visualize, 'load_normalizer', lambda *a: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(visualize, 'validate_model_normalization', lambda *a: None)
    monkeypatch.setattr(visualize, 'build_env', lambda: SimpleNamespace(
        close=lambda: None, reset=lambda **k: None, n_turbines=2))
    monkeypatch.setattr(visualize, 'rollout', lambda *a: (np.zeros((2, 2)), np.ones((2, 2)), np.ones(2)))


@pytest.mark.parametrize('existing', [True, False])
@pytest.mark.parametrize('failure', ['render', 'publish'])
def test_visualize_failed_report_preserves_all_prior_images(tmp_path, monkeypatch, existing, failure):
    setup_visualize_main(monkeypatch)
    monkeypatch.setattr(visualize, 'OUT_DIR', str(tmp_path))
    names = ['wakefield_baseline.png', 'wakefield_trained.png', 'ppo_timeseries.png']
    if existing:
        for name in names:
            (tmp_path / name).write_bytes(b'previous ' + name.encode())
    monkeypatch.setattr(visualize.wakeviz, 'visualize_cut_plane', lambda *a, **k: None)
    monkeypatch.setattr(visualize.wakeviz, 'plot_turbines_with_fi', lambda *a, **k: None)
    fi = SimpleNamespace(calculate_horizontal_plane=lambda **k: None)
    monkeypatch.setattr(visualize, 'build_env', lambda: SimpleNamespace(
        close=lambda: None, reset=lambda **k: None, n_turbines=2,
        get_inner_env=lambda: SimpleNamespace(mdp=SimpleNamespace(interface=SimpleNamespace(fi=fi)))))
    calls = []

    def save(fig, path, *args, **kwargs):
        calls.append(path)
        Path(path).write_bytes(b'new image')
        if failure == 'render' and len(calls) == 3:
            raise OSError('last image failed')

    monkeypatch.setattr(Figure, 'savefig', save)
    if failure == 'publish':
        replace = visualize.os.replace

        def fail_publish(source, target):
            if Path(source).name == 'wakefield_trained.png':
                raise OSError('last image failed')
            return replace(source, target)

        monkeypatch.setattr(visualize.os, 'replace', fail_publish)
    with pytest.raises(OSError, match='last image failed'):
        visualize.main()
    for name in names:
        if existing:
            assert (tmp_path / name).read_bytes() == b'previous ' + name.encode()
        else:
            assert not (tmp_path / name).exists()
