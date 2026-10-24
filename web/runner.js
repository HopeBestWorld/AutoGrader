const panel=document.getElementById('browser-runner');
panel.innerHTML=`<h2>Check submissions — free browser execution</h2>
<p>Upload a Canvas ZIP and assignment instructions. No AI or account is required. Supported Python code runs in a fresh browser worker. Native packages, Jupyter kernels, interactive notebook features and unknown helper APIs may be unsupported.</p>
<label>Submissions ZIP <input id="run-zip" type="file" accept=".zip"></label>
<label>Assignment instructions <input id="run-instructions" type="file" accept=".pdf,.txt,.md,.json"></label>
<label>Fallback checklist <select id="run-preset"><option value="7">Assignment 7</option><option value="8">Assignment 8</option></select></label>
<label><input id="run-mock" type="checkbox" checked>Generate missing supported data files for mock smoke tests (original inputs unverified)</label>
<label>Worker time limit (includes runtime loading) <input id="run-timeout" type="number" min="15" max="600" value="120"> seconds</label>
<p>Only upload code you are authorized to test. Browser workers are isolated from the review app but code can access the network. Mock or browser success does not verify original-data/native behavior or assignment completion.</p>
<button id="run-review">1. Extract parts and review ZIP</button> <button id="run-code" disabled>2. Run detected entrypoints</button> <button id="run-download" disabled>Download combined report</button>
<p id="run-status" role="status" aria-live="polite"></p><div id="run-results"></div>`;
let report=null, inputs=null;
const status=message=>document.getElementById('run-status').textContent=message;
async function encoded(file) {
 if(!file)return null;
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
 for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 return btoa(binary);
}
function job(payload) {
 return new Promise((resolve,reject)=>{
  const workerUrl=new URL('./runner-worker.js',import.meta.url);workerUrl.searchParams.set('build',window.APP_BUILD||window.APP_BUNDLE);
  const worker=new Worker(workerUrl);
  const seconds=Math.min(600,Math.max(15,Number(document.getElementById('run-timeout').value)||120));
  const timer=setTimeout(()=>{worker.terminate();resolve({status:'BROWSER TIMEOUT — INCONCLUSIVE',log:'Worker terminated; runtime loading is included in the limit.'});},seconds*1000);
  worker.onmessage=({data})=>{clearTimeout(timer);worker.terminate();data.ok?resolve(data.result):reject(Error(data.error));};
  worker.onerror=error=>{clearTimeout(timer);worker.terminate();reject(Error(error.message));};
  worker.postMessage({...inputs,...payload,bundle:window.APP_BUNDLE});
 });
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
  title.textContent=student.student+' — '+found+'/'+student.requirements.length+' parts have candidate evidence (not verified completion) — '+(student.execution.map(e=>e.status).join('; ')||'NOT RUN');section.append(title);
  const choose=document.createElement('input');choose.type='checkbox';choose.checked=row.run_selected;choose.onchange=()=>row.run_selected=choose.checked;
  const label=document.createElement('label');label.append(choose,document.createTextNode(' Include this student'));section.append(label);
  const entryChoice=document.createElement('select');entryChoice.multiple=true;entryChoice.setAttribute('aria-label','Entrypoints for '+row.student);
  for(const script of row.scripts){const option=document.createElement('option');option.value=script.file;option.textContent=script.file;option.selected=row.browser_entrypoints.includes(script.file);entryChoice.append(option);}
  entryChoice.onchange=()=>row.browser_entrypoints=Array.from(entryChoice.selectedOptions,o=>o.value);section.append(entryChoice);
  for(const part of student.requirements){
   const p=document.createElement('p');
   p.textContent=part.title+': '+(part.evidence.length?(part.missing_evidence_types.length?'PARTIAL CANDIDATE EVIDENCE':'CANDIDATE EVIDENCE FOUND'):'NO MATCH FOUND')+' — needs review';section.append(p);
   for(const evidence of part.evidence){const pre=document.createElement('pre');pre.textContent=evidence.file+':'+evidence.line+'\n'+evidence.text;section.append(pre);}
  }
  for(const result of student.execution){const pre=document.createElement('pre');pre.textContent=JSON.stringify(result,null,2);section.append(pre);}
  root.append(section);
 }
}
for(const id of ['run-zip','run-instructions','run-preset'])document.getElementById(id).addEventListener('change',()=>{report=null;document.getElementById('run-code').disabled=true;document.getElementById('run-download').disabled=true;status('Inputs changed. Extract and review again.');});
document.getElementById('run-review').onclick=async()=>{
 const file=document.getElementById('run-zip').files[0],instructions=document.getElementById('run-instructions').files[0];
 if(!file){status('Choose a submissions ZIP.');return;}
 const button=document.getElementById('run-review');button.disabled=true;document.getElementById('run-code').disabled=true;
 try{
  inputs={archive:await encoded(file),instructions:await encoded(instructions),instructions_name:instructions?.name,preset:document.getElementById('run-preset').value};
  status('Loading browser Python and extracting assignment parts…');
  const result=await job({mode:'review'});
  if(!result.students)throw Error(result.log||'Review did not complete');
  report=result;render();document.getElementById('run-code').disabled=false;document.getElementById('run-download').disabled=false;
  status(`Reviewed ${report.students.length} student groups and ${report.assignment.requirements.length} assignment parts. Evidence is a search aid, not a completion verdict.`);
 }catch(error){status(String(error));}finally{button.disabled=false;}
};
document.getElementById('run-code').onclick=async()=>{
 const button=document.getElementById('run-code');button.disabled=true;document.getElementById('run-review').disabled=true;
 try{
  const mock=document.getElementById('run-mock').checked;
  for(const row of report.students){
   if(!row.run_selected)continue;
   row.execution=[];
   if(row.unmatched){row.execution.push({status:'NOT RUN — UNKNOWN STUDENT GROUP'});continue;}
   const entries=row.browser_entrypoints;
   if(!entries.length)row.execution.push({status:'NOT RUN — NO UNAMBIGUOUS SUPPORTED ENTRYPOINT'});
   for(const entry of entries){
    status('Running '+row.student+' / '+entry+' in a fresh browser worker…');
    try{row.execution.push(await job({mode:'run',student:row.student,entry,mock}));}
    catch(error){row.execution.push({file:entry,status:'BROWSER RUNTIME BLOCKED',log:String(error)});}
    render();
   }
  }
  render();
  for(const input of document.querySelectorAll('#root input[type=file]')) {
   if(input.closest('[data-testid="stFileUploader"]')?.textContent.includes('Browser execution results (automatic)')) {
    const transfer=new DataTransfer();transfer.items.add(new File([JSON.stringify(report)],'browser-results.json',{type:'application/json'}));
    input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));break;
   }
  }
  status('Browser checks completed. Review unsupported features, logs and candidate evidence; no parts were automatically marked complete.');
 }finally{button.disabled=false;document.getElementById('run-review').disabled=false;}
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
 if(!(input instanceof HTMLInputElement)||input.type!=='file'||!input.files.length)return;
 const uploader=input.closest('#root [data-testid="stFileUploader"]');
 if(!uploader)return;
 const label=uploader.textContent;
 if(label.includes('Canvas submissions ZIP')) {
  mirrorUpload('run-zip',input.files);copyReviewChecklist();
  status('Your ZIP and available checklist were copied from the Streamlit review panel. Click “1. Extract parts and review ZIP”, then “2. Run detected entrypoints” above.');
 } else if(label.includes('Assignment PDF, text checklist')) {
  mirrorUpload('run-instructions',input.files);
 }
},true);

document.addEventListener('click',async event=>{
 const button=event.target.closest('button');
 if(!button||!button.closest('#root')||button.textContent.trim()!=='Run browser checks (with mocks)')return;
 copyReviewChecklist();document.getElementById('run-mock').checked=true;
 const details=document.getElementById('browser-runner-details');if(details)details.open=true;
 if(document.getElementById('run-review').disabled)return;
 await document.getElementById('run-review').onclick();
 if(report&&!document.getElementById('run-code').disabled)await document.getElementById('run-code').onclick();
},true);
