"""Stage browser supporting uploads after evidence review, before execution ZIPs."""
import hashlib
import io
from pathlib import Path


def resource_hashes(shared=None,personal=None):
    return {'shared_zip_sha256':hashlib.sha256(shared).hexdigest() if shared is not None else None,
            'student_resources_sha256':hashlib.sha256(personal).hexdigest() if personal is not None else None}


def stage_browser_resources(groups,root,shared=None,personal=None):
    from grader_core import safe_extract,unpack_nested,stage_shared
    root=Path(root)
    def extract(content,name):
        if content is None:return None
        folder=root/name;budget=safe_extract(io.BytesIO(content),folder);unpack_nested(folder,budget)
        return folder
    common=extract(shared,'shared-course-files')
    specific=extract(personal,'student-resources')
    if specific:
        invalid=[p.name for p in specific.iterdir() if not p.is_dir() or p.name not in groups]
        if invalid:raise ValueError('Student resources require exact Canvas student folders. Unknown entries: '+', '.join(invalid))
    # Check the entire copy plan before changing student folders. Student files
    # take precedence, then personal files, then shared files as in native runs.
    size=sum(p.stat().st_size for folder in groups.values() for p in folder.rglob('*') if p.is_file())
    for student,folder in groups.items():
        planned=set()
        for bundle in (specific/student if specific else None,common):
            if bundle is None or not bundle.exists():continue
            for path in bundle.rglob('*'):
                if not path.is_file():continue
                relative=path.relative_to(bundle)
                if not (folder/relative).exists() and relative not in planned:
                    size+=path.stat().st_size;planned.add(relative)
    if size>128*1024**2:raise ValueError('Combined student execution files exceed 128 MiB. Reduce shared files or supply large data only to the students who need it.')
    staged={}
    for student,folder in groups.items():
        records=[]
        for bundle,origin in ((specific/student if specific else None,'student-specific resource bundle'),(common,'shared instructor files')):
            for event in stage_shared(bundle,folder):
                records.append({**event,'origin':origin})
        staged[student]=records
    return {'configuration':resource_hashes(shared,personal),'staged':staged}
