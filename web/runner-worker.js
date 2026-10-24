/* A fresh worker is created for each job; termination enforces a wall-clock limit. */
importScripts('https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.js');
onmessage = async ({data}) => {
  try {
    const py = await loadPyodide({indexURL:'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/'});
    await py.loadPackage('micropip');
    await py.runPythonAsync("import micropip\nawait micropip.install(['pypdf==6.21.2', 'beautifulsoup4==4.15.0'])");
    const response = await fetch(new URL(data.bundle, self.location.href));
    if (!response.ok) throw Error('Application bundle unavailable');
    const bundle = await response.json();
    for (const [name, source] of Object.entries(bundle.files)) {
      const path='/app/'+name;
      py.FS.mkdirTree(path.substring(0,path.lastIndexOf('/')));
      py.FS.writeFile(path,source);
    }
    py.globals.set('job_json',JSON.stringify(data));
    await py.runPythonAsync(`import sys,json,base64,io,tempfile\nfrom pathlib import Path\nsys.path.insert(0,'/app')\njob=json.loads(job_json)\nfrom grader_core import group_submissions,load_profile,review_folder,serializable_report\nfrom browser_execution import run_browser_entry\nroot=Path(tempfile.mkdtemp())\ngroups=group_submissions(io.BytesIO(base64.b64decode(job['archive'])),root)\n`);
    if (data.mode==='run') {
      const imports=py.runPython("'\\n'.join(p.read_text(errors='replace') for p in groups[job['student']].rglob('*.py'))");
      await py.loadPackagesFromImports(imports); // Only Pyodide-supported distributions; no guessed PyPI installs.
    }
    const text=await py.runPythonAsync(`
if job['mode']=='review':
    if job.get('instructions'):
        profile=load_profile(base64.b64decode(job['instructions']),job['instructions_name'])
    else:
        profile=load_profile(Path('/app/assignments/assignment-'+job['preset']+'.json').read_bytes(),'checklist.json')
    rows=[review_folder(folder,profile,student) for student,folder in groups.items()]
    result=serializable_report(profile,rows)
    result['runtime_capabilities']={'edition':'browser execution subset','native_execution':False}
else:
    result=run_browser_entry(groups[job['student']],job['entry'],job.get('mock',False))
json.dumps(result)
`);
    postMessage({ok:true,result:JSON.parse(text)});
  } catch(error) { postMessage({ok:false,error:String(error)}); }
};
