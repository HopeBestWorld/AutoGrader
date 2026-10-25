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
SUPPORTED = {'.txt', '.csv', '.tsv', '.json', '.jsonl', '.ndjson'}


def synthetic_text(words=18000):
    paragraphs, count = [], 0
    while count < words:
        paragraph = TEXT_PARAGRAPHS[len(paragraphs) % len(TEXT_PARAGRAPHS)]
        paragraphs.append(paragraph)
        count += len(paragraph.split())
    return 'SYNTHETIC SMOKE TEST CORPUS — NOT STUDENT DATA\n\n' + '\n\n'.join(paragraphs) + '\n'


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
        if column.lower() in ('label', 'target', 'class'):
            return i % 2
        return round((i + 1) / count, 4) if any(term in column.lower() for term in ('score', 'value', 'rate')) else i + 1
    return [{column: value(column, i) for column in columns} for i in range(count)]


def write_synthetic(path, reader, columns, words):
    """Return format details or None when the format cannot be guessed responsibly."""
    suffix = path.suffix.lower()
    compressed = suffix == '.gz'
    inner = Path(path.stem).suffix.lower() if compressed else suffix
    if inner not in SUPPORTED:
        return None
    rows = synthetic_rows(columns)
    if reader == 'load_lexicon' and inner in ('.csv', '.txt'):
        content = '1,happy\n1,hope\n1,joy\n1,good\n-1,sad\n-1,fear\n-1,bad\n-1,dangerous\n'
        details = {'format': 'synthetic headerless sentiment lexicon', 'schema': ['weight', 'word']}
    elif reader == 'load_emolex' and inner in ('.txt', '.tsv'):
        content = ''.join(f'{word}\t{emotion}\t1\n' for word, emotion in [('happy','joy'),('hope','positive'),('sad','sadness'),('fear','fear'),('bad','negative')])
        details = {'format': 'synthetic emotion lexicon', 'schema': ['word','emotion','flag']}
    elif inner == '.txt':
        content = synthetic_text(words)
        details = {'format': 'synthetic prose', 'words': len(content.split())}
    elif inner in ('.csv', '.tsv'):
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


def prepare_mock_inputs(work, fixtures=None, words=18000):
    """Fill absent inputs only. Never generate Python implementations or edit originals."""
    from prerequisites import audit_prerequisites
    from grader_core import stage_paths
    if not 100 <= words <= 200000:
        raise ValueError('Mock corpus length must be between 100 and 200,000 words')
    work = Path(work).resolve()
    manifest, unresolved, skipped = [], [], []
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
    # Known, reviewed test double only. Never synthesize arbitrary helper APIs.
    for item in audit['modules']:
        if item['module']=='sentiment_toolkit' and item['status'].startswith('MISSING'):
            target=work/'sentiment_toolkit.py'
            fixture=Path(__file__).parent/'mock_helpers/sentiment_toolkit.py'
            if fixture.is_file() and not target.exists():
                shutil.copy2(fixture,target)
                manifest.append(manifest_entry(target,work,'BUNDLED MOCK HELPER',kind='sentiment toolkit test double',limitations='Simplified lexicon methods only; not the original course library or model APIs.'))
    audit=audit_prerequisites(work)
    sizing=infer_corpus_size(work,words)
    words=sizing['effective_words']
    columns = infer_columns(work)
    mappings = {}
    seen = set()
    for item in audit['inputs']:
        value = item['path']
        if item['status'] == 'AVAILABLE':
            continue
        if value.startswith(('http://', 'https://')):
            unresolved.append({**item, 'reason': 'Remote inputs are not intercepted; supply a fixture and explicit path mapping.'})
            continue
        target = (work / Path(item['source']).parent / value).resolve()
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
        details = write_synthetic(target, item.get('reader', ''), columns, words)
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
        if item['status'].startswith('MISSING'):
            unresolved.append({**item, 'reason': 'Unknown Python helpers are not invented. Supply a compatible test double in the mock-fixtures ZIP or the original module.'})
    unresolved.extend({**i, 'reason': 'Dynamic input paths cannot be generated automatically; supply a fixture or exact mapping.'} for i in audit['dynamic_inputs'])
    return {'manifest': manifest, 'sizing':sizing, 'unresolved': unresolved, 'skipped': skipped, 'path_changes': changes,
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
