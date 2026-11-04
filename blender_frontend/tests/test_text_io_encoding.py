"""Frontend text file I/O explicitly uses UTF-8 on every platform."""
import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'wfrl_blender'


def test_text_file_io_uses_explicit_utf8():
    violations = []
    for path in sorted(SOURCE_ROOT.rglob('*.py')):
        relative = path.relative_to(SOURCE_ROOT)
        if {'_vendor', 'assets'}.intersection(relative.parts):
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
            if not isinstance(encoding, ast.Constant) or encoding.value != 'utf-8':
                violations.append(f"{relative}:{node.lineno}: {name} requires encoding='utf-8'")
    assert not violations, '\n'.join(violations)
