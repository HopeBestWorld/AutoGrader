"""Deterministic synthetic input files for explicitly labeled smoke tests only."""
import ast
import csv
import gzip
import hashlib
import importlib.machinery
import io
import json
from pathlib import Path
import re
import shutil
import sys

TEXT_PARAGRAPHS = (
    'The morning brought hope and happiness to the quiet village. Anna opened the window and smiled at her friends. They walked beside the river and shared a simple meal. Everyone felt grateful for the peaceful day.',
    'At night the storm returned. The road became dark and dangerous, and Thomas worried about the journey. He felt fear and sadness when he heard the bad news. Yet his friends promised to help him.',
    'The travelers paused near the old bridge to examine a map. Although their plans had changed, they considered several possible routes, comparing distance, weather, and the supplies that remained. Their conversation continued until the rain stopped.',
    'By afternoon the sun appeared again. The children played in the garden while their parents worked nearby. The family listened to a story about a difficult voyage, a surprising discovery, and a joyful return home.',
)
SUPPORTED = {'.html','.htm','.txt', '.csv', '.tsv', '.json', '.jsonl', '.ndjson'}


def synthetic_text(words=18000, hints=None):
    hints=hints or {'starts':[],'ends':[],'phrases':[]}
    paragraphs, count = [], 0
    while count < words:
        paragraph = TEXT_PARAGRAPHS[len(paragraphs) % len(TEXT_PARAGRAPHS)]
        if len(paragraphs)%25==0:
            paragraphs.append(f"CHAPTER {len(paragraphs)//25+1}\n\n{paragraph}")
        else: paragraphs.append(paragraph)
        count += len(paragraph.split())
    # Literal text markers are fixture requirements, not expected computed answers.
    return ('SYNTHETIC SMOKE TEST CORPUS — NOT STUDENT DATA\n\n'+'\n\n'.join(hints['starts'])+'\n\n'+
            '\n\n'.join(paragraphs)+'\n\n'+'\n\n'.join(hints['phrases'])+'\n\n'+'\n\n'.join(hints['ends'])+'\n')


def infer_text_hints(work):
    from prerequisites import sources
    starts,ends,phrases=[],[],[]
    for _,source in sources(work):
        try:tree=ast.parse(source)
        except SyntaxError:continue
        bindings={}
        for n in ast.walk(tree):
            if isinstance(n,ast.Call):
                for keyword in n.keywords:
                    if keyword.arg in ('start_at','end_at'):
                        value=keyword.value.value if isinstance(keyword.value,ast.Constant) else bindings.get(getattr(keyword.value,'id',''))
                        add(value,'end_marker' if keyword.arg=='end_at' else 'start_marker')
            if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant) and isinstance(n.value.value,str):
                for t in n.targets:
                    if isinstance(t,ast.Name):bindings[t.id]=n.value.value
        def add(value,where=None):
            if not isinstance(value,str) or not 5<=len(value)<=300 or value.startswith(('http:','https:')):return
            end=bool(re.search(r'\bend\b|license|end_marker|end_text',str(where or '')+' '+value,re.I))
            destination=ends if end else starts
            if value not in destination:destination.append(value)
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='contains' and n.args and isinstance(n.args[0],ast.Constant) and isinstance(n.args[0].value,str) and len(n.args[0].value)>10:
                if any(k.arg=='regex' and isinstance(k.value,ast.Constant) and k.value.value is False for k in n.keywords):phrases.append(n.args[0].value)
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('index','find','split') and n.args:
                arg=n.args[0]
                value=arg.value if isinstance(arg,ast.Constant) else bindings.get(arg.id) if isinstance(arg,ast.Name) else None
                if isinstance(value,str) and (len(value)>12 or re.search('start|end|marker',str(getattr(arg,'id','')),re.I)):add(value,getattr(arg,'id',''))
            if isinstance(n,ast.Assign):
                for t in n.targets:
                    if isinstance(t,ast.Name) and re.search(r'start_marker|end_marker|start_text|end_text',t.id,re.I):add(bindings.get(t.id),t.id)
                if isinstance(n.value,ast.Dict) and any(isinstance(t,ast.Name) and re.search('events|anchors|quotes|phrases',t.id,re.I) for t in n.targets):
                    phrases.extend(v.value for v in n.value.values if isinstance(v,ast.Constant) and isinstance(v.value,str) and 5<len(v.value)<300)
    return {'starts':list(dict.fromkeys(starts))[:32],'ends':list(dict.fromkeys(ends))[:32],'phrases':list(dict.fromkeys(phrases))[:32],'scope':'Only literal text boundaries/anchors were used. No expected calculation results were copied and no assertions were changed.'}


