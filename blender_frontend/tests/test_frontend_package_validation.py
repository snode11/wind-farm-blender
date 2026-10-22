"""Pure validation helpers only: never build the product ZIP or launch Blender."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest


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
        target.symlink_to(copy)
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
        validator().run_command([sys.executable, '-c', 'print("not verified")'], env={}, output=tmp_path,
                                name='missing', required_markers=['ACTUAL_PASS'])
    assert (tmp_path/'logs/missing.log').exists()


def test_error_log_overrides_even_an_existing_pass_marker(tmp_path):
    with pytest.raises(RuntimeError, match='error_markers'):
        validator().run_command([sys.executable, '-c', 'print("ACTUAL_PASS\\nError: failure")'], env={}, output=tmp_path,
                                name='error', required_markers=['ACTUAL_PASS'])


def test_required_marker_and_clean_exit_pass(tmp_path):
    result = validator().run_command([sys.executable, '-c', 'print("ACTUAL_PASS")'], env={}, output=tmp_path,
                                     name='pass', required_markers=['ACTUAL_PASS'])
    assert result['status'] == 'PASS'


def test_repo_add_without_stable_stdout_marker_requires_followup_probe(tmp_path):
    result = validator().run_command([sys.executable, '-c', 'pass'], env={}, output=tmp_path, name='repo-add')
    assert result['status'] == 'EXIT_ZERO_REQUIRES_PROBE'


def test_exact_known_exit_memory_diagnostic_is_recorded_as_warning(tmp_path):
    text = 'ACTUAL_PASS\nError: Not freed memory blocks: 350, total unfreed memory 0.025940 MB'
    result = validator().run_command([sys.executable, '-c', f'print({text!r})'], env={}, output=tmp_path,
                                     name='memory-warning', required_markers=['ACTUAL_PASS'])
    assert result['status'] == 'PASS'
    assert result['warnings'][0]['blocks'] == 350
    assert result['warnings'][0]['bytes'] == round(.025940*1024*1024)
    assert result['warnings'][0]['message'] == text.splitlines()[1]


def test_other_memory_errors_are_not_filtered(tmp_path):
    text = 'ACTUAL_PASS\nError: Not freed memory blocks: invalid data'
    with pytest.raises(RuntimeError, match='error_markers'):
        validator().run_command([sys.executable, '-c', f'print({text!r})'], env={}, output=tmp_path,
                                name='other-error', required_markers=['ACTUAL_PASS'])
