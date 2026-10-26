"""Plain-language, evidence-based run summaries; raw results remain available."""
import re


def input_inventory(result):
    provided = list((result.get('supplied_inputs') or {}).get('files', []))
    provided.extend((result.get('mocks') or {}).get('provided_files', []))
    provided.extend(item for item in (result.get('mocks') or {}).get('manifest', [])
                    if item.get('origin') == 'MOCK INPUT ALIAS' and item.get('original'))
    supplied_files = list(dict.fromkeys(item.get('original') or item.get('file', '') for item in provided))
    mock_files = [f'{item.get("file", "(unknown file)")} ({item.get("origin", "mock")})'
                  for item in (result.get('mocks') or {}).get('manifest', [])]
    return {'supplied_files': supplied_files, 'mock_files': mock_files}


def summarize_attempt(result):
    status=str(result.get('status',''))
    log=str(result.get('log',''))
    manifests=(result.get('mocks') or {}).get('manifest',[])
    mock=bool(result.get('mock_used') or manifests or status.startswith('MOCK '))
    inventory = input_inventory(result)
    inputs=('Supplied files + mocks' if inventory['supplied_files'] else 'Mock data / helpers') if mock else 'Supplied inputs'
    matches=re.findall(r'(?m)^([\w.]+(?:Error|Exception|UnsupportedBrowserFeature)):\s*([^\n]*)',log)
    error,detail=matches[-1] if matches else (str(result.get('exception_type') or ''),'')
    reason=(error.rsplit('.',1)[-1]+': '+detail).strip(': ')[:350] if error else ''
    out={'answer':'Unknown','category':'unknown','inputs':inputs,'reason':reason,'next_step':'Inspect the execution log.','original_inputs_verified':False,
         'uses_mocks':mock, **inventory}
    if 'RUN SUCCEEDED' in status or 'RAN SUCCESSFULLY' in status or 'SMOKE TEST PASSED' in status:
        out.update(answer='Yes — with mocks' if mock else 'Yes — with supplied inputs',category='ran',reason='Entrypoint finished without an unhandled error.',next_step='No execution fix needed for this test. Review assignment coverage separately.')
        if mock: out['next_step']='This smoke test passed. Original-data behavior remains unverified.'
        if mock and inventory['supplied_files']:
            out['next_step']='This test passed with supplied file references and the listed substitutes. Review the mock list and assignment coverage; the substituted components remain unverified.'
        if 'Browser marimo cell adapter' in str(result.get('mode','')):
            out['reason']='All supported cells finished using the browser adapter; native marimo behavior was not tested.'
        if any(m.get('origin')=='BUNDLED MOCK MODEL API' for m in manifests):
            out.update(answer='Yes — with mock model API',inputs='Supplied files + synthetic model replies / other mocks' if inventory['supplied_files'] else 'Synthetic model replies + mock inputs',reason='Code finished using a model-API test double. No real model or AI inference ran.',next_step='This checks the caller’s execution path only; real model behavior and output quality remain unverified.')
    elif not status or status.startswith('NOT RUN'):
        out.update(answer='Not tested',category='not_run',inputs='None',reason='No execution attempt was made.',next_step='Select an entrypoint and run it.')
        if 'NO UNAMBIGUOUS' in status:
            out.update(reason='No single runnable entrypoint was selected or detected.',next_step='Supply executable Python source, or select the main script. Output snapshots alone cannot be executed.')
        elif 'UNKNOWN STUDENT' in status:
            out.update(reason='Files could not be assigned to a student.',next_step='Check Canvas file names before selecting an entrypoint.')
    elif 'TIMEOUT' in status:
        out.update(answer='Unknown — time limit',reason='The worker reached its time limit; runtime loading is included.',next_step='Increase the time limit, then retry. No pass or failure of original code is established.')
    elif mock and any(m.get('format')=='synthetic prose' for m in manifests) and any(s in detail for s in ('substring not found','start and end markers','No chapter headings matched','phrase not found:')):
        out.update(answer='Unknown — mock text incompatible',reason='The code requires book-specific phrases, markers or chapter headings absent from the synthetic text.',next_step='Supply the matching original text. A larger synthetic corpus will not reproduce book-specific content.')
    elif 'INPUT / CODE MISMATCH' in status or result.get('failure_kind')=='mock_input_or_code_mismatch':
        out.update(answer='Unknown — input/code mismatch',reason=reason or 'A key or index expected by the code is missing from the mock inputs.',next_step='Check the expected keys/indices. Use compatible fixtures or original inputs; the traceback alone cannot identify whether code or mocks are at fault.')
    elif 'BLOCKED' in status or 'UNSUPPORTED' in status or 'UNAVAILABLE' in status:
        if 'ModuleNotFoundError' in log or 'ImportError' in log:
            out.update(answer='Unknown — dependency missing',reason=reason or 'A dependency is missing or unavailable in browser Python.',next_step='Supply the real helper or use a browser-compatible package. If it requires a native runtime, this browser test cannot determine the result.')
        elif 'FileNotFoundError' in log or 'No such file or directory' in log:
            out.update(answer='Unknown — input missing',reason=reason or 'A required input file was not available.',next_step='Supply that file or a compatible fixture. Automatic mocks do not cover every path or format.')
        else:
            out.update(answer='Unknown — browser limitation',reason=reason or 'The browser could not provide a required runtime feature.',next_step='This requires a supported browser API or native Python execution. Do not count it as failed student code.')
    elif 'ERROR' in status or 'FAILED' in status or 'SYNTAX' in status:
        out.update(answer='Error — mock test stopped' if mock else 'Error — test stopped',category='test_error',reason=reason or 'The tested entrypoint raised an error.',next_step='Inspect the traceback and reproduce with matching inputs. A mock-run error does not prove original-data failure.' if mock else 'Inspect the traceback for this tested input and runtime.')
    return out


def summarize_student(row):
    attempts=[summarize_attempt(e) for e in row.get('execution',[]) if isinstance(e,dict)]
    if not attempts:
        return summarize_attempt({})
    if len(attempts)==1:
        return attempts[0]
    priority={'test_error':3,'unknown':2,'not_run':1,'ran':0}
    chosen=max(attempts,key=lambda a:priority[a['category']]).copy()
    passed=sum(a['category']=='ran' for a in attempts)
    if passed!=len(attempts):
        chosen['reason']=f'{passed}/{len(attempts)} recorded attempts finished. '+chosen['reason']
    else:
        mock=any(a['uses_mocks'] for a in attempts)
        chosen['answer']='Yes — includes mock runs' if mock else 'Yes — with supplied inputs'
        chosen['reason']=f'All {len(attempts)} recorded attempts finished. Results apply only to their tested inputs/runtime.'
    chosen['inputs']='; '.join(dict.fromkeys(a['inputs'] for a in attempts))
    chosen['uses_mocks']=any(a['uses_mocks'] for a in attempts)
    for key in ('supplied_files','mock_files'):
        chosen[key]=list(dict.fromkeys(value for a in attempts for value in a[key]))
    return chosen


def summarize_batch(rows):
    counts={'ran':0,'test_error':0,'unknown':0,'not_run':0}
    for row in rows: counts[summarize_student(row)['category']]+=1
    return counts
