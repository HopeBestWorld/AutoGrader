"""Portable TA handoff reports. Evidence suggestions never become review decisions."""
import csv
from datetime import datetime, timezone
from html import escape
import io
import json
import zipfile

from execution_summary import summarize_attempt, summarize_batch, summarize_student


GUIDANCE = (
    'Complete and Incomplete are reviewer decisions. Evidence found, partial evidence, '
    'and no evidence found are automatic search results that still need review. '
    'No evidence found does not prove an answer is absent. '
    'A successful run, including a mock run, does not mark any assignment part complete. '
    'No accuracy scores are assigned.'
)
LEGEND = [
    ('complete', 'Complete (reviewed)', 'A grader marked this part complete.'),
    ('incomplete', 'Incomplete (reviewed)', 'A grader marked this part incomplete. Read their note and inspect the work.'),
    ('missing', 'Check: no evidence found', 'Inspect the original submission and attachments for this requirement.'),
    ('partial', 'Check: partial evidence', 'Some evidence was found; inspect the missing answer types and every criterion.'),
    ('candidate', 'Review: evidence found', 'Candidate answers were found. Confirm they address every criterion.'),
    ('not_applicable', 'Not applicable (reviewed)', 'A grader marked this part not applicable.'),
]
LABELS = {key: label for key, label, _ in LEGEND}


def part_summary(part):
    evidence = part.get('evidence', [])
    missing = part.get('missing_evidence_types', [])
    review = part.get('review', 'Needs review')
    key = {'Complete': 'complete', 'Incomplete': 'incomplete', 'Not applicable': 'not_applicable'}.get(review)
    if key is None:
        key = 'missing' if not evidence else 'partial' if missing else 'candidate'
    actions = {
        'complete': 'No completion review pending. See the reviewer note for context.',
        'incomplete': 'Check the reviewer note and the unmet assignment criteria.',
        'not_applicable': 'No action unless the applicability decision changes.',
        'missing': 'Find the answer in the original source and attachments; check every criterion before deciding it is incomplete.',
        'partial': 'Look for the missing answer types: ' + ', '.join(missing) + '. Check every criterion.',
        'candidate': 'Confirm that the candidate code or writing answers every criterion; then record a completion decision.',
    }
    locations = list(dict.fromkeys(
        f'{e.get("file", "(file unknown)")} — {e.get("location", "line")} {e.get("line", "?")}'
        for e in evidence
    ))
    return {
        'id': part.get('id', ''), 'title': part.get('title', 'Untitled part'),
        'status': LABELS[key], 'category': key, 'review_decision': review,
        'automatic_evidence': 'No evidence found' if not evidence else 'Partial evidence' if missing else 'Evidence found',
        'missing_evidence_types': missing, 'evidence_locations': locations,
        'needs_review': key in ('missing', 'partial', 'candidate'),
        'needs_attention': key in ('incomplete', 'missing', 'partial', 'candidate'),
        'next_step': actions[key], 'reviewer_note': part.get('review_note', ''),
    }


def summarize_parts(row):
    parts = [part_summary(p) for p in row.get('requirements', [])]
    counts = {key: sum(p['category'] == key for p in parts) for key in LABELS}
    status = ('Incomplete (reviewed)' if counts['incomplete'] else
              'Needs review' if any(p['needs_review'] for p in parts) or not parts else
              'Complete (reviewed)' if counts['complete'] else 'Not applicable (reviewed)')
    priority = {key: i for i, key in enumerate(('incomplete', 'missing', 'partial', 'candidate', 'complete', 'not_applicable'))}
    attention = sorted((p for p in parts if p['needs_attention']), key=lambda p: priority[p['category']])
    return {
        'status': status, 'total_parts': len(parts), 'counts': counts,
        'reviewed_complete': [p['title'] for p in parts if p['category'] == 'complete'],
        'reviewed_incomplete': [p['title'] for p in parts if p['category'] == 'incomplete'],
        'awaiting_review': [p['title'] for p in parts if p['needs_review']],
        'check_first': [p['title'] for p in attention if p['category'] in ('incomplete', 'missing', 'partial')],
        'attention': [{'part_id': p['id'], 'part': p['title'], 'status': p['status'], 'next_step': p['next_step']} for p in attention],
        'parts': parts,
    }