def infer_columns(work):
    """Search hints only. Never execute student expressions or claim a real schema."""
    from prerequisites import sources
    columns = []
    excluded = {'text/plain', 'text/html', '__name__', '__main__'}
    for _, source in sources(work):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                value = node.slice
                candidates = value.elts if isinstance(value, (ast.List, ast.Tuple)) else [value]
                for candidate in candidates:
                    if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                        name = candidate.value
                        if 0 < len(name) <= 64 and name not in excluded and not any(c in name for c in '/\\\n'):
                            columns.append(name)
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg in ('usecols', 'names') and isinstance(keyword.value, (ast.List, ast.Tuple)):
                        columns.extend(v.value for v in keyword.value.elts if isinstance(v, ast.Constant) and isinstance(v.value, str))
    return list(dict.fromkeys(['id', 'text', 'label', 'score', 'value'] + columns))[:80]


def synthetic_rows(columns, count=24):
    def value(column, i):
        if any(term in column.lower() for term in ('text', 'passage', 'article', 'content', 'review', 'sentence')):
            return TEXT_PARAGRAPHS[i % len(TEXT_PARAGRAPHS)]
        if any(term in column.lower() for term in ('title', 'author', 'name', 'category')):
            return f'Synthetic {column} {i + 1}'
        if column.lower() in ('word','token'):
            return ['happy','hope','sad','fear','bad','good'][i%6]
        if column.lower()=='sentiment':
            return 'positive' if i%2 else 'negative'
        if column.lower() in ('label', 'target', 'class'):
            return i % 2
        return round((i + 1) / count, 4) if any(term in column.lower() for term in ('score', 'value', 'rate')) else i + 1
    return [{column: value(column, i) for column in columns} for i in range(count)]


def synthetic_clear_rows(count=500):
    return [{'id':i,'ID':i,'author':'Carroll' if i%10==0 else f'Synthetic author {i%23}',
             'title':f'Synthetic reference {i}','year':1800+i%200,'category':'Lit' if i%2 else 'Info',
             'excerpt':' '.join(TEXT_PARAGRAPHS[j%len(TEXT_PARAGRAPHS)] for j in range(1+i%5)),
             'difficulty':float(i%31)/5-3} for i in range(count)]


def lexicon_vocabulary(work,hints):
    """Synthetic coverage vocabulary only; weights are deterministic test values."""
    text=' '.join(TEXT_PARAGRAPHS)+' '+' '.join(hints['starts']+hints['ends']+hints['phrases'])
    for p in Path(work).rglob('*.txt'):
        if p.stat().st_size<=5*1024**2:
            text+=' '+p.read_text(encoding='utf-8',errors='replace')
    return list(dict.fromkeys(re.findall(r"[a-z][a-z'-]{1,40}",text.lower())))[:30000]


