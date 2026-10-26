"""Portable TA handoff reports. Evidence suggestions never become review decisions."""
import base64
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
    ('unassessed', 'Not assessed — review needed', 'This assignment part has no assessment record in the report.'),
]
LABELS = {key: label for key, label, _ in LEGEND}


def part_summary(part):
    evidence = part.get('evidence', [])
    missing = part.get('missing_evidence_types', [])
    review = part.get('review', 'Needs review')
    key = {'Complete': 'complete', 'Incomplete': 'incomplete', 'Not applicable': 'not_applicable'}.get(review)
    if key is None and part.get('assessment_missing'):key='unassessed'
    if key is None:
        key = 'missing' if not evidence else 'partial' if missing else 'candidate'
    actions = {
        'complete': 'No completion review pending. See the reviewer note for context.',
        'incomplete': 'Check the reviewer note and the unmet assignment criteria.',
        'not_applicable': 'No action unless the applicability decision changes.',
        'missing': 'Find the answer in the original source and attachments; check every criterion before deciding it is incomplete.',
        'partial': 'Look for the missing answer types: ' + ', '.join(missing) + '. Check every criterion.',
        'candidate': 'Confirm that the candidate code or writing answers every criterion; then record a completion decision.',
        'unassessed': 'Review this assignment part in the original submission; no assessment record was supplied.',
    }
    locations = list(dict.fromkeys(
        f'{e.get("file", "(file unknown)")} — {e.get("location", "line")} {e.get("line", "?")}'
        for e in evidence
    ))
    return {
        'id': part.get('id', ''), 'title': part.get('title', 'Untitled part'),
        'status': LABELS[key], 'category': key, 'review_decision': review,
        'automatic_evidence': 'Not assessed' if key=='unassessed' else 'No evidence found' if not evidence else 'Partial evidence' if missing else 'Evidence found',
        'missing_evidence_types': missing, 'evidence_locations': locations,
        'needs_review': key in ('missing', 'partial', 'candidate','unassessed'),
        'needs_attention': key in ('incomplete', 'missing', 'partial', 'candidate','unassessed'),
        'next_step': actions[key], 'reviewer_note': part.get('review_note', ''),
    }


def assessment_parts(row):
    recorded=row.get('requirements',[])
    known={p['id'] for p in recorded}
    return [*recorded,*[p for p in row.get('unassessed_requirements',[]) if p['id'] not in known]]


def summarize_parts(row):
    parts = [part_summary(p) for p in assessment_parts(row)]
    counts = {key: sum(p['category'] == key for p in parts) for key in LABELS}
    status = ('Incomplete (reviewed)' if counts['incomplete'] else
              'Needs review' if any(p['needs_review'] for p in parts) or not parts else
              'Complete (reviewed)' if counts['complete'] else 'Not applicable (reviewed)')
    priority = {key: i for i, key in enumerate(('incomplete', 'unassessed', 'missing', 'partial', 'candidate', 'complete', 'not_applicable'))}
    attention = sorted((p for p in parts if p['needs_attention']), key=lambda p: priority[p['category']])
    return {
        'status': status, 'total_parts': len(parts), 'counts': counts,
        'reviewed_complete': [p['title'] for p in parts if p['category'] == 'complete'],
        'reviewed_incomplete': [p['title'] for p in parts if p['category'] == 'incomplete'],
        'awaiting_review': [p['title'] for p in parts if p['needs_review']],
        'check_first': [p['title'] for p in attention if p['category'] in ('incomplete', 'missing', 'partial','unassessed')],
        'attention': [{'part_id': p['id'], 'part': p['title'], 'status': p['status'], 'next_step': p['next_step']} for p in attention],
        'parts': parts,
    }


