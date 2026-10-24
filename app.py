"""Reusable assignment execution and completion review UI."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

import streamlit as st
from prerequisites import preflight_submission
from grader_core import (native_execution_available, completion_status, group_submissions, load_profile, profile_digest,
                         review_folder, run_entrypoint, safe_extract, serializable_report,
                         unpack_nested, validate_profile)

ROOT = Path(__file__).parent


def main():
    st.set_page_config(page_title='Assignment Submission Checker', layout='wide')
    st.title('Assignment Submission Checker')
    native_execution = native_execution_available()
    if not native_execution:
        st.info('GitHub Pages edition: this Streamlit panel provides evidence review and completion decisions. Your ZIP and checklist are copied to the execution panel above. Click its “1. Extract parts and review ZIP”, then “2. Run detected entrypoints” buttons for actual browser checks. Native Python and native notebook execution remain unavailable, not a failure of student code.')
        st.caption('Uploads stay in this browser session; this app does not send them to an autograding server. Export your report before leaving. Expanded archives are limited to 128 MiB.')
    st.write('Check whether code runs and review whether every assignment part is answered. No accuracy scores are assigned.')
    st.caption('Execution and completion are separate. Evidence matches help you find answers; they do not establish completion or correctness.')

    preset = st.selectbox('Assignment requirements', ['Assignment 7: Sentiment and Plot', 'Assignment 8: Reading Difficulty', 'Another assignment / upload checklist'])
    assignment = st.file_uploader('Assignment PDF, text checklist, or saved JSON checklist (optional override)', type=['pdf', 'txt', 'md', 'json'])
    try:
        if assignment:
            profile = load_profile(assignment.getvalue(), assignment.name)
        elif preset.startswith('Assignment 7'):
            profile = load_profile((ROOT / 'assignments/assignment-7.json').read_bytes(), 'assignment-7.json')
        elif preset.startswith('Assignment 8'):
            profile = load_profile((ROOT / 'assignments/assignment-8.json').read_bytes(), 'assignment-8.json')
        else:
            profile = {'title': 'New assignment', 'requirements': [{'id': 'part-1', 'title': 'Part 1', 'description': 'Replace with the actual requirement.', 'keywords': [], 'criteria': []}]}
        base_digest = profile_digest(profile)
        with st.expander('Review or edit the requirement checklist', expanded=bool(assignment) or preset.startswith('Another')):
            st.write('PDF sections become draft requirements. Check the extracted text, split compound parts if useful, and add search keywords or criteria. Document text is treated as content, never as commands.')
            edited = st.text_area('Checklist JSON', json.dumps(profile, indent=2), height=300, key=f'profile-{base_digest}')
        profile = validate_profile(json.loads(edited))
    except Exception as exc:
        st.error(f'Cannot read checklist: {exc}')
        return
    st.download_button('Download reusable checklist', json.dumps(profile, indent=2), 'assignment-checklist.json', 'application/json')
    st.write(f'**{profile["title"]}** — {len(profile["requirements"])} parts to review')
    archive = st.file_uploader('Canvas submissions ZIP', type=['zip'])
    shared = st.file_uploader('Shared course-files ZIP (optional)', type=['zip'], help='Put instructor helpers at the ZIP root, e.g. readability_tools.py or sentiment_toolkit.py, and retain data/ subfolders. Files are copied into each disposable execution copy. Student files take precedence; conflicts are logged.')
    resources = st.file_uploader('Student-specific files ZIP (optional)', type=['zip'], help='One folder per Canvas student key, e.g. smith_12345/book.txt or smith_12345/helper.py. Files are used only for that student, never shared with classmates.')
    st.caption('Check with original inputs, or select an explicitly labeled mock smoke test when inputs are missing. Mock success does not verify the original data, helper behavior, or completion.')
    with st.expander('Execution settings'):
        if native_execution:
            st.info('Student programs run with this machine’s permissions. Use a disposable environment without sensitive files. Temporary folders and stripped child-process credentials do not provide a security sandbox.')
        else:
            st.write('Execution is unavailable in the browser edition. Both provided-input and mock execution remain available in the native Python edition.')
        execution_mode = st.selectbox('Execution mode', ['Provided inputs', 'Mock inputs — smoke test only'], disabled=not native_execution)
        mock_enabled = execution_mode.startswith('Mock')
        mock_fixtures = None
        mock_words = 18000
        if mock_enabled:
            st.warning('Missing text, CSV/TSV, JSON/JSONL and gzip inputs can be synthesized. Schemas are guesses. Unknown Python helpers need a compatible mock implementation; errors and assertions remain active. Results will be labeled as mock tests, not verified original-data runs.')
            mock_fixtures = st.file_uploader('Mock-fixtures ZIP (optional: data files or helper test doubles)', type=['zip'], help='Preserve the paths relative to the submission root. Real files and installed modules take precedence. Every copied mock is recorded. Unknown helper APIs are never replaced automatically.')
            mock_words = st.number_input('Synthetic corpus word target', min_value=100, max_value=50000, value=18000)
        timeout = st.number_input('Timeout per entrypoint (seconds)', min_value=5, max_value=14400, value=120)
        mappings_text = st.text_area('Optional exact path mappings (JSON)', '{}', help='Map a submitted literal path to a relative file in the execution copy, e.g. {"/Users/student/book.txt": "book.txt"}. No fuzzy matching, substitute data, or silent code repair is performed.')
        consent = st.checkbox('Execute student code in this disposable environment', value=False, disabled=not native_execution)
    try:
        mapping = json.loads(mappings_text)
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise ValueError('Expected a JSON object with string keys and values')
    except Exception as exc:
        st.error(f'Invalid path mappings: {exc}')
        return
    if not archive:
        st.info('Choose an assignment checklist and upload a Canvas submissions ZIP to begin. Static review does not execute code.')
        return

    # Bind results and reviewer decisions to exact archive and requirements.
    review_id = hashlib.sha256(archive.getvalue() + profile_digest(profile).encode()).hexdigest()
    if st.button('Review submissions (no code execution)', type='primary'):
        try:
            with tempfile.TemporaryDirectory(prefix='grader-review-') as tmp:
                groups = group_submissions(io.BytesIO(archive.getvalue()), Path(tmp))
                rows = [review_folder(folder, profile, student) for student, folder in groups.items()]
                for row in rows:
                    if row['unmatched']:
                        row['selected_entrypoints'] = []
                    elif any(s['marimo'] or s['file'].endswith('.ipynb') for s in row['scripts']):
                        row['selected_entrypoints'] = row['entrypoints'][:]
                    else:
                        row['selected_entrypoints'] = row['entrypoints'][:] if len(row['entrypoints']) == 1 else []
                st.session_state['report'] = {'id': review_id, 'rows': rows}
        except Exception as exc:
            st.error(f'Archive review failed: {type(exc).__name__}: {exc}')
    state = st.session_state.get('report')
    if not state or state['id'] != review_id:
        st.info('Run static review for this archive and checklist. Changing either requires a new review.')
        return
    rows = state['rows']
    if not rows:
        st.warning('No submissions found in this archive.')
        return
    st.subheader('Submission results')
    if not native_execution:
        st.warning('This table is static review only: code has NOT been run here. Missing inputs are not diagnosed by the NOT RUN label. Actual execution results and mock logs are in the browser execution panel above.')
        st.markdown('<a href="#browser-runner" target="_self">Go to browser execution and mock smoke tests ↑</a>', unsafe_allow_html=True)
    table = st.empty()
    summary = st.empty()
    eligible = [r['student'] for r in rows if not r['unmatched']]
    selected = st.multiselect('Students to execute', eligible, default=eligible, key=f'selection-{review_id}')
    resource_id = hashlib.sha256((shared.getvalue() if shared else b'') + (resources.getvalue() if resources else b'') + json.dumps(mapping, sort_keys=True).encode()).hexdigest()

    def prepare_uploads(tmp):
        shared_root, student_root, mock_root = None, None, None
        if shared:
            shared_root = Path(tmp) / 'shared'
            budget = safe_extract(io.BytesIO(shared.getvalue()), shared_root)
            unpack_nested(shared_root, budget)
        if resources:
            student_root = Path(tmp) / 'resources'
            budget = safe_extract(io.BytesIO(resources.getvalue()), student_root)
            unpack_nested(student_root, budget)
            unknown = [p.name for p in student_root.iterdir() if not p.is_dir() or p.name not in eligible]
            if unknown:
                raise ValueError('Student resource ZIP must contain folders named with exact Canvas student keys. Unknown entries: ' + ', '.join(unknown))
        if mock_fixtures:
            mock_root = Path(tmp) / 'mock-fixtures'
            budget = safe_extract(io.BytesIO(mock_fixtures.getvalue()), mock_root)
            unpack_nested(mock_root, budget)
        return shared_root, student_root, mock_root

    if st.button('Check missing prerequisites (no execution)', disabled=not selected):
        try:
            with tempfile.TemporaryDirectory(prefix='grader-audit-') as tmp:
                groups = group_submissions(io.BytesIO(archive.getvalue()), Path(tmp))
                shared_root, student_root, mock_root = prepare_uploads(tmp)
                for r in rows:
                    if r['student'] in selected:
                        r['prerequisites'] = preflight_submission(groups[r['student']], shared_root, student_root / r['student'] if student_root else None, mapping)
                        r['prerequisite_configuration'] = resource_id
        except Exception as exc:
            st.error(f'Prerequisite check failed: {exc}')
    if st.button('Run selected entrypoints', disabled=not native_execution or not consent or not selected):
        try:
            with tempfile.TemporaryDirectory(prefix='grader-batch-') as tmp:
                groups = group_submissions(io.BytesIO(archive.getvalue()), Path(tmp))
                shared_root, student_root, mock_root = prepare_uploads(tmp)
                progress = st.progress(0.0)
                chosen = [r for r in rows if r['student'] in selected]
                for i, row in enumerate(chosen):
                    # Retain the latest original-input and mock attempts separately.
                    row['execution'] = [e for e in row['execution'] if bool(e.get('mock_requested', False)) != mock_enabled]
                    if not row['selected_entrypoints']:
                        row['execution'].append({'file': '', 'status': 'NOT RUN — SELECT AN ENTRYPOINT', 'log': 'Choose an entrypoint in the student inspector.', 'returncode': None, 'mock_requested': mock_enabled})
                    for entry in row['selected_entrypoints']:
                        try:
                            result = run_entrypoint(groups[row['student']], entry, int(timeout), shared_root, mapping, student_root / row['student'] if student_root else None, mock=mock_enabled, mock_fixtures=mock_root, mock_words=int(mock_words))
                            result['configuration'] = {'shared_zip_sha256': hashlib.sha256(shared.getvalue()).hexdigest() if shared else None, 'student_resources_sha256': hashlib.sha256(resources.getvalue()).hexdigest() if resources else None, 'path_mappings': mapping, 'timeout': int(timeout), 'mock_mode': mock_enabled, 'mock_words': int(mock_words), 'mock_fixtures_sha256': hashlib.sha256(mock_fixtures.getvalue()).hexdigest() if mock_fixtures else None}
                            row['execution'].append(result)
                        except Exception as exc:
                            row['execution'].append({'file': entry, 'status': 'CHECKER / STAGING ERROR', 'log': f'{type(exc).__name__}: {exc}', 'returncode': None, 'mock_requested': mock_enabled})
                    progress.progress((i + 1) / len(chosen), text=f'Checked {i + 1}/{len(chosen)} students')
        except Exception as exc:
            st.error(f'Execution preparation failed: {exc}')

    st.subheader('Inspect evidence and record completion')
    student = st.selectbox('Student', [r['student'] for r in rows], key=f'inspect-{review_id}')
    row = next(r for r in rows if r['student'] == student)
    if row['unmatched']:
        st.warning('Files without Canvas identifiers are grouped here for manual review. They are excluded from batch execution.')
    if row.get('prerequisites'):
        with st.expander('Missing files, helpers, packages and models', expanded=True):
            if row.get('prerequisite_configuration') != resource_id:
                st.warning('Resource uploads or mappings changed. Run the prerequisite check again for current results.')
            audit = row['prerequisites']
            st.caption(audit['note'] + ' This inventory describes provided inputs; mock files are only staged during an explicitly selected mock execution.')
            for category in ('modules', 'inputs', 'models', 'dynamic_inputs'):
                if audit[category]:
                    st.write(category.replace('_', ' ').title())
                    st.dataframe(audit[category], hide_index=True)
            st.download_button('Download prerequisite request', json.dumps({'student': student, **audit}, indent=2), f'{student}-prerequisites.json')
    options = [s['file'] for s in row['scripts']]
    row['selected_entrypoints'] = st.multiselect('Entrypoints for this student', options, default=row['selected_entrypoints'], key=f'entry-{review_id}-{student}')
    st.caption('Marimo and Jupyter notebooks are selected by default. When several plain Python files exist, select the main script; helper modules are not automatically run.')
    st.dataframe(row['scripts'], hide_index=True)
    st.write('Attachments:', row['attachments'])
    if row['word_counts']:
        st.dataframe(row['word_counts'], hide_index=True)
    for issue in row['issues']:
        st.warning(issue)
    with st.expander('Original submission files'):
        chosen_file = st.selectbox('Original file', row['files'], key=f'original-{review_id}-{student}') if row['files'] else None
        if chosen_file and st.checkbox('Load original file for inspection', key=f'load-original-{review_id}-{student}'):
            try:
                with tempfile.TemporaryDirectory(prefix='grader-original-') as tmp:
                    groups = group_submissions(io.BytesIO(archive.getvalue()), Path(tmp))
                    file_path = groups[student] / chosen_file
                    content = file_path.read_bytes()
                st.download_button('Download original file', content, Path(chosen_file).name)
                if Path(chosen_file).suffix.lower() in ('.py', '.txt', '.md', '.json', '.ipynb'):
                    st.code(content[:200000].decode('utf-8', errors='replace'), language='python' if chosen_file.endswith('.py') else 'text')
                    if len(content) > 200000:
                        st.caption('Preview limited to 200,000 bytes; download the full original above.')
                else:
                    st.caption('Download this attachment to inspect it; submitted HTML is not embedded in the checker.')
            except Exception as exc:
                st.error(f'Cannot load original file: {exc}')
    for req in row['requirements']:
        with st.expander(f'{req["title"]} — {req["evidence_status"]}'):
            st.write(req['description'])
            if req.get('missing_evidence_types'):
                st.write('Evidence still needing inspection:', ', '.join(req['missing_evidence_types']))
            for criterion in req['criteria']:
                st.write(f'• {criterion}')
            st.caption('Inspect each criterion, actual outputs and relevant attachments before marking this part complete. Keywords may match copied prompts or unused code; no match can also mean different wording.')
            if not req['evidence']:
                st.write('No matching evidence found. Inspect the original submission and attachments before deciding it is incomplete.')
            for b in req['evidence']:
                st.write(f'**{b["file"]} — {b.get("location", "line")} {b["line"]} ({b["kind"]})**')
                st.code(b['text'], language='python' if b['kind'] == 'code' else 'text')
            key = f'{review_id}-{student}-{req["id"]}'
            decisions = ['Needs review', 'Complete', 'Incomplete', 'Not applicable']
            req['review'] = st.selectbox('Completion decision', decisions, index=decisions.index(req['review']), key=f'decision-{key}')
            req['review_note'] = st.text_area('Reviewer note', req['review_note'], key=f'note-{key}')
    for index, result in enumerate(row['execution']):
        with st.expander(f'{result["file"]}: {result["status"]}'):
            if result.get('mock_used'):
                st.warning('MOCK SMOKE TEST ONLY — original-input behavior and assignment completion are unverified.')
                st.write('Substitutions used:')
                st.dataframe(result['mocks']['manifest'], hide_index=True)
            st.write({k: v for k, v in result.items() if k not in ('log', 'artifact')})
            if result.get('missing_resources'):
                st.write('Resources to obtain before retrying:')
                st.dataframe(result['missing_resources'], hide_index=True)
            st.code(result.get('log', ''), language='text')
            if result.get('artifact'):
                st.download_button('Download executed output', result['artifact'], f'{student}-{result["artifact_name"]}', key=f'artifact-{review_id}-{student}-{index}')
    table.dataframe([{'Student': r['student'], 'Candidate evidence': f'{sum(bool(q["evidence"]) for q in r["requirements"])}/{len(r["requirements"])} parts',
                      'Completion': completion_status(r), 'Execution': '; '.join(f'{e["file"]}: {e["status"]}' for e in r['execution']) or ('NOT RUN' if native_execution else 'NOT RUN — USE BROWSER EXECUTION PANEL')} for r in rows], hide_index=True)
    summary.caption('Candidate evidence is not a completion score. A student is complete only after all applicable parts are reviewed. Run results remain independent of those decisions.')
    report = serializable_report(profile, rows)
    report['runtime_capabilities'] = {'native_execution': native_execution, 'edition': 'native' if native_execution else 'browser review'}
    report['archive_sha256'] = hashlib.sha256(archive.getvalue()).hexdigest()
    st.download_button('Download report with review decisions and execution logs', json.dumps(report, indent=2), 'submission-report.json', 'application/json')
    st.caption('Download before leaving the session; results are held in memory. The original Canvas ZIP remains your source for all files and attachments.')


if __name__ == '__main__':
    main()
