"""Assignment-independent evidence review and honest execution of submitted code.

Evidence is a retrieval aid, never an automatic correctness or completion judgment.
Student code is untrusted: execution needs an external disposable worker for isolation.
"""
from __future__ import annotations

import ast
from collections import defaultdict
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import tokenize
import zipfile

MAX_BYTES = 2 * 1024**3
MAX_MEMBERS = 10000
MAX_TEXT = 2_000_000
CANVAS = re.compile(r'^(.+?)(?:_LATE)?_(\d+)_(\d+)_(.+)$')
STOPWORDS = set('the a an and or to of in for with your you is be as on at by this that from it use using part assignment should must include display results methods code provided notebook document text passages'.split())


def native_execution_available():
    """Browser review does not provide a native process/notebook runtime."""
    return sys.platform not in ("emscripten", "wasi")


def safe_extract(archive, destination, budget=None, depth=0):
    budget = budget if budget is not None else [0, 0]
    if depth > 4:
        raise ValueError('Nested ZIP depth exceeds 4')
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            if '__MACOSX' in parts or any(p.startswith('._') for p in parts):
                continue
            if not parts or name.startswith('/') or '..' in parts or ':' in name or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f'Unsafe ZIP entry: {name}')
            target = destination.joinpath(*parts)
            if not target.is_relative_to(destination):
                raise ValueError(f'Unsafe ZIP entry: {name}')
            budget[0] += info.file_size
            budget[1] += 1
            byte_limit = MAX_BYTES if native_execution_available() else 128 * 1024**2
            if budget[0] > byte_limit or budget[1] > MAX_MEMBERS:
                raise ValueError(f'ZIP exceeds {byte_limit // 1024**2} MiB / 10,000 entry limit')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
    return budget


def unpack_nested(root, budget, depth=1):
    # Only visit archives directly discovered at this level, avoiding repeated expansion.
    for path in sorted(Path(root).rglob('*.zip')):
        dest = path.with_name(path.name + '_unpacked')
        safe_extract(path, dest, budget, depth)
        unpack_nested(dest, budget, depth + 1)