def prepare_report(report):
    """Recompute derived fields so an older browser summary cannot hide later decisions."""
    rows = []
    legacy_status = {'Complete (reviewed)': 'COMPLETE (reviewed)', 'Incomplete (reviewed)': 'INCOMPLETE (reviewed)',
                     'Not applicable (reviewed)': 'NOT APPLICABLE (reviewed)', 'Needs review': 'NEEDS REVIEW'}
    for row in report.get('students', []):
        # A partial/legacy record must not appear complete because unassessed
        # assignment parts were omitted. Preserve the record as data, not a grade.
        row=dict(row)
        recorded={part['id'] for part in row.get('requirements',[])}
        absent=[{**part,'review':'Needs review','evidence':[],'assessment_missing':True}
                for part in report.get('assignment',{}).get('requirements',[]) if part['id'] not in recorded]
        if absent:row['unassessed_requirements']=absent
        else:row.pop('unassessed_requirements',None)
        summary = summarize_parts(row)
        rows.append({**row, 'completion_summary': summary, 'completion': legacy_status[summary['status']],
                     'run_summary': summarize_student(row)})
    return {**report, 'generated_at': report.get('generated_at') or datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'handoff_guidance': GUIDANCE, 'execution_counts': summarize_batch(rows), 'students': rows}


def overview_rows(report):
    report=prepare_report(report)
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
    report=prepare_report(report)
    rows = []
    for student in report.get('students', []):
        run = summarize_student(student)
        for part in assessment_parts(student):
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


