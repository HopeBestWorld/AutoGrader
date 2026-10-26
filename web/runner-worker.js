/* One student per fresh worker. Progress separates runtime loading from execution. */
importScripts('https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js');
onmessage=async({data})=>{
 let currentPhase='loading_runtime',applicationUnavailable=false;
 const phase=value=>{currentPhase=value;if(data.protocol_version>=2)postMessage({type:'progress',phase:value});};
 try {
  phase('loading_runtime');
  const py=await loadPyodide({indexURL:'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/'});
  if(data.mode!=='export') {
   phase('loading_packages');
   await py.loadPackage('micropip');
   const requirements=data.mode==='review'?['pypdf==6.21.2','beautifulsoup4==4.15.0']:['beautifulsoup4==4.15.0'];
   py.globals.set('base_requirements_json',JSON.stringify(requirements));
   await py.runPythonAsync("import json,micropip\nawait micropip.install(json.loads(base_requirements_json))");
  }
  phase('loading_application');
  const response=await fetch(new URL(data.bundle,self.location.href));
  if(!response.ok){
   applicationUnavailable=response.status===404||response.status===410;
   throw Error('Application bundle unavailable ('+response.status+').'+(applicationUnavailable?' This tab may refer to an older deployment; save the current report and refresh the app.':''));
  }
  const bundle=await response.json();
  for(const [name,source] of Object.entries(bundle.files)) {
   const path='/app/'+name;py.FS.mkdirTree(path.substring(0,path.lastIndexOf('/')));py.FS.writeFile(path,source);
  }
  py.globals.set('job_json',JSON.stringify(data));
  if(data.mode==='export') {
   phase('exporting_report');
   const encoded=await py.runPythonAsync(`import sys,json,base64\nsys.path.insert(0,'/app')\nfrom report_exports import handoff_files,handoff_zip\nbase64.b64encode(handoff_zip(handoff_files(json.loads(job_json)['report']))).decode('ascii')`);
   postMessage({ok:true,result:{archive:encoded}});return;
  }
  phase('preparing_files');
  await py.runPythonAsync(`
import sys,json,base64,io,tempfile,hashlib,zipfile
from pathlib import Path
sys.path.insert(0,'/app')
job=json.loads(job_json)
from grader_core import group_submissions,load_profile,review_folder,serializable_report,safe_extract
from browser_execution import run_browser_entry
root=Path(tempfile.mkdtemp())
if job['mode']=='run' and job.get('submission_archive'):
    execution_bytes=base64.b64decode(job['submission_archive'])
    if job.get('submission_sha256') and hashlib.sha256(execution_bytes).hexdigest()!=job['submission_sha256']:
        raise ValueError('Student execution archive checksum mismatch')
    folder=root/'submission'
    safe_extract(io.BytesIO(execution_bytes),folder)
    groups={job['student']:folder}
else:
    execution_bytes=base64.b64decode(job['archive'])
    groups=group_submissions(io.BytesIO(execution_bytes),root)
`);
  if(data.mode==='run') {
   phase('loading_packages');
   const imports=py.runPython("'\\n'.join(p.read_text(errors='replace') for p in groups[job['student']].rglob('*.py'))");
   await py.loadPackage(['pandas','numpy']);
   if(/\b(?:spearman|kendall)\b/i.test(imports))await py.loadPackage('scipy');
   await py.runPythonAsync("await micropip.install('altair')");
   if(/\b(?:from|import)\s+vaderSentiment\b/.test(imports))await py.runPythonAsync("await micropip.install('vaderSentiment==3.3.2')");
   if(/\b(?:from|import)\s+pronouncing\b/.test(imports))await py.runPythonAsync("await micropip.install('pronouncing==0.3.0')");
   if(/\b(?:from|import)\s+pypdf\b/.test(imports))await py.runPythonAsync("await micropip.install('pypdf==6.21.2')");
   await py.loadPackagesFromImports(imports); // Only registered Pyodide distributions.
  }
  phase(data.mode==='run'?'running_code':'reviewing_submissions');
  const text=await py.runPythonAsync(`
if job['mode']=='review':
    if job.get('instructions'):
        profile=load_profile(base64.b64decode(job['instructions']),job['instructions_name'])
    else:
        profile=load_profile(Path('/app/assignments/assignment-'+job['preset']+'.json').read_bytes(),'checklist.json')
    rows=[review_folder(folder,profile,student) for student,folder in groups.items()]
    result=serializable_report(profile,rows)
    result['archive_sha256']=hashlib.sha256(execution_bytes).hexdigest()
    result['runtime_capabilities']={'edition':'browser execution subset','native_execution':False}
    # Session-only side channel; the UI removes these before any report/export.
    archives={}
    for student,folder in (groups.items() if job.get('protocol_version',0)>=2 else []):
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    info=zipfile.ZipInfo(str(path.relative_to(folder)),date_time=(2020,1,1,0,0,0))
                    archive.writestr(info,path.read_bytes(),compress_type=zipfile.ZIP_DEFLATED)
        payload=buffer.getvalue()
        archives[student]={'archive':base64.b64encode(payload).decode('ascii'),'sha256':hashlib.sha256(payload).hexdigest()}
    if job.get('protocol_version',0)>=2:result['_execution_archives']=archives
else:
    result=run_browser_entry(groups[job['student']],job['entry'],job.get('mock',False),job.get('mock_words',30000),job.get('model_apis',False))
    result['execution_input']={'scope':'one student' if job.get('submission_archive') else 'cohort archive (legacy)','sha256':hashlib.sha256(execution_bytes).hexdigest(),'bytes':len(execution_bytes)}
    from execution_summary import summarize_attempt
    result['outcome']=summarize_attempt(result)
json.dumps(result)
`);
  postMessage({ok:true,result:JSON.parse(text)});
 }catch(error) {
  const detail=[String(error),error?.stack].filter(Boolean).join('\n');
  postMessage({ok:false,error:detail,phase:currentPhase,retryable:!applicationUnavailable&&currentPhase!=='preparing_files'&&currentPhase!=='reviewing_submissions',
   failure_kind:applicationUnavailable?'browser_app_version_unavailable':undefined,action:applicationUnavailable?'Save the current report, refresh the app, then upload the ZIP again.':undefined});
 }
};
