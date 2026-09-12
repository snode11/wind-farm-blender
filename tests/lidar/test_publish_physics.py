"""Production publication gates; all writes confined to pytest temporary paths."""
import importlib.util
import json
import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('lidar_publisher', ROOT / 'scripts/lidar/publish_physics.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


def test_empty_evidence_rejected_before_upgrade(tmp_path, monkeypatch):
    raw = tmp_path / 'results/lidar/raw/base'
    raw.mkdir(parents=True)
    (raw / 'run_config.json').write_text('{}')
    evidence = tmp_path / 'validation.json'
    evidence.write_text(json.dumps(dict(run_id='base', analytic={}, spatial={}, temporal={}, sampling={})))
    upgrade = Mock(side_effect=AssertionError('upgrade reached before evidence validation'))
    monkeypatch.setattr(publisher, 'ROOT', tmp_path)
    monkeypatch.setattr(publisher, 'upgrade', upgrade)
    with pytest.raises(ValueError):
        publisher.publish('base', 'normal', evidence, tmp_path / 'package')
    upgrade.assert_not_called()


@pytest.mark.parametrize('duration', [1, 18])
def test_no_evaluation_window_rejected_before_copy(duration, monkeypatch):
    spec = importlib.util.spec_from_file_location('lidar_run', ROOT / 'scripts/lidar/run_physics.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    copy = Mock(side_effect=AssertionError('copy reached before duration validation'))
    monkeypatch.setattr(cli.shutil, 'copytree', copy)
    with pytest.raises(ValueError):
        cli.prepare('short', 8, duration=duration)
    copy.assert_not_called()


def package_fixture():
    spec = importlib.util.spec_from_file_location('replay_fixture', ROOT / 'tests/lidar/test_replay.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PackageTests().fixture()


@pytest.mark.parametrize('mutation', ['nonfinite', 'wrong_run', 'wrong_temporal_base', 'wrong_interval'])
def test_new_publication_rejects_invalid_evidence(tmp_path, mutation):
    from wfrl.lidar.replay import publish_package
    m, motion, rows = package_fixture()
    evidence = m['validation']['numerical_evidence']
    if mutation == 'nonfinite':
        evidence['spatial']['max_truth_difference_m'] = float('inf')
    elif mutation == 'wrong_run':
        evidence['run_id'] = 'other-run'
    elif mutation == 'wrong_temporal_base':
        evidence['temporal']['base_run'] = 'other-run'
    else:
        evidence['sampling']['time_range_s'] = [1, 9]
    with pytest.raises(ValueError):
        publish_package(tmp_path/'new', m, motion, rows, 'fixture report')
    assert not (tmp_path/'new').exists()


def test_legacy_readable_but_cannot_publish_new(tmp_path):
    from wfrl.lidar.replay import ReplayPackage, publish_package, precompute, FILES
    m, motion, rows = package_fixture()
    m['validation'] = dict(analytic_verified=True, spatial_convergence_verified=True,
        temporal_convergence_verified=True, collision_excluded=True, truth_numerical_error_m=.01)
    with pytest.raises(ValueError, match='new publication requires'):
        publish_package(tmp_path/'new', m, motion, rows, 'legacy fixture')
    # Assemble an archived old-format fixture without using the new producer.
    old = tmp_path/'old'; old.mkdir()
    cumulative, statistics = precompute(rows, motion, m['replay'])
    content = dict(zip(FILES, (motion, rows, cumulative, statistics, 'legacy fixture')))
    for name, value in content.items():
        (old/name).write_text(value if name.endswith('.md') else json.dumps(value))
    m['files'] = {name: hashlib.sha256((old/name).read_bytes()).hexdigest() for name in FILES}
    (old/'manifest.json').write_text(json.dumps(m))
    assert ReplayPackage.load(old).statistics['valid_samples'] == 4


def test_publication_metadata_uses_configured_window_and_rate(tmp_path, monkeypatch):
    m, motion, rows = package_fixture()
    run = tmp_path/'results/lidar/raw/custom'; (run/'FarmInputs').mkdir(parents=True)
    cfg = dict(duration_s=40, startup_discard_s=20, fps=160, wind_mps=8, controller='fixture')
    (run/'run_config.json').write_text(json.dumps(cfg))
    (run/'FarmInputs/Case.T1.out').write_text('fixture provenance')
    (run/'surface_hashes.json').write_text('[]')
    evidence_path = tmp_path/'evidence.json'
    evidence_path.write_text(json.dumps(m['validation']['numerical_evidence']))
    draft = dict(collision_excluded=True, collision_certificates=1, tip_reference='fixture',
        calibration={}, measurements=rows, motion=motion)
    capture = Mock(return_value=tmp_path/'output')
    monkeypatch.setattr(publisher, 'ROOT', tmp_path)
    monkeypatch.setattr(publisher, 'validate_sources', lambda *args: [])
    monkeypatch.setattr(publisher, 'upgrade', lambda *args: draft)
    monkeypatch.setattr(publisher, 'publish_package', capture)
    publisher.publish('custom', 'normal', evidence_path, tmp_path/'output')
    manifest, report = capture.call_args.args[1], capture.call_args.args[4]
    assert manifest['segment']['start_s'] == 20
    assert manifest['segment']['end_s'] == 40
    assert '160Hz' in manifest['validation']['scope']
    assert '真实40秒' in report and '20–40秒' in report and '固定160Hz' in report
    assert '80Hz' not in report and '真实36秒' not in report