def prepare_report(report):
    """Recompute derived fields so an older browser summary cannot hide later decisions."""
    rows = []
    legacy_status = {'Complete (reviewed)': 'COMPLETE (reviewed)', 'Incomplete (reviewed)': 'INCOMPLETE (reviewed)',
                     'Not applicable (reviewed)': 'NOT APPLICABLE (reviewed)', 'Needs review': 'NEEDS REVIEW'}
    for row in report.get('students', []):
        summary = summarize_parts(row)
        rows.append({**row, 'completion_summary': summary, 'completion': legacy_status[summary['status']],
                     'run_summary': summarize_student(row)})
    return {**report, 'generated_at': report.get('generated_at') or datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'handoff_guidance': GUIDANCE, 'execution_counts': summarize_batch(rows), 'students': rows}


def overview_rows(report):
    rows = []
    requirements = report.get('assignment', {}).get('requirements', [])
    for student in report.get('students', []):
        summary = summarize_parts(student)
        run = summarize_student(student)
        row = {'Assignment': report.get('assignment', {}).get('title', 'Assignment'),
               'Exported at (UTC)': report.get('generated_at', 'Not recorded'),
               'Student': student['student'], 'Runs?': run['answer'], 'Execution runtime':run['runtime'], 'Setup scope':run['setup_scope'], 'Setup adaptations':'; '.join(run['setup_adaptations']) or 'None', 'Test inputs': run['inputs'],
               'Supplied files referenced': '; '.join(run['supplied_files']) or 'None matched',
               'Mocks substituted': '; '.join(run['mock_files']) or 'None',
               'Run reason': run['reason'], 'Run next step': run['next_step'], 'Completion': summary['status'],
               'Parts marked complete': '; '.join(summary['reviewed_complete']) or 'None marked',
               'Parts marked incomplete': '; '.join(summary['reviewed_incomplete']) or 'None marked',
               'Check first': '; '.join(summary['check_first']) or 'No flagged gaps',
               'Still awaiting review': '; '.join(summary['awaiting_review']) or 'None',
               'How to read this report': GUIDANCE}
        lookup = {p['id']: p for p in summary['parts']}
        for part in requirements:
            value = lookup.get(part['id'])
            row[f'{part["id"]}: {part["title"]}'] = value['status'] if value else 'Check: requirement not assessed'
        rows.append(row)
    return rows


def checklist_rows(report):
    rows = []
    for student in report.get('students', []):
        run = summarize_student(student)
        for part in student.get('requirements', []):
            summary = part_summary(part)
            rows.append({
                'Assignment': report.get('assignment', {}).get('title', 'Assignment'),
                'Exported at (UTC)': report.get('generated_at', 'Not recorded'),
                'Student': student['student'], 'Runs?': run['answer'], 'Test inputs': run['inputs'],
                'Supplied files referenced': '; '.join(run['supplied_files']) or 'None matched',
                'Mocks substituted': '; '.join(run['mock_files']) or 'None',
                'Execution runtime':run['runtime'],'Setup scope':run['setup_scope'],'Part ID': summary['id'], 'Assignment part': summary['title'],
                'Status': summary['status'], 'Reviewer decision': summary['review_decision'],
                'Automatic evidence': summary['automatic_evidence'], 'What to check': summary['next_step'],
                'Missing answer types': '; '.join(summary['missing_evidence_types']),
                'Assignment requirement': part.get('description', ''),
                'Criteria to check': '\n'.join(part.get('criteria', [])),
                'Evidence locations': '\n'.join(summary['evidence_locations']),
                'Candidate evidence (not verified answers)': '\n\n'.join(e.get('text', '') for e in part.get('evidence', [])),
                'Reviewer note': summary['reviewer_note'],
            })
    return rows


