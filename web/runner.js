const {runWorkerJob,prepareWorkerRequest,splitReviewResult,canRetryInterrupted,planInterruptedRetries}=await import('./worker-client.mjs?build='+encodeURIComponent(window.APP_BUILD||window.APP_BUNDLE));
const panel=document.getElementById('browser-runner');
panel.innerHTML=`<h2>Check submissions — free browser execution</h2>
<p>Upload a Canvas ZIP and assignment instructions. No AI or account is required. Supported Python code runs in a fresh browser worker. Native packages, Jupyter kernels, interactive notebook features and unknown helper APIs may be unsupported.</p>
<p>Each student’s bundled documents are used first. Mocks fill supported missing inputs. Supplied file references and substitutions are listed separately in the TA report.</p>
<p>Reviewed package/model setup and system SSL calls can use labeled browser adaptations. These test application code; the original native setup remains unverified. A mismatched supplied URL-document alias may retry once with a separate synthetic input, retaining the earlier attempt.</p>
<label>Submissions ZIP <input id="run-zip" type="file" accept=".zip"></label>
<label>Shared course-files ZIP (optional) <input id="run-shared" type="file" accept=".zip"></label>
<label>Student-specific files ZIP (optional) <input id="run-resources" type="file" accept=".zip"></label>
<label>Assignment instructions <input id="run-instructions" type="file" accept=".pdf,.txt,.md,.json"></label>
<label>Fallback checklist <select id="run-preset"><option value="7">Assignment 7</option><option value="8">Assignment 8</option></select></label>
<label><input id="run-mock" type="checkbox" checked>Generate missing supported data files for mock smoke tests (original inputs unverified)</label>
<label><input id="run-model-mock" type="checkbox" checked>Use the supported model-API test double when needed (synthetic replies, no real AI; model behavior unverified)</label>
<label>Synthetic corpus word target <input id="run-mock-words" type="number" min="100" max="200000" value="30000"> (increase for hard-coded passage indices; does not recreate the original text)</label>
<label>Code execution time limit <input id="run-timeout" type="number" min="15" max="600" value="120"> seconds</label>
<p>Python/package loading has a separate 3-minute deadline. Interrupted startup can retry once automatically.</p>
<p>Only upload code you are authorized to test. Browser workers are isolated from the review app but code can access the network. Mock or browser success does not verify original-data/native behavior or assignment completion.</p>
<button id="run-review">1. Extract parts and review ZIP</button> <button id="run-code" disabled>2. Run detected entrypoints</button> <button id="run-retry" disabled>Retry interrupted runs</button> <button id="run-handoff" disabled>Download TA handoff ZIP</button> <button id="run-download" disabled>Download combined report</button>
<p>The TA handoff ZIP includes an offline readable report and per-part spreadsheets. Use the main Streamlit downloads to include completion decisions you recorded there.</p>
<p id="run-status" role="status" aria-live="polite"></p><div id="run-results"></div>`;
let report=null, inputs=null, studentArchives=null, activeReview=false, activeExecution=false, inputVersion=0;
const status=message=>{
 document.getElementById('run-status').textContent=message;
 const live=document.getElementById('submission-run-progress');if(live)live.textContent=message;
};
function waitForReportImport(digest) {
 return new Promise(resolve=>{
  const ready=()=>document.querySelector('#root #browser-report-ready')?.dataset.resultDigest===digest;
  if(ready()){resolve(true);return;}
  const observer=new MutationObserver(()=>{if(ready()){clearTimeout(timer);observer.disconnect();resolve(true);}});
  const timer=setTimeout(()=>{observer.disconnect();resolve(false);},30000);
  observer.observe(document.getElementById('root'),{subtree:true,childList:true,attributes:true});
 });
}
async function encoded(file) {
 if(!file)return null;
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
 for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 return btoa(binary);
}
function job(payload) {
 const workerUrl=new URL('./runner-worker.js',import.meta.url);workerUrl.searchParams.set('build',window.APP_BUILD||window.APP_BUNDLE);
 const seconds=Math.min(600,Math.max(15,Number(document.getElementById('run-timeout').value)||120));
 const request=prepareWorkerRequest({payload,inputs,studentArchives,bundle:window.APP_BUNDLE});
 return runWorkerJob({url:workerUrl,payload:request,executionTimeoutMs:seconds*1000,automaticRetry:payload.mode==='run',
  onProgress:progress=>status((payload.mode==='run'?'Running '+payload.student+' / '+payload.entry:payload.mode==='review'?'Review':'Report')+' — '+progress.label+'…')});
}
function updateRetryButton() {
 document.getElementById('run-retry').disabled=activeReview||activeExecution||!report?.students.some(row=>row.execution.some(result=>result.file&&canRetryInterrupted(result)));
}
function plainSummary(row){
 const attempts=row.execution.map(e=>e.outcome||{answer:e.status?.startsWith('NOT RUN')?'Not tested':'Unknown — runtime stopped',category:e.status?.startsWith('NOT RUN')?'not_run':'unknown',inputs:'See run details',reason:e.status||'No result',next_step:'Inspect this run’s log.'});
 if(!attempts.length)return {answer:'Not tested',category:'not_run',inputs:'None',reason:'No execution attempt yet.',next_step:'Select an entrypoint and run it.'};
 const priority={ran:0,not_run:1,unknown:2,test_error:3};
 const worst=attempts.reduce((a,b)=>priority[a.category]>=priority[b.category]?a:b);
 if(attempts.length===1)return worst;
 const passed=attempts.filter(a=>a.category==='ran').length;
 return {...worst,reason:`${passed}/${attempts.length} attempts finished. `+worst.reason};
}
function render(){
 const root=document.getElementById('run-results');root.replaceChildren();
 for(const student of report.students){
  const row=student;
  row.run_selected ??= !row.unmatched;
  row.browser_entrypoints ??= row.scripts.some(s=>s.marimo)?row.scripts.filter(s=>s.marimo).map(s=>s.file):(row.entrypoints.length===1?row.entrypoints:[]);
  const section=document.createElement('details'),title=document.createElement('summary');
  const found=student.requirements.filter(p=>p.evidence.length&&!p.missing_evidence_types.length).length;
  student.automated_coverage={candidate_parts:found,total_parts:student.requirements.length,completion_verified:false};
  student.run_summary=plainSummary(student);
  title.textContent=student.student+' — '+student.run_summary.answer+' — '+found+'/'+student.requirements.length+' parts with candidate evidence';section.append(title);
  const explanation=document.createElement('p');explanation.textContent='Test inputs: '+student.run_summary.inputs+'. '+student.run_summary.reason+' Next step: '+student.run_summary.next_step;section.append(explanation);
  const choose=document.createElement('input');choose.type='checkbox';choose.checked=row.run_selected;choose.onchange=()=>row.run_selected=choose.checked;
  const label=document.createElement('label');label.append(choose,document.createTextNode(' Include this student'));section.append(label);
  const entryChoice=document.createElement('select');entryChoice.multiple=true;entryChoice.setAttribute('aria-label','Entrypoints for '+row.student);
  for(const script of row.scripts){const option=document.createElement('option');option.value=script.file;option.textContent=script.file;option.selected=row.browser_entrypoints.includes(script.file);entryChoice.append(option);}
  entryChoice.onchange=()=>row.browser_entrypoints=Array.from(entryChoice.selectedOptions,o=>o.value);section.append(entryChoice);
  const evidencePanel=document.createElement('details');const evidenceTitle=document.createElement('summary');evidenceTitle.textContent='Assignment evidence (separate from execution)';evidencePanel.append(evidenceTitle);
  for(const part of student.requirements){
   const p=document.createElement('p');
   p.textContent=part.title+': '+(part.evidence.length?(part.missing_evidence_types.length?'PARTIAL CANDIDATE EVIDENCE':'CANDIDATE EVIDENCE FOUND'):'NO MATCH FOUND')+' — needs review';evidencePanel.append(p);
   for(const evidence of part.evidence){const pre=document.createElement('pre');pre.textContent=evidence.file+':'+evidence.line+'\n'+evidence.text;evidencePanel.append(pre);}
  }
  section.append(evidencePanel);
  for(const result of student.execution){const details=document.createElement('details');const heading=document.createElement('summary');heading.textContent=(result.file||'(no entrypoint)')+' — technical details and traceback';details.append(heading);const pre=document.createElement('pre');pre.textContent=JSON.stringify(result,null,2);details.append(pre);section.append(details);}
  root.append(section);
 }
 report.execution_counts={ran:0,test_error:0,unknown:0,not_run:0};for(const row of report.students)report.execution_counts[row.run_summary.category]++;updateRetryButton();
}
for(const id of ['run-zip','run-shared','run-resources','run-instructions','run-preset'])document.getElementById(id).addEventListener('change',()=>{inputVersion++;report=null;studentArchives=null;updateRetryButton();document.getElementById('run-code').disabled=true;document.getElementById('run-download').disabled=true;document.getElementById('run-handoff').disabled=true;status('Inputs changed. Click Run submissions to check the current files.');});
document.getElementById('run-review').onclick=async()=>{
 const file=document.getElementById('run-zip').files[0],instructions=document.getElementById('run-instructions').files[0];
 if(!file){status('Choose a submissions ZIP.');return;}
 if(activeReview||activeExecution)return;
 const version=inputVersion;activeReview=true;updateRetryButton();
 const button=document.getElementById('run-review');button.disabled=true;document.getElementById('run-code').disabled=true;
 try{
  inputs={archive:await encoded(file),shared_archive:await encoded(document.getElementById('run-shared').files[0]),student_resources_archive:await encoded(document.getElementById('run-resources').files[0]),instructions:await encoded(instructions),instructions_name:instructions?.name,preset:document.getElementById('run-preset').value};
  status('Loading browser Python and extracting assignment parts…');
  const result=await job({mode:'review'});
  if(!result.students)throw Error(result.log||'Review did not complete');
  if(version!==inputVersion){status('Uploads changed; review the current inputs again.');return;}
  const review=splitReviewResult(result);report=review.report;studentArchives=review.studentArchives;render();document.getElementById('run-code').disabled=false;document.getElementById('run-download').disabled=false;document.getElementById('run-handoff').disabled=false;
  status(`Reviewed ${report.students.length} student groups and ${report.assignment.requirements.length} assignment parts. Evidence is a search aid, not a completion verdict.`);
 }catch(error){status(String(error));}finally{button.disabled=false;activeReview=false;updateRetryButton();}
};
async function runCode(retryOnly=false){
 if(!report||activeExecution||activeReview)return;
 const currentReport=report;activeExecution=true;updateRetryButton();
 const button=document.getElementById('run-code');button.disabled=true;document.getElementById('run-review').disabled=true;
 document.getElementById('run-handoff').disabled=true;document.getElementById('run-download').disabled=true;
 try{
  const mock=document.getElementById('run-mock').checked;
  for(const row of report.students){
   if(!row.run_selected)continue;
   const plan=retryOnly?planInterruptedRetries(row.execution):null;
   const previous=plan?.previous||new Map();
   if(!retryOnly)row.execution=[];
   if(row.unmatched){row.execution.push({file:'',status:'NOT RUN — UNKNOWN STUDENT GROUP'});continue;}
   const entries=retryOnly?[...previous.keys()]:row.browser_entrypoints;
   if(retryOnly&&!entries.length)continue;
   if(retryOnly)row.execution=plan.retained;
   if(!entries.length)row.execution.push({file:'',status:'NOT RUN — NO UNAMBIGUOUS SUPPORTED ENTRYPOINT'});
   for(const entry of entries){
    status('Running '+row.student+' / '+entry+' in a fresh browser worker…');
    try{
     const result=await job({mode:'run',student:row.student,entry,mock,model_apis:mock&&document.getElementById('run-model-mock').checked,mock_words:Math.min(200000,Math.max(100,Number(document.getElementById('run-mock-words').value)||30000))});
     if(report!==currentReport){status('Uploads changed; stopped the previous batch.');return;}
     if(previous.has(entry)){
      const history=previous.get(entry).flatMap(prior=>{const {infrastructure_history,...record}=prior;return [...(infrastructure_history||[]),record];});
      result.infrastructure_history=[...history,...(result.infrastructure_history||[])].slice(-5);
     }
     row.execution.push(result);
    }catch(error){row.execution.push({file:entry,status:'BROWSER RUNTIME BLOCKED',retryable:true,log:error?.stack||String(error)});}
    render();
    await new Promise(resolve=>setTimeout(resolve,250)); // Allow worker cleanup and UI repaint.
   }
  }
  render();
  let imported=false;
  for(const input of document.querySelectorAll('#root input[type=file]')) {
   if(input.closest('[data-testid="stFileUploader"]')?.textContent.includes('Browser execution results (automatic)')) {
    const content=JSON.stringify(report);
    const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(content))),b=>b.toString(16).padStart(2,'0')).join('');
    const transfer=new DataTransfer();transfer.items.add(new File([content],'browser-results.json',{type:'application/json'}));
    status('Browser runs finished. Updating the Streamlit table and TA downloads…');
    input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));
    imported=await waitForReportImport(digest);break;
   }
  }
  status(`Browser checks completed. ${report.execution_counts.ran} ran, ${report.execution_counts.test_error} had a test error, ${report.execution_counts.unknown} remain undetermined, ${report.execution_counts.not_run} were not tested. `+(imported?'The Streamlit table and TA downloads are updated.':'Use the downloads in browser diagnostics; the Streamlit import has not been confirmed.'));
 }finally{activeExecution=false;button.disabled=false;document.getElementById('run-review').disabled=false;document.getElementById('run-handoff').disabled=!report;document.getElementById('run-download').disabled=!report;updateRetryButton();}
}
document.getElementById('run-code').onclick=()=>runCode(false);
document.getElementById('run-retry').onclick=()=>runCode(true);
document.getElementById('run-handoff').onclick=async()=>{
 const button=document.getElementById('run-handoff');button.disabled=true;
 try{
  const result=await job({mode:'export',report});
  const bytes=Uint8Array.from(atob(result.archive),c=>c.charCodeAt(0));
  const url=URL.createObjectURL(new Blob([bytes],{type:'application/zip'}));
  const a=document.createElement('a');a.href=url;a.download='TA-handoff.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  status('TA handoff downloaded. Open TA-report.html from the ZIP, or share the ZIP with another TA. Evidence matches still need review.');
 }catch(error){status('Could not create TA handoff: '+String(error));}finally{button.disabled=false;}
};
document.getElementById('run-download').onclick=()=>{
 const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
 const a=document.createElement('a');a.href=url;a.download='browser-submission-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
};