def write_synthetic(path, reader, columns, words, hints=None, vocabulary=None):
    """Return format details or None when the format cannot be guessed responsibly."""
    suffix = path.suffix.lower()
    compressed = suffix == '.gz'
    inner = Path(path.stem).suffix.lower() if compressed else suffix
    if inner=='' and reader in ('read_text','load_segments'):inner='.txt'
    if inner not in SUPPORTED:
        return None
    rows = synthetic_rows(columns)
    if (reader=='load_lexicon' or path.name.lower() in ('bingliu.csv','syuzhet.csv')) and inner in ('.csv','.txt'):
        content = '1,happy\n1,hope\n1,joy\n1,good\n-1,sad\n-1,fear\n-1,bad\n-1,dangerous\n'
        for index,word in enumerate(vocabulary or []):
            if word not in {'happy','hope','joy','good','sad','fear','bad','dangerous'}:content+=f'{1 if index%2 else -1},{word}\n'
        details = {'format': 'synthetic headerless sentiment lexicon', 'schema': ['weight', 'word'],'weight_source':'Deterministic synthetic test weights; not a trained sentiment lexicon.'}
    elif (reader == 'load_emolex' or 'emolex' in path.name.lower()) and inner in ('.txt', '.tsv'):
        content = ''.join(f'{word}\t{emotion}\t1\n' for word, emotion in [('happy','joy'),('hope','positive'),('sad','sadness'),('fear','fear'),('bad','negative'),('storm','anger'),('friends','trust'),('dangerous','disgust'),('journey','anticipation'),('surprising','surprise')])
        labels=['joy','sadness','fear','anger','trust','disgust','anticipation','surprise']
        for index,word in enumerate(vocabulary or []):
            content+=f'{word}\t{"positive" if index%2 else "negative"}\t1\n{word}\t{labels[index%len(labels)]}\t1\n'
        details = {'format': 'synthetic emotion lexicon', 'schema': ['word','emotion','flag'],'label_source':'Deterministic synthetic test labels; not original NRC annotations.'}
    elif inner in ('.html','.htm'):
        import html
        content='<html><body>'+''.join('<p>'+html.escape(p)+'</p>' for p in synthetic_text(words,hints).split('\n\n'))+'</body></html>'
        details={'format':'synthetic HTML prose','words':words}
    elif inner == '.txt':
        content = synthetic_text(words,hints)
        details = {'format': 'synthetic prose', 'words': len(content.split())}
    elif inner in ('.csv', '.tsv'):
        if 'clear' in path.stem.lower():
            rows=synthetic_clear_rows();columns=list(rows[0])
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, delimiter='\t' if inner == '.tsv' else ',')
        writer.writeheader(); writer.writerows(rows)
        content = buffer.getvalue()
        details = {'format': inner.lstrip('.'), 'schema': columns, 'rows': len(rows)}
    elif inner == '.json':
        content = json.dumps(rows)
        details = {'format': 'JSON array of synthetic records', 'schema': columns, 'rows': len(rows)}
    else:
        content = '\n'.join(json.dumps(row) for row in rows) + '\n'
        details = {'format': 'JSON lines of synthetic records', 'schema': columns, 'rows': len(rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    if compressed:
        # gzip.compress otherwise embeds a varying wall-clock timestamp.
        path.write_bytes(gzip.compress(content.encode('utf-8'), mtime=0))
    else:
        path.write_text(content, encoding='utf-8')
    return details


def manifest_entry(path, work, origin, **extra):
    data = path.read_bytes()
    return {'file': str(path.relative_to(work)), 'origin': origin, 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), **extra}


def infer_corpus_size(work, minimum):
    """Bound synthetic text size using explicit segment sizes/passage indices only."""
    from prerequisites import sources
    indices=[]; sizes=[200]
    for _,source in sources(work):
        try: tree=ast.parse(source)
        except SyntaxError: continue
        for node in ast.walk(tree):
            candidates=[]
            if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Attribute) and node.value.attr in ('loc','iloc'):
                candidates=[node.slice.elts[0] if isinstance(node.slice,ast.Tuple) else node.slice]
            if isinstance(node,ast.Call):
                name=node.func.id if isinstance(node.func,ast.Name) else getattr(node.func,'attr','')
                if re.search(r'passage|evidence',name,re.I) and node.args:candidates.append(node.args[0])
                for kw in node.keywords:
                    if kw.arg in ('words_per_segment','words_per_chunk','segment_size','passage_size') and isinstance(kw.value,ast.Constant) and isinstance(kw.value.value,int) and 1<=kw.value.value<=5000:sizes.append(kw.value.value)
            for value in candidates:
                if isinstance(value,ast.Constant) and isinstance(value.value,int) and 0<=value.value<=10000:indices.append(value.value)
    needed=max(minimum,int((max(indices)+1)*max(sizes)*1.25)+5000) if indices else minimum
    return {'minimum_words':minimum,'effective_words':min(needed,200000),'largest_explicit_passage_index':max(indices) if indices else None,'assumed_words_per_segment':max(sizes),'capped':needed>200000,'scope':'Synthetic capacity hint only; this does not reproduce original text or guarantee its schema/structure.'}


