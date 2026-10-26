"""Explicitly limited in-browser execution; no subprocesses or native marimo runtime."""
import ast
import __future__
import contextlib
import copy
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

class DisplayValue:
    def __init__(self,value): self.value=value
    def __bool__(self):
        raise UnsupportedBrowserFeature('Read the widget .value explicitly')

class DisplayUI:
    def __init__(self,model_apis=False): self.assumptions=[];self.model_apis=model_apis
    def run_button(self,label='',**kwargs):
        if not self.model_apis or not any(w in str(label).lower() for w in ('model','llm','language','classifier')):
            raise UnsupportedBrowserFeature('Run button needs an explicitly enabled compatible mock model test')
        self.assumptions.append({'widget':'run_button','value':True,'scope':'One simulated click for an explicitly enabled model-API mock. Actual model behavior is unverified.'})
        return DisplayValue(True)
    def checkbox(self,value=False,**kwargs):
        if not isinstance(value,bool):raise ValueError('Checkbox default must be boolean')
        self.assumptions.append({'widget':'checkbox','value':value,'scope':'Declared default only; toggling was not tested.'})
        return DisplayValue(value)
    def text(self,value='',**kwargs):
        if not isinstance(value,str): raise ValueError('Text widget default must be a string')
        self.assumptions.append({'widget':'text','value':value,'scope':'Declared default only; no user text was entered.'})
        return DisplayValue(value)
    def dropdown(self,options,value=None,**kwargs):
        if value is None: raise UnsupportedBrowserFeature('Dropdown has no declared default; a test choice is required')
        if value not in options: raise ValueError('Dropdown default is not in its options')
        selected=options[value] if isinstance(options,dict) else value
        self.assumptions.append({'widget':'choice','value':str(selected),'scope':'Declared default only; other choices were not tested.'})
        return DisplayValue(selected)
    radio=dropdown
    def multiselect(self,options,value=None,**kwargs):
        selected=[] if value is None else list(value)
        if any(v not in options for v in selected):raise ValueError('Multiselect default is not in options')
        self.assumptions.append({'widget':'multiselect','value':selected,'scope':'Only the declared initial selection was tested.'})
        return DisplayValue([options[v] for v in selected] if isinstance(options,dict) else selected)
    def altair_chart(self,data,**kwargs):
        self.assumptions.append({'widget':'altair_chart','scope':'Chart was computed; interactive selections were not tested.'})
        return DisplayTabs(data)
    def table(self,data,**kwargs):
        self.assumptions.append({'widget':'table','scope':'Display data was computed; table selections were not simulated.'})
        return DisplayTabs(data)

    def slider(self,start=None,stop=None,step=None,value=None,**kwargs):
        selected=value if value is not None else start
        if not isinstance(selected,(int,float)):
            raise UnsupportedBrowserFeature('Only numeric sliders with a declared default are supported')
        if start is not None and selected<start or stop is not None and selected>stop:
            raise ValueError('Slider default is outside its declared bounds')
        self.assumptions.append({'widget':'slider','value':selected,'label':str(kwargs.get('label','')),'scope':'Only the declared initial value was tested; interaction was not tested.'})
        return DisplayValue(selected)
    def number(self,start=None,stop=None,step=None,value=None,**kwargs):
        return self.slider(start=start,stop=stop,step=step,value=value,**kwargs)
    def tabs(self,items,**kwargs):
        return DisplayTabs(items)
    def __getattr__(self,name):
        raise UnsupportedBrowserFeature(f'marimo.ui.{name} is not supported by the browser adapter')


class DisplayMarimo(types.ModuleType):
    """Only display primitives are adapted; computational APIs are never stubbed."""
    def __init__(self,model_apis=False):
        super().__init__('marimo')
        self.App = CellApp
        self.ui = DisplayUI(model_apis)
        self.status = DisplayStatus()
    def md(self, text):
        print(str(text)[:4000])
        return text
    def hstack(self, items, **kwargs):
        return items
    vstack = hstack
    def plain_text(self,value):
        return str(value)
    def callout(self,value,**kwargs):
        return value
    def plain(self,value):
        return value
    def Html(self,value):
        return str(value)
    def accordion(self,items,**kwargs):
        return items
    def stop(self,condition,output=None):
        if condition:
            raise UnsupportedBrowserFeature('A marimo stop condition is active: this cell did not finish; no success was inferred')
    def notebook_dir(self):
        return Path(os.getcwd())
    def __getattr__(self, name):
        raise UnsupportedBrowserFeature(f'marimo.{name} requires an unsupported interactive/native feature')


