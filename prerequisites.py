"""Read-only prerequisite discovery. Never imports student modules or installs guesses."""
import ast
from contextlib import contextmanager
import importlib.machinery
import json
from pathlib import Path
import shutil
import sys
import tempfile

PACKAGE_NAMES = {'sklearn': 'scikit-learn', 'PIL': 'Pillow', 'bs4': 'beautifulsoup4',
                 'cv2': 'opencv-python', 'yaml': 'PyYAML', 'sentence_transformers': 'sentence-transformers'}
READERS = {'read_csv', 'read_table', 'read_excel', 'read_parquet', 'read_json',
           'load_segments', 'load_lexicon', 'load_emolex'}


def sources(folder):
    from grader_core import read_source
    for path in sorted(Path(folder).rglob('*')):
        if path.suffix == '.py':
            try:
                yield path, read_source(path)
            except (UnicodeError, SyntaxError):
                continue
        elif path.suffix == '.ipynb':
            for cell in json.loads(path.read_text()).get('cells', []):
                if cell.get('cell_type') == 'code':
                    yield path, ''.join(cell.get('source', []))


def literal_path(node, values):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left, right = literal_path(node.left, values), literal_path(node.right, values)
        if left is not None and right is not None:
            return str(Path(left) / right) if isinstance(node.op, ast.Div) else left + right
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
        if name in ('Path', 'str') and node.args:
            return literal_path(node.args[0], values)
        if name == 'notebook_dir':
            return '.'
        if name == 'join' and node.args:
            parts = [literal_path(a, values) for a in node.args]
            if all(p is not None for p in parts):
                return str(Path(*parts))
    return None


def audit_prerequisites(folder):
    root = Path(folder).resolve()
    modules, inputs, models, unresolved = [], [], [], []
    for path, source in sources(root):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        relative = str(path.relative_to(root))
        values = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = literal_path(node.value, values)
                for target in node.targets:
                    if isinstance(target, ast.Name) and value is not None:
                        values[target.id] = value
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split('.')[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [(node.module or '').split('.')[0]]
            for name in names:
                if not name:
                    continue
                local = any((base / f'{name}.py').is_file() or (base / name).is_dir() for base in (path.parent, root))
                installed = name in sys.stdlib_module_names or importlib.machinery.PathFinder.find_spec(name, sys.path) is not None
                status = 'LOCAL FILE AVAILABLE' if local else 'INSTALLED' if installed else 'MISSING — PACKAGE OR HELPER'
                modules.append({'source': relative, 'line': node.lineno, 'module': name, 'status': status,
                                'action': '' if local or installed else f'Supply the original {name}.py/package, or verify its official package distribution before installing. Import names are not always PyPI names.',
                                'known_distribution': PACKAGE_NAMES.get(name)})
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
            argument = None
            if name == 'open':
                receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
                is_path_open = receiver is not None and literal_path(receiver, values) is not None
                argument = receiver if is_path_open else node.args[0] if node.args else None
                mode_index = 0 if is_path_open else 1
                mode = literal_path(node.args[mode_index], values) if len(node.args) > mode_index else 'r'
                mode = next((literal_path(k.value, values) for k in node.keywords if k.arg == 'mode'), mode)
                if mode and any(c in mode for c in 'wax') and '+' not in mode:
                    continue
            elif name in ('read_text', 'read_bytes') and isinstance(node.func, ast.Attribute):
                argument = node.func.value
            elif name in READERS:
                argument = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg in ('path', 'filepath_or_buffer', 'io')), None)
            elif name == 'load' and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == 'spacy' and node.args:
                model = literal_path(node.args[0], values)
                if model and model.startswith(('en_core_', 'zh_core_', 'fr_core_', 'de_core_')):
                    available = importlib.machinery.PathFinder.find_spec(model, sys.path) is not None
                    models.append({'source': relative, 'line': node.lineno, 'model': model, 'status': 'INSTALLED' if available else 'MISSING — MODEL', 'action': '' if available else 'Install the original spaCy model from its official release.'})
            if argument is None:
                continue
            value = literal_path(argument, values)
            if value is None:
                unresolved.append({'source': relative, 'line': node.lineno, 'expression': ast.unparse(argument), 'status': 'DYNAMIC INPUT — CHECK AT RUNTIME'})
                continue
            if value.startswith(('http://', 'https://')):
                status = 'REMOTE INPUT — REQUIRES NETWORK / ACCESS'
            else:
                target = (path.parent / value).resolve()
                status = ('EXTERNAL PATH — SUPPLY FILE AND MAP PATH' if not target.is_relative_to(root)
                          else 'AVAILABLE' if target.is_file() else 'MISSING OR GENERATED AT RUNTIME')
            inputs.append({'source': relative, 'line': node.lineno, 'path': value, 'status': status,
                           'action': '' if status == 'AVAILABLE' else 'Supply the actual input in the shared or student-specific files ZIP; use an exact path mapping if needed. Generated/conditional inputs may not need supplying.'})
    # Some references are optional, conditional or generated. Only execution establishes a blocker.
    return {'modules': modules, 'inputs': inputs, 'models': models, 'dynamic_inputs': unresolved,
            'note': 'Potential prerequisites only: conditional imports, generated files, dynamic paths, package versions and network inputs require runtime verification. No student code was executed.'}


@contextmanager
def prepared_submission(folder, shared=None, resources=None, mapping=None):
    from grader_core import stage_paths, stage_shared
    with tempfile.TemporaryDirectory(prefix='grader-preflight-') as tmp:
        work = Path(tmp) / 'submission'
        shutil.copytree(folder, work)
        events = stage_shared(resources, work)
        for event in events:
            event['origin'] = 'student-specific resource bundle'
        common = stage_shared(shared, work)
        for event in common:
            event['origin'] = 'shared instructor files'
        changes = stage_paths(work, mapping or {})
        yield work, events + common, changes


def preflight_submission(folder, shared=None, resources=None, mapping=None):
    with prepared_submission(folder, shared, resources, mapping) as (work, events, changes):
        return {**audit_prerequisites(work), 'staged_files': events, 'path_changes': changes}


def recovery_requirements(log):
    import re
    needs = []
    for module in sorted(set(re.findall(r"No module named ['\"]([^'\"]+)['\"]", log))):
        needs.append({'kind': 'module', 'name': module, 'action': 'Provide the original helper module in a resource ZIP, or install its verified official distribution; then rerun.'})
    for path in sorted(set(re.findall(r"(?:No such file or directory|FileNotFoundError):?\s*['\"]([^'\"]+)['\"]", log))):
        # Make paths in a disposable run portable in the resource request.
        path = re.sub(r'^.*?/grader-run-[^/]+/submission/', '', path)
        needs.append({'kind': 'file', 'name': path, 'action': 'Request the actual file from the student or instructor, preserve its expected relative path or add an exact mapping, then rerun.'})
    if not needs:
        needs.append({'kind': 'inspection', 'name': 'See execution log', 'action': 'Inspect the first exception for missing models, access, package versions or dynamically constructed inputs. No replacement data or code was generated.'})
    return needs
