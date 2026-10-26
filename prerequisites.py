"""Static prerequisite discovery and staging in disposable copies; no student imports."""
import ast
from contextlib import contextmanager
import hashlib
import importlib.machinery
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
import tempfile

PACKAGE_NAMES = {'sklearn': 'scikit-learn', 'PIL': 'Pillow', 'bs4': 'beautifulsoup4',
                 'cv2': 'opencv-python', 'yaml': 'PyYAML', 'sentence_transformers': 'sentence-transformers'}
READERS = {'read_csv', 'read_table', 'read_excel', 'read_parquet', 'read_json',
           'load_segments', 'load_lexicon', 'load_emolex', 'load_book', 'fetch_url'}


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
    if isinstance(node,ast.BoolOp) and isinstance(node.op,ast.Or):
        for candidate in node.values:
            value=literal_path(candidate,values)
            if value:return value
    if isinstance(node,ast.IfExp):
        left,right=literal_path(node.body,values),literal_path(node.orelse,values)
        return left if left is not None else right
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.Add)):
        left, right = literal_path(node.left, values), literal_path(node.right, values)
        if left is not None and right is not None:
            return str(Path(left) / right) if isinstance(node.op, ast.Div) else left + right
    if isinstance(node,ast.Attribute):
        value=literal_path(node.value,values)
        if value is not None and node.attr=='parent':return str(Path(value).parent)
        if value is not None and node.attr=='value':return value
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, 'attr', '')
        if name in ('Path', 'str') and node.args:
            return literal_path(node.args[0], values)
        if name=='cwd':return '.'
        if name=='strip' and isinstance(node.func,ast.Attribute):
            value=literal_path(node.func.value,values)
            return value.strip() if isinstance(value,str) else None
        if name=='home' and isinstance(node.func,ast.Attribute):return str(Path.home())
        if name=='next' and len(node.args)>1:return literal_path(node.args[1],values)
        if name in ('resolve','absolute') and isinstance(node.func,ast.Attribute):
            return literal_path(node.func.value,values)
        if name=='text' and isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Attribute) and node.func.value.attr=='ui':
            return next((literal_path(k.value,values) for k in node.keywords if k.arg=='value'),None)
        if name == 'notebook_dir':
            return '.'
        if name == 'join' and node.args:
            parts = [literal_path(a, values) for a in node.args]
            if all(p is not None for p in parts):
                return str(Path(*parts))
    return None


def output_path(node, values):
    if not isinstance(node,ast.Call):return None
    name=node.func.id if isinstance(node.func,ast.Name) else getattr(node.func,'attr','')
    if name in ('write_text','write_bytes') and isinstance(node.func,ast.Attribute):
        return literal_path(node.func.value,values)
    if name in ('to_csv','to_json','to_excel','to_parquet','to_pickle','savefig'):
        arg=node.args[0] if node.args else next((k.value for k in node.keywords if k.arg in ('path','path_or_buf','path_or_buffer','fname')),None)
        return literal_path(arg,values)
    if name=='open':
        receiver=node.func.value if isinstance(node.func,ast.Attribute) else None
        path_open=receiver is not None and not (isinstance(receiver,ast.Name) and receiver.id in ('io','builtins'))
        index=0 if path_open else 1
        mode=literal_path(node.args[index],values) if len(node.args)>index else None
        mode=next((literal_path(k.value,values) for k in node.keywords if k.arg=='mode'),mode)
        if mode and any(c in mode for c in 'wax'):
            return literal_path(receiver if path_open else node.args[0] if node.args else None,values)
    return None


