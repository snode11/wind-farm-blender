"""Bundled vendor modules must include their static local import dependencies."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import PurePosixPath

import pytest

from blender_frontend.tests.test_extension_package import load_builder


def _module_name(path):
    parts = PurePosixPath(path).with_suffix('').parts
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


def _missing_vendor_imports(payloads, source_roots):
    bundled = {_module_name(path): (path, data) for path, data in payloads
               if path.startswith('_vendor/') and path.endswith('.py')}
    available = set(bundled)
    for package, roots in source_roots.items():
        for root in roots:
            for path in root.rglob('*.py'):
                suffix = _module_name(path.relative_to(root).as_posix())
                available.add(f'_vendor.{package}' + ('.' + suffix if suffix else ''))

    def absolute_name(name, package):
        for vendor in source_roots:
            for prefix in (f'wfrl_blender._vendor.{vendor}', f'wfrl.{vendor}', vendor):
                if name == prefix or name.startswith(prefix + '.'):
                    return f'_vendor.{vendor}' + name[len(prefix):]
        local = f'_vendor.{package}.{name}'
        return local if local in available else name

    missing = set()
    for module, (path, data) in bundled.items():
        context = module if path.endswith('/__init__.py') else module.rpartition('.')[0]
        package = module.split('.')[1] if '.' in module else ''
        for node in ast.walk(ast.parse(data, filename=path)):
            if isinstance(node, ast.Import):
                dependencies = {absolute_name(alias.name, package) for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                target = (importlib.util.resolve_name('.' * node.level + (node.module or ''), context)
                          if node.level else absolute_name(node.module or '', package))
                dependencies = {target}
                # Imported attributes are not modules; source files identify submodule imports.
                dependencies.update(f'{target}.{alias.name}' for alias in node.names
                                    if f'{target}.{alias.name}' in available)
            else:
                continue
            for dependency in dependencies:
                if not dependency.startswith('_vendor.'):
                    continue
                required = {dependency}
                parent = dependency.rpartition('.')[0]
                while parent:
                    if parent in available:
                        required.add(parent)
                    parent = parent.rpartition('.')[0]
                for target in required - bundled.keys():
                    missing.add(f'{path}:{node.lineno}: {ast.unparse(node)} -> missing {target}')
    return sorted(missing)


def test_bundled_vendor_imports_are_closed():
    builder = load_builder()
    payloads = builder._payloads()
    packages = {PurePosixPath(path).parts[1] for path, _ in payloads
                if path.startswith('_vendor/') and path.endswith('.py')
                and len(PurePosixPath(path).parts) >= 3}
    roots = {package: (builder.PACKAGE_SOURCE / '_vendor' / package,
                       builder.ROOT / 'wfrl' / package, builder.ROOT / package)
             for package in packages}
    missing = _missing_vendor_imports(payloads, roots)
    assert not missing, 'Unbundled vendor import dependencies:\n' + '\n'.join(missing)


def test_radar_observation_modules_are_bundled():
    builder = load_builder()
    payloads = dict(builder._payloads())
    for name in ('detection.py', 'clearance.py'):
        assert payloads['_vendor/radar_x100/' + name] == (builder.RADAR_SOURCE / name).read_bytes()


@pytest.mark.parametrize('statement', [
    'from .helper import VALUE',
    'from . import helper as local',
    'from wfrl.example.helper import VALUE',
    'from wfrl.example import helper',
    'import wfrl.example.helper as local',
    'from example import helper',
    'import wfrl_blender._vendor.example.helper',
    'import helper as local',
    'from helper import VALUE',
])
def test_vendor_guard_finds_lazy_local_imports(tmp_path, statement):
    (tmp_path / 'helper.py').write_text('VALUE = 1\n', encoding='utf-8')
    payloads = [('_vendor/__init__.py', b''), ('_vendor/example/__init__.py', b''),
                ('_vendor/example/reader.py', f'def read():\n    {statement}\n'.encode())]
    missing = _missing_vendor_imports(payloads, {'example': (tmp_path,)})
    assert len(missing) == 1
    assert missing[0].startswith('_vendor/example/reader.py:2:')
    assert missing[0].endswith('-> missing _vendor.example.helper')
    payloads.append(('_vendor/example/helper.py', b'VALUE = 1\n'))
    assert not _missing_vendor_imports(payloads, {'example': (tmp_path,)})


def test_vendor_guard_resolves_parent_imports_and_ignores_attributes(tmp_path):
    (tmp_path / 'helper.py').write_text('VALUE = 1\n', encoding='utf-8')
    payloads = [('_vendor/__init__.py', b''),
                ('_vendor/example/__init__.py', b'VALUE = 1\n'),
                ('_vendor/example/nested/__init__.py', b''),
                ('_vendor/example/nested/reader.py', b'''import math
import numpy as np
from .. import VALUE
def read():
    from ..helper import VALUE as result
''')]
    missing = _missing_vendor_imports(payloads, {'example': (tmp_path,)})
    assert len(missing) == 1
    assert missing[0].endswith('-> missing _vendor.example.helper')
    payloads.append(('_vendor/example/helper.py', b'VALUE = 1\n'))
    assert not _missing_vendor_imports(payloads, {'example': (tmp_path,)})
