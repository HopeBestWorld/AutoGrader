/* Worker lifecycle and bounded infrastructure recovery; student errors are results. */
const phaseLabels={loading_runtime:'loading Python',loading_packages:'loading packages',loading_application:'loading the app',preparing_files:'preparing files',running_code:'running submitted code',reviewing_submissions:'reviewing submissions',exporting_report:'creating the report'};
export const canRetryInterrupted=result=>result?.retryable===false?false:Boolean(result?.retryable)||/^(BROWSER (?:STARTUP |EXECUTION )?TIMEOUT|BROWSER RUNTIME BLOCKED)/.test(result?.status||'');

export function prepareWorkerRequest({payload,inputs,studentArchives,bundle}) {
 const request={...payload,bundle,protocol_version:2};
 if(payload.mode==='review')return {...inputs,...request};
 if(payload.mode==='run') {
  const archive=studentArchives?.[payload.student];
  if(archive)Object.assign(request,{submission_archive:archive.archive,submission_sha256:archive.sha256});
  else request.archive=inputs.archive; // Compatibility with older review workers.
 }
 return request;
}

export function splitReviewResult(result) {
 const {_execution_archives,...report}=result;
 return {report,studentArchives:_execution_archives||null};
}

export function planInterruptedRetries(execution) {
 const previous=new Map();
 for(const result of execution)if(result.file&&canRetryInterrupted(result)) {
  if(!previous.has(result.file))previous.set(result.file,[]);
  previous.get(result.file).push(result);
 }
 return {previous,entries:[...previous.keys()],retained:execution.filter(result=>!result.file||!canRetryInterrupted(result))};
}

export async function runWorkerJob({url,payload,onProgress=()=>{},executionTimeoutMs=120000,startupTimeoutMs=180000,automaticRetry=true,retryDelayMs=350,WorkerClass=globalThis.Worker,timers=globalThis,now=()=>performance.now()}) {
 const history=[];
 const attempt=()=>new Promise(resolve=>{
  let worker,timer,done=false,phase='loading_runtime',executionStarted=false,executionAt=null;
  const started=now();
  const cleanup=()=>{
   timers.clearTimeout(timer);
   if(worker){worker.onmessage=null;worker.onerror=null;worker.onmessageerror=null;worker.terminate();}
  };
  const finish=result=>{
   if(done)return;done=true;cleanup();
   if(payload.mode==='run')result.browser_runtime={phase,execution_started:executionStarted,startup_ms:Math.round((executionAt??now())-started),execution_ms:executionAt===null?0:Math.round(now()-executionAt),startup_limit_ms:startupTimeoutMs,execution_limit_ms:executionTimeoutMs};
   resolve(result);
  };
  const interrupted=(kind,detail)=>{
   const timeout=kind.includes('timeout');
   const status=timeout?(executionStarted?'BROWSER EXECUTION TIMEOUT — INCONCLUSIVE':'BROWSER STARTUP TIMEOUT — INCONCLUSIVE'):'BROWSER RUNTIME BLOCKED';
   const reason=timeout?(executionStarted?'Submitted code reached its execution time limit.':'Python or package loading reached the startup time limit.'):`Browser worker stopped while ${phaseLabels[phase]||phase}: ${detail}`;
   finish({file:payload.entry||'',status,failure_kind:kind,runtime_phase:phase,retryable:true,log:reason+'\n'+detail,
    outcome:{answer:timeout?(executionStarted?'Unknown — code time limit':'Unknown — loading time limit'):'Unknown — browser runtime stopped',category:'unknown',inputs:'Not verified',reason,next_step:executionStarted?'Retry interrupted runs. If the limit persists, inspect performance or use native Python.':'Retry interrupted runs; check browser memory and connectivity if startup keeps failing.'}});
  };
  const arm=(limit,kind)=>{timers.clearTimeout(timer);timer=timers.setTimeout(()=>interrupted(kind,`${limit/1000}-second ${executionStarted?'execution':'startup'} deadline.`),limit);};
  arm(startupTimeoutMs,'browser_startup_timeout');
  try {
   worker=new WorkerClass(url);
   worker.onmessage=({data})=>{
    if(!data||typeof data!=='object'){interrupted('browser_message_error','Worker returned an invalid message.');return;}
    if(data.type==='progress') {
     phase=data.phase;
     if(phase==='running_code'&&!executionStarted){executionStarted=true;executionAt=now();arm(executionTimeoutMs,'browser_execution_timeout');}
     onProgress({phase,label:phaseLabels[phase]||phase});return;
    }
    if(data.ok&&data.result&&typeof data.result==='object')finish(data.result);
    else {
     const detail=data.error||'The worker did not return error details.';
     if(data.retryable===false)finish({file:payload.entry||'',status:'BROWSER RUNTIME BLOCKED',failure_kind:data.failure_kind||'browser_job_error',action:data.action,retryable:false,runtime_phase:phase,log:detail,
      outcome:{answer:data.failure_kind==='browser_app_version_unavailable'?'Unknown — app refresh needed':'Unknown — browser job blocked',category:'unknown',inputs:'Not verified',reason:detail,next_step:data.action||'Inspect the reported input error and review the current uploads again.'}});
     else interrupted('browser_worker_error',detail);
    }
   };
   worker.onerror=event=>{
    event.preventDefault?.();
    const parts=[event.message,event.error?.stack,event.filename&&`${event.filename}:${event.lineno||0}:${event.colno||0}`].filter(Boolean);
    interrupted('browser_worker_error',parts.join('\n')||'The browser supplied no further error detail.');
   };
   worker.onmessageerror=()=>interrupted('browser_message_error','The browser could not transfer the worker result.');
   worker.postMessage(payload);
  }catch(error){interrupted('browser_worker_error',error?.stack||String(error));}
 });
 let result=await attempt();
 // Ordinary code errors do not request retry; infrastructure/resource limits may.
 // Execution timeouts require the explicit retry control; a fresh load may retry once.
 if(automaticRetry&&result.retryable&&result.failure_kind!=='browser_execution_timeout') {
  history.push(result);
  onProgress({phase:'retrying',label:'retrying once in a fresh worker'});
  if(retryDelayMs)await new Promise(resolve=>timers.setTimeout(resolve,retryDelayMs));
  result=await attempt();
 }
 if(history.length)result.infrastructure_history=[...(result.infrastructure_history||[]),...history];
 return result;
}