def group_submissions(archive, root):
    root = Path(root)
    raw = root / 'canvas'
    budget = safe_extract(archive, raw)
    groups = {}
    for source in sorted(raw.rglob('*')):
        if not source.is_file():
            continue
        match = CANVAS.match(source.name)
        if match:
            student, ident, _, original = match.groups()
            key = f'{student}_{ident}'
            relative = Path(original)
        else:
            # Unknown files are retained for review, never silently assigned to a student.
            key = 'UNMATCHED_REVIEW'
            relative = source.relative_to(raw)
        folder = groups.setdefault(key, root / 'students' / key)
        target = folder / relative
        if target.exists():
            target = target.with_name(source.name)
        if target.exists():
            raise ValueError(f'Duplicate submission file: {source.name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for folder in groups.values():
        unpack_nested(folder, budget)
    return groups


def pdf_text(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > 200:
        raise ValueError('PDF exceeds 200 pages')
    text = '\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
    if not text.strip():
        raise ValueError('PDF has no extractable text. Supply a text checklist for scanned PDFs.')
    return text


def profile_from_text(text, title='Uploaded assignment'):
    """Extract explicitly numbered sections; never interpret document text as commands."""
    text = text.replace('\r\n', '\n')
    parts = list(re.finditer(r'(?mi)^\s*((?:part|section|task|question)\s+\d+[a-z]?\s*[:.)\-]?[^\n]*)', text))
    if not parts:
        parts = list(re.finditer(r'(?m)^\s*(\d+[.)]\s+[^\n]+)', text))
    requirements = []
    for i, part in enumerate(parts):
        body = text[part.end():parts[i + 1].start() if i + 1 < len(parts) else len(text)].strip()
        requirements.append({'id': f'part-{i + 1}', 'title': part.group(1).strip(), 'description': body,
                             'keywords': [], 'criteria': []})
    if not requirements:
        # Plain checklist: one nonempty line per requirement.
        requirements = [{'id': f'item-{i + 1}', 'title': line.strip('-* '), 'description': '', 'keywords': [], 'criteria': []}
                        for i, line in enumerate(text.splitlines()) if line.strip()]
    if not requirements:
        raise ValueError('No requirements supplied')
    return validate_profile({'title': title, 'requirements': requirements})


def validate_profile(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get('title'), str):
        raise ValueError('Checklist needs a title and requirements list')
    reqs = profile.get('requirements')
    if not isinstance(reqs, list) or not 1 <= len(reqs) <= 200:
        raise ValueError('Checklist must have 1–200 requirements')
    seen = set()
    for i, req in enumerate(reqs):
        if not isinstance(req, dict) or not isinstance(req.get('title'), str) or not req['title'].strip():
            raise ValueError('Each requirement needs a nonempty title')
        req.setdefault('id', f'item-{i + 1}')
        if not isinstance(req['id'], str) or req['id'] in seen:
            raise ValueError('Requirement IDs must be unique strings')
        seen.add(req['id'])
        for field in ('description',):
            req.setdefault(field, '')
            if not isinstance(req[field], str):
                raise ValueError(f'{field} must be text')
        for field in ('keywords', 'criteria', 'evidence_types'):
            req.setdefault(field, [])
            if not isinstance(req[field], list) or any(not isinstance(s, str) for s in req[field]):
                raise ValueError(f'{field} must be a list of strings')
        if any(kind not in ('code', 'prose') for kind in req['evidence_types']):
            raise ValueError('evidence_types may contain code and/or prose')
    return profile


def load_profile(data, filename):
    if filename.lower().endswith('.json'):
        return validate_profile(json.loads(data))
    text = pdf_text(data) if filename.lower().endswith('.pdf') else data.decode('utf-8-sig')
    title = next((line.strip() for line in text.splitlines() if line.strip()), 'Uploaded assignment')
    return profile_from_text(text, title)


def profile_digest(profile):
    return hashlib.sha256(json.dumps(profile, sort_keys=True).encode()).hexdigest()


def read_source(path):
    with tokenize.open(path) as handle:
        return handle.read()


def source_blocks(source, name):
    """AST-only analysis: don't execute imports, eval, notebook cells, or markup."""
    blocks = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], {'syntax': f'line {exc.lineno}: {exc.msg}', 'imports': [], 'marimo': False,
                    'disabled_cells': 0, 'path_literals': [], 'stop_calls': 0}
    imports, paths = set(), set()
    prose_ranges = []
    marimo_aliases = {'marimo'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split('.')[0])
                if alias.name == 'marimo':
                    marimo_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or '').split('.')[0])
            if node.module == 'marimo':
                marimo_aliases.update(a.asname or a.name for a in node.names)
    marimo = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'App'
                 and isinstance(n.func.value, ast.Name) and n.func.value.id in marimo_aliases for n in ast.walk(tree))
    disabled = 0
    stop_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(d, ast.Call) and any(k.arg == 'disabled' and isinstance(k.value, ast.Constant) and k.value.value is True for k in d.keywords) for d in node.decorator_list):
                disabled += 1
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ('stop', 'stop_if'):
                stop_calls += 1
            if node.func.attr in ('md', 'markdown', 'write', 'text') and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    text = arg.value
                elif isinstance(arg, ast.JoinedStr):
                    text = ''.join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else '{expression}' for v in arg.values)
                else:
                    continue
                blocks.append({'file': name, 'line': node.lineno, 'kind': 'prose', 'text': text})
                prose_ranges.append((node.lineno, node.end_lineno))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if '\n' not in s and len(s) < 500 and s.lower().endswith(('.txt', '.csv', '.tsv', '.json', '.jsonl', '.gz', '.xlsx', '.parquet')):
                paths.add(s)
    # Code evidence excludes comments, docstrings and prose-only constant strings.
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Call, ast.Import, ast.ImportFrom)):
            if any(a <= node.lineno <= b for a, b in prose_ranges):
                continue
            excerpt = '\n'.join(lines[node.lineno - 1:min(node.end_lineno, node.lineno + 5)])
            blocks.append({'file': name, 'line': node.lineno, 'kind': 'code', 'text': excerpt})
    return blocks, {'syntax': 'OK', 'imports': sorted(imports - {''}), 'marimo': marimo,
                    'disabled_cells': disabled, 'path_literals': sorted(paths), 'stop_calls': stop_calls}


