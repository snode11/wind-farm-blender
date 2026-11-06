"""Pure validation helpers only: never build the product ZIP or launch Blender."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from blender_frontend.tests._platform import symlink_or_skip


COMMAND_ENV = {'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' and 'SystemRoot' in os.environ else {}


def validator():
    source = Path(__file__).resolve().parents[2]/'scripts/blender/validate_frontend_package.py'
    spec = importlib.util.spec_from_file_location('frontend_package_validator', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def package(tmp_path):
    # Two trivial byte strings exercise integrity logic; no repository source
    # or extension builder is used to construct this synthetic fixture.
    payload = {'__init__.py': b'# fixture\n', 'assets/fixture.json': b'{"fixture":true}\n'}
    archive, inventory_path = tmp_path/'fixture.zip', tmp_path/'fixture.inventory.json'
    with ZipFile(archive, 'w') as zipped:
        for name, data in payload.items():
            zipped.writestr(name, data)
    inventory = {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'files': [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in payload.items()]}
    inventory_path.write_text(json.dumps(inventory))
    installed = tmp_path/'installed'
    for name, data in payload.items():
        path = installed/name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_bytes(data)
    return archive, inventory_path, inventory, installed


def test_zip_and_installed_copy_match_inventory(package):
    archive, inventory_path, inventory, installed = package
    module = validator()
    assert module.verify_archive(archive, inventory_path) == inventory
    assert module.verify_installed(installed, inventory)['verified_files'] == 2


def test_mutated_zip_bytes_fail(package):
    archive, inventory_path, _, _ = package
    archive.write_bytes(archive.read_bytes()+b'changed')
    with pytest.raises(ValueError, match='Archive SHA-256'):
        validator().verify_archive(archive, inventory_path)


def test_same_archive_with_false_member_digest_fails(package):
    archive, inventory_path, inventory, _ = package
    inventory['files'][0]['sha256'] = '0'*64
    inventory_path.write_text(json.dumps(inventory))
    with pytest.raises(ValueError, match='ZIP payload'):
        validator().verify_archive(archive, inventory_path)


@pytest.mark.parametrize('change', ['modified', 'missing', 'extra', 'symlink'])
def test_installed_mismatch_or_extra_source_cannot_pass(package, change, tmp_path):
    _, _, inventory, installed = package
    target = installed/'__init__.py'
    if change == 'modified':
        target.write_bytes(b'altered')
    elif change == 'missing':
        target.unlink()
    elif change == 'extra':
        (installed/'rogue.py').write_text('pass')
    else:
        copy = tmp_path/'outside.py'
        copy.write_bytes(target.read_bytes())
        target.unlink()
        symlink_or_skip(target, copy)
    with pytest.raises(ValueError):
        validator().verify_installed(installed, inventory)


def test_bytecode_is_the_only_ignored_installed_file_kind(package):
    _, _, inventory, installed = package
    cache = installed/'__pycache__'
    cache.mkdir()
    (cache/'fixture.cpython-311.pyc').write_bytes(b'cache')
    assert validator().verify_installed(installed, inventory)['status'] == 'PASS'


@pytest.mark.parametrize('name', ['../outside', '/absolute', 'folder/../../bad', 'folder\\bad'])
def test_unsafe_inventory_paths_are_rejected(name):
    with pytest.raises(ValueError, match='Unsafe'):
        validator().safe_relative(name)


def test_zero_exit_without_required_pass_marker_is_rejected(tmp_path):
    with pytest.raises(RuntimeError, match='missing_markers'):
        validator().run_command([sys.executable, '-c', 'print("not verified")'], env=COMMAND_ENV, output=tmp_path,
                                name='missing', required_markers=['ACTUAL_PASS'])
    assert (tmp_path/'logs/missing.log').exists()


def test_error_log_overrides_even_an_existing_pass_marker(tmp_path):
    with pytest.raises(RuntimeError, match='error_markers'):
        validator().run_command([sys.executable, '-c', 'print("ACTUAL_PASS\\nError: failure")'], env=COMMAND_ENV, output=tmp_path,
                                name='error', required_markers=['ACTUAL_PASS'])


def test_required_marker_and_clean_exit_pass(tmp_path):
    result = validator().run_command([sys.executable, '-c', 'print("ACTUAL_PASS")'], env=COMMAND_ENV, output=tmp_path,
                                     name='pass', required_markers=['ACTUAL_PASS'])
    assert result['status'] == 'PASS'


def test_repo_add_without_stable_stdout_marker_requires_followup_probe(tmp_path):
    result = validator().run_command([sys.executable, '-c', 'pass'], env=COMMAND_ENV, output=tmp_path, name='repo-add')
    assert result['status'] == 'EXIT_ZERO_REQUIRES_PROBE'


def test_exact_known_exit_memory_diagnostic_is_recorded_as_warning(tmp_path):
    text = 'ACTUAL_PASS\nError: Not freed memory blocks: 350, total unfreed memory 0.025940 MB'
    result = validator().run_command([sys.executable, '-c', f'print({text!r})'], env=COMMAND_ENV, output=tmp_path,
                                     name='memory-warning', required_markers=['ACTUAL_PASS'])
    assert result['status'] == 'PASS'
    assert result['warnings'][0]['blocks'] == 350
    assert result['warnings'][0]['bytes'] == round(.025940*1024*1024)
    assert result['warnings'][0]['message'] == text.splitlines()[1]


def test_other_memory_errors_are_not_filtered(tmp_path):
    text = 'ACTUAL_PASS\nError: Not freed memory blocks: invalid data'
    with pytest.raises(RuntimeError, match='error_markers'):
        validator().run_command([sys.executable, '-c', f'print({text!r})'], env=COMMAND_ENV, output=tmp_path,
                                name='other-error', required_markers=['ACTUAL_PASS'])


@pytest.mark.parametrize(('failing_test', 'invalid_field'), [(None, None)] + [
    (name, field) for name in ('radar-x100-detection-mode', 'radar-x100-clearance')
    for field in ('status', 'module', 'addon_path')
])
def test_validation_checks_installed_regressions_in_isolation(
        package, tmp_path, monkeypatch, failing_test, invalid_field):
    archive, inventory_path, inventory, _ = package
    inventory.update(package_id='wfrl_blender', version='1.0.0')
    inventory_path.write_text(json.dumps(inventory), encoding='utf-8')
    checksum = tmp_path / 'fixture.sha256'
    checksum.write_text(f'{inventory["archive_sha256"]}  {archive.name}\n', encoding='utf-8')
    built = SimpleNamespace(archive=archive, inventory=inventory_path, checksum=checksum,
                            sha256=inventory['archive_sha256'])
    monkeypatch.setitem(sys.modules, 'build_extension', SimpleNamespace(build=lambda *args, **kwargs: built))
    for key in ('BLENDER_USER_CONFIG', 'BLENDER_USER_RESOURCES', 'BLENDER_USER_EXTENSIONS',
                'WFRL_EXPECTED_ADDON_ROOT'):
        monkeypatch.setenv(key, '/inherited/daily')
    monkeypatch.setenv('PYTHONPATH', '/inherited/workspace')
    module = validator()
    output = tmp_path / 'validation'
    resources = output / 'profile'
    repository = resources / 'extensions'
    addon = repository / 'wfrl_blender'
    calls = []
    regressions = {
        'three-camera-default': ('three_camera_default_regression.py', 'THREE_CAMERA_DEFAULT_PASS', 'default-validation.json'),
        'native-projection': ('native_camera_projection_regression.py', 'NATIVE_PROJECTION_PASS', 'native-projection-validation.json'),
        'radar-x100-detection-mode': ('radar_x100_detection_mode.py', 'X100_DETECTION_MODE_PASS', 'result.json'),
        'radar-x100-clearance': ('radar_x100_clearance.py', 'X100_CLEARANCE_PASS', 'result.json'),
    }

    def fake_command(command, *, env, name, **kwargs):
        assert command[0] == '/fake/Blender'
        assert '--disable-autoexec' in command
        assert command[command.index('--python-expr') + 1] == 'import sys; sys.dont_write_bytecode=True'
        assert env['BLENDER_USER_RESOURCES'] == str(resources)
        assert env['BLENDER_USER_EXTENSIONS'] == str(repository)
        assert env['BLENDER_USER_CONFIG'] == str(resources / 'config')
        assert env['WFRL_ADDON_MODULE'] == module.MODULE
        assert env['WFRL_EXPECTED_ADDON_ROOT'] == str(addon)
        assert 'PYTHONPATH' not in env
        assert '/inherited/daily' not in env.values()
        calls.append(name)
        data = dict(status='PASS', module=module.MODULE, addon_path=str(addon / '__init__.py'))
        if name.endswith('-probe'):
            path = Path(command[command.index('--output') + 1])
            path.write_text(json.dumps(data), encoding='utf-8')
        elif name == 'install-file':
            with ZipFile(archive) as zipped:
                zipped.extractall(addon)
        elif name in regressions:
            script, marker, filename = regressions[name]
            assert '--background' in command
            assert Path(command[command.index('--python') + 1]).name == script
            assert kwargs['required_markers'] == [marker]
            destination = Path(env['WFRL_TEST_OUTPUT'])
            assert not destination.exists()
            destination.mkdir(parents=True)
            if name == failing_test:
                data[invalid_field] = {'status': 'FAIL', 'module': 'wfrl_blender',
                    'addon_path': str(module.ROOT / 'blender_frontend/wfrl_blender/__init__.py')}[invalid_field]
            (destination / filename).write_text(json.dumps(data), encoding='utf-8')
        return dict(status='PASS')

    monkeypatch.setattr(module, 'run_command', fake_command)
    code = module.validate(SimpleNamespace(output=output, blender='/fake/Blender', build_only=False, timeout=1))
    result = json.loads((output / 'package-validation.json').read_text(encoding='utf-8'))
    expected_calls = ['repo-add', 'repo-probe', 'install-file', 'module-probe', *regressions]
    if failing_test:
        assert code == 1 and result['status'] == 'ERROR'
        assert 'AssertionError' in result['error']
        assert failing_test not in result['checks']
        assert calls == expected_calls[:expected_calls.index(failing_test) + 1]
    else:
        assert code == 0 and result['status'] == 'PASS'
        assert calls == expected_calls
        for name in regressions:
            assert result['checks'][name]['status'] == 'PASS'
            assert result['checks'][name]['module'] == module.MODULE
            assert Path(result['checks'][name]['addon_path']).parent == addon
    environment = json.loads((output / 'environment.json').read_text(encoding='utf-8'))
    assert environment['BLENDER_USER_RESOURCES'] == str(resources)
    assert environment['BLENDER_USER_EXTENSIONS'] == str(repository)
    assert environment['WFRL_EXPECTED_ADDON_ROOT'] == str(addon)
    assert result['environment'] == environment


@pytest.fixture
def installed_provenance(tmp_path, monkeypatch):
    source = Path(__file__).parent / 'blender' / '_native.py'
    spec = importlib.util.spec_from_file_location('native_package_helpers', source)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    package = tmp_path / 'installed'
    addon = SimpleNamespace(__name__='bl_ext.wfrl_frontend.wfrl_blender',
                            __file__=str(package / '__init__.py'))
    modules = {addon.__name__: addon}
    for name in ('detection', 'clearance'):
        modules[addon.__name__ + '._vendor.radar_x100.' + name] = SimpleNamespace(
            __file__=str(package / '_vendor/radar_x100' / (name + '.py')))
    monkeypatch.setattr(helpers, 'sys', SimpleNamespace(modules=modules))
    return helpers.installed_provenance, addon, modules


def test_installed_provenance_reports_vendor_modules_and_bundled_data(installed_provenance):
    report, addon, modules = installed_provenance
    data = Path(addon.__file__).parent / 'assets/mappo'
    result = report(addon, farm_package=data)
    assert result['imported_modules'] == {name: module.__file__ for name, module in modules.items()}
    assert result['data_paths'] == {'farm_package': str(data)}


@pytest.mark.parametrize('name', ['wfrl', 'wfrl.radar_x100.detection',
                                  'wfrl_blender', 'wfrl_blender.radar_x100.runtime'])
def test_installed_provenance_rejects_workspace_modules(installed_provenance, name):
    report, addon, modules = installed_provenance
    modules[name] = SimpleNamespace()
    with pytest.raises(AssertionError, match='workspace module'):
        report(addon)


@pytest.mark.parametrize('kind', ['module', 'data'])
def test_installed_provenance_rejects_paths_outside_package(installed_provenance, tmp_path, kind):
    report, addon, modules = installed_provenance
    data = Path(addon.__file__).parent / 'assets/mappo'
    if kind == 'module':
        modules[addon.__name__ + '._vendor.radar_x100.detection'].__file__ = str(tmp_path / 'detection.py')
    else:
        data = tmp_path / 'workspace/assets/mappo'
    with pytest.raises(AssertionError):
        report(addon, farm_package=data)


def test_installed_provenance_does_not_restrict_source_mode(installed_provenance, tmp_path):
    report, addon, modules = installed_provenance
    addon.__name__ = 'wfrl_blender'
    modules['wfrl.radar_x100.detection'] = SimpleNamespace()
    assert report(addon, farm_package=tmp_path / 'source') == {}


def test_part7_install_isolates_every_blender_child(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'scripts/blender/verify_part7_install.py'
    spec = importlib.util.spec_from_file_location('part7_install_validator', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    built = SimpleNamespace(archive=tmp_path / 'fixture.zip')
    monkeypatch.setitem(sys.modules, 'build_extension', SimpleNamespace(build=lambda output_dir: built))
    monkeypatch.setenv('BLENDER_USER_RESOURCES', '/inherited/daily')
    monkeypatch.setenv('BLENDER_USER_EXTENSIONS', '/inherited/daily/extensions')
    calls = []

    class Probed(Exception):
        pass

    def fake_run(command, env, log, *, cwd):
        resources = Path(env['BLENDER_USER_CONFIG']).parent
        assert env['BLENDER_USER_RESOURCES'] == str(resources)
        assert env['BLENDER_USER_EXTENSIONS'] == str(resources / 'extensions')
        assert (resources / 'extensions').is_dir()
        calls.append(command)
        if '--inside' in command:
            raise Probed

    monkeypatch.setattr(module, '_run', fake_run)
    with pytest.raises(Probed):
        module.verify(evidence_dir=tmp_path / 'evidence')
    assert len(calls) == 3
