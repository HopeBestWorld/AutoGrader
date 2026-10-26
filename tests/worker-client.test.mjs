import test from 'node:test';
import assert from 'node:assert/strict';
import {runWorkerJob,prepareWorkerRequest,splitReviewResult,canRetryInterrupted,planInterruptedRetries} from '../web/worker-client.mjs';

function harness(){
 let time=0,sequence=0;const tasks=new Map(),workers=[];
 const timers={setTimeout(fn,ms){const id=++sequence;tasks.set(id,{at:time+ms,fn});return id;},clearTimeout(id){tasks.delete(id);}};
 const advance=ms=>{const end=time+ms;while(true){const next=[...tasks].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];if(!next)break;time=next[1].at;tasks.delete(next[0]);next[1].fn();}time=end;};
 class FakeWorker{
  constructor(url){this.url=url;this.terminated=false;workers.push(this);}
  postMessage(payload){this.payload=payload;}
  terminate(){this.terminated=true;}
  progress(phase){this.onmessage({data:{type:'progress',phase}});}
  result(result){this.onmessage({data:{ok:true,result}});}
 }
 const run=options=>runWorkerJob({url:'worker.js',payload:{mode:'run',entry:'main.py'},WorkerClass:FakeWorker,timers,now:()=>time,startupTimeoutMs:1000,executionTimeoutMs:100,automaticRetry:false,retryDelayMs:0,...options});
 return {workers,tasks,advance,run};
}

test('loading and execution have separate clocks and workers always close',async()=>{
 const h=harness();const promise=h.run();const worker=h.workers[0];
 worker.progress('loading_packages');h.advance(900);
 assert.equal(worker.terminated,false);
 worker.progress('running_code');h.advance(90);
 worker.result({file:'main.py',status:'BROWSER RUN SUCCEEDED'});
 const result=await promise;
 assert.equal(result.browser_runtime.startup_ms,900);
 assert.equal(result.browser_runtime.execution_ms,90);
 assert.equal(worker.terminated,true);assert.equal(worker.onmessage,null);assert.equal(h.tasks.size,0);
});

test('startup timeout identifies that submitted code never started',async()=>{
 const h=harness();const promise=h.run();h.advance(1000);const result=await promise;
 assert.equal(result.failure_kind,'browser_startup_timeout');
 assert.equal(result.browser_runtime.execution_started,false);
 assert.match(result.outcome.answer,/loading time limit/);assert.equal(h.workers[0].terminated,true);
});

test('execution timeout is inconclusive and does not automatically run code twice',async()=>{
 const h=harness();const promise=h.run({automaticRetry:true});h.workers[0].progress('running_code');h.advance(100);
 const result=await promise;assert.equal(result.failure_kind,'browser_execution_timeout');
 assert.equal(h.workers.length,1);assert.equal(result.retryable,true);assert.equal(h.tasks.size,0);
});

test('worker crash retries once, records the original error and releases resources',async()=>{
 const h=harness();const promise=h.run({automaticRetry:true});const first=h.workers[0];first.progress('loading_packages');
 first.onerror({message:'Error',filename:'worker.js',lineno:42,preventDefault(){}});
 await Promise.resolve();await Promise.resolve();
 assert.equal(h.workers.length,2);assert.equal(first.terminated,true);assert.equal(first.onerror,null);
 h.workers[1].progress('running_code');h.workers[1].result({file:'main.py',status:'BROWSER RUN SUCCEEDED'});
 const result=await promise;assert.match(result.infrastructure_history[0].log,/loading packages/);
 assert.match(result.infrastructure_history[0].log,/worker.js:42/);assert.equal(result.status,'BROWSER RUN SUCCEEDED');
 assert.equal(h.tasks.size,0);
});

test('a second infrastructure failure ends the retry without a third worker',async()=>{
 const h=harness();const promise=h.run({automaticRetry:true});h.workers[0].onmessageerror();
 await Promise.resolve();await Promise.resolve();h.workers[1].onmessageerror();
 const result=await promise;assert.equal(h.workers.length,2);assert.equal(result.infrastructure_history.length,1);
 assert.equal(result.failure_kind,'browser_message_error');assert.ok(h.workers.every(w=>w.terminated));
});