def inspect_folder(folder):
    folder = Path(folder)
    blocks, scripts, attachments, issues, word_counts = [], [], [], [], []
    for path in sorted(folder.rglob('*')):
        if not path.is_file():
            continue
        name = str(path.relative_to(folder))
        if path.stat().st_size > 20 * 1024**2:
            issues.append(f'{name}: too large for static text inspection')
            continue
        try:
            if path.suffix.lower() == '.py':
                source = read_source(path)
                found, meta = source_blocks(source, name)
                blocks.extend(found)
                scripts.append({'file': name, **meta})
            elif path.suffix.lower() == '.ipynb':
                nb = json.loads(path.read_text(encoding='utf-8-sig'))
                cell_syntax = []
                imports, paths = set(), set()
                for i, cell in enumerate(nb.get('cells', [])):
                    text = ''.join(cell.get('source', []))
                    if cell.get('cell_type') == 'markdown':
                        blocks.append({'file': name, 'line': i + 1, 'kind': 'prose', 'text': text, 'location': 'cell'})
                    elif cell.get('cell_type') == 'code':
                        found, meta = source_blocks(text, name)
                        for b in found:
                            b.update(line=i + 1, location='cell')
                        blocks.extend(found)
                        cell_syntax.append(meta['syntax'])
                        imports.update(meta['imports']); paths.update(meta['path_literals'])
                scripts.append({'file': name, 'syntax': 'OK' if all(s == 'OK' for s in cell_syntax) else 'Notebook syntax/magic requires runtime review', 'imports': sorted(imports), 'marimo': False, 'disabled_cells': 0, 'stop_calls': 0, 'path_literals': sorted(paths)})
            else:
                attachments.append(name)
                if path.name.lower().endswith('.py.json'):
                    snapshot = json.loads(path.read_text(encoding='utf-8-sig'))
                    issues.append(f'{name}: saved output snapshot, not executable Python source. Prior outputs do not prove this run succeeded.')
                    for index, cell in enumerate(snapshot.get('cells', [])):
                        for output in cell.get('outputs', []):
                            data = output.get('data', {})
                            text = data.get('text/markdown', '')
                            if text:
                                from bs4 import BeautifulSoup
                                text = BeautifulSoup(str(text), 'html.parser').get_text('\n')
                                blocks.append({'file': name, 'line': index + 1, 'kind': 'attachment', 'location': 'saved-output cell', 'text': text[:MAX_TEXT]})
                if path.suffix.lower() in ('.md', '.txt', '.pdf', '.html', '.htm'):
                    if path.suffix.lower() == '.pdf':
                        text = pdf_text(path.read_bytes())
                    elif path.suffix.lower() in ('.html', '.htm'):
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(path.read_text(encoding='utf-8', errors='replace'), 'html.parser')
                        for tag in soup(['script', 'style']):
                            tag.decompose()
                        text = soup.get_text('\n')
                    else:
                        text = path.read_text(encoding='utf-8-sig', errors='replace')
                    if path.suffix.lower() == '.txt':
                        word_counts.append({'file': name, 'words': len(text.split()), 'note': 'Raw attachment count; not proof this text was analyzed or is an eligible work.'})
                    # Large corpus files cannot provide answer evidence through incidental keywords.
                    if len(text.split()) <= 10000:
                        blocks.append({'file': name, 'line': 1, 'kind': 'attachment', 'text': text[:MAX_TEXT]})
        except Exception as exc:
            issues.append(f'{name}: {type(exc).__name__}: {exc}')
    return {'blocks': blocks, 'scripts': scripts, 'attachments': attachments, 'issues': issues, 'word_counts': word_counts}


