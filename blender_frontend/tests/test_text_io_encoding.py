"""Frontend and Blender script text I/O uses UTF-8 with specific format exceptions."""
import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'wfrl_blender'
SCRIPTS_ROOT = Path(__file__).resolve().parents[2] / 'scripts' / 'blender'


def _assert_text_file_io_uses_explicit_encoding(source_root, *, excluded_parts=(),
                                             encoding_exceptions=None):
    # Exceptions map (file, call name) to the one non-UTF-8 encoding that call may use there.
    encoding_exceptions = encoding_exceptions or {}
    used_exceptions = set()
    violations = []
    for path in sorted(source_root.rglob('*.py')):
        relative = path.relative_to(source_root)
        if set(excluded_parts).intersection(relative.parts):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            owner = None
            if isinstance(function, ast.Name):
                name = function.id
            elif isinstance(function, ast.Attribute):
                name = function.attr
                if isinstance(function.value, ast.Name):
                    owner = function.value.id
            else:
                continue
            if name not in {'read_text', 'write_text', 'open'}:
                continue
            # atomic_json.write_text always writes UTF-8 and takes no encoding.
            if owner == 'atomic_json' and name == 'write_text':
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords}
            if name == 'open':
                # os.open uses descriptor flags, not text encodings.
                if owner == 'os':
                    continue
                mode_index = 1 if isinstance(function, ast.Name) or owner in {'builtins', 'io'} else 0
                mode = keywords.get('mode', node.args[mode_index]
                                    if len(node.args) > mode_index else ast.Constant(value='r'))
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and 'b' in mode.value:
                    continue
            encoding = keywords.get('encoding')
            allowed = {'utf-8', encoding_exceptions.get((relative.as_posix(), name))} - {None}
            if not isinstance(encoding, ast.Constant) or encoding.value not in allowed:
                violations.append(f"{relative}:{node.lineno}: {name} requires encoding in {sorted(allowed)}")
            elif encoding.value != 'utf-8':
                used_exceptions.add((relative.as_posix(), name))
    assert not violations, '\n'.join(violations)
    assert used_exceptions == set(encoding_exceptions), \
        f'unused encoding exceptions: {sorted(set(encoding_exceptions) - used_exceptions)}'


def test_text_file_io_uses_explicit_utf8():
    _assert_text_file_io_uses_explicit_encoding(SOURCE_ROOT, excluded_parts={'_vendor', 'assets'})


def test_blender_script_text_file_io_uses_explicit_utf8():
    _assert_text_file_io_uses_explicit_encoding(SCRIPTS_ROOT, encoding_exceptions={
        # SHA-256 checksum files are plain ASCII by convention.
        ('build_extension.py', 'write_text'): 'ascii',
        # The AeroDyn blade input files are read and rewritten as Latin-1.
        ('make_rootkink_blade.py', 'read_text'): 'latin-1',
        ('make_rootkink_blade.py', 'write_text'): 'latin-1',
    })
