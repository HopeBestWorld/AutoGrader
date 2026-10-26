"""Reusable assignment execution and completion review UI."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import zipfile

import streamlit as st
from execution_summary import summarize_student, summarize_batch, summarize_attempt, retryable_browser_result
from report_exports import handoff_files, handoff_zip, summarize_parts
from execution_diagnostics import recovery_rows
from native_followup import followup_plan, make_colab_notebook
from browser_resources import resource_hashes
from prerequisites import preflight_submission
from grader_core import (normalize_execution_results, native_execution_available, group_submissions, load_profile, profile_digest,
                         review_folder, run_entrypoint, safe_extract, serializable_report,
                         unpack_nested, validate_profile)

ROOT = Path(__file__).parent


def main():
    st.set_page_config(page_title='Assignment Submission Checker', layout='wide')
    st.title('Assignment Submission Checker')
    native_execution = native_execution_available()
    if not native_execution:
        st.info('GitHub Pages edition: Upload the ZIP and instructions, then click Run submissions. The Runs? column reports whether the tested path finished. Unknown means the browser or inputs prevented a determination; it is not a student-code failure.')
        st.caption('Uploads stay in this browser session; this app does not send them to an autograding server. Export your report before leaving. Expanded archives are limited to 128 MiB.')
    st.write('Check whether code runs and review whether every assignment part is answered. No accuracy scores are assigned.')
    st.caption('Execution and completion are separate. Evidence matches help you find answers; they do not establish completion or correctness.')
    st.caption('Documents bundled with each student’s submission are used first. Reports list supplied files and any test substitutes separately.')

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
    if native_execution:
        st.caption('Check with original inputs, or select an explicitly labeled mock smoke test when inputs are missing. Mock success does not verify the original data, helper behavior, or completion.')
    browser_result_upload = None
    native_result_upload = None
    if not native_execution:
        with st.expander('Advanced: browser execution report import'):
            browser_result_upload = st.file_uploader('Browser execution results (automatic)', type=['json'])
        with st.expander('Optional: import a native-runtime report'):
            native_result_upload = st.file_uploader('Import native follow-up results (optional)', type=['json'],help='Extract submission-report.json from the native follow-up TA ZIP. The submissions and checklist must match this session.')
    mock_enabled, mock_fixtures, mock_words = False, None, 18000
    timeout, mappings_text, consent = 120, '{}', False
    if native_execution:
        with st.expander('Execution settings'):
            st.info('Student programs run with this machine’s permissions. Use a disposable environment without sensitive files. Temporary folders and stripped child-process credentials do not provide a security sandbox.')
            execution_mode = st.selectbox('Execution mode', ['Provided inputs', 'Mock inputs — smoke test only'])
            mock_enabled = execution_mode.startswith('Mock')
            if mock_enabled:
                st.warning('Missing text, CSV/TSV, JSON/JSONL and gzip inputs can be synthesized. Schemas are guesses. Unknown Python helpers need a compatible mock implementation; errors and assertions remain active. Results will be labeled as mock tests, not verified original-data runs.')
                mock_fixtures = st.file_uploader('Mock-fixtures ZIP (optional: data files or helper test doubles)', type=['zip'], help='Preserve the paths relative to the submission root. Real files and installed modules take precedence. Every copied mock is recorded. Unknown helper APIs are never replaced automatically.')
                mock_words = st.number_input('Synthetic corpus word target', min_value=100, max_value=200000, value=18000)
            timeout = st.number_input('Timeout per entrypoint (seconds)', min_value=5, max_value=14400, value=120)
            mappings_text = st.text_area('Optional exact path mappings (JSON)', '{}', help='Map a submitted literal path to a relative file in the execution copy, e.g. {"/Users/student/book.txt": "book.txt"}. No fuzzy matching, substitute data, or silent code repair is performed.')
            consent = st.checkbox('Execute student code in this disposable environment', value=False)
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

    browser_run_requested = st.button('Run submissions', type='primary') if not native_execution else False
    if not native_execution:
        st.caption('Uses supplied files first, fills supported missing inputs with mocks, and applies reviewed browser setup adaptations when needed. Results identify adapted setup and unverified original behavior. No mode selection is needed.')
        st.markdown('<div id="submission-run-progress" role="status" aria-live="polite"></div>',unsafe_allow_html=True)
    if browser_run_requested:
        st.info('Browser execution started. Results will return to this table automatically. Open browser execution diagnostics below for progress.')

    # Bind results and reviewer decisions to exact archive and requirements.
    review_id = hashlib.sha256(archive.getvalue() + profile_digest(profile).encode()).hexdigest()
    if st.button('Review submissions (no code execution)', type='primary') or browser_run_requested:
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
    browser_resources=resource_hashes(shared.getvalue() if shared else None,resources.getvalue() if resources else None)
    if state.get('execution_source')=='browser' and state.get('browser_resources',resource_hashes())!=browser_resources:
        for current in rows:
            current.setdefault('execution_history',[]).extend(current['execution'])
            current['execution']=[]
        state['execution_source']='browser inputs changed'
        state.pop('applied_browser_digest',None)
    browser_results_applied = False
    native_results_applied = False
    browser_result_digest = None
    if browser_result_upload:
        try:
            incoming = json.loads(browser_result_upload.getvalue())
            if not isinstance(incoming,dict):raise ValueError('Expected a report JSON object.')
            expected_hash = hashlib.sha256(archive.getvalue()).hexdigest()
            if incoming.get('archive_sha256') != expected_hash or incoming.get('assignment_digest') != profile_digest(profile):
                st.warning('Browser results belong to different submissions or checklist; run the current inputs again.')
            elif incoming.get('browser_resources',resource_hashes())!=browser_resources:
                st.warning('Supporting ZIPs changed. Run submissions again to test the current course/student files; earlier results are retained in history.')
            else:
                lookup = {r['student']:r for r in incoming.get('students',[])}
                browser_result_digest = hashlib.sha256(browser_result_upload.getvalue()).hexdigest()
                if browser_result_digest!=state.get('applied_browser_digest'):
                    normalized={key:normalize_execution_results(value.get('execution',[])) for key,value in lookup.items()}
                    for current in rows:
                        if current['student'] in normalized:
                            current['execution']=normalized[current['student']]
                            current['resource_staging']=lookup[current['student']].get('resource_staging',[])
                    state['applied_browser_digest']=browser_result_digest
                    state.pop('native_followup',None)
                    state['execution_source']='browser'
                    state['browser_resources']=browser_resources
        except (ValueError,TypeError,KeyError) as exc:
            st.warning(f'Cannot read browser execution results: {exc}')

    if native_result_upload and st.button('Apply native follow-up results'):
        try:
            incoming=json.loads(native_result_upload.getvalue())
            if not isinstance(incoming,dict):raise ValueError('Expected a native report JSON object.')
            if incoming.get('archive_sha256')!=hashlib.sha256(archive.getvalue()).hexdigest() or incoming.get('assignment_digest')!=profile_digest(profile):
                raise ValueError('The native results belong to different submissions or a different checklist.')
            if not incoming.get('native_followup'):
                raise ValueError('Choose submission-report.json from a native follow-up TA ZIP.')
            lookup={row['student']:row for row in incoming.get('students',[])}
            # Validate the whole import before mutating any current rows.
            normalized={key:normalize_execution_results(row.get('execution',[])) for key,row in lookup.items()}
            histories={key:normalize_execution_results(row.get('execution_history',[])) for key,row in lookup.items()}
            for current in rows:
                if current['student'] in lookup:
                    current['execution']=normalized[current['student']]
                    current['execution_history']=histories[current['student']]
            native_results_applied=True
            state['native_followup']=incoming['native_followup']
            state['execution_source']='native follow-up'
        except (ValueError,TypeError,KeyError) as exc:
            st.warning(f'Cannot import native follow-up results: {exc}')
    native_results_applied=state.get('execution_source')=='native follow-up'
    browser_results_applied=state.get('execution_source')=='browser' and any(r['execution'] for r in rows)

    if not rows:
        st.warning('No submissions found in this archive.')
        return
    st.subheader('Submission results')
    if not native_execution and any(e.get('file') and retryable_browser_result(e) for row in rows for e in row['execution']):
        st.button('Retry interrupted runs')
        st.caption('Retries browser timeouts and runtime interruptions. Completed runs and code-error results are kept.')
    if native_results_applied:
        st.info('Native follow-up results are imported. Earlier browser attempts are retained in the execution history; completion decisions in this session are preserved.')
    elif browser_results_applied:
        st.info('Browser execution results are displayed below. These are browser/mock checks, not native marimo validation.')
    elif not native_execution:
        st.warning('Code has not been run for this review yet. Click Run submissions to test supported entrypoints; open browser diagnostics below for progress.')
        st.markdown('<a href="#browser-runner" target="_self">Go to browser execution diagnostics ↓</a>', unsafe_allow_html=True)
    counts_placeholder = st.empty()
    run_filter = st.selectbox('Show run outcomes', ['All students','Ran successfully','Run hit an error','Cannot determine','Not tested'])
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
    st.write('**Part-by-part review checklist**')
    part_table = st.empty()
    st.caption('Check flagged gaps first. Complete and Incomplete are reviewer decisions; automatic evidence matches still need confirmation. Update decisions below; exports include your latest decisions.')
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
    part_review = summarize_parts(row)
    part_table.dataframe([{'Assignment part': p['title'], 'Status': p['status'], 'What to check': p['next_step'],
                          'Evidence location': '; '.join(p['evidence_locations']) or 'Inspect originals', 'Reviewer note': p['reviewer_note']}
                         for p in part_review['parts']], hide_index=True)
    for index, result in enumerate(row['execution']):
        with st.expander(f'{result.get("file") or "(no entrypoint)"}: {result.get("status", "RESULT INCOMPLETE")}'):
            if result.get('mock_used'):
                st.warning('MOCK SMOKE TEST ONLY — original-input behavior and assignment completion are unverified.')
                st.write('Substitutions used:')
                st.dataframe(result['mocks']['manifest'], hide_index=True)
            outcome = summarize_attempt(result)
            st.write('Run result:', outcome['answer'])
            st.write('Execution runtime:',outcome['runtime'])
            st.write('Setup scope:',outcome['setup_scope'])
            if result.get('original_input_attempt'):
                with st.expander('Supplied-input attempt before synthetic URL fallback'):
                    previous=result['original_input_attempt']
                    st.write('The supplied document was unchanged. This earlier attempt did not finish; the current synthetic-input test has a different scope.')
                    st.write(previous.get('status',''))
                    st.code(previous.get('log',''),language='text')
            st.write('Supplied files referenced:', ', '.join(outcome['supplied_files']) or 'None matched by the input inventory')
            st.write('Mocks substituted:', '; '.join(outcome['mock_files']) or 'None')
            st.caption('This is a file-reference inventory; a match alone does not prove that every conditional code path read that file.')
            st.write('Reason:', outcome['reason'])
            st.write('Next step:', outcome['next_step'])
            with st.expander('Technical result details'):
                st.write({k: v for k, v in result.items() if k not in ('log', 'artifact','original_input_attempt')})
            if result.get('missing_resources'):
                st.write('Resources to obtain before retrying:')
                st.dataframe(result['missing_resources'], hide_index=True)
            st.code(result.get('log', ''), language='text')
            if result.get('artifact'):
                st.download_button('Download executed output', result['artifact'], f'{student}-{result["artifact_name"]}', key=f'artifact-{review_id}-{student}-{index}')
    if row.get('execution_history'):
        with st.expander('Earlier execution attempts (retained history)'):
            st.caption('These attempts are retained for comparison. The table summarizes the current active results above.')
            for previous in row['execution_history']:
                st.write(previous.get('file',''),previous.get('status',''))
                st.code(previous.get('log',''),language='text')
    display_rows=[]
    categories={'Ran successfully':'ran','Run hit an error':'test_error','Cannot determine':'unknown','Not tested':'not_run'}
    for current in rows:
        outcome=summarize_student(current)
        if run_filter!='All students' and outcome['category']!=categories[run_filter]: continue
        parts = summarize_parts(current)
        display_rows.append({'Student':current['student'],'Runs?':outcome['answer'],'Runtime':outcome['runtime'],'Setup scope':outcome['setup_scope'],'Test inputs':outcome['inputs'],'Reason':outcome['reason'],'Next step':outcome['next_step'],
                             'Supplied files referenced':'; '.join(outcome['supplied_files']) or 'None matched', 'Mocks substituted':'; '.join(outcome['mock_files']) or 'None',
                             'Completion':parts['status'], 'Parts marked complete':'; '.join(parts['reviewed_complete']) or 'None marked',
                             'Parts marked incomplete':'; '.join(parts['reviewed_incomplete']) or 'None marked',
                             'Check first':'; '.join(parts['check_first']) or 'No flagged gaps', 'Parts awaiting review':len(parts['awaiting_review'])})
    table.dataframe(display_rows,hide_index=True)
    counts=summarize_batch(rows)
    counts_placeholder.info(f"{counts['ran']} finished {'execution' if native_execution or native_results_applied else 'browser tests'} · {counts['test_error']} hit a test error · {counts['unknown']} cannot be determined · {counts['not_run']} not tested. Success applies only to the tested inputs/runtime; adapted setup and synthetic inputs remain unverified.")
    summary.caption('Candidate evidence is not a completion score. A student is complete only after all applicable parts are reviewed. Run results remain independent of those decisions.')
    report = serializable_report(profile, rows)
    report['runtime_capabilities'] = {'native_execution': native_execution, 'edition': 'native' if native_execution else 'browser execution subset' if browser_results_applied else 'browser review', 'browser_execution':browser_results_applied}
    report['archive_sha256'] = hashlib.sha256(archive.getvalue()).hexdigest()
    if browser_results_applied:report['browser_resources']=browser_resources
    if native_results_applied:
        report['native_followup']=state['native_followup']
        report['runtime_capabilities']={'edition':'browser results with native follow-up','native_followup':True}
    recovery=recovery_rows(report)
    if recovery:
        st.subheader('How to check the remaining submissions')
        st.dataframe(recovery,hide_index=True)
        st.caption('Source diagnostics are static hints. An unresolved generated argument may be unused on the tested path; a name that is actually read must be defined. Native follow-up checks the original code with real Python. No values or successful results are invented.')
        try:
            native_plan=followup_plan(report)
        except ValueError as exc:
            native_plan={};st.warning(f'Cannot prepare native follow-up: {exc}')
        if native_plan:
            with st.expander('Optional: native Python validation with Google Colab'):
                st.write('Download the notebook, open Google Colab → File → Upload notebook, and follow its four cells. Upload the original Canvas ZIP there, review the settings, then enable execution. Free CPU runtimes require Google sign-in and have availability limits. No paid AI or GPU is needed.')
                st.caption('The notebook includes your current report and checker source. The app sends nothing to Google automatically. Keep the notebook with the grading team and use a fresh Colab runtime without mounted Drive files or secrets.')
                st.download_button('Download native follow-up notebook',make_colab_notebook(report),'AutoGrader-native-followup.ipynb','application/x-ipynb+json')
                st.link_button('Open Google Colab','https://colab.research.google.com/')
    st.subheader('Download and share with another TA')
    st.write('Share one HTML report. The TA can open it in any browser to see a class grid of run results and every assignment part, then click a badge for evidence and notes. It works offline and includes a compact CSV download.')
    st.caption('Report only: original submissions are not embedded, keeping the file smaller. Send the original Canvas ZIP alongside it when full source is needed. Downloads include ALL students and the latest review decisions, regardless of the filter above.')
    files = handoff_files(report)
    st.download_button('Download shareable TA report (HTML)',files['TA-report.html'],'TA-report.html','text/html',type='primary')
    st.download_button('Download combined results CSV',files['class-results.csv'],'TA-class-results.csv','text/csv')
    with st.expander('More export formats and full execution records'):
        st.download_button('Download TA handoff ZIP', handoff_zip(files), 'TA-handoff.zip', 'application/zip')
        st.download_button('Download plain-language results CSV', files['student-overview.csv'], 'student-overview.csv', 'text/csv')
        st.download_button('Download part-by-part checklist CSV', files['part-checklist.csv'], 'part-checklist.csv', 'text/csv')
        st.download_button('Download report with review decisions and execution logs', files['submission-report.json'], 'submission-report.json', 'application/json')
    st.caption('Download before leaving the session; results are held in memory. The original Canvas ZIP remains your source for all files and attachments.')
    if browser_result_digest:
        # The browser bridge waits for this marker after the updated downloads render.
        st.markdown(f'<span id="browser-report-ready" data-result-digest="{browser_result_digest}"></span>', unsafe_allow_html=True)


if __name__ == '__main__':
    main()
