"""Product text I/O must select an encoding independently of the host locale."""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT_ROOTS = ('blender_frontend/wfrl_blender', 'wfrl', 'scripts')

# Keys identify one exact call, not a whole file or API. Values explain why its
# producer requires the native locale instead of the project's UTF-8 encoding.
LOCALE_ENCODING_ALLOWLIST = {
    ('scripts/experiments/probe_episodes.py',
     "subprocess.run(['tasklist'], capture_output=True, text=True)"):
        'Windows tasklist emits localized system output; its decoder must use the native locale.',
}

# (mode argument position, encoding argument position, default mode).
FILE_APIS = {
    'open': (1, 3, 'r'),
    'builtins.open': (1, 3, 'r'),
    'io.open': (1, 3, 'r'),
    'os.fdopen': (1, 3, 'r'),
    'gzip.open': (1, 3, 'rb'),
    'bz2.open': (1, 3, 'rb'),
    'lzma.open': (1, None, 'rb'),
    'tempfile.NamedTemporaryFile': (0, 3, 'w+b'),
    'tempfile.TemporaryFile': (0, 3, 'w+b'),
    'tempfile.SpooledTemporaryFile': (1, 3, 'w+b'),
}
COMPRESSED_APIS = {'gzip.open', 'bz2.open', 'lzma.open'}
SUBPROCESS_APIS = {f'subprocess.{name}' for name in (
    'run', 'Popen', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput',
)}
PATH_CLASSES = {'pathlib.Path', 'pathlib.PosixPath', 'pathlib.WindowsPath'}
NON_TEXT_OPEN_APIS = {'os.open', 'PIL.Image.open', 'tarfile.open', 'zipfile.ZipFile.open'}


def _qualified_name(node, imports):
    if isinstance(node, ast.Name):
        return imports.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f'{_qualified_name(node.value, imports)}.{node.attr}'
    return ''


def _argument(call, name, position=None, default=None):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if position is not None and len(call.args) > position:
        return call.args[position]
    return ast.Constant(value=default)


def _enabled(value):
    return not isinstance(value, ast.Constant) or bool(value.value)


def _has_encoding(call, position=None):
    encoding = _argument(call, 'encoding', position)
    return not isinstance(encoding, ast.Constant) or encoding.value is not None