test('student exceptions and invalid input do not trigger infrastructure retries',async()=>{
 const h=harness();const promise=h.run({automaticRetry:true});h.workers[0].progress('running_code');
 h.workers[0].result({file:'main.py',status:'MOCK BROWSER EXECUTION ERROR',retryable:false,exception_type:'NameError'});
 const result=await promise;assert.equal(result.exception_type,'NameError');assert.equal(h.workers.length,1);
 const other=harness();const second=other.run({automaticRetry:true});
 other.workers[0].onmessage({data:{ok:false,error:'Unsafe ZIP entry',retryable:false}});
 const invalid=await second;assert.equal(other.workers.length,1);assert.match(invalid.log,/Unsafe ZIP entry/);
 assert.equal(canRetryInterrupted(invalid),false);
});

test('legacy interruptions can retry while dependency and code failures stay separate',()=>{
 assert.equal(canRetryInterrupted({status:'BROWSER RUNTIME BLOCKED'}),true);
 assert.equal(canRetryInterrupted({status:'BROWSER TIMEOUT — INCONCLUSIVE'}),true);
 assert.equal(canRetryInterrupted({status:'MOCK BLOCKED / UNSUPPORTED IN BROWSER',retryable:false}),false);
 assert.equal(canRetryInterrupted({status:'MOCK BROWSER EXECUTION ERROR'}),false);
});

test('execution sends only the selected student archive; exports carry no source archives',()=>{
 const inputs={archive:'COHORT',instructions:'ASSIGNMENT',preset:'8',shared_archive:'COURSE',student_resources_archive:'ALL_STUDENT_RESOURCES'};
 const selected={one:{archive:'STUDENT',sha256:'SHA'}};
 const payload=prepareWorkerRequest({payload:{mode:'run',student:'one',entry:'main.py'},inputs,studentArchives:selected,bundle:'app.json'});
 assert.equal(payload.submission_archive,'STUDENT');assert.equal(payload.submission_sha256,'SHA');
 assert.equal(payload.protocol_version,2);
 assert.equal('archive' in payload,false);assert.equal('instructions' in payload,false);
 assert.equal('shared_archive' in payload,false);assert.equal('student_resources_archive' in payload,false);
 const reviewed=prepareWorkerRequest({payload:{mode:'review'},inputs,bundle:'app.json'});
 assert.equal(reviewed.shared_archive,'COURSE');assert.equal(reviewed.student_resources_archive,'ALL_STUDENT_RESOURCES');
 const split=splitReviewResult({students:[{student:'one'}],_execution_archives:selected});
 assert.equal('_execution_archives' in split.report,false);assert.equal(split.studentArchives,selected);
 const exported=prepareWorkerRequest({payload:{mode:'export',report:split.report},inputs,studentArchives:selected,bundle:'app.json'});
 assert.equal('archive' in exported,false);assert.equal('submission_archive' in exported,false);
 assert.equal(JSON.stringify(exported).includes('STUDENT'),false);
});

test('retry planning retains completed and code-error attempts for the same file',()=>{
 const passed={file:'main.py',status:'BROWSER RUN SUCCEEDED'};
 const failed={file:'main.py',status:'BROWSER EXECUTION ERROR',retryable:false};
 const timeout={file:'main.py',status:'BROWSER EXECUTION TIMEOUT',retryable:true};
 const plan=planInterruptedRetries([passed,failed,timeout]);
 assert.deepEqual(plan.retained,[passed,failed]);assert.deepEqual(plan.entries,['main.py']);
 assert.deepEqual(plan.previous.get('main.py'),[timeout]);
});

test('an expired app bundle requests refresh without retrying or failing student code',async()=>{
 const h=harness();const promise=h.run({automaticRetry:true});h.workers[0].progress('loading_application');
 h.workers[0].onmessage({data:{ok:false,error:'Application bundle unavailable (404)',failure_kind:'browser_app_version_unavailable',action:'Save the report and refresh.',retryable:false}});
 const result=await promise;assert.equal(h.workers.length,1);assert.equal(result.outcome.category,'unknown');
 assert.equal(result.failure_kind,'browser_app_version_unavailable');assert.match(result.outcome.next_step,/refresh/);
});
