"""Assignment 5 review and execution checker. Static review never executes submissions."""
import ast
import collections
import io
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tokenize
import zipfile

DATA_SUFFIXES = ('.json', '.jsonl', '.jsonl.gz', '.gz')
MAX_BYTES = 2 * 1024**3
MAX_MEMBERS = 10000


def read_source(path):
    with tokenize.open(path) as handle:
        return handle.read()


def safe_extract(archive, destination, budget=None, depth=0):
    """Bounded extraction; reject traversal, symlinks, duplicates and nested bombs."""
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
            if name.startswith('/') or '..' in parts or ':' in name or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f'Unsafe ZIP entry: {name}')
            target = destination.joinpath(*parts)
            if not target.is_relative_to(destination):
                raise ValueError(f'Unsafe ZIP entry: {name}')
            budget[0] += info.file_size
            budget[1] += 1
            if budget[0] > MAX_BYTES or budget[1] > MAX_MEMBERS:
                raise ValueError('Archive exceeds extraction limits (2 GiB / 10,000 entries)')
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open('xb') as dst:
                shutil.copyfileobj(src, dst)
    return budget


def group_submissions(archive, root):
    raw = root / 'canvas'
    budget = safe_extract(archive, raw)
    groups = {}
    pattern = re.compile(r'^(.+?)(?:_LATE)?_(\d+)_(\d+)_(.+)$')
    for source in sorted(raw.rglob('*')):
        if not source.is_file():
            continue
        m = pattern.match(source.name)
        if m:
            student, ident, _, original = m.groups()
            key = f'{student}_{ident}'
        else:
            key, original = 'UNMATCHED_REVIEW', source.name
        folder = groups.setdefault(key, root / 'students' / key)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / original
        if target.exists():
            target = folder / source.name
        if target.exists():
            raise ValueError(f'Duplicate attachment: {source.name}')
        shutil.move(str(source), target)
    for folder in groups.values():
        pending = [(p, 1) for p in folder.rglob('*.zip')]
        while pending:
            archive_path, depth = pending.pop()
            dest = archive_path.with_name(archive_path.name + '_unpacked')
            safe_extract(archive_path, dest, budget, depth)
            pending.extend((p, depth + 1) for p in dest.rglob('*.zip'))
    return groups