def csv_bytes(rows):
    """Excel-friendly UTF-8; prevent student-controlled cells becoming formulas."""
    output = io.StringIO(newline='')
    if rows:
        writer = csv.writer(output)
        def safe(value):
            text = str(value)
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')) else text
        writer.writerow([safe(key) for key in rows[0]])
        for row in rows:
            writer.writerow([safe(row.get(key, '')) for key in rows[0]])
    return output.getvalue().encode('utf-8-sig')


STYLE = """
:root{color-scheme:light;font-family:system-ui,sans-serif;color:#172033;background:#f4f6fa}
body{margin:0}main{max-width:1280px;margin:auto;padding:24px}h1{margin-bottom:8px}h2{margin-top:28px}
a{color:#174da6}p,li{line-height:1.55}.note,.student,.guide{background:white;border:1px solid #d8dfeb;border-radius:10px;padding:18px;margin:16px 0}
.muted{color:#4b5568}.meta{font-size:.85rem;overflow-wrap:anywhere}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #d8dfeb}th{background:#edf1f8}caption{text-align:left;padding:8px 0}
.badge{display:inline-block;padding:4px 8px;border-radius:5px;font-weight:650;font-size:.85rem}
.complete{background:#dcf3e3;color:#15552c}.incomplete{background:#fde4e4;color:#8c2424}.missing,.partial{background:#fff0cb;color:#6b4900}
.candidate{background:#e3edff;color:#1d4789}.not_applicable{background:#edf0f4;color:#46505e}.cards{display:flex;gap:10px;flex-wrap:wrap}.card{background:white;border:1px solid #d8dfeb;border-radius:8px;padding:12px;min-width:130px}
.card strong{display:block;font-size:1.5rem}.filters{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}label{font-weight:600}input,select,button{font:inherit;padding:8px;border:1px solid #aebacc;border-radius:5px;background:white}
input{max-width:90%}.student{scroll-margin-top:12px}.part{border-top:1px solid #d8dfeb;padding:12px 0}summary{cursor:pointer;font-weight:600;line-height:1.6}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6fa;padding:12px;font-size:.85rem}blockquote{border-left:3px solid #aebacc;padding-left:12px;margin-left:0;white-space:pre-wrap}
[hidden]{display:none!important}@media print{body{background:white}main{padding:0}.filters,button,.back{display:none}.student{break-before:page}.scroll{overflow:visible}details>*{display:block!important}}
"""
FILTER_SCRIPT = """
const students=[...document.querySelectorAll('.student')];
function filter(){
 const term=document.getElementById('search').value.toLowerCase();
 const category=document.getElementById('filter').value;
 let visible=0;
 for(const student of students){
  const show=student.dataset.name.includes(term)&&(category==='all'||student.dataset[category]==='yes');
  student.hidden=!show;document.getElementById(student.id+'-overview').hidden=!show;if(show)visible++;
 }
 document.getElementById('visible').textContent=visible+' of '+students.length+' students shown';
}
document.getElementById('search').addEventListener('input',filter);
document.getElementById('filter').addEventListener('change',filter);
document.querySelectorAll('a[href^="#student-"]').forEach(link=>link.addEventListener('click',()=>{
 const target=document.getElementById(link.getAttribute('href').slice(1));
 if(target instanceof HTMLDetailsElement)target.open=true;
}));
document.getElementById('print').addEventListener('click',()=>{
 const opened=[...document.querySelectorAll('details')].filter(d=>!d.open);opened.forEach(d=>d.open=true);
 window.print();opened.forEach(d=>d.open=false);
});
filter();
"""