// Share the user's existing uploads with the execution panel instead of requiring
// a second ZIP upload. Only user file-selection events inside Stlite are observed.
function mirrorUpload(target, files) {
 const transfer=new DataTransfer();
 for(const file of files) transfer.items.add(file);
 const input=document.getElementById(target);input.files=transfer.files;
 input.dispatchEvent(new Event('change',{bubbles:true}));
}
function copyReviewChecklist() {
 for(const textarea of document.querySelectorAll('#root textarea')) {
  const field=textarea.closest('[data-testid="stTextArea"]');
  if(field?.textContent.includes('Checklist JSON')) {
   try {
    const profile=JSON.parse(textarea.value);
    if(profile.title && Array.isArray(profile.requirements)) {
     mirrorUpload('run-instructions',[new File([JSON.stringify(profile)],'review-checklist.json',{type:'application/json'})]);
     return;
    }
   } catch(error) { /* The editable checklist is temporarily incomplete. */ }
  }
 }
}
document.addEventListener('change',event=>{
 const input=event.target;
 if(!(input instanceof HTMLInputElement)||input.type!=='file')return;
 const uploader=input.closest('#root [data-testid="stFileUploader"]');
 if(!uploader)return;
 const label=uploader.textContent;
 if(label.includes('Canvas submissions ZIP')) {
  mirrorUpload('run-zip',input.files);copyReviewChecklist();
  status('ZIP ready. Click Run submissions to begin.');
 } else if(label.includes('Assignment PDF, text checklist')) {
  mirrorUpload('run-instructions',input.files);
 } else if(label.includes('Shared course-files ZIP')) {
  mirrorUpload('run-shared',input.files);
 } else if(label.includes('Student-specific files ZIP')) {
  mirrorUpload('run-resources',input.files);
 }
},true);