def audit_prerequisites(folder):
    root = Path(folder).resolve()
    modules, inputs, models, unresolved, generated = [], [], [], [], []
    for path, source in sources(root):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        relative = str(path.relative_to(root))
        values = {'__file__':str(path)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                value = literal_path(node.value, values)
                for target in node.targets:
                    if isinstance(target, ast.Name) and value is not None:
                        values[target.id] = value
        for node in ast.walk(tree):
            output=output_path(node,values)
            if output is not None:generated.append({'source':relative,'line':node.lineno,'path':output})
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
                is_path_open = receiver is not None and not (isinstance(receiver, ast.Name) and receiver.id in ('io', 'builtins'))
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
            inputs.append({'source': relative, 'line': node.lineno, 'path': value, 'reader': name, 'status': status,
                           'action': '' if status == 'AVAILABLE' else 'Supply the actual input in the shared or student-specific files ZIP; use an exact path mapping if needed. Generated/conditional inputs may not need supplying.'})
        # Concrete Path literals are also prerequisites for pre-read is_file guards.
        parents={id(child):node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            parent=parents.get(id(node))
            call=parents.get(id(parent)) if parent is not None else None
            if isinstance(parent,ast.Attribute) and parent.attr in ('open','write_text','write_bytes') and isinstance(call,ast.Call):
                mode=next((literal_path(k.value,values) for k in call.keywords if k.arg=='mode'),literal_path(call.args[0],values) if call.args else None)
                if parent.attr.startswith('write_') or mode and any(c in mode for c in 'wax'):continue
            dictionary=parents.get(id(node))
            assignment=parents.get(id(dictionary)) if dictionary is not None else None
            declared_lexicon_input=isinstance(dictionary,ast.Dict) and isinstance(assignment,ast.Assign) and any(isinstance(t,ast.Name) and 'lexicon' in t.id.lower() for t in assignment.targets)
            if declared_lexicon_input and isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Path' and node.args:
                value=literal_path(node.args[0],values)
                if value and Path(value).suffix.lower() in ('.txt','.csv','.tsv','.html','.htm'):
                    target=(path.parent/value).resolve()
                    status='AVAILABLE' if target.is_file() else 'MISSING OR GENERATED AT RUNTIME'
                    inputs.append({'source':relative,'line':node.lineno,'path':value,'reader':'path_literal','status':status,'action':'Concrete file path; may be used by a file-existence guard.'})
        # Resolve I/O inside local reader functions when the call supplies literal paths.
        definitions={n.name:n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)}
        for call in ast.walk(tree):
            if not isinstance(call,ast.Call) or not isinstance(call.func,ast.Name):continue
            fn=definitions.get(call.func.id)
            if fn is None:continue
            bindings=dict(values)
            for param,default in zip(fn.args.args[-len(fn.args.defaults):] if fn.args.defaults else [],fn.args.defaults):
                value=literal_path(default,values)
                if value is not None:bindings[param.arg]=value
            for param,arg in zip(fn.args.args,call.args):
                value=literal_path(arg,values)
                if value is not None:bindings[param.arg]=value
            for keyword in call.keywords:
                value=literal_path(keyword.value,values)
                if value is not None:bindings[keyword.arg]=value
            for assignment in ast.walk(fn):
                if isinstance(assignment,ast.Assign):
                    value=literal_path(assignment.value,bindings)
                    if value is not None:
                        for target in assignment.targets:
                            if isinstance(target,ast.Name):bindings[target.id]=value
            for read in ast.walk(fn):
                if not isinstance(read,ast.Call):continue
                output=output_path(read,bindings)
                if output is not None:generated.append({'source':relative,'line':call.lineno,'path':output})
                reader=read.func.id if isinstance(read.func,ast.Name) else getattr(read.func,'attr','')
                receiver=read.func.value if isinstance(read.func,ast.Attribute) else None
                path_open=reader=='open' and receiver is not None and not (isinstance(receiver,ast.Name) and receiver.id in ('io','builtins'))
                arg=receiver if path_open or reader in ('read_text','read_bytes') and receiver is not None else read.args[0] if read.args else None
                if reader not in ('open','read_text','read_bytes','read_csv','read_table'):continue
                if reader=='open':
                    mode_arg=0 if path_open else 1
                    mode=literal_path(read.args[mode_arg],bindings) if len(read.args)>mode_arg else None
                    mode=next((literal_path(k.value,bindings) for k in read.keywords if k.arg=='mode'),mode)
                    if mode and any(c in mode for c in 'wax') and '+' not in mode:continue
                value=literal_path(arg,bindings)
                if value is None or value.startswith(('http://','https://')):continue
                target=(path.parent/value).resolve()
                status='EXTERNAL PATH — SUPPLY FILE AND MAP PATH' if not target.is_relative_to(root) else 'AVAILABLE' if target.is_file() else 'MISSING OR GENERATED AT RUNTIME'
                inputs.append({'source':relative,'line':call.lineno,'path':value,'reader':call.func.id,'status':status,'action':'Resolved from a literal call into the submitted reader function.'})
    # Some references are optional, conditional or generated. Only execution establishes a blocker.
    return {'modules': modules, 'inputs': inputs, 'models': models, 'dynamic_inputs': unresolved,'generated_outputs':generated,
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


def provided_candidates(paths, basename):
    """Prefer exact names, then a unique numbered-upload variant such as book-1.txt."""
    exact = [p for p in paths if p.name.casefold() == basename.casefold()]
    if exact:
        return exact, 'Unique basename (case-insensitive); bytes unchanged'
    requested = Path(basename)
    numbered = [p for p in paths if p.suffix.casefold() == requested.suffix.casefold()
                and re.sub(r'-[1-9]\d?$', '', p.stem).casefold() == requested.stem.casefold()
                and p.stem.casefold() != requested.stem.casefold()]
    return numbered, 'Unique numbered-upload filename; bytes unchanged. Confirm the supplied document version.'


def guarded_path_literals(tree):
    """Resolve Path inputs from unambiguous literal constants in their own scope.

    Cell/local names must not borrow another cell's same-named variable. Dynamic
    assignments, conflicting reassignments and parameters are left unresolved.
    No submitted expressions are executed and no missing files are synthesized.
    """
    def scoped_nodes(node):
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
                yield from scoped_nodes(child)
    def constants(nodes):
        stores={};literals={}
        for node in nodes:
            if isinstance(node,ast.Name) and isinstance(node.ctx,ast.Store):stores.setdefault(node.id,[]).append(node)
            if isinstance(node,(ast.Assign,ast.AnnAssign)) and isinstance(node.value,ast.Constant) and isinstance(node.value.value,str):
                targets=node.targets if isinstance(node,ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target,ast.Name):literals[id(target)]=node.value.value
        result={}
        for name,assignments in stores.items():
            values=[literals.get(id(node)) for node in assignments]
            result[name]=values[0] if values and None not in values and len(set(values))==1 else None
        return result
    module_nodes=list(scoped_nodes(tree));globals_=constants(module_nodes)
    scopes=[tree]+[node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))]
    for scope in scopes:
        nodes=module_nodes if scope is tree else list(scoped_nodes(scope))
        values=dict(globals_)
        if scope is not tree:
            for argument in scope.args.posonlyargs+scope.args.args+scope.args.kwonlyargs:values[argument.arg]=None
        values.update(constants(nodes))
        for node in nodes:
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='Path' and node.args:
                value=literal_path(node.args[0],values)
                if value:yield node,value