def _missing_encodings(source):
    tree = ast.parse(source)
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split('.')[0]] = (
                    alias.name if alias.asname else alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports[alias.asname or alias.name] = f'{node.module}.{alias.name}'

    missing = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = _qualified_name(call.func, imports)
        method = call.func.attr if isinstance(call.func, ast.Attribute) else None
        owner = name.rpartition('.')[0]
        if name in NON_TEXT_OPEN_APIS:
            continue
        if name in SUBPROCESS_APIS:
            text_mode = (name in {'subprocess.getoutput', 'subprocess.getstatusoutput'}
                         or _enabled(_argument(call, 'text'))
                         or _enabled(_argument(call, 'universal_newlines', 11))
                         or _enabled(_argument(call, 'errors')))
            if text_mode and not _has_encoding(call):
                missing.append(call)
            continue
        if name == 'io.TextIOWrapper':
            if not _has_encoding(call, 1):
                missing.append(call)
            continue
        if method in {'read_text', 'write_text'}:
            position = (0 if method == 'read_text' else 1) + (owner in PATH_CLASSES)
            if not _has_encoding(call, position):
                missing.append(call)
            continue
        if name in FILE_APIS:
            mode_position, encoding_position, default_mode = FILE_APIS[name]
        elif method == 'open':
            unbound = owner in PATH_CLASSES
            mode_position, encoding_position, default_mode = int(unbound), 2 + unbound, 'r'
        elif method == 'makefile':
            mode_position, encoding_position, default_mode = 0, None, 'r'
        else:
            continue
        mode = _argument(call, 'mode', mode_position, default_mode)
        if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
            if 'b' in mode.value or (name in COMPRESSED_APIS and 't' not in mode.value):
                continue
        if not _has_encoding(call, encoding_position):
            missing.append(call)
    # Multiline Python literals include subprocess -c programs and generated scripts.
    for literal in ast.walk(tree):
        if not (isinstance(literal, ast.Constant) and isinstance(literal.value, str)
                and '\n' in literal.value):
            continue
        try:
            embedded = _missing_encodings(literal.value)
        except SyntaxError:
            continue
        for call in embedded:
            ast.increment_lineno(call, literal.lineno - 1)
            missing.append(call)
    return sorted(missing, key=lambda node: (node.lineno, node.col_offset))


def test_product_text_io_has_explicit_encoding():
    violations = []
    used_exceptions = set()
    for directory in PRODUCT_ROOTS:
        for path in sorted((ROOT / directory).rglob('*.py')):
            relative = path.relative_to(ROOT)
            if 'tests' in relative.parts:
                continue
            for call in _missing_encodings(path.read_text(encoding='utf-8')):
                key = (relative.as_posix(), ast.unparse(call))
                if key in LOCALE_ENCODING_ALLOWLIST:
                    assert LOCALE_ENCODING_ALLOWLIST[key].strip(), f'Missing reason: {key}'
                    used_exceptions.add(key)
                else:
                    violations.append(f'{relative.as_posix()}:{call.lineno}: '
                                      f'{ast.unparse(call.func)} requires encoding')
    assert not violations, f'{len(violations)} text I/O calls lack encoding:\n' + '\n'.join(violations)
    assert used_exceptions == set(LOCALE_ENCODING_ALLOWLIST), (
        f'Unused locale exceptions: {set(LOCALE_ENCODING_ALLOWLIST) - used_exceptions}')


@pytest.mark.parametrize('source', [
    'open(path)',
    'open(path, "w")',
    'open(path, mode="a+")',
    'json.load(open(path))',
    'path.open()',
    'Path(path).open("x")',
    'path.open(mode="r", encoding=None)',
    'path.read_text()',
    'path.write_text(json.dumps(data))',
    'path.write_text("中文")',
    'path.read_text(encoding=None)',
    'from pathlib import Path as P\nP.read_text(path)',
    'import io as streams\nstreams.open(path)',
    'from io import open as text_open\ntext_open(path, "w")',
    'import builtins as b\nb.open(path)',
    'import subprocess as sp\nsp.run(command, text=True)',
    'from subprocess import Popen as Process\nProcess(command, universal_newlines=True)',
    'subprocess.check_output(command, text=True, encoding=None)',
    'subprocess.check_call(command, universal_newlines=True)',
    'subprocess.call(command, text=use_text)',
    'subprocess.run(command, errors="replace")',
    'subprocess.getoutput(command)',
    'subprocess.getstatusoutput(command)',
    'os.fdopen(fd, "w")',
    'io.TextIOWrapper(stream)',
    'gzip.open(path, "wt")',
    'lzma.open(path, "wt")',
    'tempfile.NamedTemporaryFile(mode="w")',
    'tempfile.SpooledTemporaryFile(mode="w")',
    'socket.makefile("r")',
    'path.open(mode=mode)',
])
def test_guard_detects_implicit_text_encoding(source):
    assert len(_missing_encodings(source)) == 1


@pytest.mark.parametrize('source', [
    'open(path, encoding="utf-8")',
    'open(path, "w", -1, "utf-8")',
    'path.open("r", -1, "utf-8")',
    'path.read_text("utf-8")',
    'path.write_text(data, "utf-8")',
    'from pathlib import Path as P\nP.open(path, "r", -1, "utf-8")',
    'from pathlib import Path as P\nP.read_text(path, "utf-8")',
    'from pathlib import Path as P\nP.write_text(path, data, "utf-8")',
    'io.open(path, "w", encoding="utf-8")',
    'path.read_text(encoding="latin-1")',
    'path.write_text(data, encoding="ascii")',
    'open(path, "rb")',
    'path.open("wb")',
    'path.open(mode="ab+")',
    'io.open(path, mode="rb")',
    'from io import open as text_open\ntext_open(path, "rb")',
    'os.open(path, os.O_RDONLY)',
    'from os import open as descriptor_open\ndescriptor_open(path, flags)',
    'from PIL import Image as picture\npicture.open(path)',
    'subprocess.run(command)',
    'subprocess.run(command, text=False, universal_newlines=False)',
    'subprocess.Popen(command, text=True, encoding="utf-8")',
    'from subprocess import check_output as output\noutput(command, text=True, encoding="utf-8")',
    'subprocess.getoutput(command, encoding="utf-8")',
    'os.fdopen(fd, "w", encoding="utf-8")',
    'io.TextIOWrapper(stream, "utf-8")',
    'gzip.open(path)',
    'gzip.open(path, "w")',
    'gzip.open(path, "wt", encoding="utf-8")',
    'lzma.open(path, "wt", encoding="utf-8")',
    'tempfile.NamedTemporaryFile()',
    'tempfile.NamedTemporaryFile(mode="w", encoding="utf-8")',
    'tempfile.SpooledTemporaryFile(0, "w", -1, "utf-8")',
    'socket.makefile("rb")',
    'socket.makefile("r", encoding="utf-8")',
])
def test_guard_accepts_encoded_text_and_non_text_io(source):
    assert not _missing_encodings(source)


def test_guard_reports_nested_calls_with_source_lines():
    calls = _missing_encodings('import json\njson.load(open(path))\npath.read_text()\n')
    assert [call.lineno for call in calls] == [2, 3]


def test_guard_checks_embedded_python_with_outer_source_lines():
    source = "code = '''\nimport json\njson.load(open(path))\n'''\n"
    calls = _missing_encodings(source)
    assert [call.lineno for call in calls] == [3]
    assert not _missing_encodings(source.replace('open(path)', 'open(path, encoding="utf-8")'))