def inspect_source(source):
    result = {'syntax': 'OK', 'execution': 'NOT RUN', 'evidence': {}, 'data_literals': [], 'imports': [], 'markdown': []}
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        result['syntax'] = f'{type(exc).__name__}: line {exc.lineno}: {exc.msg}'
        return result
    aliases, imports = {}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split('.')[0]] = a.name
                imports.add(a.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or '').split('.')[0])
            for a in node.names:
                aliases[a.asname or a.name] = f'{node.module}.{a.name}'
    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + '.' + node.attr
        return ''
    def cue(key, node, detail=None):
        result['evidence'].setdefault(key, []).append({'line': node.lineno, 'text': detail or ast.get_source_segment(source, node)[:350]})
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = qualified(node.func)
            if name == 'marimo.App':
                cue('Marimo notebook', node)
            if name == 'pyrmallet.LatentDirichletAllocation':
                cue('pyrmallet LDA construction', node)
                for kw in node.keywords:
                    if kw.arg in ('n_components', 'num_topics', 'n_topics'):
                        cue('Topic count expressions', node, ast.unparse(kw.value))
            if name.endswith(('.fit', '.fit_transform')):
                cue('Fit calls (verify receiver)', node)
            if name.endswith('.md') and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    result['markdown'].append({'line': node.lineno, 'text': arg.value})
                elif isinstance(arg, ast.JoinedStr):
                    result['markdown'].append({'line': node.lineno, 'text': ''.join(v.value if isinstance(v, ast.Constant) else '{expression}' for v in arg.values)})
        if isinstance(node, ast.Attribute) and node.attr in ('components_', 'feature_names_in_', 'doc_topic_distributions_', 'transform'):
            cue('Topic analysis API cues', node)
        if isinstance(node, ast.Name) and re.search(r'stop.?words?|stoplist', node.id, re.I):
            if isinstance(node.ctx, ast.Store):
                cue('Stoplist definitions (verify iteration)', node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if '\n' not in value and value.lower().endswith(DATA_SUFFIXES) and value not in DATA_SUFFIXES:
                result['data_literals'].append(value)
    result['imports'] = sorted(imports - {''})
    result['data_literals'] = sorted(set(result['data_literals']))
    for section in result['markdown']:
        for key, pattern in [('Prediction prose cues', r'predict|expect|anticipat|hypothes'), ('Discussion prose cues', r'surpris|found|finding|help|hinder|compar'), ('Iteration prose cues', r'stop.?word|stoplist|number of topics|\bk\s*=|iterat')]:
            if re.search(pattern, section['text'], re.I):
                result['evidence'].setdefault(key, []).append({'line': section['line'], 'text': section['text'][:500]})
    return result


def audit_groups(groups):
    rows = []
    for student, folder in sorted(groups.items()):
        files = sorted(p for p in folder.rglob('*') if p.is_file())
        attachments = [str(p.relative_to(folder)) for p in files if p.suffix.lower() != '.py']
        scripts = [p for p in files if p.suffix.lower() == '.py']
        if not scripts:
            rows.append({'student': student, 'script': '', 'syntax': 'NO PYTHON FILE — REVIEW ATTACHMENTS', 'execution': 'NOT RUN', 'attachments': attachments, 'evidence': {}})
        for script in scripts:
            source = read_source(script)
            row = inspect_source(source)
            row.update(student=student, script=str(script.relative_to(folder)), attachments=attachments)
            rows.append(row)
    return rows


def stage_data(source, work, shared, mapping, auto_match):
    """Only rewrite exact string tokens in a disposable copy; log every change."""
    pool = collections.defaultdict(list)
    if shared:
        for p in Path(shared).rglob('*'):
            if p.is_file() and p.name.lower().endswith(DATA_SUFFIXES):
                pool[p.name.casefold()].append(p)
    changes, tokens = [], []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.STRING:
            try:
                value = ast.literal_eval(tok.string)
            except (ValueError, SyntaxError):
                value = None
            replacement = None
            if isinstance(value, str) and value in mapping:
                replacement = Path(mapping[value]).expanduser().resolve()
                if not replacement.is_file():
                    raise ValueError(f'Mapped file does not exist: {replacement}')
            elif isinstance(value, str) and auto_match and '\n' not in value and value.lower().endswith(DATA_SUFFIXES):
                candidates = pool.get(value.replace('\\', '/').rsplit('/', 1)[-1].casefold(), [])
                if len(candidates) == 1:
                    replacement = candidates[0]
                elif len(candidates) > 1:
                    raise ValueError(f'Ambiguous shared dataset: {value}. Supply an explicit mapping.')
            if replacement:
                dest = work / '_checker_data' / str(len(changes)) / replacement.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(replacement, dest)
                changes.append({'original': value, 'real_file': str(replacement), 'staged_file': str(dest)})
                tok = tok._replace(string=repr(str(dest)))
        tokens.append(tok)
    return tokenize.untokenize(tokens), changes


def diagnose(output):
    """Keep independent causes instead of allowing one error to hide others."""
    issues = []
    if 'sqlglot' in output and any(t in output for t in ('required to execute sql', 'No package metadata', 'ModuleNotFoundError')):
        issues.append('ENVIRONMENT: sqlglot is missing')
    elif any(t in output for t in ('ModuleNotFoundError', 'ImportError', 'ManyModulesNotFoundError', 'PackageNotFoundError', 'packages are required')):
        issues.append('ENVIRONMENT: missing or incompatible dependency')
    if 'Run in a sandboxed venv' in output:
        issues.append('CHECKER: waiting for dependency confirmation, not training')
    if any(t in output for t in ('FileNotFoundError', 'No such file or directory', 'Missing relative file(s)', 'not found.')):
        issues.append('DATA/PATH: a referenced file was not found')
    if 'MultipleDefinitionError' in output:
        issues.append('NOTEBOOK: duplicate variable definitions across cells')
    if "name 'BASE_STOP' is not defined" in output:
        issues.append('CODE: BASE_STOP is undefined')
    if "name '_preview' is not defined" in output:
        issues.append('CODE: _preview is undefined on the executed branch')
    if 'Total articles sampled for processing: 0' in output:
        issues.append('DATA: zero articles loaded; inspect missing inputs before judging the model')
    if "has no attribute 'components_'" in output:
        issues.append('MODEL: fitted components unavailable; check whether training received any data')
    return issues


def classify(returncode, output):
    if returncode == 0:
        return 'EXECUTED — REVIEW OUTPUTS'
    issues = diagnose(output)
    families = {i.split(':', 1)[0] for i in issues}
    if len(families) > 1:
        return 'MULTIPLE ISSUES — REVIEW DIAGNOSTICS'
    if 'CHECKER' in families:
        return 'BLOCKED — DEPENDENCY PROMPT'
    if 'ENVIRONMENT' in families:
        return 'ENVIRONMENT / DEPENDENCY ERROR'
    if 'DATA/PATH' in families:
        return 'BLOCKED — DATA OR PATH MISSING'
    return 'EXECUTION ERROR — REVIEW TRACEBACK'


def run_submission(folder, relative, timeout, shared='', mapping=None, auto_match=False):
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='a5-run-') as tmp:
        work = Path(tmp) / 'submission'
        shutil.copytree(folder, work)
        script = work / relative
        source = read_source(script)
        check = inspect_source(source)
        if check['syntax'] != 'OK':
            return {'execution': 'SYNTAX ERROR', 'log': check['syntax']}
        if 'Marimo notebook' not in check['evidence']:
            return {'execution': 'REVIEW — NOT A DETECTED MARIMO NOTEBOOK', 'log': 'Plain Python scripts are not automatically executed.'}
        source, changes = stage_data(source, work, shared, mapping or {}, auto_match)
        script.write_text(source, encoding='utf-8')
        output_html = Path(tmp) / 'executed.html'
        command = [sys.executable, '-m', 'marimo', 'export', 'html', '--no-sandbox', str(script), '-o', str(output_html)]
        env = os.environ.copy()
        env.update(MPLBACKEND='Agg', PYTHONUNBUFFERED='1')
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(command, cwd=script.parent, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=(os.name == 'posix'))
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
            log.seek(0, 2)
            size = log.tell()
            log.seek(max(0, size - 2_000_000))
            text = log.read().decode('utf-8', errors='replace')
        return {'execution': ('BLOCKED — DEPENDENCY PROMPT' if 'Run in a sandboxed venv' in text else 'TIMEOUT — INCONCLUSIVE') if timed_out else classify(proc.returncode, text),
                'diagnostics': diagnose(text),
                'returncode': proc.returncode, 'seconds': round(time.monotonic() - start, 2),
                'log': text, 'log_truncated': size > 2_000_000, 'path_changes': changes,
                'html': output_html.read_bytes() if output_html.exists() else None}