def prepare_mock_inputs(work, fixtures=None, words=18000, model_apis=False, browser=False):
    """Fill absent inputs only. Never generate Python implementations or edit originals."""
    from prerequisites import audit_prerequisites, provided_candidates, sources
    from grader_core import stage_paths
    if not 100 <= words <= 200000:
        raise ValueError('Mock corpus length must be between 100 and 200,000 words')
    work = Path(work).resolve()
    manifest, unresolved, skipped = [], [], []
    provided_files=[]
    if fixtures:
        for source in sorted(Path(fixtures).rglob('*')):
            if not source.is_file():
                continue
            relative = source.relative_to(fixtures)
            target = work / relative
            if target.exists():
                skipped.append({'file': str(relative), 'reason': 'Real/student-provided file takes precedence'})
                continue
            module = relative.parts[0].removesuffix('.py')
            if source.suffix == '.py' and (module in sys.stdlib_module_names or importlib.machinery.PathFinder.find_spec(module, sys.path) is not None):
                skipped.append({'file': str(relative), 'reason': 'Installed Python module takes precedence over a mock'})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            manifest.append(manifest_entry(target, work, 'UPLOADED MOCK FIXTURE', kind='helper test double' if source.suffix == '.py' else 'data fixture'))
    audit = audit_prerequisites(work)
    modules={item['module'] for item in audit['modules']}
    missing={item['module'] for item in audit['modules'] if item['status'].startswith('MISSING')}
    uses_readability_model=any('load_language_model' in source for _,source in sources(work))
    readability_fixture='readability_tools' in missing and not (work/'readability_tools.py').exists()
    dependency_fixtures=[name for name in ('spacy','en_core_web_sm','wordfreq','textstat')
                         if name in modules and (browser or name in missing) and not (work/(name+'.py')).exists() and not (work/name).exists()]
    if readability_fixture or dependency_fixtures:
        templates=Path(__file__).parent/'mock_helpers'
        for name in ['_readability_nlp']+(['readability_tools'] if readability_fixture else [])+dependency_fixtures:
            target=work/(name+'.py')
            if target.exists():continue
            source=(templates/(name+'.py')).read_text()
            if name=='readability_tools':source=source.replace('MODEL_APIS_ENABLED = False','MODEL_APIS_ENABLED = '+str(bool(model_apis)))
            target.write_text(source)
            manifest.append(manifest_entry(target,work,'BUNDLED MOCK HELPER',kind='readability/NLP API test double',limitations='Heuristic syllables, synthetic dependency trees/tags and fixture frequencies; not the actual course library, trained NLP model or validated readability values.'))
        if readability_fixture:
            target=work/'_mock_readability_clear.csv'
            if not target.exists():
                rows=synthetic_clear_rows()
                with target.open('w',newline='') as stream:
                    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
                manifest.append(manifest_entry(target,work,'AUTOMATIC SYNTHETIC DATA',format='synthetic CLEAR reference',rows=len(rows),schema=list(rows[0]),limitations='Fabricated reference excerpts and labels, not real human difficulty ratings.'))
            if model_apis and uses_readability_model:
                manifest.append(manifest_entry(work/'readability_tools.py',work,'BUNDLED MOCK MODEL API',kind='GPT-2 surprisal API test double',limitations='Deterministic synthetic per-word values. No model weights, token probabilities or real inference.'))
    # Known, reviewed test double only. Never synthesize arbitrary helper APIs.
    for item in audit['modules']:
        if item['module']=='sentiment_toolkit' and item['status'].startswith('MISSING'):
            target=work/'sentiment_toolkit.py'
            fixture=Path(__file__).parent/'mock_helpers/sentiment_toolkit.py'
            if fixture.is_file() and not target.exists():
                shutil.copy2(fixture,target)
                manifest.append(manifest_entry(target,work,'BUNDLED MOCK HELPER',kind='sentiment toolkit test double',limitations='Simplified lexicon methods only; not the original course library or model APIs.'))
    if model_apis and any(m['module']=='transformers' for m in audit['modules']):
        target=work/'transformers.py'
        if not target.exists():
            shutil.copy2(Path(__file__).parent/'mock_helpers/transformers.py',target)
            manifest.append(manifest_entry(target,work,'BUNDLED MOCK MODEL API',kind='text-generation chat API test double',limitations='Hash-based synthetic replies. No actual model, prediction quality, inference or network was tested.'))
    audit=audit_prerequisites(work)
    generated_outputs={str((work/Path(item['source']).parent/item['path']).resolve()) for item in audit.get('generated_outputs',[])}
    hints=infer_text_hints(work)
    sizing=infer_corpus_size(work,words)
    words=sizing['effective_words']
    vocabulary=lexicon_vocabulary(work,hints)
    columns = infer_columns(work)
    mappings = {}
    seen = set()
    for item in audit['inputs']:
        value = item['path']
        if item['status'] == 'AVAILABLE':
            continue
        if value.startswith(('http://', 'https://')):
            if readability_fixture and item.get('reader')=='fetch_url':
                documents=[p for p in work.rglob('*') if p.is_file() and p.suffix.lower() in ('.txt','.html','.htm')
                           and not any(word in p.name.lower() for word in ('lexicon','emolex','bingliu','syuzhet','readme','license'))
                           and not any(m['file']==str(p.relative_to(work)) for m in manifest)]
                by_hash={hashlib.sha256(p.read_bytes()).hexdigest():p for p in documents}
                if len(by_hash)==1:
                    provided=next(iter(by_hash.values()))
                    mappings[value]=str(provided.relative_to(work))
                    manifest.append(manifest_entry(provided,work,'MOCK INPUT ALIAS',original=str(provided.relative_to(work)),requested_path=value,
                                                   reason='Only supplied prose document used in place of the helper URL. Contents unchanged; URL/document identity is an explicit unverified assumption.'))
                    continue
                if not by_hash:
                    target=work/'_mock_inputs'/hashlib.sha256(value.encode()).hexdigest()[:12]/'remote-text.txt'
                    details=write_synthetic(target,'read_text',columns,words,hints,vocabulary)
                    if sum(m['origin']=='AUTOMATIC SYNTHETIC DATA' for m in manifest)<64 and sum(m['bytes'] for m in manifest)+target.stat().st_size<=16*1024**2:
                        manifest.append(manifest_entry(target,work,'AUTOMATIC SYNTHETIC DATA',requested_path=value,source=item['source'],
                                                       limitations='Synthetic text replaces this reviewed helper URL request. No remote download was tested.',**details))
                        mappings[value]=str(target.relative_to(work))
                        continue
                    target.unlink()
            unresolved.append({**item, 'reason': 'Remote inputs are not intercepted; supply a fixture and explicit path mapping.'})
            continue
        target = (work / Path(item['source']).parent / value).resolve()
        if str(target) in generated_outputs and not target.is_file():
            skipped.append({'file':value,'reason':'Submitted code writes this path. Leave absent caches/outputs for the submission to create.'})
            continue
        external = not target.is_relative_to(work)
        if external:
            filename = value.replace('\\', '/').rsplit('/', 1)[-1]
            if not filename or filename in ('.', '..'):
                unresolved.append({**item, 'reason': 'Cannot resolve a file name for this external input.'})
                continue
            target = work / '_mock_inputs' / hashlib.sha256(value.encode()).hexdigest()[:12] / filename
        if target in seen or target.exists():
            continue
        seen.add(target)
        if sum(m['origin'] == 'AUTOMATIC SYNTHETIC DATA' for m in manifest) >= 64:
            unresolved.append({**item, 'reason': 'Automatic mock limit of 64 files reached; supply a fixture bundle.'})
            continue
        candidates,match_reason=provided_candidates([p for p in work.rglob('*') if p.is_file() and p!=target and not any(m['file']==str(p.relative_to(work)) for m in manifest)],target.name)
        if len(candidates)>1:
            unresolved.append({**item,'reason':'Multiple supplied files have this basename. Select an exact path mapping; synthetic data is not substituted for an ambiguous supplied input.'})
            continue
        if len(candidates)==1:
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(candidates[0],target)
            provided_files.append(manifest_entry(target,work,'STAGED PROVIDED FILE',original=str(candidates[0].relative_to(work)),reason=match_reason))
            if external:mappings[value]=str(target.relative_to(work))
            continue
        if target.suffix.lower() in ('.html','.htm'):
            html_candidates=[p for p in work.rglob('*') if p.is_file() and p.suffix.lower() in ('.html','.htm') and not any(m['file']==str(p.relative_to(work)) for m in manifest)]
            if len(html_candidates)==1:
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(html_candidates[0],target)
                manifest.append(manifest_entry(target,work,'MOCK INPUT ALIAS',original=str(html_candidates[0].relative_to(work)),reason='Only HTML attachment staged at requested HTML path; dataset identity is an explicit unverified test assumption. Contents unchanged.'))
                continue
        details = write_synthetic(target, item.get('reader', ''), columns, words,hints,vocabulary)
        if details is None:
            unresolved.append({**item, 'reason': 'Unsupported automatic mock format; provide a mock fixture with the required schema.'})
            continue
        if sum(m['bytes'] for m in manifest) + target.stat().st_size > 16 * 1024**2:
            target.unlink()
            unresolved.append({**item, 'reason': 'Automatic mock byte budget reached; supply a fixture bundle.'})
            continue
        manifest.append(manifest_entry(target, work, 'AUTOMATIC SYNTHETIC DATA', requested_path=value,
                                       source=item['source'], **details))
        if external:
            mappings[value] = str(target.relative_to(work))
    changes = stage_paths(work, mappings) if mappings else []
    for item in audit['modules']:
        if item['status'].startswith('MISSING') and not (browser and item['module']=='marimo'):
            unresolved.append({**item, 'reason': 'Unknown Python helpers are not invented. Supply a compatible test double in the mock-fixtures ZIP or the original module.'})
    unresolved.extend({**i, 'reason': 'Dynamic input paths cannot be generated automatically; supply a fixture or exact mapping.'} for i in audit['dynamic_inputs'])
    return {'manifest': manifest, 'provided_files':provided_files,'sizing':sizing, 'text_hints':hints, 'unresolved': unresolved, 'skipped': skipped, 'path_changes': changes,
            'limitations': ['Synthetic data and helper test doubles validate only this smoke-test path, not original inputs, real helper behavior, analysis validity, or assignment completion.',
                           'Inferred table schemas/types and corpus text may be incompatible with a submission. A mock failure alone does not establish a failure with real inputs.']}


def label_mock_artifact(data, filename):
    warning = 'MOCK SMOKE TEST OUTPUT — synthetic inputs or helper test doubles were used. Original-input behavior and assignment completion are NOT verified.'
    if filename.endswith('.html'):
        text = data.decode('utf-8', errors='replace')
        banner = f'<div role="alert" style="padding:16px;background:#fff3cd;color:#533f03;font-weight:bold">{warning}</div>'
        if re.search(r'<body\b[^>]*>', text, re.I):
            text = re.sub(r'(<body\b[^>]*>)', lambda m: m.group(1) + banner, text, count=1, flags=re.I)
        else:
            text = banner + text
        return text.encode('utf-8')
    if filename.endswith('.ipynb'):
        import nbformat
        notebook = nbformat.reads(data.decode('utf-8'), as_version=4)
        notebook.metadata['grader_test_scope'] = 'MOCK SMOKE TEST'
        notebook.cells.insert(0, nbformat.v4.new_markdown_cell(warning))
        return nbformat.writes(notebook).encode('utf-8')
    return data
