import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from grader_core import load_profile,profile_digest
from native_followup import FOLLOWUP_FILES,followup_plan,make_colab_notebook,run_followup


class NativeFollowupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            z.writestr('ok_123_456_main.py','raise RuntimeError("DO_NOT_RERUN_SUCCESS")')
            z.writestr('blocked_124_457_main.py','import subprocess,sys\nsubprocess.check_call([sys.executable,"-c","print(42)"])')
            z.writestr('bad_125_458_main.py','raise ValueError("REAL_ERROR_RETAINED")')
        self.archive=self.root/'submissions.zip';self.archive.write_bytes(buffer.getvalue())
        profile=load_profile(Path('assignments/assignment-8.json').read_bytes(),'assignment.json')
        self.report={'assignment':profile,'assignment_digest':profile_digest(profile),
            'archive_sha256':hashlib.sha256(buffer.getvalue()).hexdigest(),'students':[
                {'student':'ok_123','requirements':[{'id':'part-1','title':'Setup','review':'Complete','review_note':'Keep this decision'}],
                 'execution':[{'file':'main.py','status':'BROWSER RUN SUCCEEDED'}]},
                {'student':'blocked_124','requirements':[],'execution':[{'file':'main.py','status':'BLOCKED / UNSUPPORTED IN BROWSER'}]},
                {'student':'bad_125','requirements':[],'execution':[{'file':'main.py','status':'BROWSER EXECUTION ERROR','log':'ValueError: REAL_ERROR_RETAINED'}]}]}

    def test_plan_selects_only_unresolved_entrypoints(self):
        self.assertEqual(followup_plan(self.report),{'blocked_124':['main.py'],'bad_125':['main.py']})

    def test_real_native_process_and_real_error_keep_browser_history_and_reviews(self):
        original=copy.deepcopy(self.report)
        result=run_followup(self.report,self.archive,mock=False,timeout=15)
        self.assertEqual(self.report,original)
        rows={r['student']:r for r in result['students']}
        self.assertEqual(rows['ok_123']['execution'],original['students'][0]['execution'])
        self.assertEqual(rows['ok_123']['requirements'],original['students'][0]['requirements'])
        self.assertEqual(rows['blocked_124']['execution'][0]['returncode'],0)
        self.assertIn('42',rows['blocked_124']['execution'][0]['log'])
        self.assertEqual(rows['blocked_124']['execution_history'],original['students'][1]['execution'])
        self.assertNotEqual(rows['bad_125']['execution'][0]['returncode'],0)
        self.assertIn('REAL_ERROR_RETAINED',rows['bad_125']['execution'][0]['log'])
        self.assertEqual(result['execution_counts'],{'ran':2,'test_error':1,'unknown':0,'not_run':0})
        json.dumps(result)

    def test_wrong_archive_and_wrong_checklist_fail_before_execution(self):
        for key in ('archive_sha256','assignment_digest'):
            report=copy.deepcopy(self.report);report[key]='WRONG'
            with patch('grader_core.run_entrypoint') as run:
                with self.assertRaises(ValueError):run_followup(report,self.archive)
                run.assert_not_called()

    def test_unknown_selection_and_unsafe_entrypoints_are_rejected(self):
        with self.assertRaises(ValueError):run_followup(self.report,self.archive,students=['ok_123'])
        with self.assertRaises(ValueError):run_followup(self.report,self.archive,students=[])
        for entry in ('../main.py','/main.py','C:\\main.py'):
            report=copy.deepcopy(self.report);report['students'][1]['execution'][0]['file']=entry
            with self.assertRaises(ValueError):followup_plan(report)

    def test_uploaded_resources_are_scoped_to_the_selected_student(self):
        bundle=self.root/'resources.zip'
        with zipfile.ZipFile(bundle,'w') as z:z.writestr('blocked_124/book.txt','Only this student')
        def execute(folder,entry,timeout,shared,**kwargs):
            self.assertEqual((kwargs['resources']/'book.txt').read_text(),'Only this student')
            self.assertFalse((folder/'book.txt').exists())
            return {'file':entry,'status':'RAN SUCCESSFULLY','returncode':0,'log':''}
        with patch('grader_core.run_entrypoint',side_effect=execute):
            run_followup(self.report,self.archive,students=['blocked_124'],resources=bundle)

    def test_notebook_is_valid_and_embeds_only_allowlisted_checker_files(self):
        import base64,nbformat
        notebook=json.loads(make_colab_notebook(self.report))
        nbformat.validate(nbformat.from_dict(notebook))
        for cell in notebook['cells']:
            if cell['cell_type']=='code':ast.parse(''.join(cell['source']))
        install=''.join(notebook['cells'][1]['source'])
        # Inspect actual embedded archives rather than executing notebook setup.
        tree=ast.parse(install)
        payload=next(n.args[0].value for n in sorted(ast.walk(tree),key=lambda n:getattr(n,'lineno',0)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='b64decode')
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload))) as z:
            self.assertEqual(set(z.namelist()),set(FOLLOWUP_FILES))
            self.assertFalse(any('student' in name for name in z.namelist()))
        execute=''.join(notebook['cells'][3]['source'])
        self.assertIn('EXECUTE_STUDENT_CODE = False',execute)
        self.assertIn('Google sign-in',''.join(notebook['cells'][0]['source']))
