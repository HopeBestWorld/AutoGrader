"""Read-only source diagnostics. Never supply variables or rewrite student code."""
import ast
from pathlib import Path


def inspect_source(source, filename):
    result={'file':filename,'missing_notebook_variables':[],'native_features':[]}
    try:
        tree=ast.parse(source,filename)
    except (SyntaxError,ValueError):
        return result
    aliases={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            for name in node.names:aliases[name.asname or name.name.split('.')[0]]=name.name
        elif isinstance(node,ast.ImportFrom):
            for name in node.names:aliases[name.asname or name.name]=(node.module or '')+'.'+name.name
    def qualified(node):
        if isinstance(node,ast.Name):return aliases.get(node.id,node.id)
        if isinstance(node,ast.Attribute):return qualified(node.value)+'.'+node.attr
        return ''
    cells=[n for n in tree.body if isinstance(n,ast.FunctionDef) and any(
        isinstance(d.func if isinstance(d,ast.Call) else d,ast.Attribute)
        and (d.func if isinstance(d,ast.Call) else d).attr=='cell' for d in n.decorator_list)]
    # Only cell-level returns define exports. Nested helpers have separate returns.
    def returns(statements):
        for node in statements:
            if isinstance(node,ast.Return):yield node
            elif not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                for _,value in ast.iter_fields(node):
                    if isinstance(value,list):yield from returns([n for n in value if isinstance(n,ast.stmt)])
    # Avoid compiling cell-level future imports: native marimo compiles cells
    # separately. Only module-scope bindings are global notebook definitions.
    available=set()
    def bindings(statements):
        for node in statements:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):
                available.add(node.name)
                continue
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                available.update(n.asname or n.name.split('.')[0] for n in node.names)
            for child in ast.iter_child_nodes(node):
                if isinstance(child,ast.Name) and isinstance(child.ctx,ast.Store):available.add(child.id)
                elif isinstance(child,(ast.Tuple,ast.List)):
                    available.update(n.id for n in ast.walk(child) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Store))
            for _,value in ast.iter_fields(node):
                if isinstance(value,list):bindings([n for n in value if isinstance(n,ast.stmt)])
    bindings(tree.body)
    for cell in cells:
        for node in returns(cell.body):
            values=node.value.elts if isinstance(node.value,(ast.Tuple,ast.List)) else [node.value]
            available.update(value.id for value in values if isinstance(value,ast.Name))
    for cell in cells:
        positional=cell.args.posonlyargs+cell.args.args
        required=positional[:len(positional)-len(cell.args.defaults)]
        required += [arg for arg,default in zip(cell.args.kwonlyargs,cell.args.kw_defaults) if default is None]
        for arg in required:
            if arg.arg not in available and '*' not in available:
                result['missing_notebook_variables'].append({'name':arg.arg,'cell_line':cell.lineno,
                    'action':f'No definition/export was found for {arg.arg}. Check whether the tested cell body reads it; a stale generated argument can be unused. If read, define/export the correct variable or correct the dependency. Files alone do not define variables.'})
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):continue
        name=qualified(node.func)
        if name.startswith('subprocess.') or name in ('os.system','os.fork','os.execv','os.execve'):
            result['native_features'].append({'line':node.lineno,'feature':name,'action':'Run the original submission in native Python; browser workers cannot launch operating-system processes.'})
        elif name.startswith('truststore.'):
            result['native_features'].append({'line':node.lineno,'feature':name,'action':'Use native Python with the real truststore package and system certificate store. Do not replace SSL configuration with a successful no-op.'})
    return result


def inspect_folder_sources(folder):
    root=Path(folder)
    return [inspect_source(path.read_text(encoding='utf-8-sig',errors='replace'),str(path.relative_to(root)))
            for path in sorted(root.rglob('*.py'))]


def recovery_rows(report):
    from execution_summary import summarize_student
    rows=[]
    for student in report.get('students',[]):
        outcome=summarize_student(student)
        if outcome['category'] in ('ran','not_run'):continue
        found=False
        attempted={e.get('file') for e in student.get('execution',[]) if isinstance(e,dict)}
        for diagnostic in student.get('source_diagnostics',[]):
            if diagnostic['file'] not in attempted:continue
            for problem in diagnostic.get('missing_notebook_variables',[]):
                found=True
                rows.append({'Student':student['student'],'File':diagnostic['file'],'Line':problem['cell_line'],
                    'Needs attention':'Notebook variable: '+problem['name'],'Next step':problem['action']})
            for problem in diagnostic.get('native_features',[]):
                found=True
                rows.append({'Student':student['student'],'File':diagnostic['file'],'Line':problem['line'],
                    'Needs attention':'Native Python: '+problem['feature'],'Next step':problem['action']})
        if not found:
            rows.append({'Student':student['student'],'File':'See execution log','Line':None,
                'Needs attention':outcome['reason'],'Next step':outcome['next_step']})
    return rows
