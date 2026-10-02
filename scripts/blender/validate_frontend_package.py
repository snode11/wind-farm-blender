#!/usr/bin/env python3
"""Build and validate a ZIP in a retained, completely isolated Blender profile.

No dist overwrite, version edit, existing installation change, or publication.
The default performs background checks only. UI checks run separately with the
saved environment.json and WFRL_ADDON_MODULE from the result.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
import traceback
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ID = 'wfrl_frontend'
MODULE = 'bl_ext.wfrl_frontend.wfrl_blender'
EXIT_MEMORY_WARNING = re.compile(r'^Error: Not freed memory blocks: (?P<blocks>\d+), total unfreed memory (?P<mb>\d+(?:\.\d+)?) MB[ \t]*$', re.MULTILINE)
ERROR_PATTERN = re.compile(r'Traceback \(most recent call last\)|(?:^|\n)(?:Error:|ERROR[ :]|FATAL[ :])|Segmentation fault|EXCEPTION_ACCESS_VIOLATION')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or '\\' in name:
        raise ValueError(f'Unsafe inventory path: {name}')
    return relative


def verify_archive(archive, inventory):
    data = json.loads(Path(inventory).read_text())
    actual = sha256(archive)
    if actual != data['archive_sha256']:
        raise ValueError('Archive SHA-256 differs from inventory')
    expected = {row['path']: row for row in data['files']}
    if len(expected) != len(data['files']):
        raise ValueError('Duplicate inventory path')
    with ZipFile(archive) as zipped:
        if sorted(zipped.namelist()) != sorted(expected):
            raise ValueError('ZIP entries differ from inventory')
        for name, row in expected.items():
            safe_relative(name)
            payload = zipped.read(name)
            if len(payload) != row['size'] or hashlib.sha256(payload).hexdigest() != row['sha256']:
                raise ValueError(f'ZIP payload differs from inventory: {name}')
    return data


def verify_installed(package, inventory):
    package = Path(package).resolve()
    expected = {row['path']: row for row in inventory['files']}
    mismatches = []
    for name, row in expected.items():
        relative = safe_relative(name)
        path = package.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(package):
            mismatches.append({'path': name, 'reason': 'missing file or unsafe link'})
        elif path.stat().st_size != row['size'] or sha256(path) != row['sha256']:
            mismatches.append({'path': name, 'reason': 'size or SHA-256 mismatch'})
    extras = sorted(path.relative_to(package).as_posix() for path in package.rglob('*')
                    if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'
                    and path.relative_to(package).as_posix() not in expected)
    if mismatches or extras:
        raise ValueError(json.dumps({'installed_mismatches': mismatches, 'unexpected_files': extras}))
    return {'status': 'PASS', 'package_path': str(package), 'verified_files': len(expected),
            'mismatches': [], 'unexpected_files': [], 'ignored': ['__pycache__', '*.pyc']}


def run_command(command, *, env, output, name, required_markers=(), timeout=240.):
    started = time.monotonic()
    log = output/'logs'/f'{name}.log'
    log.parent.mkdir(exist_ok=True)
    try:
        completed = subprocess.run(command, cwd=output, env=env, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        content, code = completed.stdout, completed.returncode
    except subprocess.TimeoutExpired as exc:
        content = exc.stdout or ''
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        log.write_text(content, encoding='utf-8')
        raise RuntimeError(f'{name} timeout after {timeout}s; see {log}') from exc
    log.write_text(content, encoding='utf-8')
    missing = [marker for marker in required_markers if marker not in content]
    warnings = [{'kind': 'blender_exit_memory_diagnostic', 'message': match.group(0),
        'blocks': int(match.group('blocks')), 'reported_mb': float(match.group('mb')),
        'bytes': round(float(match.group('mb'))*1024*1024),
        'bytes_note': 'Reported MB converted by 1024^2 and rounded; limited by log precision'}
        for match in EXIT_MEMORY_WARNING.finditer(content)]
    errors = ERROR_PATTERN.findall(EXIT_MEMORY_WARNING.sub('', content))
    if code != 0 or missing or errors:
        raise RuntimeError(f'{name} failed: exit={code}, missing_markers={missing}, error_markers={errors}; see {log}')
    return {'command': command, 'returncode': code, 'log': str(log),
            'required_markers': list(required_markers), 'error_markers': errors, 'warnings': warnings,
            'elapsed_s': time.monotonic()-started,
            'status': 'PASS' if required_markers else 'EXIT_ZERO_REQUIRES_PROBE'}


def inside_probe(args):
    import bpy
    repository = Path(args.repository).resolve()
    repo = next((r for r in bpy.context.preferences.extensions.repos if r.module == REPOSITORY_ID), None)
    assert repo is not None, 'isolated repository was not saved'
    assert Path(repo.directory).resolve() == repository, (repo.directory, repository)
    assert not repo.remote_url, 'validation repository must be local'
    result = {'status': 'PASS', 'repository': str(repository), 'repo_module': repo.module,
              'blender': bpy.app.version_string, 'background': bpy.app.background}
    marker = 'FRONTEND_REPOSITORY_PROBE_PASS'
    if args.inside_probe == 'module':
        assert MODULE in bpy.context.preferences.addons, 'install-file --enable did not persist enablement'
        addon = importlib.import_module(MODULE)
        module_path = Path(addon.__file__).resolve()
        expected = repository/'wfrl_blender'/'__init__.py'
        assert module_path == expected, (module_path, expected)
        assert 'wfrl_blender' not in sys.modules, 'bare source addon imported alongside installed namespace'
        native = importlib.import_module(MODULE+'.native_camera_views')
        assert native.WFRL_OT_NativeCamera.is_registered, 'installed native camera operator is not registered'
        assert hasattr(bpy.types.Scene, 'wfrl_farm_panel_page'), 'installed scene properties absent'
        assert addon.__name__ == MODULE
        package = expected.parent
        imported = {}
        for name, module in tuple(sys.modules.items()):
            if name == MODULE or name.startswith(MODULE+'.'):
                path = getattr(module, '__file__', None)
                if path:
                    path = Path(path).resolve()
                    assert path.is_relative_to(package), (name, path)
                    imported[name] = str(path)
        result.update(module=MODULE, addon_path=str(module_path), imported_modules=imported,
                      installed_operator_registered=True)
        marker = 'FRONTEND_INSTALLED_MODULE_PROBE_PASS'
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(marker, json.dumps(result, ensure_ascii=False), flush=True)


def validate(args):
    output = args.output.expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('--output must be a new or empty directory')
    output.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 'wfrl.frontend-isolated-package.v1', 'status': 'INCOMPLETE',
              'output': str(output), 'module': MODULE, 'commands': [], 'checks': {},
              'build_only': args.build_only,
              'scope': 'isolated ZIP integrity and background regression; visible UI and presentation unverified'}
    result_path = output/'package-validation.json'
    def save():
        result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    save()
    try:
        from build_extension import build
        built = build(output/'package', dual_package=getattr(args, 'dual_package', None))
        inventory = verify_archive(built.archive, built.inventory)
        checksum = built.checksum.read_text().strip().split()
        assert checksum == [built.sha256, built.archive.name], 'checksum sidecar mismatch'
        result['package'] = {'archive': str(built.archive), 'sha256': built.sha256,
            'checksum': str(built.checksum), 'inventory': str(built.inventory),
            'package_id': inventory['package_id'], 'version': inventory['version'],
            'file_count': len(inventory['files'])}
        result['checks']['archive_inventory'] = 'PASS'
        if args.build_only:
            result.update(status='BUILT', installation='NOT_RUN', background_regressions='NOT_RUN')
            save()
            print('FRONTEND_PACKAGE_BUILT', str(result_path))
            return 0
        profile = output/'profile'
        directories = {key: profile/value for key, value in (
            ('BLENDER_USER_CONFIG', 'config'), ('BLENDER_USER_SCRIPTS', 'scripts'),
            ('BLENDER_USER_DATAFILES', 'datafiles'))}
        repository = profile/'extensions'
        for directory in (*directories.values(), repository):
            directory.mkdir(parents=True)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('BLENDER_USER_') and key not in {'PYTHONPATH', 'WFRL_ADDON_ROOT', 'WFRL_ADDON_PATH', 'WFRL_ADDON_MODULE', 'WFRL_TEST_OUTPUT'}}
        overrides = {key: str(value) for key, value in directories.items()}
        overrides['WFRL_ADDON_MODULE'] = MODULE
        env.update(overrides)
        (output/'environment.json').write_text(json.dumps(overrides, indent=2))
        result['environment'] = overrides
        result['repository'] = str(repository)
        def command(name, values, markers=(), test_output=None):
            child_env = dict(env)
            if test_output:
                test_output.mkdir(parents=True)
                child_env['WFRL_TEST_OUTPUT'] = str(test_output)
            row = run_command([str(args.blender), *values], env=child_env, output=output,
                              name=name, required_markers=markers, timeout=args.timeout)
            result['commands'].append(row)
            save()
        def probe(kind, marker):
            probe_path = output/f'{kind}-probe.json'
            command(f'{kind}-probe', ['--background', '--python-exit-code', '1',
                '--python', str(Path(__file__).resolve()), '--', '--inside-probe', kind,
                '--repository', str(repository), '--output', str(probe_path)], [marker])
            data = json.loads(probe_path.read_text())
            assert data['status'] == 'PASS'
            return data
        command('repo-add', ['--command', 'extension', 'repo-add', REPOSITORY_ID,
                '--name', 'WFRL Frontend Isolated Validation', '--directory', str(repository), '--clear-all'])
        result['checks']['repository_probe'] = probe('repo', 'FRONTEND_REPOSITORY_PROBE_PASS')
        command('install-file', ['--command', 'extension', 'install-file', '-r', REPOSITORY_ID,
                '--enable', str(built.archive)], [f'Installed "{inventory["package_id"]}"'])
        installed = repository/inventory['package_id']
        result['checks']['installed_inventory'] = verify_installed(installed, inventory)
        result['checks']['module_probe'] = probe('module', 'FRONTEND_INSTALLED_MODULE_PROBE_PASS')
        result['addon_path'] = result['checks']['module_probe']['addon_path']
        tests = (
            ('three-camera-default', 'three_camera_default_regression.py', 'THREE_CAMERA_DEFAULT_PASS', 'default-validation.json'),
            ('native-projection', 'native_camera_projection_regression.py', 'NATIVE_PROJECTION_PASS', 'native-projection-validation.json'),
        )
        for name, filename, marker, datafile in tests:
            destination = output/'regressions'/name
            command(name, ['--background', '--python-exit-code', '1', '--python',
                str(ROOT/'blender_frontend/tests/blender'/filename)], [marker], destination)
            data = json.loads((destination/datafile).read_text())
            assert data['status'] == 'PASS' and data['module'] == MODULE, data
            assert Path(data['addon_path']).resolve().is_relative_to(installed), data['addon_path']
            result['checks'][name] = data
        result['checks']['installed_inventory_after_regressions'] = verify_installed(installed, inventory)
        result.update(status='PASS', installation='PASS', background_regressions='PASS',
                      visible_ui='NOT_RUN', actual_presentation='NOT_RUN')
        save()
        print('FRONTEND_PACKAGE_VALIDATION_PASS', str(result_path))
        return 0
    except Exception:
        result.update(status='ERROR', error=traceback.format_exc())
        save()
        print('FRONTEND_PACKAGE_VALIDATION_ERROR', str(result_path), file=sys.stderr)
        print(result['error'], file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dual-package', type=Path)
    parser.add_argument('--blender', type=Path, default=Path('/Applications/Blender.app/Contents/MacOS/Blender'))
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--timeout', type=float, default=240., help='Per subprocess timeout in seconds')
    parser.add_argument('--inside-probe', choices=('repo', 'module'), help=argparse.SUPPRESS)
    parser.add_argument('--repository', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else None)
    if args.inside_probe:
        inside_probe(args)
        return 0
    return validate(args)


if __name__ == '__main__':
    raise SystemExit(main())