def compact_rows(report):
    """One combined row per student, with dynamic assignment-part columns."""
    report=prepare_report(report)
    result=[]
    for student in report['students']:
        run=student['run_summary'];parts=student['completion_summary']
        row={'Assignment':report.get('assignment',{}).get('title','Assignment'),'Student':student['student'],
             'Files':'; '.join(dict.fromkeys(str(e.get('file') or '') for e in student.get('execution',[]) if e.get('file'))) or 'No entrypoint recorded',
             'Run result':run['answer'],'Test inputs':run['inputs'],'Setup scope':run['setup_scope'],
             'Completion review':parts['status'],'Parts complete (reviewed)':len(parts['reviewed_complete']),
             'Parts incomplete (reviewed)':len(parts['reviewed_incomplete']),
             'Parts awaiting review':len(parts['awaiting_review']),
             'Check first':'; '.join(parts['check_first']) or ('Confirm remaining evidence' if parts['awaiting_review'] else 'No pending review'),
             'Run reason':run['reason'],'Run next step':run['next_step']}
        lookup={p['id']:p for p in parts['parts']}
        for index,requirement in enumerate(report.get('assignment',{}).get('requirements',[]),1):
            row[f'{index}. {requirement["title"]}']=lookup[requirement['id']]['status']
        result.append(row)
    return result


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
:root{color-scheme:light;font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#17243a;background:#f3f6fb;font-size:15px}
*{box-sizing:border-box}body{margin:0}main{max-width:1540px;margin:auto;padding:28px}h1,h2,h3,h4{letter-spacing:-.025em}h1{font-size:clamp(1.7rem,3vw,2.5rem);margin:6px 0 10px;line-height:1.2}h2{font-size:1.3rem;margin:28px 0 12px}h3{font-size:1.3rem;margin-top:0}h4{margin-bottom:8px}
a{color:#244cba;text-underline-offset:3px}p,li{line-height:1.6}button,a,input,select,summary{outline-offset:4px}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #6c93ee}
.hero,.note,.student,.guide,.panel{background:white;border:1px solid #dce4f0;border-radius:14px;padding:22px;margin:16px 0;box-shadow:0 3px 14px #17243a04}.hero{border-top:4px solid #4265d6;margin-top:0;padding:26px 28px}.hero-top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}.eyebrow{text-transform:uppercase;letter-spacing:.14em;font-size:.72rem;font-weight:750;color:#496180}.tag{display:inline-block;border:1px solid #dce4f0;border-radius:99px;padding:5px 11px;font-size:.76rem;color:#4b5e78;background:#f8faff}
.hero p{max-width:1000px;margin:8px 0}.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:18px}button,.button{display:inline-flex;align-items:center;justify-content:center;gap:7px;font:inherit;font-weight:650;padding:10px 15px;border:1px solid #cad5e5;border-radius:8px;background:white;color:#223650;cursor:pointer;text-decoration:none}button:hover,.button:hover{background:#eff4ff;border-color:#99b0dc}.primary{background:#3155bf;color:white;border-color:#3155bf}.primary:hover{background:#2547a8;color:white}
.table-meta button{padding:3px 8px;font-size:.78rem}.meta-toggle{padding:12px 16px;font-size:.84rem}
.muted{color:#596b83}.meta{font-size:.8rem;overflow-wrap:anywhere}.small{font-size:.8rem}.cards{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:12px;margin:20px 0}.card{background:white;border:1px solid #dce4f0;border-radius:12px;padding:17px;box-shadow:0 2px 8px #17243a03;font-size:.8rem;color:#596b83}.card strong{display:block;font-size:1.9rem;line-height:1.2;color:#17243a;margin-bottom:6px}.card.success{border-top:3px solid #2a8972}.card.warning{border-top:3px solid #c38c2b}
.legend-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:12px 0}.badge{display:inline-block;padding:5px 8px;border-radius:6px;font-size:.76rem;font-weight:650;line-height:1.35;border:1px solid transparent;text-decoration:none;white-space:normal}.complete,.run-pass{background:#e4f5ed;color:#176346;border-color:#c5e5d5}.incomplete,.run-error{background:#fdebec;color:#9a2938;border-color:#f3d0d6}.missing,.partial,.unassessed,.run-unknown{background:#fff4db;color:#815b11;border-color:#edd9a9}.candidate,.run-adapted{background:#eaf0ff;color:#2d4d9c;border-color:#d0ddfb}.not_applicable,.run-not_run{background:#f0f3f7;color:#596579;border-color:#dce3ed}.badge:hover{filter:brightness(.98)}
.filters{display:grid;grid-template-columns:minmax(220px,1.5fr) repeat(3,minmax(150px,1fr));gap:12px;margin:18px 0 10px}label{font-weight:650;font-size:.8rem;display:flex;flex-direction:column;gap:7px}input,select{font:inherit;font-size:.9rem;width:100%;padding:10px 11px;border:1px solid #c8d3e2;border-radius:7px;background:white;color:#17243a;min-width:0}.table-meta{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;font-size:.82rem;color:#596b83;margin:12px 0}.scroll{overflow:auto;border:1px solid #dce4f0;border-radius:9px}.matrix-scroll{max-height:72vh}.matrix{min-width:750px}.matrix th{position:sticky;top:0;z-index:2}.matrix th:first-child{left:0;z-index:4}.matrix td:first-child{position:sticky;left:0;z-index:1;background:white;box-shadow:2px 0 4px #17243a05}.matrix th:first-child,.matrix td:first-child{min-width:180px;max-width:235px}.matrix td.run-cell{min-width:165px;max-width:210px}.matrix td.review-cell{min-width:135px;max-width:165px}.matrix .part-cell{min-width:112px;max-width:155px;text-align:center}.part-col{text-align:center;min-width:112px;max-width:155px}.part-number{display:block;font-size:.66rem;font-weight:750;text-transform:uppercase;letter-spacing:.08em;color:#6a7d97;margin-bottom:5px}.student-name{font-weight:700;display:block;overflow-wrap:anywhere}.student-sub{display:block;font-size:.74rem;font-weight:450;color:#66788f;margin-top:5px}.row-attention{box-shadow:inset 3px 0 #d2a64f}.matrix tbody tr:hover td{background:#f8faff}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:.85rem}th,td{text-align:left;vertical-align:top;padding:13px 11px;border-bottom:1px solid #e7ecf4}th{background:#f2f5fb;font-weight:650}tr:last-child td{border-bottom:0}caption{text-align:left;padding:10px;color:#596b83;font-size:.8rem}.matrix .badge{display:block}.panel{padding:18px}.empty{padding:30px;text-align:center;color:#66788f}.student{scroll-margin-top:18px}.student-head{display:flex;gap:15px;align-items:flex-start;justify-content:space-between;flex-wrap:wrap}.student-head h3{margin-bottom:6px}.student-subtitle{font-size:.83rem;color:#596b83;margin:0}.run-summary{padding:15px 17px;border-radius:9px;background:#f6f8fc;border:1px solid #e3eaf4;margin:16px 0}.run-summary p{margin:6px 0}.part{border-top:1px solid #e3eaf4;padding:13px 0}summary{cursor:pointer;font-weight:650;line-height:1.6}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f7fc;border:1px solid #e5ebf4;border-radius:7px;padding:13px;font-size:.8rem;line-height:1.55;max-height:420px;overflow:auto}blockquote{border-left:3px solid #9fb2d9;padding-left:12px;margin-left:0;white-space:pre-wrap}.review-queue{border-left:3px solid #d2a64f;padding:4px 0 4px 16px;margin:18px 0}.back{font-size:.8rem}.report-footer{margin:25px 0;font-size:.8rem;color:#66788f}
[hidden]{display:none!important}@media(max-width:1000px){main{padding:16px}.cards{grid-template-columns:repeat(3,1fr)}.filters{grid-template-columns:repeat(2,1fr)}.hero{padding:22px}.student,.note{padding:18px}}@media(max-width:540px){main{padding:10px}.cards{grid-template-columns:repeat(2,1fr);gap:8px}.filters{grid-template-columns:1fr}.hero{padding:18px}.hero-top{align-items:flex-start}.card{padding:13px}.matrix th:first-child,.matrix td:first-child{min-width:145px;max-width:175px}.actions button{flex:1}.panel{padding:12px}}
@page{size:landscape;margin:12mm}@media print{body{background:white;font-size:10pt}main{padding:0;max-width:none}.filters,button,.back,.actions,.table-meta,.meta-toggle{display:none}.printing-overview #student-details,.printing-overview #details-heading{display:none}.hero,.student,.guide,.panel,.note{box-shadow:none;border-radius:0}.cards{grid-template-columns:repeat(6,1fr)}.student{break-before:page}.scroll,.matrix-scroll{overflow:visible;max-height:none}.matrix{min-width:0;width:100%;font-size:7pt}.matrix th,.matrix td{padding:5px;min-width:0!important;max-width:none!important;position:static!important}.matrix th:first-child,.matrix td:first-child{box-shadow:none}.badge{font-size:7pt;print-color-adjust:exact;-webkit-print-color-adjust:exact}thead{display:table-header-group}tr{break-inside:avoid}pre{max-height:none;overflow:visible}details>*{display:block!important}}
"""
FILTER_SCRIPT = """
const students=[...document.querySelectorAll('.student')];
const overview=document.getElementById('overview-body');
const details=document.getElementById('student-details');
function update(){
 const term=document.getElementById('search').value.toLowerCase();
 const category=document.getElementById('filter').value;
 const run=document.getElementById('run-filter').value;
 const sort=document.getElementById('sort').value;
 const ordered=[...students].sort((a,b)=>{
  const key=sort==='priority'?'priority':sort==='run'?'runlevel':sort==='review'?'reviewcount':null;
  return (key?Number(b.dataset[key])-Number(a.dataset[key]):0)||a.dataset.name.localeCompare(b.dataset.name);
 });
 let visible=0,pending=0;
 for(const student of ordered){
  const show=(student.dataset.search||student.dataset.name).includes(term)&&(category==='all'||student.dataset[category]==='yes')&&(run==='all'||student.dataset.run===run||(run==='adapted'&&student.dataset.adapted==='yes'));
  student.hidden=!show;
  const row=document.getElementById(student.id+'-overview');row.hidden=!show;
  overview.append(row);details.append(student);
  if(show){visible++;pending+=Number(student.dataset.reviewcount);}
 }
 document.getElementById('visible').textContent=visible+' of '+students.length+' students shown · '+pending+' parts awaiting review';
 document.getElementById('no-results').hidden=visible!==0;
}
document.getElementById('search').addEventListener('input',update);
for(const id of ['filter','run-filter','sort'])document.getElementById(id).addEventListener('change',update);
document.getElementById('clear-filters').addEventListener('click',()=>{
 document.getElementById('search').value='';document.getElementById('filter').value='all';document.getElementById('run-filter').value='all';update();
});
document.querySelectorAll('a[href^="#student-"]').forEach(link=>link.addEventListener('click',()=>{
 const target=document.getElementById(link.getAttribute('href').slice(1));
 if(target instanceof HTMLDetailsElement)target.open=true;
}));
document.getElementById('print').addEventListener('click',()=>{
 document.body.classList.add('printing-overview');
 try{window.print();}finally{document.body.classList.remove('printing-overview');}
});
document.getElementById('csv-download').addEventListener('click',()=>{
 const binary=atob(CLASS_CSV);const bytes=new Uint8Array(binary.length);
 for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);
 const url=URL.createObjectURL(new Blob([bytes],{type:'text/csv;charset=utf-8'}));
 const link=document.createElement('a');link.href=url;link.download='TA-class-results.csv';link.click();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
});
const exported=document.getElementById('export-time');
const date=new Date(exported.dateTime);
if(Number.isFinite(date.getTime()))exported.textContent=new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}).format(date);
update();
"""


def html_report(report):
    report = prepare_report(report)
    def h(value):
        return escape(str(value), quote=True)
    def badge(part):
        return f'<span class="badge {part["category"]}">{h(part["status"])}</span>'
    def run_badge(run):
        if run['category']=='ran':
            text='✓ Passed · Adapted setup' if run['setup_adaptations'] else '✓ Passed · Mock inputs/helpers' if run['uses_mocks'] else '✓ Passed · Supplied inputs'
            style='run-adapted' if run['setup_adaptations'] else 'run-pass'
        else:
            text={'test_error':'× Test stopped with error','unknown':'? Undetermined','not_run':'— Not tested'}[run['category']]
            style='run-'+('error' if run['category']=='test_error' else run['category'])
        return f'<span class="badge {style}" title="{h(run["answer"]+": "+run["reason"])}">{h(text)}</span>'
    def matrix_badge(part,target):
        text={'complete':'✓ Complete','incomplete':'× Incomplete','missing':'! No evidence found','partial':'! Partial evidence',
              'candidate':'? Review evidence','not_applicable':'— N/A','unassessed':'? Not assessed'}[part['category']]
        return f'<a class="badge {part["category"]}" href="#{target}" title="{h(part["status"]+": "+part["next_step"])}">{h(text)}</a>'
    title = report.get('assignment', {}).get('title', 'Assignment')
    rows = report['students']
    counts = report['execution_counts']
    requirements=report.get('assignment',{}).get('requirements',[])
    totals={key:sum(row['completion_summary']['counts'][key] for row in rows) for key in LABELS}
    chunks = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)} — TA report</title><style>{STYLE}</style></head><body><main id="top">',
              '<header class="hero"><div class="hero-top"><span class="eyebrow">Assignment checker · TA handoff</span><span class="tag">Offline · Report only · Read-only snapshot</span></div>',
              f'<h1>{h(title)}</h1>',
              '<p class="muted small">Run checks and part-by-part review for your grading team. Evidence matches await a grader decision; mock and adapted tests are labeled.</p>',
              f'<p class="muted small">Exported <time id="export-time" datetime="{h(report["generated_at"])}">{h(report["generated_at"])}</time> · {len(rows)} students · {len(requirements)} assignment parts</p>',
              '<div class="actions"><button id="csv-download" type="button" class="primary">Download class CSV</button><button id="print" type="button">Print overview / PDF</button><a class="button" href="#overview">Go to class overview ↓</a></div></header>',
              '<div class="cards" aria-label="Entire class totals">']
    for count,label,style in [(len(rows),'Students',''),(counts['ran'],'Run tests finished','success'),
                              (counts['test_error']+counts['unknown']+counts['not_run'],'Runs needing attention','warning'),
                              (totals['complete'],'Parts complete · reviewed','success'),(totals['incomplete'],'Parts incomplete · reviewed','warning'),
                              (sum(len(r['completion_summary']['awaiting_review']) for r in rows),'Parts awaiting review','')]:
        chunks.append(f'<div class="card {style}"><strong>{count}</strong>{h(label)}</div>')
    chunks.append('</div>')
    metadata=['<details class="note meta-toggle" id="report-details"><summary>Report details, validation scope &amp; status legend</summary>']
    metadata.append(f'<p class="meta">Submissions ZIP SHA-256: {h(report.get("archive_sha256", "Not recorded"))}<br>Checklist SHA-256: {h(report.get("assignment_digest", "Not recorded"))}</p><p>{h(GUIDANCE)}</p>')
    notes=report.get('validation_notes',[])
    if isinstance(notes,str):notes=[notes]
    if notes:
        metadata.append('<strong>Validation scope</strong><ul>'+''.join('<li>'+h(note)+'</li>' for note in notes)+'</ul>')
    metadata.append('<strong>Status legend</strong><ul>')
    for key, label, meaning in LEGEND:
        metadata.append(f'<li><span class="badge {key}">{h(label)}</span> {h(meaning)}</li>')
    metadata.append('</ul><p class="small muted">This file includes results, criteria, evidence excerpts and summarized logs. Original submissions and attachments are not included. Share the original Canvas ZIP separately when the TA needs full source.</p></details>')
    chunks.append('<section class="panel" id="overview"><h2 style="margin-top:0">Class overview</h2><p class="muted small">Each assignment part has its own column. Blue asks for review; amber flags a gap to inspect. Green and red part badges reflect recorded grader decisions.</p>')
    chunks.append('<div class="legend-row" aria-label="Part status key"><span class="badge complete">✓ Complete · reviewed</span><span class="badge incomplete">× Incomplete · reviewed</span><span class="badge candidate">? Review evidence</span><span class="badge missing">! Check a gap</span><span class="badge not_applicable">— Not applicable</span></div>')
    chunks.append('<div class="filters"><label>Find a student<input id="search" type="search" placeholder="Name, Canvas ID or file"></label><label>Run result<select id="run-filter"><option value="all">All run results</option><option value="ran">Finished tests</option><option value="test_error">Test errors</option><option value="unknown">Undetermined</option><option value="not_run">Not tested</option><option value="adapted">Setup adapted</option></select></label><label>Part review<select id="filter"><option value="all">All review statuses</option><option value="gaps">Flagged gaps / incomplete</option><option value="pending">Parts awaiting review</option><option value="incomplete">Marked incomplete</option><option value="complete">All applicable parts complete</option></select></label><label>Sort by<select id="sort"><option value="priority">Needs attention first</option><option value="name">Student name A–Z</option><option value="run">Run issues first</option><option value="review">Most parts to review</option></select></label></div>')
    chunks.append('<div class="table-meta"><span id="visible" role="status"></span><span>Totals above and CSV download include the whole class. <button id="clear-filters" type="button">Clear filters</button></span></div>')
    chunks.append('<div class="scroll matrix-scroll" tabindex="0" role="region" aria-label="Class results with one column per assignment part"><table class="matrix"><caption>Part statuses are review decisions or evidence-search results, not accuracy scores. Click a badge to see its evidence and criteria.</caption><thead><tr><th scope="col">Student</th><th scope="col">Run check</th><th scope="col">Completion review</th>')
    for number,requirement in enumerate(requirements,1):
        chunks.append(f'<th scope="col" class="part-col" title="{h(requirement.get("description", ""))}"><span class="part-number">Requirement {number}</span>{h(requirement["title"])}</th>')
    chunks.append('</tr></thead><tbody id="overview-body">')
    for index, row in enumerate(rows):
        summary,run=row['completion_summary'],row['run_summary']
        name,_,ident=row['student'].rpartition('_')
        display=name if ident.isdigit() else row['student']
        files=list(dict.fromkeys(e.get('file') for e in row.get('execution',[]) if e.get('file')))
        sublabel=('Canvas '+ident+' · ' if ident.isdigit() else '')+(', '.join(files) or 'No entrypoint run')
        state='incomplete' if summary['counts']['incomplete'] else 'candidate' if summary['awaiting_review'] else 'complete' if summary['counts']['complete'] else 'not_applicable'
        chunks.append(f'<tr id="student-{index}-overview"><td><a class="student-name" href="#student-{index}">{h(display)}</a><span class="student-sub">{h(sublabel)}</span></td><td class="run-cell">{run_badge(run)}<span class="student-sub">{h(run["runtime"])}</span></td><td class="review-cell"><span class="badge {state}">{h(summary["status"])}</span><span class="student-sub">{summary["counts"]["complete"]} complete · {summary["counts"]["incomplete"]} incomplete<br>{len(summary["awaiting_review"])} awaiting review</span></td>')
        lookup={part['id']:(n,part) for n,part in enumerate(summary['parts'])}
        for requirement in requirements:
            number,part=lookup[requirement['id']]
            chunks.append(f'<td class="part-cell" data-part="{h(part["id"])}">{matrix_badge(part,f"student-{index}-part-{number}")}</td>')
        chunks.append('</tr>')
    chunks.append('</tbody></table></div><p id="no-results" class="empty" hidden>No students match these filters. Try another name or clear the filters.</p></section>'+''.join(metadata)+'<h2 id="details-heading">Student details &amp; evidence</h2><div id="student-details">')
    for index, row in enumerate(rows):
        summary, run = row['completion_summary'], row['run_summary']
        flags = {'gaps': bool(summary['check_first']), 'pending': bool(summary['awaiting_review']),'incomplete':bool(summary['counts']['incomplete']),
                 'runissue': run['category'] != 'ran', 'complete': summary['status'] == 'Complete (reviewed)'}
        attrs = ' '.join(f'data-{key}="{"yes" if value else "no"}"' for key, value in flags.items())
        priority=4 if run['category']!='ran' else 3 if summary['counts']['incomplete'] else 2 if summary['check_first'] else 1 if summary['awaiting_review'] else 0
        runlevel={'ran':0,'not_run':1,'unknown':2,'test_error':3}[run['category']]
        search=' '.join([row['student']]+[str(e.get('file') or '') for e in row.get('execution',[])])
        chunks.append(f'<section class="student" id="student-{index}" data-name="{h(row["student"].lower())}" data-search="{h(search.lower())}" data-run="{h(run["category"])}" data-adapted="{"yes" if run["setup_adaptations"] else "no"}" data-priority="{priority}" data-runlevel="{runlevel}" data-reviewcount="{len(summary["awaiting_review"])}" {attrs}><div class="student-head"><div><h3>{h(row["student"])}</h3><p class="student-subtitle">{h(summary["status"])} · {len(summary["parts"])} assignment parts</p></div>{run_badge(run)}</div><div class="run-summary"><p><strong>Runs? {h(run["answer"])}</strong><br>Test inputs: {h(run["inputs"])}</p><p>{h(run["reason"])}</p><p><strong>Next step:</strong> {h(run["next_step"])}</p></div><p><strong>Completion: {h(summary["status"])}</strong></p>')
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
            chunks.append('<div class="review-queue"><strong>Review queue · flagged gaps first</strong><ol>')
            for part in summary['attention']:
                target = next(i for i, p in enumerate(summary['parts']) if p['id'] == part['part_id'])
                chunks.append(f'<li><a href="#student-{index}-part-{target}">{h(part["part"])}</a> — {h(part["status"])}</li>')
            chunks.append('</ol></div>')
        chunks.append('<div class="scroll"><table><thead><tr><th scope="col">Assignment part</th><th scope="col">Status</th><th scope="col">What to check / reviewer note</th><th scope="col">Evidence location</th></tr></thead><tbody>')
        for number, part in enumerate(summary['parts']):
            chunks.append(f'<tr><td><a href="#student-{index}-part-{number}">{h(part["title"])}</a></td><td>{badge(part)}</td><td>{h(part["next_step"])}' + (f'<blockquote>{h(part["reviewer_note"])}</blockquote>' if part['reviewer_note'] else '') + f'</td><td>{"<br>".join(h(loc) for loc in part["evidence_locations"]) or "No matching evidence; inspect originals"}</td></tr>')
        chunks.append('</tbody></table></div>')
        for number, (original, part) in enumerate(zip(assessment_parts(row), summary['parts'])):
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
        chunks.append('<p class="back"><a href="#overview">↑ Back to class overview</a></p></section>')
    encoded_csv=base64.b64encode(csv_bytes(compact_rows(report))).decode('ascii')
    chunks.append('</div><p class="report-footer">Report-only snapshot. Send the original submissions ZIP separately for full source and attachments. Record review decisions in the autograder and export again; this file does not edit grades or execute student code. Share student evidence through your usual course channel.</p><noscript><p>JavaScript is needed for filters and the CSV download. The class table, results and evidence remain readable below.</p></noscript>')
    chunks.append(f'</main><script>const CLASS_CSV={json.dumps(encoded_csv)};\n{FILTER_SCRIPT}</script></body></html>')
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
   class-results.csv is the compact combined sheet: run result, input/setup scope, completion review and one column per assignment part.
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
            'class-results.csv':csv_bytes(compact_rows(report)),
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
