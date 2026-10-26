"""Native follow-up for unresolved browser checks; report history stays intact."""
import argparse
import base64
import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tempfile
import zipfile
import zlib

ROOT=Path(__file__).parent
FOLLOWUP_FILES=('native_followup.py','execution_diagnostics.py','grader_core.py','prerequisites.py',
                'execution_summary.py','report_exports.py','mock_inputs.py','notebook_worker.py',
                'mock_helpers/sentiment_toolkit.py','mock_helpers/transformers.py','mock_helpers/readability_tools.py',
                'mock_helpers/_readability_nlp.py','mock_helpers/spacy.py','mock_helpers/en_core_web_sm.py',
                'mock_helpers/textstat.py','mock_helpers/wordfreq.py')
NATIVE_PACKAGES=['marimo>=0.24,<1','pandas>=2.2','numpy>=1.26','altair>=5,<7','scipy>=1.11',
                 'pypdf>=6','beautifulsoup4>=4.12','nbclient>=0.10','nbformat>=5.10','ipykernel>=6',
                 'pronouncing>=0.2','spacy>=3.8,<3.9','truststore>=0.10','textstat','wordfreq',
                 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl']


def followup_plan(report):
    from execution_summary import summarize_attempt
    plan={}
    for row in report.get('students',[]):
        if row.get('unmatched'):continue
        entries=[]
        for attempt in row.get('execution',[]):
            entry=attempt.get('file')
            if not entry or summarize_attempt(attempt)['category'] in ('ran','not_run'):continue
            path=PurePosixPath(entry)
            if path.is_absolute() or '..' in path.parts or ':' in entry or '\\' in entry or path.suffix not in ('.py','.ipynb'):
                raise ValueError('Unsafe follow-up entrypoint: '+entry)
            if entry not in entries:entries.append(entry)
        if entries:plan[row['student']]=entries
    return plan


def run_followup(report, submissions, *, students=None, shared=None, resources=None, mock=True, timeout=300, mock_words=30000):
    from grader_core import group_submissions, profile_digest, run_entrypoint, safe_extract, unpack_nested, native_execution_available
    from execution_diagnostics import inspect_folder_sources
    from report_exports import prepare_report
    if not native_execution_available():raise RuntimeError('Native follow-up requires a Linux/macOS/Windows Python runtime, such as free Colab.')
    if timeout<1 or not 100<=mock_words<=200000:raise ValueError('Invalid timeout or synthetic corpus size')
    source=Path(submissions).read_bytes()
    if hashlib.sha256(source).hexdigest()!=report.get('archive_sha256'):
        raise ValueError('Submissions ZIP does not match this report. Upload the original ZIP; do not change it to apply a repair.')
    if profile_digest(report['assignment'])!=report.get('assignment_digest'):
        raise ValueError('Assignment checklist checksum mismatch')
    plan=followup_plan(report)
    selected=set(plan if students is None else students)
    if not selected:raise ValueError('No unresolved students were selected for native follow-up')
    if selected-plan.keys():raise ValueError('Only unresolved students can be selected: '+', '.join(sorted(selected-plan.keys())))
    result=copy.deepcopy(report)
    with tempfile.TemporaryDirectory(prefix='grader-followup-') as tmp:
        root=Path(tmp)
        groups=group_submissions(io.BytesIO(source),root/'students')
        if selected-groups.keys():raise ValueError('Report refers to students absent from the ZIP')
        def extract(path,name):
            if not path:return None
            folder=root/name;budget=safe_extract(path,folder);unpack_nested(folder,budget)
            return folder
        common=extract(shared,'shared');personal=extract(resources,'resources')
        if personal and any(not p.is_dir() or p.name not in groups for p in personal.iterdir()):
            raise ValueError('Student resource folders must use exact Canvas student keys')
        for row in result['students']:
            if row['student'] not in selected:continue
            entries=plan[row['student']]
            # The previous environment remains available as evidence, not an active
            # failed attempt that can mask a later successful native verification.
            prior=[e for e in row['execution'] if e.get('file') in entries]
            row.setdefault('execution_history',[]).extend(prior)
            row['execution']=[e for e in row['execution'] if e.get('file') not in entries]
            row['source_diagnostics']=inspect_folder_sources(groups[row['student']])
            for entry in entries:
                print('Native check:',row['student'],entry,flush=True)
                try:
                    attempt=run_entrypoint(groups[row['student']],entry,timeout,common,
                        resources=personal/row['student'] if personal else None,mock=mock,mock_words=mock_words)
                except Exception as error:
                    attempt={'file':entry,'status':'CHECKER / STAGING ERROR','log':f'{type(error).__name__}: {error}','returncode':None}
                attempt.pop('artifact',None)
                if attempt.get('mock_used') and 'FAILED' in attempt.get('status','') and any(message in attempt.get('log','') for message in ('is not in list','list index out of range')):
                    attempt.update(status='MOCK INPUT / CODE MISMATCH — INCONCLUSIVE',failure_kind='mock_input_or_code_mismatch')
                attempt['runtime']='native Python'
                attempt['verification']={'archive_sha256':report['archive_sha256'],'timeout':timeout,'mock_requested':mock}
                row['execution'].append(attempt)
                from execution_summary import summarize_attempt
                outcome=summarize_attempt(attempt)
                print('  Result:',outcome['answer'],'—',outcome['reason'],flush=True)
    result['generated_at']=datetime.now(timezone.utc).isoformat(timespec='seconds')
    result['runtime_capabilities']={'edition':'browser results with native follow-up','native_followup':True}
    result['native_followup']={'students':sorted(selected),'original_sources_edited':False,
                              'note':'Active results for selected entrypoints are native tests. Earlier results are retained in execution_history. Completion decisions are preserved.'}
    if result.get('validation_notes'):result['prior_validation_notes']=result['validation_notes']
    result['validation_notes']=['Native follow-up attempted '+str(len(selected))+' unresolved students using the matching original ZIP. Active results apply to the recorded native runtime and inputs; earlier browser attempts remain in execution_history. No student computations, assertions or SSL setup calls were bypassed.']
    return prepare_report(result)


def make_colab_notebook(report):
    """Self-contained private handoff: allowlisted checker code and report data."""
    plan=followup_plan(report)
    snapshot=io.BytesIO()
    with zipfile.ZipFile(snapshot,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FOLLOWUP_FILES:archive.writestr(name,(ROOT/name).read_bytes())
    encoded_code=base64.b64encode(snapshot.getvalue()).decode('ascii')
    encoded_report=base64.b64encode(zlib.compress(json.dumps(report,ensure_ascii=False).encode())).decode('ascii')
    introduction=f'''# Finish unresolved checks with native Python

This notebook checks {len(plan)} unresolved student submissions using a free **CPU** Google Colab runtime. Google sign-in is required; free runtime availability and limits apply. No paid AI or GPU is needed.

1. Run **Install checker** below.
2. Run **Upload files**, and choose the original Canvas submissions ZIP. Optional shared course-files and student-resource ZIPs can be uploaded at the same time.
3. Review the selected students and settings. Check **EXECUTE_STUDENT_CODE**, then run **Check remaining submissions**.
4. Download the updated TA handoff ZIP. Import its `submission-report.json` into the Pages app under **Import native follow-up results** if you want to continue reviewing there.

The notebook contains your current report and checker source; keep it with the grading team. The Pages app does not upload anything to Google. Files are sent to your Colab session only when you upload them there. Use a fresh runtime without Drive mounts or secrets: student code runs with runtime permissions, and stripped child-process environment variables are not a security sandbox.

Native execution can expose additional real errors. Missing notebook variables still need correct definitions/exports in the submitted source. No code is repaired or errors bypassed. Mocks cover supported missing inputs/helpers only; completion decisions remain separate.
'''
    install=f'''#@title Install checker
import base64, io, json, pathlib, subprocess, sys, zipfile, zlib
checker = pathlib.Path('/content/autograder-native-followup')
checker.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(base64.b64decode({encoded_code!r}))) as bundle:
    bundle.extractall(checker)  # Allowlisted checker sources generated by the app.
sys.path.insert(0, str(checker))
REPORT = json.loads(zlib.decompress(base64.b64decode({encoded_report!r})))
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', *{NATIVE_PACKAGES!r}])
from native_followup import followup_plan, run_followup
from report_exports import handoff_files, handoff_zip
print('Ready. Unresolved students:', list(followup_plan(REPORT)))
'''
    upload='''#@title Upload files
from google.colab import files
uploaded = files.upload()
'''
    execute=f'''#@title Check remaining submissions
EXECUTE_STUDENT_CODE = False #@param {{type:"boolean"}}
MOCK_MISSING_INPUTS = True #@param {{type:"boolean"}}
TIMEOUT_SECONDS = 300 #@param {{type:"integer"}}
SHARED_COURSE_ZIP = "" #@param {{type:"string"}}
STUDENT_RESOURCES_ZIP = "" #@param {{type:"string"}}
STUDENT_KEYS = {', '.join(plan)!r} #@param {{type:"string"}}
STUDENTS = [key.strip() for key in STUDENT_KEYS.split(',') if key.strip()]
if not EXECUTE_STUDENT_CODE:
    raise ValueError('Review the permissions note, then check EXECUTE_STUDENT_CODE to run original student code.')
import hashlib, tempfile
matches = [content for content in uploaded.values() if hashlib.sha256(content).hexdigest()==REPORT['archive_sha256']]
if len(matches)!=1:
    raise ValueError('Upload the original Canvas submissions ZIP matching this report.')
with tempfile.TemporaryDirectory() as temporary:
    data = pathlib.Path(temporary)
    archive = data/'submissions.zip'; archive.write_bytes(matches[0])
    def optional_zip(name, target):
        if not name: return None
        if name not in uploaded: raise ValueError('Optional ZIP not uploaded: '+name)
        path=data/target; path.write_bytes(uploaded[name]); return path
    updated = run_followup(REPORT, archive, students=STUDENTS,
        shared=optional_zip(SHARED_COURSE_ZIP,'shared.zip'), resources=optional_zip(STUDENT_RESOURCES_ZIP,'resources.zip'),
        mock=MOCK_MISSING_INPUTS, timeout=TIMEOUT_SECONDS)
print(updated['execution_counts'])
import pandas as pd
display(pd.DataFrame([{{'Student':r['student'],'Runs?':r['run_summary']['answer'],'Reason':r['run_summary']['reason'],'Next step':r['run_summary']['next_step']}} for r in updated['students'] if r['student'] in STUDENTS]))
'''
    download='''#@title Download results
output=pathlib.Path('/content/native-followup-TA-handoff.zip')
output.write_bytes(handoff_zip(handoff_files(updated)))
files.download(str(output))
'''
    cells=[]
    for kind,source in [('markdown',introduction),('code',install),('code',upload),('code',execute),('code',download)]:
        cell={'cell_type':kind,'metadata':{},'source':source.splitlines(keepends=True)}
        if kind=='code':cell.update(execution_count=None,outputs=[]);cell['metadata']={'cellView':'form'}
        cell['id']=hashlib.sha256(source.encode()).hexdigest()[:12]
        cells.append(cell)
    return json.dumps({'nbformat':4,'nbformat_minor':5,'metadata':{'kernelspec':{'name':'python3','display_name':'Python 3'},'colab':{'name':'AutoGrader native follow-up.ipynb'}},'cells':cells},ensure_ascii=False).encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--submissions',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--student',action='append')
    parser.add_argument('--shared',type=Path)
    parser.add_argument('--resources',type=Path)
    parser.add_argument('--provided-inputs',action='store_true')
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--execute',action='store_true',help='Run student code in this disposable native runtime')
    args=parser.parse_args()
    report=json.loads(args.report.read_text())
    if not args.execute:
        print(json.dumps(followup_plan(report),indent=2));print('Plan only. Add --execute in a disposable native runtime to run these submissions.');return
    updated=run_followup(report,args.submissions,students=args.student,shared=args.shared,resources=args.resources,mock=not args.provided_inputs,timeout=args.timeout)
    from report_exports import handoff_files,handoff_zip
    args.output.mkdir(parents=True,exist_ok=True)
    files=handoff_files(updated)
    for name,content in files.items():(args.output/name).write_bytes(content)
    (args.output/'native-followup-TA-handoff.zip').write_bytes(handoff_zip(files))
    print(json.dumps(updated['execution_counts']))


if __name__=='__main__':main()
