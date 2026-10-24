from pathlib import Path
import tempfile
import unittest
from prerequisites import audit_prerequisites, preflight_submission, recovery_requirements
from grader_core import run_entrypoint


class PrerequisiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.student = self.root/'student'; self.student.mkdir()

    def test_audit_does_not_import_helpers_or_run_code(self):
        (self.student/'main.py').write_text('import very_specific_course_helper\nfrom pathlib import Path\nbook=Path("book.txt")\ntext=book.read_text()\nraise RuntimeError("must not run")')
        (self.student/'very_specific_course_helper.py').write_text('raise RuntimeError("must not import")')
        audit = audit_prerequisites(self.student)
        self.assertEqual(next(m for m in audit['modules'] if m['module']=='very_specific_course_helper')['status'], 'LOCAL FILE AVAILABLE')
        self.assertEqual(audit['inputs'][0]['path'], 'book.txt')
        self.assertIn('MISSING', audit['inputs'][0]['status'])

    def test_output_files_are_not_requested_as_inputs(self):
        (self.student/'main.py').write_text('open("output.txt","w").write("hi")\nfrom pathlib import Path\nPath("other.txt").open(mode="w")')
        self.assertEqual(audit_prerequisites(self.student)['inputs'], [])

    def test_missing_helper_and_data_then_resources_enable_real_execution(self):
        (self.student/'main.py').write_text('from unique_course_helper_123 import read_book\nassert read_book("book.txt") == "real text"\nprint("REAL_RESOURCES_OK")')
        before = preflight_submission(self.student)
        self.assertIn('MISSING', before['modules'][0]['status'])
        blocked = run_entrypoint(self.student, 'main.py', 10)
        self.assertIn('BLOCKED', blocked['status'])
        self.assertEqual(blocked['missing_resources'][0]['name'], 'unique_course_helper_123')
        resources = self.root/'resources'; resources.mkdir()
        (resources/'unique_course_helper_123.py').write_text('def read_book(path):\n    return open(path).read()')
        (resources/'book.txt').write_text('real text')
        after = preflight_submission(self.student, resources=resources)
        self.assertEqual(next(m for m in after['modules'] if m['module']=='unique_course_helper_123')['status'], 'LOCAL FILE AVAILABLE')
        run = run_entrypoint(self.student, 'main.py', 10, resources=resources)
        self.assertEqual(run['returncode'], 0, run['log'])
        self.assertIn('REAL_RESOURCES_OK',run['log'])
        self.assertFalse((self.student/'book.txt').exists())

    def test_each_student_gets_only_own_data(self):
        (self.student/'main.py').write_text('assert open("book.txt").read() == "student one"')
        one = self.root/'one'; one.mkdir(); (one/'book.txt').write_text('student one')
        two = self.root/'two'; two.mkdir(); (two/'book.txt').write_text('student two')
        self.assertEqual(run_entrypoint(self.student,'main.py',10,resources=one)['returncode'],0)
        self.assertNotEqual(run_entrypoint(self.student,'main.py',10,resources=two)['returncode'],0)

    def test_precedence_original_then_personal_then_shared(self):
        (self.student/'main.py').write_text('assert open("book.txt").read() == "personal"')
        shared=self.root/'shared'; shared.mkdir(); (shared/'book.txt').write_text('common')
        personal=self.root/'personal'; personal.mkdir(); (personal/'book.txt').write_text('personal')
        self.assertEqual(run_entrypoint(self.student,'main.py',10,shared,resources=personal)['returncode'],0)
        (self.student/'book.txt').write_text('original')
        (self.student/'main.py').write_text('assert open("book.txt").read() == "original"')
        self.assertEqual(run_entrypoint(self.student,'main.py',10,shared,resources=personal)['returncode'],0)

    def test_dynamic_paths_not_claimed_verified(self):
        (self.student/'main.py').write_text('import os\nopen(os.environ["DATA_FILE"]).read()')
        audit=audit_prerequisites(self.student)
        self.assertEqual(audit['inputs'],[])
        self.assertEqual(len(audit['dynamic_inputs']),1)

    def test_runtime_file_request_is_portable(self):
        needs=recovery_requirements("FileNotFoundError: [Errno 2] No such file or directory: '/tmp/grader-run-abc/submission/data/book.txt'")
        self.assertEqual(needs[0]['name'],'data/book.txt')

    def test_external_inputs_need_mapping_and_missing_input_is_not_success(self):
        (self.student/'main.py').write_text('open("/Users/student/book.txt").read()')
        self.assertIn('EXTERNAL',audit_prerequisites(self.student)['inputs'][0]['status'])
        blocked=run_entrypoint(self.student,'main.py',10)
        self.assertNotEqual(blocked['returncode'],0)
        resource=self.root/'data';resource.mkdir();(resource/'book.txt').write_text('genuine')
        rerun=run_entrypoint(self.student,'main.py',10,mapping={'/Users/student/book.txt':'book.txt'},resources=resource)
        self.assertEqual(rerun['returncode'],0,rerun['log'])


if __name__=='__main__': unittest.main()