class DisplayStatus:
    def spinner(self, *args, **kwargs):
        return contextlib.nullcontext()
    def progress_bar(self, iterable, **kwargs):
        # Preserve iteration and exceptions; only the progress display is adapted.
        return iter(iterable)


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


def run_source(source, filename, model_apis=False):
    tree = ast.parse(source, filename)
    from grader_core import source_blocks
    _, metadata = source_blocks(source, filename)
    if not metadata['marimo']:
        exec(compile(tree, filename, 'exec'), {'__name__':'__main__','__file__':filename})
        return {'mode':'Python script in browser', 'cells_executed':None}
    # Use the real submitted function bodies and returned dependency names. This
    # adapter deliberately does not claim native marimo graph equivalence.
    cells, exports, cell_nodes = [], {}, []
    cell_future_flags = {}
    top = []
    for node in tree.body:
        if isinstance(node, ast.If) and '__name__' in ast.unparse(node.test):
            if node.orelse or len(node.body)!=1 or not isinstance(node.body[0],ast.Expr) or not isinstance(node.body[0].value,ast.Call) or not isinstance(node.body[0].value.func,ast.Attribute) or node.body[0].value.func.attr!='run':
                raise UnsupportedBrowserFeature('Nonstandard main guard contains code that cannot be skipped; native execution is required')
            continue
        top.append(node)
        if isinstance(node, ast.FunctionDef) and any(isinstance(d, (ast.Call,ast.Attribute)) and 'cell' in ast.unparse(d) for d in node.decorator_list):
            prefix=1 if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str) else 0
            future_end=prefix
            while future_end<len(node.body) and isinstance(node.body[future_end],ast.ImportFrom) and node.body[future_end].module=='__future__':
                future=node.body[future_end]
                if any(alias.name!='annotations' for alias in future.names):
                    raise UnsupportedBrowserFeature('This cell future feature needs native marimo execution')
                cell_future_flags[id(node)]=__future__.annotations.compiler_flag
                future_end+=1
            if future_end>prefix:
                del node.body[prefix:future_end]
                if not node.body:node.body=[ast.copy_location(ast.Pass(),node)]
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
            cell_nodes.append(node)
    module=DisplayMarimo(model_apis)
    previous=sys.modules.get('marimo')
    sys.modules['marimo']=module
    try:
        namespace={'__name__':'browser_notebook','__file__':filename}
        compiled=compile(ast.Module(body=top,type_ignores=[]),filename,'exec')
        if cell_future_flags:
            # Native marimo compiles cell bodies separately. Keep future-annotation
            # semantics scoped to the declaring cell, while preserving registration order.
            flags=0
            for statement in top:
                if isinstance(statement,ast.ImportFrom) and statement.module=='__future__':
                    for alias in statement.names:flags|=getattr(__future__,alias.name).compiler_flag
                exec(compile(ast.Module(body=[statement],type_ignores=[]),filename,'exec',flags=flags|cell_future_flags.get(id(statement),0),dont_inherit=True),namespace)
        else:
            exec(compiled,namespace)
        apps=[a for a in namespace.values() if isinstance(a,CellApp)]
        if len(apps)!=1 or len(apps[0].cells)!=len(cells):
            raise UnsupportedBrowserFeature('Notebook structure is not supported by the browser cell adapter')
        values={k:v for k,v in namespace.items() if not k.startswith('__')}
        registered=[]; deferred_dependencies=[]
        global_flags=0
        for statement in top:
            if isinstance(statement,ast.ImportFrom) and statement.module=='__future__':
                for alias in statement.names:global_flags|=getattr(__future__,alias.name).compiler_flag
        for (fn,options),node in zip(apps[0].cells,cell_nodes):
            missing={name for name,p in inspect.signature(fn).parameters.items()
                     if p.default is p.empty and p.kind in (p.POSITIONAL_ONLY,p.POSITIONAL_OR_KEYWORD,p.KEYWORD_ONLY)
                     and name not in values and name not in exports}
            if missing:
                # Generated signatures can retain unused/conditional names. Native
                # marimo evaluates the cell body; an absent name fails only when
                # read. Leave these names unbound globals, never placeholder data.
                body=copy.deepcopy(node);body.decorator_list=[]
                body.args.posonlyargs=[arg for arg in body.args.posonlyargs if arg.arg not in missing]
                body.args.args=[arg for arg in body.args.args if arg.arg not in missing]
                kept=[(arg,default) for arg,default in zip(body.args.kwonlyargs,body.args.kw_defaults) if arg.arg not in missing]
                body.args.kwonlyargs=[arg for arg,_ in kept];body.args.kw_defaults=[default for _,default in kept]
                local={}
                exec(compile(ast.Module(body=[body],type_ignores=[]),filename,'exec',flags=global_flags|cell_future_flags.get(id(node),0),dont_inherit=True),namespace,local)
                fn=local[body.name]
                deferred_dependencies.append({'cell_line':node.lineno,'names':sorted(missing),
                    'scope':'Unbound declared names are resolved only if read in the actual cell body. No values were supplied.'})
            registered.append((fn,options))
        pending=list(zip(registered,cells)); executed=0
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
                    namespace.update(zip(names,result))
                pending.remove(item);executed+=1;progressed=True
            if not progressed:
                unknown=sorted({name for (fn,_),_ in pending for name,p in inspect.signature(fn).parameters.items() if p.default is p.empty and name not in values and name not in exports})
                if unknown:raise NameError('Notebook dependencies not defined or exported: '+', '.join(unknown))
                raise ValueError('Unresolved or cyclic cell dependencies: '+', '.join(str(inspect.signature(i[0][0])) for i in pending))
        return {'mode':'Browser marimo cell adapter (not native export)', 'cells_executed':executed, 'widget_defaults_tested':module.ui.assumptions,
                'deferred_declared_dependencies':deferred_dependencies,
                'adapter_assumptions':['Cell-level future annotations were compiled with the cell annotation flag.'] if cell_future_flags else []}
    finally:
        if previous is None: sys.modules.pop('marimo',None)
        else: sys.modules['marimo']=previous


