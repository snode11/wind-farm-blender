"""High-confidence name checks for frontend code, scripts and tests."""
import ast
from pathlib import Path
import symtable
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCAN_PATHS = ('blender_frontend', 'scripts', 'tests', 'tip_3d.py', 'tip_3d_vis.py')


class _BlenderPropertyExpressions(ast.NodeTransformer):
    def __init__(self, tree):
        self.imported_properties = {
            alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == 'bpy.props'
            for alias in node.names if alias.name.endswith('Property')
        }

    def visit_AnnAssign(self, node):
        call = node.annotation
        if not isinstance(call, ast.Call):
            return self.generic_visit(node)
        imported = isinstance(call.func, ast.Name) and call.func.id in self.imported_properties
        qualified = (isinstance(call.func, ast.Attribute) and call.func.attr.endswith('Property')
                     and isinstance(call.func.value, ast.Attribute) and call.func.value.attr == 'props'
                     and isinstance(call.func.value.value, ast.Name) and call.func.value.value.id == 'bpy')
        if node.value is None and (imported or qualified):
            # RNA properties are runtime calls: their strings are not forward types.
            # Keep every argument expression so undefined callbacks still fail.
            return ast.copy_location(ast.Assign(targets=[node.target], value=call), node)
        return self.generic_visit(node)


def _local_import_failures(sources, trees):
    exports = {}
    failures = []
    for filename, tree in trees.items():
        path = Path(filename)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level > len(path.parents):
                continue
            module = Path(*(node.module or '').split('.'))
            bases = ((path.parents[node.level - 1] / module,) if node.level else
                     (module, Path('blender_frontend') / module, path.parent / module))
            candidates = {candidate.as_posix() for base in bases
                          for candidate in (base.with_suffix('.py'), base / '__init__.py')
                          if candidate.as_posix() in trees}
            # Only resolve unambiguous modules inside the explicit scan scope.
            if len(candidates) != 1:
                continue
            target = candidates.pop()
            if target not in exports:
                symbols = {symbol.get_name() for symbol in
                           symtable.symtable(sources[target], target, 'exec').get_symbols()
                           if symbol.is_assigned() or symbol.is_imported()}
                dynamic = any(
                    isinstance(item, ast.ImportFrom) and any(alias.name == '*' for alias in item.names)
                    or isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                    and item.func.id in {'exec', 'globals'}
                    for item in ast.walk(trees[target]))
                exports[target] = None if dynamic or '__getattr__' in symbols else symbols
            names = exports[target]
            if names is None:
                continue
            for alias in node.names:
                if alias.name == '*':
                    continue
                package = Path(target).parent
                if (Path(target).name == '__init__.py'
                        and any(child.as_posix() in trees for child in
                                (package / (alias.name + '.py'), package / alias.name / '__init__.py'))):
                    continue
                if alias.name not in names:
                    failures.append(f'{filename}:{node.lineno}: {target} has no static export {alias.name}')
    return failures


class StaticNameTests(unittest.TestCase):
    def test_python_names_and_install_panel_targets(self):
        try:
            from pyflakes.checker import Checker
            from pyflakes.messages import UndefinedExport, UndefinedLocal, UndefinedName
        except ImportError:
            self.skipTest('pyflakes is not installed')

        failures = []
        sources = {}
        trees = {}
        for entry in SCAN_PATHS:
            root = ROOT / entry
            paths = sorted(root.rglob('*.py')) if root.is_dir() else [root]
            for path in paths:
                relative = path.relative_to(ROOT).as_posix()
                sources[relative] = path.read_text(encoding='utf-8')
                tree = ast.parse(sources[relative], filename=relative)
                trees[relative] = tree
                tree = ast.fix_missing_locations(_BlenderPropertyExpressions(tree).visit(tree))
                failures.extend(str(message) for message in Checker(tree, filename=relative).messages
                                if isinstance(message, (UndefinedExport, UndefinedLocal, UndefinedName)))

        failures.extend(_local_import_failures(sources, trees))

        # Pyflakes cannot resolve string-based import_module/getattr panel lookups.
        smoke = trees['scripts/blender/verify_part7_install.py']
        required = next(node.value for node in ast.walk(smoke)
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == 'required_panels'
                                for target in node.targets))
        for module, name in ast.literal_eval(required):
            source = 'blender_frontend/wfrl_blender/' + module.replace('.', '/') + '.py'
            tree = trees.get(source)
            classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)} if tree else set()
            registrations = {item.id for node in tree.body
                             if isinstance(node, ast.Assign)
                             and any(isinstance(target, ast.Name) and target.id == 'CLASSES'
                                     for target in node.targets)
                             for item in ast.walk(node.value) if isinstance(item, ast.Name)} if tree else set()
            if name not in classes or name not in registrations:
                failures.append(f'{source}: panel {name} must exist and be included in CLASSES')

        self.assertEqual(failures, [], '\n'.join(failures))


if __name__ == '__main__':
    unittest.main()