def main():
    import streamlit as st
    st.set_page_config(page_title='Assignment 5 Checker', layout='wide')
    st.title('Assignment 5: Topic Modeling — Grading Assistant')
    st.info('Missing original newspaper data is expected: the assignment explicitly says not to upload it. Static cues are review aids, not grades or proof that cells executed.')
    archive = st.file_uploader('Canvas submissions ZIP', type=['zip'])
    shared = st.text_input('Local folder containing instructor newspaper data (optional)')
    auto = st.checkbox('Match exact filenames in the shared folder and log path changes', value=False)
    mappings_text = st.text_area('Explicit data paths: JSON object mapping submitted string to real local file', '{}')
    timeout = st.number_input('Timeout per notebook (seconds)', 30, 14400, 1000)
    st.caption('Execution runs student code with your account permissions. Use a disposable grading environment. Temporary folders are not a security sandbox.')
    execute = st.checkbox('Execute selected notebooks with real data', value=False)
    chosen = st.text_input('Student IDs to execute (comma-separated); blank means all', disabled=not execute)
    if archive and st.button('Review Assignment 5 submissions', type='primary'):
        try:
            mapping = json.loads(mappings_text)
            if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
                raise ValueError('Path mappings must be a JSON object with string keys and values.')
            if shared and not Path(shared).is_dir():
                raise ValueError('Shared newspaper folder does not exist.')
            with tempfile.TemporaryDirectory(prefix='a5-review-') as tmp:
                if execute:
                    missing = [name for name in ('marimo', 'pyrmallet', 'duckdb', 'sqlglot', 'pyarrow') if importlib.util.find_spec(name) is None]
                    if missing:
                        raise ValueError('Grading environment is missing: ' + ', '.join(missing) + '. Run uv sync with the updated pyproject.toml, then restart the app.')
                groups = group_submissions(io.BytesIO(archive.getvalue()), Path(tmp))
                rows = audit_groups(groups)
                codes = {f'{r["student"]}/{r["script"]}': read_source(groups[r['student']] / r['script']) for r in rows if r['script']}
                progress = st.progress(0.0)
                status = st.empty()
                selection = {s.strip() for s in chosen.split(',') if s.strip()}
                unknown = selection - groups.keys()
                if unknown:
                    raise ValueError(f'Unknown student IDs: {sorted(unknown)}. Use IDs from the static review.')
                for i, row in enumerate(rows):
                    status.text(f'{i+1}/{len(rows)}: {row["student"]}')
                    if execute and row['script'] and (not selection or row['student'] in selection):
                        try:
                            row.update(run_submission(groups[row['student']], row['script'], int(timeout), shared, mapping, auto))
                        except Exception as exc:
                            row.update(execution='CHECKER ERROR', log=f'{type(exc).__name__}: {exc}')
                    progress.progress((i + 1) / len(rows))
                st.session_state['review'] = (rows, codes)
        except Exception as exc:
            st.error(f'{type(exc).__name__}: {exc}')
    if 'review' not in st.session_state:
        return
    rows, codes = st.session_state['review']
    st.dataframe([{'Student': r['student'], 'Script': r['script'], 'Syntax': r['syntax'], 'Execution': r['execution'], 'Diagnostics': '; '.join(r.get('diagnostics', [])), 'pyrmallet LDA cue': bool(r['evidence'].get('pyrmallet LDA construction')), 'Topic count expressions': ', '.join(e['text'] for e in r['evidence'].get('Topic count expressions', []))} for r in rows], hide_index=True)
    serializable = [{k: v for k, v in r.items() if k != 'html'} for r in rows]
    st.download_button('Download review JSON', json.dumps(serializable, indent=2), 'assignment5-review.json', 'application/json')
    if not rows:
        return
    idx = st.selectbox('Inspect submission', range(len(rows)), format_func=lambda i: f'{rows[i]["student"]} / {rows[i]["script"]}')
    row = rows[idx]
    st.write('Attachments:', row['attachments'])
    if row.get('diagnostics'):
        st.write('Execution diagnostics:', row['diagnostics'])
    st.write('Data path literals (may include outputs or unused code):', row.get('data_literals', []))
    st.json(row['evidence'], expanded=False)
    st.markdown('**Manual review:** newspaper title/location/date range; predictions made before inspection; pyrmallet training; topic keywords and highly weighted articles; experiments with topic counts and added stopwords; findings versus predictions, surprises, and how methods helped or hindered. Review saved outputs and any separate discussion attachments. SQL is an available tool, not a mandatory implementation.')
    with st.expander('Notebook prose'):
        for section in row.get('markdown', []):
            st.text(f'Line {section["line"]}\n{section["text"]}')
    with st.expander('Original submitted code'):
        st.code(codes.get(f'{row["student"]}/{row["script"]}', ''), language='python')
    if 'log' in row:
        st.code(row['log'], language='text')
        st.download_button('Download execution log', row['log'], f'{row["student"]}-execution.log')
    if row.get('path_changes'):
        st.write('Path changes in execution copy:', row['path_changes'])
    if row.get('html'):
        st.download_button('Download executed HTML (may contain errors)', row['html'], f'{row["student"]}-executed.html', 'text/html')


if __name__ == '__main__':
    main()