def run_browser_entry(folder, entry, mock=False, mock_words=18000, model_apis=False, setup_adaptations=True, force_synthetic_urls=False):
    from prerequisites import prepared_submission, recovery_requirements, stage_provided_inputs
    from mock_inputs import prepare_mock_inputs
    from browser_setup import browser_setup,setup_requirements,UnsupportedBrowserSetup
    buffer=io.StringIO(); detail={}; mocks={'manifest':[]}; supplied={'files':[]}; failure_kind=None; exception_type=None; action=None; adaptations=[]
    with prepared_submission(folder) as (work,_,_):
        original=os.getcwd(); old_path=sys.path[:]; old_home=os.environ.get('HOME');old_backend=os.environ.get('MPLBACKEND')
        saved_model_modules={}; mock_module_names=set()
        try:
            os.environ['MPLBACKEND']='Agg'
            if mock:
                os.environ['HOME']=str(work/'_mock_home')
            supplied=stage_provided_inputs(work)
            if mock:
                mocks=prepare_mock_inputs(work,words=mock_words,model_apis=model_apis,browser=True,force_synthetic_urls=force_synthetic_urls)
            mock_module_names={Path(m['file']).stem for m in mocks['manifest'] if m['file'].endswith('.py') and '/' not in m['file']}
            mock_module_names.update(p.stem for p in work.glob('*.py') if p.stem not in sys.stdlib_module_names)
            if mock_module_names:
                saved_model_modules={k:v for k,v in sys.modules.items() if any(k==name or k.startswith(name+'.') for name in mock_module_names)}
                for name in saved_model_modules:sys.modules.pop(name,None)
            script=work/entry
            os.chdir(script.parent);sys.path.insert(0,str(work));sys.path.insert(0,str(script.parent))
            with contextlib.redirect_stdout(buffer),contextlib.redirect_stderr(buffer):
                if script.suffix=='.ipynb':
                    raise UnsupportedBrowserFeature('Jupyter execution requires native kernel support')
                source=script.read_text(encoding='utf-8-sig')
                with browser_setup(work,setup_requirements(source),mocks['manifest'],enabled=mock and setup_adaptations) as adaptations:
                    detail=run_source(source,str(script),model_apis=any(m.get('origin')=='BUNDLED MOCK MODEL API' for m in mocks['manifest']))
            status='BROWSER RUN SUCCEEDED'
        except (UnsupportedBrowserFeature,UnsupportedBrowserSetup,ModuleNotFoundError,ImportError,FileNotFoundError,OSError) as exc:
            status='BLOCKED / UNSUPPORTED IN BROWSER'
            exception_type=type(exc).__name__
            failure_kind='unsupported_browser_feature' if isinstance(exc,(UnsupportedBrowserFeature,UnsupportedBrowserSetup)) else 'missing_dependency' if isinstance(exc,(ImportError,ModuleNotFoundError)) else 'missing_input_or_environment'
            action='Inspect the exception. Supply the missing resource or use a compatible browser dependency; this is not a demonstrated student-code failure.'
            buffer.write(traceback.format_exc())
        except BaseException as exc:
            status='BROWSER EXECUTION ERROR'
            exception_type=type(exc).__name__
            frame=exc.__traceback__
            while frame and frame.tb_next:frame=frame.tb_next
            unsupported_fixture=isinstance(exc,NotImplementedError) and frame and any(str(work/m['file'])==frame.tb_frame.f_code.co_filename for m in mocks['manifest'] if m['file'].endswith('.py'))
            failure_kind='browser_resource_limit' if isinstance(exc,MemoryError) else 'unsupported_mock_api' if unsupported_fixture else 'mock_input_or_code_mismatch' if mocks['manifest'] and isinstance(exc,(KeyError,IndexError)) else 'runtime_error'
            if isinstance(exc,MemoryError):
                status='BROWSER RUNTIME BLOCKED'
                action='Browser memory was exhausted. Retry in a fresh worker or use native Python for this workload.'
            elif unsupported_fixture:
                status='BLOCKED / UNSUPPORTED MOCK API'
                action='This call exceeds the reviewed fixture contract. Supply the real helper/model or a compatible fixture before retrying.'
            else:
                action='Increase synthetic corpus size or supply schema-compatible fixtures; the original data may contain rows or keys absent from mocks.' if failure_kind=='mock_input_or_code_mismatch' else 'Inspect the traceback: a mock run alone cannot establish behavior with original inputs.'
            buffer.write(traceback.format_exc())
        finally:
            if mock_module_names:
                for name in list(sys.modules):
                    if any(name==module or name.startswith(module+'.') for module in mock_module_names):sys.modules.pop(name,None)
                sys.modules.update(saved_model_modules)
            os.chdir(original);sys.path[:]=old_path
            if old_backend is None:os.environ.pop('MPLBACKEND',None)
            else:os.environ['MPLBACKEND']=old_backend
            if old_home is None:os.environ.pop('HOME',None)
            else:os.environ['HOME']=old_home
    if failure_kind=='mock_input_or_code_mismatch':
        status='INPUT / CODE MISMATCH — INCONCLUSIVE'
    if mock and mocks['manifest']:
        status='MOCK '+status+' — ORIGINAL INPUTS UNVERIFIED'
    result={'file':entry,'status':status, 'failure_kind':failure_kind,'retryable':failure_kind=='browser_resource_limit','exception_type':exception_type,'action':action,'mock_words':mock_words,'log':buffer.getvalue()[-2000000:], 'mocks':mocks, 'supplied_inputs':supplied, 'mock_requested':mock, 'mock_used':bool(mocks['manifest']),
            'browser_adaptations':adaptations,'original_native_setup_verified':False if adaptations else None,
            'missing_resources':recovery_requirements(buffer.getvalue()) if status.startswith('BLOCKED') or 'BLOCKED' in status else [],
            'mock_home_redirected':mock, 'model_api_mock_requested':model_apis, 'scope':'Browser-compatible execution only; not native Python/notebook equivalence.',**detail}
    url_alias=any(m.get('origin')=='MOCK INPUT ALIAS' and str(m.get('requested_path','')).startswith(('http://','https://')) for m in mocks['manifest'])
    if mock and setup_adaptations and not force_synthetic_urls and url_alias and exception_type=='ValueError' and any(text in result['log'] for text in ('is not in list','substring not found','Start marker not found','End marker not found')):
        result.update(failure_kind='mock_input_or_code_mismatch',status='MOCK INPUT / CODE MISMATCH — INCONCLUSIVE')
        retried=run_browser_entry(folder,entry,mock,mock_words,model_apis,setup_adaptations,force_synthetic_urls=True)
        retried['original_input_attempt']=result
        retried['synthetic_url_fallback']=True
        return retried
    return result