def html_report(report):
    report = prepare_report(report)
    def h(value):
        return escape(str(value), quote=True)
    def badge(part):
        return f'<span class="badge {part["category"]}">{h(part["status"])}</span>'
    title = report.get('assignment', {}).get('title', 'Assignment')
    rows = report['students']
    counts = report['execution_counts']
    chunks = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)} — TA report</title><style>{STYLE}</style></head><body><main id="top">',
              f'<h1>TA submission report</h1><p><strong>{h(title)}</strong></p>',
              f'<p class="meta">Exported {h(report["generated_at"])} · {len(rows)} students<br>Submissions ZIP SHA-256: {h(report.get("archive_sha256", "Not recorded"))}<br>Checklist SHA-256: {h(report.get("assignment_digest", "Not recorded"))}</p>',
              '<div class="guide"><strong>Start here</strong><ol><li>Read the run result and its test inputs.</li><li>Check flagged gaps first, then confirm every remaining unreviewed part using the criteria and evidence locations.</li><li>Record completion decisions in the app and export again. This file is a read-only snapshot.</li></ol>',
              f'<p>{h(GUIDANCE)}</p><p>This report opens offline without the app. For full source and attachments, keep the original Canvas ZIP available; it is not included in this report. Share student evidence with the grading team through your usual course channel.</p></div>',
              '<div class="cards">']
    notes=report.get('validation_notes',[])
    if isinstance(notes,str):notes=[notes]
    if notes:
        chunks.insert(-1,'<aside class="note"><strong>Validation scope</strong><ul>'+''.join('<li>'+h(note)+'</li>' for note in notes)+'</ul></aside>')
    for count, label in [(counts['ran'], 'Ran successfully'), (counts['test_error'], 'Test stopped with error'),
                         (counts['unknown'], 'Run undetermined'), (counts['not_run'], 'Not tested'),
                         (sum(len(summarize_parts(r)['awaiting_review']) for r in rows), 'Parts awaiting review')]:
        chunks.append(f'<div class="card"><strong>{count}</strong>{label}</div>')
    chunks.append('</div><details class="note"><summary>Status legend — how to read the checklist</summary><ul>')
    for key, label, meaning in LEGEND:
        chunks.append(f'<li><span class="badge {key}">{h(label)}</span> {h(meaning)}</li>')
    chunks.append('</ul></details><div class="filters"><label>Find student <input id="search" type="search" placeholder="Name or Canvas ID"></label><label>Show <select id="filter"><option value="all">All students</option><option value="gaps">Flagged gaps / marked incomplete</option><option value="pending">Any parts awaiting review</option><option value="runissue">Run needs attention</option><option value="complete">All applicable parts reviewed complete</option></select></label><button id="print" type="button">Print / save PDF</button></div><p id="visible" role="status"></p>')
    chunks.append('<h2>Class overview</h2><div class="scroll"><table><caption>Click a student to open their part-by-part checklist. Part counts are review counts, not grades.</caption><thead><tr><th scope="col">Student</th><th scope="col">Runs?</th><th scope="col">Completion review</th><th scope="col">Check first</th></tr></thead><tbody>')
    for index, row in enumerate(rows):
        summary = row['completion_summary']
        counts = summary['counts']
        chunks.append(f'<tr id="student-{index}-overview"><td><a href="#student-{index}">{h(row["student"])}</a></td><td>{h(row["run_summary"]["answer"])}</td><td>{h(summary["status"])}<br>{counts["complete"]} complete · {counts["incomplete"]} incomplete · {len(summary["awaiting_review"])} awaiting review · {counts["not_applicable"]} not applicable</td><td>{h("; ".join(summary["check_first"]) or ("Confirm candidate evidence" if summary["awaiting_review"] else "No pending parts"))}</td></tr>')
    chunks.append('</tbody></table></div><h2>Student checklists</h2>')
    for index, row in enumerate(rows):
        summary, run = row['completion_summary'], row['run_summary']
        flags = {'gaps': bool(summary['check_first']), 'pending': bool(summary['awaiting_review']),
                 'runissue': run['category'] != 'ran', 'complete': summary['status'] == 'Complete (reviewed)'}
        attrs = ' '.join(f'data-{key}="{"yes" if value else "no"}"' for key, value in flags.items())
        chunks.append(f'<section class="student" id="student-{index}" data-name="{h(row["student"].lower())}" {attrs}><h3>{h(row["student"])}</h3><p><strong>Runs? {h(run["answer"])}</strong><br>Test inputs: {h(run["inputs"])}<br>{h(run["reason"])}<br>Run next step: {h(run["next_step"])}</p><p><strong>Completion: {h(summary["status"])}</strong></p>')
        chunks.append(f'<p><strong>Execution runtime:</strong> {h(run["runtime"])}<br><strong>Setup scope:</strong> {h(run["setup_scope"])}<br><strong>Supplied files referenced:</strong> {h("; ".join(run["supplied_files"]) or "None matched by the input inventory")}<br><strong>Mocks substituted:</strong> {h("; ".join(run["mock_files"]) or "None")}</p><p class="muted">Matched references describe files available to the test; they do not prove every file was read. Supplied documents can be used together with mock helpers or lexicons.</p>')
        from execution_diagnostics import recovery_rows
        recovery=recovery_rows({'students':[row]})
        if recovery:
            chunks.append('<details class="part"><summary>Execution recovery checklist</summary><p>Source diagnostics are static hints; check the actual runtime result before deciding whether a definition is required.</p><ul>')
            for issue in recovery:
                chunks.append(f'<li><strong>{h(issue["File"])} · line {h(issue["Line"] or "unknown")}: {h(issue["Needs attention"])}</strong><br>{h(issue["Next step"])}</li>')
            chunks.append('</ul></details>')
        if row.get('unmatched'):
            chunks.append('<p class="note">Student identity was not matched. Resolve these files before assigning results to a student.</p>')
        if summary['attention']:
            chunks.append('<p><strong>Review queue (flagged gaps first)</strong></p><ol>')
            for part in summary['attention']:
                target = next(i for i, p in enumerate(summary['parts']) if p['id'] == part['part_id'])
                chunks.append(f'<li><a href="#student-{index}-part-{target}">{h(part["part"])}</a> — {h(part["status"])}</li>')
            chunks.append('</ol>')
        chunks.append('<div class="scroll"><table><thead><tr><th scope="col">Assignment part</th><th scope="col">Status</th><th scope="col">What to check / reviewer note</th><th scope="col">Evidence location</th></tr></thead><tbody>')
        for number, part in enumerate(summary['parts']):
            chunks.append(f'<tr><td><a href="#student-{index}-part-{number}">{h(part["title"])}</a></td><td>{badge(part)}</td><td>{h(part["next_step"])}' + (f'<blockquote>{h(part["reviewer_note"])}</blockquote>' if part['reviewer_note'] else '') + f'</td><td>{"<br>".join(h(loc) for loc in part["evidence_locations"]) or "No matching evidence; inspect originals"}</td></tr>')
        chunks.append('</tbody></table></div>')
        for number, (original, part) in enumerate(zip(row.get('requirements', []), summary['parts'])):
            chunks.append(f'<details class="part" id="student-{index}-part-{number}"><summary>{h(part["title"])} — {h(part["status"])}</summary><p>{h(original.get("description", ""))}</p><strong>Criteria to check</strong><ul>')
            for criterion in original.get('criteria', []):
                chunks.append(f'<li>{h(criterion)}</li>')
            if not original.get('criteria'):
                chunks.append('<li>Check the full requirement above and the original assignment instructions.</li>')
            chunks.append(f'</ul><p>{h(part["next_step"])}</p>')
            if part['reviewer_note']:
                chunks.append(f'<p><strong>Reviewer note</strong></p><blockquote>{h(part["reviewer_note"])}</blockquote>')
            for evidence in original.get('evidence', []):
                location = f'{evidence.get("file", "(file unknown)")} — {evidence.get("location", "line")} {evidence.get("line", "?")}'
                chunks.append(f'<p><strong>Candidate {h(evidence.get("kind", "evidence"))}: {h(location)}</strong></p><pre>{h(evidence.get("text", ""))}</pre>')
            chunks.append('</details>')
        if row.get('execution'):
            chunks.append('<details class="part"><summary>Run details and logs</summary>')
            for result in row['execution']:
                outcome = summarize_attempt(result)
                log = str(result.get('log', ''))
                chunks.append(f'<h4>{h(result.get("file") or "(no entrypoint)")}</h4><p>{h(outcome["answer"])}<br>{h(result.get("status", ""))}</p><pre>{h(log[-20000:])}</pre>')
                if len(log) > 20000:
                    chunks.append('<p>Last 20,000 characters shown; see submission-report.json for the retained log.</p>')
                if result.get('original_input_attempt'):
                    previous=result['original_input_attempt']
                    chunks.append('<details><summary>Supplied-input attempt before synthetic URL fallback</summary><p>The original supplied document was unchanged. This earlier attempt did not finish; the current synthetic-input test has a different scope.</p><p>'+h(previous.get('status',''))+'</p><pre>'+h(str(previous.get('log',''))[-20000:])+'</pre></details>')
            chunks.append('</details>')
        if row.get('execution_history'):
            chunks.append('<details class="part"><summary>Earlier execution attempts (retained history)</summary><p>These attempts are retained for comparison. The class overview summarizes the current active results.</p>')
            for previous in row['execution_history']:
                chunks.append(f'<h4>{h(previous.get("file") or "(no entrypoint)")}</h4><p>{h(previous.get("status", ""))}</p><pre>{h(str(previous.get("log", ""))[-20000:])}</pre>')
            chunks.append('</details>')
        chunks.append('<p class="back"><a href="#top">Back to class overview</a></p></section>')
    chunks.append(f'</main><script>{FILTER_SCRIPT}</script></body></html>')
    return ''.join(chunks).encode('utf-8')


