"""Explicitly limited in-browser execution; no subprocesses or native marimo runtime."""
import ast
import contextlib
import inspect
import io
import json
import os
from pathlib import Path
import sys
import traceback
import types


class UnsupportedBrowserFeature(Exception):
    pass


class DisplayTabs:
    def __init__(self,items):
        self.items=items
    def __getattr__(self,name):
        raise UnsupportedBrowserFeature('Interactive tab state is unsupported in the browser adapter')

class DisplayUI:
    def tabs(self,items,**kwargs):
        return DisplayTabs(items)
    def __getattr__(self,name):
        raise UnsupportedBrowserFeature(f'marimo.ui.{name} is not supported by the browser adapter')


class DisplayMarimo(types.ModuleType):
    """Only display primitives are adapted; computational APIs are never stubbed."""
    def __init__(self):
        super().__init__('marimo')
        self.App = CellApp
        self.ui = DisplayUI()
    def md(self, text):
        print(str(text)[:4000])
        return text
    def hstack(self, items, **kwargs):
        return items
    vstack = hstack
    def notebook_dir(self):
        return Path(os.getcwd())
    def __getattr__(self, name):
        raise UnsupportedBrowserFeature(f'marimo.{name} requires an unsupported interactive/native feature')


class CellApp:
    def __init__(self, **kwargs):
        self.cells = []
        self.setup = contextlib.nullcontext()
    def cell(self, function=None, **options):
        def register(fn):
            self.cells.append((fn, options))
            return fn
        return register(function) if function else register
    def function(self, fn):
        return fn
    def __getattr__(self,name):
        raise UnsupportedBrowserFeature(f'app.{name} is not supported by the browser adapter')
    def run(self):
        raise UnsupportedBrowserFeature('Native marimo app.run is not available in the browser')


def run_source(source, filename):
    tree = ast.parse(source, filename)
    from grader_core import source_blocks
    _, metadata = source_blocks(source, filename)
    if not metadata['marimo']:
        exec(compile(tree, filename, 'exec'), {'__name__':'__main__','__file__':filename})
        return {'mode':'Python script in browser', 'cells_executed':None}
    # Use the real submitted function bodies and returned dependency names. This
    # adapter deliberately does not claim native marimo graph equivalence.
    cells, exports = [], {}
    top = []
    for node in tree.body:
        if isinstance(node, ast.If) and '__name__' in ast.unparse(node.test):
            continue
        top.append(node)
        if isinstance(node, ast.FunctionDef) and any(isinstance(d, (ast.Call,ast.Attribute)) and 'cell' in ast.unparse(d) for d in node.decorator_list):
            names=[]
            # Nested helper functions have their own returns, not cell exports.
            returns=[]
            def collect(block):
                for child in block:
                    if isinstance(child,ast.Return) and child.value is not None: returns.append(child)
                    elif not isinstance(child,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef,ast.Lambda)):
                        for _,value in ast.iter_fields(child):
                            if isinstance(value,list): collect([v for v in value if isinstance(v,ast.stmt)])
            collect(node.body)
            if len(returns)>1:
                raise UnsupportedBrowserFeature('Conditional or nested cell returns need native marimo review')
            if returns:
                value=returns[0].value
                values=value.elts if isinstance(value,(ast.Tuple,ast.List)) else [value]
                if any(not isinstance(v,ast.Name) for v in values):
                    raise UnsupportedBrowserFeature('Non-name cell exports require native marimo review')
                names=[v.id for v in values]
            for name in names:
                if name in exports:
                    raise ValueError(f'Duplicate cell export: {name}')
                exports[name]=node.name
            cells.append(names)
    module=DisplayMarimo()
    previous=sys.modules.get('marimo')
    sys.modules['marimo']=module
    try:
        namespace={'__name__':'browser_notebook','__file__':filename}
        exec(compile(ast.Module(body=top,type_ignores=[]),filename,'exec'),namespace)
        apps=[a for a in namespace.values() if isinstance(a,CellApp)]
        if len(apps)!=1 or len(apps[0].cells)!=len(cells):
            raise UnsupportedBrowserFeature('Notebook structure is not supported by the browser cell adapter')
        pending=list(zip(apps[0].cells,cells)); values={k:v for k,v in namespace.items() if not k.startswith('__')}; executed=0
        while pending:
            progressed=False
            for item in pending[:]:
                (fn,options),names=item
                if options.get('disabled'):
                    raise UnsupportedBrowserFeature('Disabled cells prevent a complete browser execution check')
                signature=inspect.signature(fn)
                if any(p.kind in (p.VAR_POSITIONAL,p.VAR_KEYWORD) for p in signature.parameters.values()):
                    raise UnsupportedBrowserFeature('Variadic cell dependencies require native marimo')
                required=[n for n,p in signature.parameters.items() if p.default is p.empty]
                if not all(n in values for n in required):
                    continue
                result=fn(**{n:values[n] for n in signature.parameters if n in values})
                if names:
                    result=result if isinstance(result,tuple) else (result,)
                    if len(names)!=len(result):
                        raise ValueError('Cell return/export count mismatch')
                    values.update(zip(names,result))
                pending.remove(item);executed+=1;progressed=True
            if not progressed:
                raise ValueError('Unresolved or cyclic cell dependencies: '+', '.join(str(inspect.signature(i[0][0])) for i in pending))
        return {'mode':'Browser marimo cell adapter (not native export)', 'cells_executed':executed}
    finally:
        if previous is None: sys.modules.pop('marimo',None)
        else: sys.modules['marimo']=previous


def run_browser_entry(folder, entry, mock=False):
    from prerequisites import prepared_submission, recovery_requirements
    from mock_inputs import prepare_mock_inputs
    buffer=io.StringIO(); detail={}; mocks={'manifest':[]}
    with prepared_submission(folder) as (work,_,_):
        original=os.getcwd(); old_path=sys.path[:]
        try:
            if mock: mocks=prepare_mock_inputs(work)
            script=work/entry
            os.chdir(script.parent);sys.path.insert(0,str(work));sys.path.insert(0,str(script.parent))
            with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(buffer):
                if script.suffix=='.ipynb':
                    raise UnsupportedBrowserFeature('Jupyter execution requires native kernel support')
                detail=run_source(script.read_text(encoding='utf-8-sig'),str(script))
            status='BROWSER RUN SUCCEEDED'
        except (UnsupportedBrowserFeature,ModuleNotFoundError,ImportError,FileNotFoundError,OSError) as exc:
            status='BLOCKED / UNSUPPORTED IN BROWSER'
            buffer.write(traceback.format_exc())
        except BaseException as exc:
            status='BROWSER EXECUTION ERROR'
            buffer.write(traceback.format_exc())
        finally:
            os.chdir(original);sys.path[:]=old_path
    if mock and mocks['manifest']:
        status='MOCK '+status+' — ORIGINAL INPUTS UNVERIFIED'
    return {'file':entry,'status':status,'log':buffer.getvalue()[-2000000:], 'mocks':mocks, 'mock_requested':mock, 'mock_used':bool(mocks['manifest']),
            'missing_resources':recovery_requirements(buffer.getvalue()) if status.startswith('BLOCKED') or 'BLOCKED' in status else [],
            'scope':'Browser-compatible execution only; not native Python/notebook equivalence.',**detail}