def stage_provided_inputs(work):
    """Match referenced files within one disposable student copy, before any mocks.

    Exact paths win. A unique basename can repair a downloaded/nested location or
    a laptop's absolute path. Different names and ambiguous matches are not guessed.
    This is an inventory of matched references, not a trace of actual file reads.
    """
    from grader_core import stage_paths
    work = Path(work).resolve()
    available = [p for p in work.rglob('*') if p.is_file()]
    records, unresolved, mapping, seen = [], [], {}, set()
    copied_bytes = 0
    inputs=audit_prerequisites(work)['inputs']
    # Guarded candidate paths can select inputs before a read expression is resolved.
    # Only a unique already supplied file may satisfy these Path references.
    for source,code in sources(work):
        try:tree=ast.parse(code)
        except SyntaxError:continue
        for node,value in guarded_path_literals(tree):
            if Path(value).suffix and provided_candidates(available,value.replace('\\','/').rsplit('/',1)[-1])[0]:
                inputs.append({'path':value,'source':str(source.relative_to(work)),'line':node.lineno,'reader':'supplied_path_literal'})
    for item in inputs:
        value = item['path']
        if value.startswith(('http://', 'https://')):
            continue
        target = (work / Path(item['source']).parent / value).resolve()
        external = not target.is_relative_to(work) or PureWindowsPath(value).is_absolute()
        basename = value.replace('\\', '/').rsplit('/', 1)[-1]
        if external:
            target = work / '_provided_inputs' / hashlib.sha256(value.encode()).hexdigest()[:12] / basename
        if target in seen:
            continue
        seen.add(target)
        if not external and target.is_file():
            source, match = target, 'Submitted path'
        else:
            candidates, match = provided_candidates(available, basename)
            if len(candidates) != 1:
                if len(candidates) > 1:
                    unresolved.append({'requested_path': value, 'reason': 'Multiple supplied files have this basename; select an exact path mapping.'})
                continue
            source = candidates[0]
            if len(records) >= 64 or copied_bytes + source.stat().st_size > 128 * 1024**2:
                unresolved.append({'requested_path': value, 'reason': 'Supplied-file staging budget exceeded; use an exact path mapping.'})
                continue
            if source != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied_bytes += source.stat().st_size
            if external:
                mapping[value] = str(target.relative_to(work))
        digest = hashlib.sha256()
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        records.append({'file': str(target.relative_to(work)), 'original': str(source.relative_to(work)),
                        'requested_path': value, 'source': item['source'], 'line': item['line'],
                        'origin': 'SUPPLIED FILE', 'match': match, 'sha256': digest.hexdigest(), 'bytes': source.stat().st_size})
    return {'files': records, 'unresolved': unresolved, 'path_changes': stage_paths(work, mapping) if mapping else [],
            'note': 'Matched file references from this student’s execution copy. Static matching does not prove every file was read at runtime.'}


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
        needs.append({'kind': 'inspection', 'name': 'See execution log', 'action': 'Inspect the first exception for missing models, access, package versions or dynamically constructed inputs. Inspect any mock manifest separately; a mock run does not verify the original inputs.'})
    return needs