document.addEventListener('click',async event=>{
 const button=event.target.closest('button');
 if(!button||!button.closest('#root'))return;
 const uploader=button.closest('[data-testid="stFileUploader"]')||button.closest('[data-testid="stElementContainer"]');
 if(uploader&&event.target.closest('[data-testid="stFileUploaderDeleteBtn"]')) {
  const text=uploader.textContent;
  if(text.includes('Shared course-files ZIP'))mirrorUpload('run-shared',[]);
  if(text.includes('Student-specific files ZIP'))mirrorUpload('run-resources',[]);
  if(text.includes('Canvas submissions ZIP'))mirrorUpload('run-zip',[]);
 }
 const label=button.textContent.trim();
 if(!['Run submissions','Retry interrupted runs'].includes(label))return;
 if(activeReview||activeExecution){event.preventDefault();event.stopImmediatePropagation();return;}
 if(label==='Retry interrupted runs') {
  if(!report||!studentArchives) {
   let previous=null;
   for(const input of document.querySelectorAll('#root input[type=file]')) {
    if(input.closest('[data-testid="stFileUploader"]')?.textContent.includes('Browser execution results (automatic)')&&input.files[0]) {
     try{previous=JSON.parse(await input.files[0].text());}catch(error){status('Cannot read prior browser results.');return;}
    }
   }
   if(!previous){status('Run submissions first to prepare the execution files.');return;}
   copyReviewChecklist();await document.getElementById('run-review').onclick();
   if(!report||report.archive_sha256!==previous.archive_sha256||report.assignment_digest!==previous.assignment_digest||JSON.stringify(report.browser_resources)!==JSON.stringify(previous.browser_resources||{shared_zip_sha256:null,student_resources_sha256:null})){status('Previous results belong to different inputs or supporting files. Run the current submissions again.');return;}
   const prior=new Map(previous.students.map(row=>[row.student,row]));
   for(const row of report.students)row.execution=prior.get(row.student)?.execution||[];
  }
  await runCode(true);return;
 }
 copyReviewChecklist();document.getElementById('run-mock').checked=true;
 const details=document.getElementById('browser-runner-details');if(details)details.open=false;
 await document.getElementById('run-review').onclick();
 if(report&&!document.getElementById('run-code').disabled)await runCode(false);
},true);
