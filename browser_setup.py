"""Explicit browser setup test doubles; never claim a native installer/TLS ran."""
import ast
from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
import types


class UnsupportedBrowserSetup(Exception):
    pass


PACKAGES={'pronouncing':'pronouncing','spacy':'spacy','numpy':'numpy','pandas':'pandas','altair':'altair'}


def setup_command(command):
    if not isinstance(command,(list,tuple)) or any(not isinstance(arg,str) for arg in command[1:]):return None
    if len(command)<3 or command[0] not in (sys.executable,'python','python3'):return None
    if list(command[1:4])==['-m','pip','install'] and len(command)>4 and all(name in PACKAGES for name in command[4:]):
        return ('packages',list(command[4:]))
    if list(command[1:])==['-m','spacy','download','en_core_web_sm']:
        return ('model',['en_core_web_sm'])
    return None


def setup_requirements(source):
    """Only literal, reviewed setup calls supply dependency hints."""
    result={'packages':[],'models':[],'truststore':False}
    try:tree=ast.parse(source)
    except SyntaxError:return result
    aliases={}
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            for name in node.names:
                aliases[name.asname or name.name]=name.name
                if name.name=='truststore':result['truststore']=True
        if isinstance(node,ast.ImportFrom):
            for name in node.names:aliases[name.asname or name.name]=(node.module or '')+'.'+name.name
            if node.module=='truststore':result['truststore']=True
    def qualified(node):
        if isinstance(node,ast.Name):return aliases.get(node.id,node.id)
        if isinstance(node,ast.Attribute):return qualified(node.value)+'.'+node.attr
        return ''
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call) or qualified(node.func)!='subprocess.check_call' or not node.args or node.keywords:continue
        if not isinstance(node.args[0],(ast.List,ast.Tuple)):continue
        args=[]
        for value in node.args[0].elts:
            if isinstance(value,ast.Constant) and isinstance(value.value,str):args.append(value.value)
            elif qualified(value)=='sys.executable':args.append(sys.executable)
            else:break
        else:
            parsed=setup_command(args)
            if parsed:
                key='packages' if parsed[0]=='packages' else 'models'
                result[key]=list(dict.fromkeys(result[key]+parsed[1]))
    return result


@contextmanager
def browser_setup(work,requirements,manifest,enabled=True):
    records=[]
    if not enabled:
        yield records;return
    import subprocess
    original_check_call=subprocess.check_call
    prior_truststore=sys.modules.get('truststore')
    replaced_tls=False
    def record(item):
        records.append(item)
        manifest.append({'file':'(browser setup) '+item['operation'],'origin':'BROWSER SETUP ADAPTATION',
                         'kind':item['kind'],'native_operation_verified':False,'limitations':item['scope']})
    def check_call(command,*args,**kwargs):
        parsed=setup_command(command)
        if parsed is None or args or kwargs:
            raise UnsupportedBrowserSetup('This process command is outside the reviewed browser setup contract: '+repr(command))
        kind,names=parsed
        checked=[]
        for name in names:
            module=importlib.import_module(PACKAGES[name] if kind=='packages' else name)
            if kind=='model':
                if not callable(getattr(module,'load',None)):raise UnsupportedBrowserSetup('The browser model fixture has no load API: '+name)
                parser=module.load()
                if not len(parser('Browser model fixture test.')):raise UnsupportedBrowserSetup('The browser model fixture failed its load check')
            checked.append({'name':name,'module':getattr(module,'__name__',name),'version':str(getattr(module,'__version__','not reported'))})
        record({'kind':'package setup' if kind=='packages' else 'model setup','operation':' '.join(command[1:]),
                'command':list(command),'checked_dependencies':checked,
                'scope':'Available browser modules/model test doubles were checked. The native installation/download command was not executed or verified.'})
        return 0
    # The shim changes no SSL classes, trust roots or certificate settings.
    # It records native setup as unverified, rather than claiming native TLS.
    work=Path(work)
    if requirements.get('truststore') and not any((base/'truststore.py').exists() or (base/'truststore').exists() for base in (work,Path.cwd())):
        shim=types.ModuleType('truststore');shim.__version__='browser-setup-test-double'
        def inject_into_ssl():
            record({'kind':'system SSL setup','operation':'truststore.inject_into_ssl',
                    'scope':'Native system SSL injection was omitted in this browser-adapted application test. Browser certificate validation was not changed. Native truststore/SSL behavior is unverified.'})
        def unsupported(name):
            if name.startswith('__'):raise AttributeError(name)
            raise UnsupportedBrowserSetup('truststore.'+name+' is outside the reviewed browser setup contract')
        shim.inject_into_ssl=inject_into_ssl;shim.__getattr__=unsupported
        sys.modules['truststore']=shim;replaced_tls=True
        record({'kind':'TLS module test double','operation':'truststore import',
                'scope':'A reviewed browser setup test double was supplied. Native truststore behavior is unverified; no SSL classes, certificate settings or trust roots were changed.'})
    subprocess.check_call=check_call
    try:yield records
    finally:
        subprocess.check_call=original_check_call
        if replaced_tls:
            if prior_truststore is None:sys.modules.pop('truststore',None)
            else:sys.modules['truststore']=prior_truststore