def evidence_for(requirement, blocks):
    terms = requirement['keywords'] or [w for w in re.findall(r'[a-z][a-z_-]{3,}', (requirement['title'] + ' ' + requirement['description']).lower()) if w not in STOPWORDS]
    terms = list(dict.fromkeys(t.casefold() for t in terms))[:60]
    ranked = []
    for b in blocks:
        text = b['text']
        # Strip Markdown headings, copied questions and TODO-only text from prose evidence.
        if b['kind'] != 'code':
            text = '\n'.join(line for line in text.splitlines() if not re.match(r'^\s*(?:#+|[-*]?\s*(?:TODO|YOUR ANSWER|WRITE HERE)\b)', line, re.I) and not line.rstrip().endswith('?'))
            if len(re.findall(r'\w+', text)) < 12:
                continue
        lower = text.casefold()
        matches = [t for t in terms if re.search(r'(?<!\w)' + re.escape(t) + r'(?!\w)', lower)]
        kind = 'prose' if b['kind'] == 'attachment' else b['kind']
        if matches and (not requirement.get('evidence_types') or kind in requirement['evidence_types']):
            ranked.append((len(matches), {**b, 'text': text[:2200], 'matched_terms': matches}))
    ranked.sort(key=lambda x: -x[0])
    # Keep both code and written evidence even when many code matches rank higher.
    if requirement.get('evidence_types') == ['code', 'prose']:
        code = [x for x in ranked if x[1]['kind'] == 'code'][:4]
        prose = [x for x in ranked if x[1]['kind'] != 'code'][:4]
        ranked = prose + code
    found, seen = [], set()
    for _, b in ranked:
        key = (b['file'], b['line'], b['kind'])
        if key not in seen:
            found.append(b); seen.add(key)
        if len(found) >= 8:
            break
    return found


def review_folder(folder, profile, student='submission'):
    audit = inspect_folder(folder)
    requirements = []
    for req in profile['requirements']:
        evidence = evidence_for(req, audit['blocks'])
        kinds = {'prose' if b['kind'] == 'attachment' else b['kind'] for b in evidence}
        missing_types = sorted(set(req.get('evidence_types', [])) - kinds)
        status = ('PARTIAL EVIDENCE — REVIEW' if missing_types else 'EVIDENCE FOUND — REVIEW') if evidence else 'NO MATCH FOUND — REVIEW'
        requirements.append({**req, 'evidence': evidence, 'missing_evidence_types': missing_types, 'evidence_status': status,
                             'review': 'Needs review', 'review_note': ''})
    entries = [s['file'] for s in audit['scripts'] if s['marimo'] or s['file'].endswith('.ipynb')]
    if not entries:
        entries = [s['file'] for s in audit['scripts'] if Path(s['file']).name != '__init__.py']
    return {'student': student, 'profile_digest': profile_digest(profile), 'requirements': requirements,
            'scripts': audit['scripts'], 'entrypoints': entries, 'attachments': audit['attachments'],
            'issues': audit['issues'], 'word_counts': audit['word_counts'], 'files': sorted(str(p.relative_to(folder)) for p in Path(folder).rglob('*') if p.is_file()), 'execution': [],
            'unmatched': student == 'UNMATCHED_REVIEW'}


def completion_status(row):
    statuses = [r['review'] for r in row['requirements']]
    if any(s == 'Incomplete' for s in statuses):
        return 'INCOMPLETE (reviewed)'
    if all(s in ('Complete', 'Not applicable') for s in statuses) and any(s == 'Complete' for s in statuses):
        return 'COMPLETE (reviewed)'
    return 'NEEDS REVIEW'