def handoff_files(report):
    report = prepare_report(report)
    from execution_diagnostics import recovery_rows
    recovery=recovery_rows(report)
    readme = f'''TA HANDOFF — {report.get('assignment', {}).get('title', 'Assignment')}
Exported: {report['generated_at']}

1. Open TA-report.html in any browser. It works offline; search/filter students and click a name for the checklist.
2. Check flagged gaps first, then confirm all parts awaiting review. Criteria, candidate excerpts, file/line locations and reviewer notes are included.
3. For spreadsheets, use student-overview.csv (one row per student, one column per part) or part-checklist.csv (one row per student and part).
4. submission-report.json contains the detailed record, including execution logs and mock manifests.
5. execution-next-steps.csv, when present, lists unresolved runtime issues, source/cell lines and suggested follow-up checks. Native follow-up reports retain earlier attempts in execution_history.

{GUIDANCE}

This is a read-only snapshot. Record decisions in the original app session and export again. Reports cannot currently restore a review session.
The ZIP contains student evidence. Share it with the grading team through your usual course channel.
Keep the original Canvas submissions ZIP available for full source and attachments; it is not included in this handoff.
CSV cells that could be interpreted as spreadsheet formulas are prefixed with an apostrophe for safe viewing.

App: https://hopebestworld.github.io/AutoGrader/
'''
    files={'START-HERE.txt': readme.encode('utf-8'), 'TA-report.html': html_report(report),
            'student-overview.csv': csv_bytes(overview_rows(report)), 'part-checklist.csv': csv_bytes(checklist_rows(report)),
            'submission-report.json': json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')}
    if recovery:files['execution-next-steps.csv']=csv_bytes(recovery)
    return files


def handoff_zip(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()
