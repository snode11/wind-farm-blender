"""Packaging failure contracts using only small temporary payloads and fake Blender."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from scripts.blender import build_extension as builder
from scripts.blender import build_farm_flex_delivery as delivery
from scripts.blender import validate_frontend_package as validator
from scripts.blender import verify_part7_install as smoke
from blender_frontend.tests.test_verify_part7_install import stub_install


@pytest.mark.parametrize('script', ['build_extension.py', 'validate_frontend_package.py',
                                  'verify_part7_install.py', 'blender_preflight.py',
                                  'build_farm_flex_delivery.py'])
def test_packaging_cli_help_does_not_require_workspace_pythonpath(tmp_path, script):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    env.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, str(root / 'scripts/blender' / script), '--help'],
                            cwd=tmp_path, env=env, capture_output=True, text=True,
                            encoding='utf-8', timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'usage:' in result.stdout
    assert not list(tmp_path.iterdir())


@pytest.fixture
def miniature_sources(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'blender_manifest.toml').write_text('id="fixture"\nversion="1.0.0"\n', encoding='utf-8')
    (source / '__init__.py').write_bytes(b'VALUE = 1\n')
    protocol = tmp_path / 'protocol.py'
    protocol.write_bytes(b'PROTOCOL = 1\n')
    lidar = tmp_path / 'lidar'
    lidar.mkdir()
    for name in ('replay.py', 'evidence.py', 'dual_beam_replay.py', 'molas_cl.py'):
        (lidar / name).write_bytes(b'VALUE = 1\n')
    radar = tmp_path / 'radar'
    radar.mkdir()
    for name in builder.RADAR_MODULES:
        (radar / name).write_bytes(b'VALUE = 1\n')
    farm = tmp_path / 'farm'
    farm.mkdir()
    (farm / 'data.json').write_bytes(b'{}\n')
    (farm / 'manifest.json').write_text(json.dumps({
        'files': {'data.json': hashlib.sha256(b'{}\n').hexdigest()},
        'controller_validation': [{'passed': True}],
    }), encoding='utf-8')
    for name, value in {
        'PACKAGE_SOURCE': source, 'PROTOCOL_SOURCE': protocol,
        'LIDAR_REPLAY_SOURCE': lidar / 'replay.py', 'LIDAR_EVIDENCE_SOURCE': lidar / 'evidence.py',
        'RADAR_SOURCE': radar,
    }.items():
        monkeypatch.setattr(builder, name, value)
    return source, farm, radar


def test_payload_collection_excludes_development_artifacts(miniature_sources):
    source, _, _ = miniature_sources
    artifacts = ('debug.log', 'scratch.tmp', 'editor.py~', 'editor.swp', 'module.pyo',
                 'tests/test_fixture.py', '.ruff_cache/entry', '.mypy_cache/entry',
                 '__pycache__/fixture.pyc', '.pytest_cache/entry', '.DS_Store')
    for name in artifacts:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'temporary')
    payloads = dict(builder._payloads())
    assert not set(artifacts).intersection(payloads)
    assert payloads['__init__.py'] == b'VALUE = 1\n'


def test_radar_identity_hashes_the_captured_zip_bytes(miniature_sources, tmp_path, monkeypatch):
    _, farm, radar = miniature_sources
    collect = builder._payloads

    def collect_then_edit():
        payloads = collect()
        (radar / 'detection.py').write_bytes(b'VALUE = 2\n')
        return payloads

    monkeypatch.setattr(builder, '_payloads', collect_then_edit)
    result = builder.build(tmp_path / 'output', farm_package=farm)
    validator.verify_archive(result.archive, result.inventory)
    with ZipFile(result.archive) as archive:
        info = json.loads(archive.read('build-info.json'))
        for name, digest in info['radar_core_sha256'].items():
            assert digest == hashlib.sha256(archive.read('_vendor/radar_x100/' + name)).hexdigest()


def test_farm_inventory_cannot_match_an_absent_empty_file():
    manifest = {'files': {'missing.json': hashlib.sha256(b'').hexdigest()}}
    with pytest.raises(ValueError, match='integrity'):
        builder._validate_farm_payloads([('assets/mappo/manifest.json', json.dumps(manifest).encode())])


def test_dual_payload_is_checked_after_source_validation(tmp_path, monkeypatch):
    payload = b'{"accepted": true}'
    (tmp_path / 'results.json').write_bytes(b'{"accepted": false}')
    overlay = {'config': {}, 'manifest': {
        'source_hashes': {}, 'files': {'results.json': hashlib.sha256(payload).hexdigest()},
    }}
    monkeypatch.setattr(builder, '_dual_reader', lambda: SimpleNamespace(resolve_package=lambda path: (path, overlay)))
    with pytest.raises(ValueError, match='integrity'):
        builder._bundle_dual([], tmp_path, 'dual_beam', 'hub-axis.v1')


@pytest.mark.parametrize('overwrite', [False, True])
@pytest.mark.parametrize('component', ['zip', 'checksum'])
def test_corrupt_staged_output_never_replaces_final_outputs(
        miniature_sources, tmp_path, monkeypatch, overwrite, component):
    _, farm, _ = miniature_sources
    output = tmp_path / 'output'
    output.mkdir()
    names = ('fixture-1.0.0.zip', 'fixture-1.0.0.zip.sha256', 'fixture-1.0.0.inventory.json')
    previous = {name: name.encode() for name in names} if overwrite else {}
    for name, data in previous.items():
        (output / name).write_bytes(data)
    write = ZipFile.writestr

    def corrupt(archive, info, data, *args, **kwargs):
        if getattr(info, 'filename', info) == '__init__.py':
            data = b'CORRUPTED\n'
        return write(archive, info, data, *args, **kwargs)

    if component == 'zip':
        monkeypatch.setattr(ZipFile, 'writestr', corrupt)
    else:
        write_text = Path.write_text

        def corrupt_checksum(path, content, *args, **kwargs):
            if path.name.endswith('.sha256'):
                content = '0' * 64 + '  fixture-1.0.0.zip\n'
            return write_text(path, content, *args, **kwargs)

        monkeypatch.setattr(Path, 'write_text', corrupt_checksum)
    with pytest.raises(ValueError, match='ZIP payload|checksum'):
        builder.build(output, farm_package=farm, overwrite=overwrite)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == previous


@pytest.mark.parametrize('name', ['alias//module.py', './module.py', 'alias/./module.py', 'bad\x00name'])
def test_inventory_paths_cannot_alias_other_members(name):
    with pytest.raises(ValueError, match='Unsafe'):
        validator.safe_relative(name)


def test_missing_installation_cannot_pass_with_empty_inventory(tmp_path):
    with pytest.raises(ValueError, match='[Ii]nstall|[Pp]ackage'):
        validator.verify_installed(tmp_path / 'missing', {'files': []})


@pytest.mark.parametrize('installed', [False, True])
def test_empty_package_inventory_cannot_pass(tmp_path, installed):
    inventory = {'files': []}
    if installed:
        with pytest.raises(ValueError, match='[Ee]mpty'):
            validator.verify_installed(tmp_path, inventory)
    else:
        archive = tmp_path / 'empty.zip'
        with ZipFile(archive, 'w'):
            pass
        inventory['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        path = tmp_path / 'inventory.json'
        path.write_text(json.dumps(inventory), encoding='utf-8')
        with pytest.raises(ValueError, match='[Ee]mpty'):
            validator.verify_archive(archive, path)


@pytest.mark.parametrize('failure', ['build', 'copy'])
def test_failed_delivery_leaves_no_destination(miniature_sources, tmp_path, monkeypatch, failure):
    _, farm, _ = miniature_sources
    destination = tmp_path / 'delivery'

    def fail(*args, **kwargs):
        raise OSError('injected delivery failure')

    if failure == 'build':
        monkeypatch.setattr(delivery, 'build', fail)
    else:
        monkeypatch.setattr(delivery.shutil, 'copytree', fail)
    before = set(tmp_path.iterdir())
    with pytest.raises(OSError, match='injected delivery failure'):
        delivery.bundle(farm, destination)
    assert set(tmp_path.iterdir()) == before


@pytest.mark.parametrize('validation', [[], [{'passed': 'false'}], [{'passed': 1}]])
def test_delivery_requires_explicit_controller_acceptance(miniature_sources, tmp_path, validation):
    _, farm, _ = miniature_sources
    manifest_path = farm / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['controller_validation'] = validation
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='controller'):
        delivery.bundle(farm, tmp_path / 'delivery')
    assert not (tmp_path / 'delivery').exists()


def test_delivery_data_must_match_captured_runtime(miniature_sources, tmp_path, monkeypatch):
    _, farm, _ = miniature_sources
    build = delivery.build

    def build_then_edit(*args, **kwargs):
        result = build(*args, **kwargs)
        (farm / 'data.json').write_bytes(b'{"changed": true}')
        return result

    monkeypatch.setattr(delivery, 'build', build_then_edit)
    with pytest.raises(ValueError, match='[Dd]elivery data'):
        delivery.bundle(farm, tmp_path / 'delivery')
    assert not (tmp_path / 'delivery').exists()


def test_successful_miniature_delivery_keeps_archive_and_inventory_contract(miniature_sources, tmp_path):
    _, farm, _ = miniature_sources
    destination = tmp_path / 'delivery'
    before = set(tmp_path.iterdir())
    delivery.bundle(farm, destination)
    archive = destination / 'runtime-archive/fixture-1.0.0.zip'
    inventory = validator.verify_archive(archive, archive.with_suffix('.inventory.json'))
    assert inventory['schema_version'] == 'wfrl.extension-package.v1'
    assert inventory['package_id'] == 'fixture'
    assert inventory['version'] == '1.0.0'
    assert archive.with_suffix('.zip.sha256').read_text(encoding='utf-8') == (
        f'{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n')
    validator.verify_installed(destination / 'blender_frontend/wfrl_blender', inventory)
    with ZipFile(archive) as zipped:
        assert zipped.namelist() == sorted(zipped.namelist())
        assert {info.date_time for info in zipped.infolist()} == {builder.ZIP_TIMESTAMP}
        assert zipped.read('assets/mappo/data.json') == (destination / 'data/data.json').read_bytes()
    report = json.loads((destination / 'inventory.json').read_text(encoding='utf-8'))
    assert report['status'] == 'REVIEW_ONLY'
    for name, digest in report['files'].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == digest
    assert set(tmp_path.iterdir()) == before | {destination}


@pytest.mark.parametrize('name', ['../escape.py', '/absolute.py', 'folder/../../escape.py', 'folder\\escape.py'])
def test_delivery_rejects_unsafe_archive_before_extraction(
        miniature_sources, tmp_path, monkeypatch, name):
    _, farm, _ = miniature_sources
    destination = tmp_path / 'delivery'

    def unsafe_build(output, **kwargs):
        output.mkdir(parents=True)
        archive = output / 'fixture.zip'
        with ZipFile(archive, 'w') as zipped:
            zipped.writestr(name, b'unsafe')
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        inventory = archive.with_suffix('.inventory.json')
        inventory.write_text(json.dumps({'archive_sha256': digest, 'files': [
            {'path': name, 'size': 6, 'sha256': hashlib.sha256(b'unsafe').hexdigest()},
        ]}), encoding='utf-8')
        return SimpleNamespace(archive=archive, inventory=inventory, sha256=digest)

    monkeypatch.setattr(delivery, 'build', unsafe_build)
    with pytest.raises(ValueError, match='Unsafe'):
        delivery.bundle(farm, destination)
    assert not destination.exists()


@pytest.mark.parametrize('status', ['FAIL', 'ERROR', None])
def test_install_smoke_requires_pass_report(tmp_path, monkeypatch, status):
    evidence = tmp_path / 'evidence'
    stub_install(monkeypatch, evidence)
    run = smoke.subprocess.run

    def invalid_report(command, **kwargs):
        result = run(command, **kwargs)
        if '--inside' in command:
            path = Path(command[command.index('--output') + 1])
            path.write_text(json.dumps({'status': status}), encoding='utf-8')
            result.stdout += 'WFRL_PART7_INSTALLED_SMOKE=PASS\n'
        return result

    monkeypatch.setattr(smoke.subprocess, 'run', invalid_report)
    with pytest.raises(RuntimeError, match='PASS'):
        smoke.verify(evidence_dir=evidence)
    assert not (evidence / 'package-install.json').exists()


@pytest.mark.parametrize('content', ['no completion marker', 'WFRL_PART7_INSTALLED_SMOKE=PASS\nError: failed'])
def test_install_smoke_rejects_missing_marker_or_error_log(tmp_path, monkeypatch, content):
    evidence = tmp_path / 'evidence'
    stub_install(monkeypatch, evidence)
    run = smoke.subprocess.run

    def incomplete(command, **kwargs):
        result = run(command, **kwargs)
        if '--inside' in command:
            result.stdout = content
        return result

    monkeypatch.setattr(smoke.subprocess, 'run', incomplete)
    with pytest.raises(RuntimeError):
        smoke.verify(evidence_dir=evidence)
    assert not (evidence / 'package-install.json').exists()


@pytest.fixture
def acceptance(tmp_path, monkeypatch):
    runtime = SimpleNamespace(build_live_scene=lambda scene: None,
                              get_state=lambda: SimpleNamespace(run_status='STOPPED'),
                              shutdown=lambda: None)
    quit_calls = []
    bpy = SimpleNamespace(
        context=SimpleNamespace(preferences=SimpleNamespace(view=SimpleNamespace())),
        app=SimpleNamespace(background=False, timers=SimpleNamespace(register=lambda *a, **k: None)),
        data=SimpleNamespace(screens=[]),
        ops=SimpleNamespace(wfrl=SimpleNamespace(
            connection_mode=lambda **kwargs: {'FINISHED'}, bridge_connect=lambda: {'FINISHED'}),
            wm=SimpleNamespace(quit_blender=lambda: quit_calls.append(True))))
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('dg_atomic_json', root / 'blender_frontend/wfrl_blender/atomic_json.py')
    atomic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(atomic)
    monkeypatch.setitem(sys.modules, 'bpy', bpy)
    monkeypatch.setitem(sys.modules, 'addon_utils', SimpleNamespace(enable=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, 'wfrl_blender', SimpleNamespace(runtime=runtime, charts=None, atomic_json=atomic))
    monkeypatch.setitem(sys.modules, 'wfrl_blender.atomic_json', atomic)
    monkeypatch.setitem(sys.modules, 'wfrl_blender.preferences', SimpleNamespace(get_preferences=lambda: SimpleNamespace()))
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setenv('WFRL_ACCEPT_OUTPUT', str(tmp_path))
    spec = importlib.util.spec_from_file_location('dg_acceptance', root / 'scripts/blender/accept_frontend.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def exit_failed(code):
        raise SystemExit(code)

    monkeypatch.setattr(module, 'os', SimpleNamespace(_exit=exit_failed))
    return module, quit_calls, atomic


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_failed_acceptance_exits_nonzero_and_records_failure(acceptance, cleanup_fails):
    module, quit_calls, _ = acceptance
    module.stage = 'complete' if cleanup_fails else 'running'
    if cleanup_fails:
        def fail():
            raise RuntimeError('cleanup failed')
        module.runtime.shutdown = fail
    with pytest.raises(SystemExit) as stopped:
        module.finish()
    assert stopped.value.code == 1
    report = json.loads((module.OUT / 'report.json').read_text(encoding='utf-8'))
    assert report['passed'] is False
    assert not quit_calls


def test_acceptance_finish_is_idempotent(acceptance):
    module, quit_calls, _ = acceptance
    module.stage = 'complete'
    cleanup = []
    module.runtime.shutdown = lambda: cleanup.append(True)
    module.finish()
    module.finish()
    assert cleanup == [True]
    assert quit_calls == [True]
    assert json.loads((module.OUT / 'report.json').read_text(encoding='utf-8'))['passed'] is True


def test_progress_write_failure_preserves_complete_previous_json(acceptance, monkeypatch):
    module, _, atomic = acceptance
    output = module.OUT / 'progress.json'
    output.write_text('{"previous": true}', encoding='utf-8')
    write = Path.write_text

    def fail_direct(path, *args, **kwargs):
        if path == output:
            path.write_bytes(b'{')
            raise OSError('write failed')
        return write(path, *args, **kwargs)

    def fail_replace(*args, **kwargs):
        raise OSError('write failed')

    monkeypatch.setattr(Path, 'write_text', fail_direct)
    monkeypatch.setattr(atomic.os, 'replace', fail_replace)
    with pytest.raises(OSError, match='write failed'):
        module.transition('running')
    assert json.loads(output.read_text(encoding='utf-8')) == {'previous': True}
    assert list(module.OUT.iterdir()) == [output]