def stage_shared(shared, work):
    """Preserve submitted files, recording conflicts instead of overwriting them."""
    events = []
    if not shared:
        return events
    for path in sorted(Path(shared).rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(shared)
        target = work / relative
        if target.exists():
            events.append({'file': str(relative), 'action': 'kept student file; shared file not copied'})
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            events.append({'file': str(relative), 'action': 'copied instructor file'})
    return events


def stage_paths(work, mapping):
    """Rewrite exact Python string literals only in disposable scripts/notebook cells."""
    events = []
    if not mapping:
        return events
    for source_name, target_name in mapping.items():
        if not isinstance(source_name, str) or not isinstance(target_name, str):
            raise ValueError('Path mappings must contain string keys and values')
        target = (work / target_name).resolve()
        if not target.is_relative_to(work.resolve()) or not target.is_file():
            raise ValueError(f'Mapping destination must be an existing file inside the staged submission: {target_name}')

    def rewrite(source, filename, cell=None):
        tokens, changes = [], []
        try:
            for tok in tokenize.generate_tokens(io.StringIO(source).readline):
                if tok.type == tokenize.STRING:
                    try:
                        value = ast.literal_eval(tok.string)
                    except (ValueError, SyntaxError):
                        value = None
                    if isinstance(value, str) and value in mapping:
                        replacement = str((work / mapping[value]).resolve())
                        tok = tok._replace(string=repr(replacement))
                        event = {'file': filename, 'original': value, 'staged_destination': mapping[value]}
                        if cell is not None:
                            event['cell'] = cell
                        changes.append(event)
                tokens.append(tok)
        except (tokenize.TokenError, IndentationError):
            # An unrelated malformed source must not prevent the selected entrypoint's run.
            return source, []
        return (tokenize.untokenize(tokens) if changes else source), changes

    for script in sorted(work.rglob('*')):
        if script.suffix == '.py':
            source = read_source(script)
            replaced, changes = rewrite(source, str(script.relative_to(work)))
            if changes:
                script.write_text(replaced, encoding='utf-8')
                events.extend(changes)
        elif script.suffix == '.ipynb':
            try:
                notebook = json.loads(script.read_text(encoding='utf-8-sig'))
            except (ValueError, UnicodeError):
                continue
            changed = False
            for index, cell in enumerate(notebook.get('cells', [])):
                if cell.get('cell_type') != 'code':
                    continue
                source = ''.join(cell.get('source', []))
                replaced, changes = rewrite(source, str(script.relative_to(work)), index + 1)
                if changes:
                    cell['source'] = replaced.splitlines(keepends=True)
                    events.extend(changes); changed = True
            if changed:
                script.write_text(json.dumps(notebook), encoding='utf-8')
    return events


def classify_execution(returncode, log, timed_out=False):
    if timed_out:
        return 'TIMEOUT — INCONCLUSIVE'
    if returncode == 0:
        return 'RAN SUCCESSFULLY — REVIEW OUTPUTS'
    if any(s in log for s in ('ModuleNotFoundError', 'ImportError', '[E050]', 'Can\'t find model', 'Resource\x1b[0m', 'LookupError', 'CERTIFICATE_VERIFY_FAILED', 'ConnectionError', 'Connection refused', 'Network is unreachable')):
        return 'BLOCKED — DEPENDENCY / ENVIRONMENT'
    if any(s in log for s in ('FileNotFoundError', 'No such file or directory')):
        return 'BLOCKED — MISSING FILE / PATH'
    return 'EXECUTION FAILED — REVIEW LOG'


def run_entrypoint(folder, relative, timeout=120, shared=None, mapping=None, resources=None, mock=False, mock_fixtures=None, mock_words=18000):
    if not native_execution_available():
        raise RuntimeError('EXECUTION UNAVAILABLE IN BROWSER: use the native Python edition for actual student-code or mock execution. This is not a student-code failure.')
    import signal
    import subprocess
    if mock_fixtures and not mock:
        raise ValueError('Mock fixtures require explicit mock mode')
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='grader-run-') as tmp:
        work = Path(tmp) / 'submission'
        shutil.copytree(folder, work)
        script = (work / relative).resolve()
        if not script.is_relative_to(work.resolve()) or not script.is_file() or script.suffix not in ('.py', '.ipynb'):
            raise ValueError('Choose a Python script or Jupyter notebook within the submission')
        staged = stage_shared(resources, work)
        for event in staged:
            event['origin'] = 'student-specific resource bundle'
        common = stage_shared(shared, work)
        for event in common:
            event['origin'] = 'shared instructor files'
        staged.extend(common)
        changes = stage_paths(work, mapping or {})
        from prerequisites import audit_prerequisites, recovery_requirements
        prerequisites = audit_prerequisites(work)
        mock_report = {'manifest': [], 'unresolved': [], 'skipped': [], 'path_changes': []}
        if mock:
            from mock_inputs import prepare_mock_inputs
            mock_report = prepare_mock_inputs(work, mock_fixtures, mock_words)
        mock_used = bool(mock_report['manifest'])
        output = Path(tmp) / ('executed.ipynb' if script.suffix == '.ipynb' else 'executed.html')
        if script.suffix == '.ipynb':
            command = [sys.executable, str(Path(__file__).with_name('notebook_worker.py')), str(script), str(output), str(timeout)]
            mode = 'Jupyter execution'
        else:
            _, meta = source_blocks(read_source(script), relative)
            if meta['syntax'] != 'OK':
                return {'file': relative, 'status': 'SYNTAX ERROR', 'log': meta['syntax'], 'returncode': None, 'seconds': 0, 'staged_files': staged, 'path_changes': changes, 'mock_requested': mock, 'mock_used': mock_used, 'mocks': mock_report}
            mode = 'Native marimo HTML export' if meta['marimo'] else 'Python script'
            command = [sys.executable, '-m', 'marimo', 'export', 'html', '--no-sandbox', str(script), '-o', str(output)] if meta['marimo'] else [sys.executable, str(script)]
        # No account/API credentials are inherited by student programs. This is
        # hygiene, not a security sandbox; filesystem and network need external isolation.
        keep = ('PATH', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE')
        env = {k: os.environ[k] for k in keep if k in os.environ}
        home = Path(tmp) / 'home'; home.mkdir()
        env.update(HOME=str(home), XDG_CACHE_HOME=str(home / '.cache'), XDG_CONFIG_HOME=str(home / '.config'),
                   IPYTHONDIR=str(home / '.ipython'), JUPYTER_PLATFORM_DIRS='1',
                   MPLBACKEND='Agg', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1',
                   PYTHONPATH=os.pathsep.join(dict.fromkeys([str(script.parent), str(work)])))
        with tempfile.TemporaryFile() as handle:
            proc = subprocess.Popen(command, cwd=script.parent, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT,
                                    env=env, start_new_session=os.name == 'posix')
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == 'posix':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait()
            finally:
                # Clean up background descendants even when the parent exits early.
                if os.name == 'posix':
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            handle.seek(0, 2); size = handle.tell()
            handle.seek(max(0, size - MAX_TEXT))
            log = handle.read().decode('utf-8', errors='replace')
        underlying_status = classify_execution(proc.returncode, log, timed_out)
        status = underlying_status
        if mock_used:
            status = ('MOCK SMOKE TEST PASSED — ORIGINAL INPUTS UNVERIFIED' if proc.returncode == 0
                      else 'MOCK SMOKE TEST TIMEOUT — INCONCLUSIVE' if timed_out
                      else 'MOCK SMOKE TEST BLOCKED — SEE MISSING RESOURCES' if underlying_status.startswith('BLOCKED')
                      else 'MOCK SMOKE TEST FAILED — ORIGINAL INPUTS UNVERIFIED')
            log = 'MOCK SMOKE TEST: synthetic data or helper test doubles were substituted. Original-input behavior is NOT verified.\n' + log
        result = {'file': relative, 'mode': mode, 'status': status, 'underlying_status': underlying_status,
                  'test_scope': 'MOCK SMOKE TEST' if mock_used else 'PROVIDED INPUTS', 'mock_requested': mock, 'mock_used': mock_used, 'mocks': mock_report,
                  'returncode': proc.returncode, 'seconds': round(time.monotonic() - start, 2),
                  'log': log, 'log_truncated': size > MAX_TEXT, 'staged_files': staged, 'path_changes': changes,
                  'prerequisites': prerequisites, 'missing_resources': recovery_requirements(log) if proc.returncode != 0 else [],
                  'warnings': ['Exit zero does not prove every cell ran: inspect disabled cells, stop calls, caught exceptions and outputs.']}
        if output.exists() and output.stat().st_size > 30 * 1024**2:
            result['warnings'].append('Executed output exceeds the 30 MiB in-memory artifact limit and was not retained.')
        if output.exists() and output.stat().st_size <= 30 * 1024**2:
            result['artifact'] = output.read_bytes()
            result['artifact_name'] = output.name
            if mock_used:
                from mock_inputs import label_mock_artifact
                result['artifact'] = label_mock_artifact(result['artifact'], output.name)
                result['artifact_name'] = 'mock-' + output.name
        return result


def serializable_report(profile, rows):
    return {'schema_version': 1, 'assignment': profile, 'assignment_digest': profile_digest(profile),
            'policy': 'No accuracy grading. Static matches are evidence candidates; completion requires review. Execution is independent. Mock smoke-test success does not verify original-input behavior or completion.',
            'students': [{**row, 'completion': completion_status(row),
                          'execution': [{k: v for k, v in e.items() if k != 'artifact'} for e in row['execution']]} for row in rows]}
