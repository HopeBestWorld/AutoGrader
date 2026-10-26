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

    def test_provided_mode_matches_nested_and_laptop_paths_without_mocks(self):
        for requested in ('data/book.txt', '/Users/student/book.txt', r'C:\Users\student\book.txt'):
            with self.subTest(requested=requested):
                source = f'assert open({requested!r}).read()=="genuine book"'
                (self.student/'main.py').write_text(source)
                (self.student/'Book.TXT').write_text('genuine book')
                result = run_entrypoint(self.student, 'main.py', 10)
                self.assertEqual(result['returncode'], 0, result['log'])
                self.assertFalse(result['mock_used'])
                self.assertEqual(result['supplied_inputs']['files'][0]['original'], 'Book.TXT')
                self.assertEqual((self.student/'main.py').read_text(), source)
                self.assertFalse((self.student/'data').exists())

    def test_ambiguous_documents_are_not_selected_by_guess(self):
        (self.student/'main.py').write_text('open("missing/book.txt").read()')
        for folder in ('one', 'two'):
            (self.student/folder).mkdir()
            (self.student/folder/'book.txt').write_text(folder)
        for mock in (False, True):
            result = run_entrypoint(self.student, 'main.py', 10, mock=mock)
            self.assertNotEqual(result['returncode'], 0)
            self.assertFalse(result['supplied_inputs']['files'])
            self.assertIn('Multiple supplied files', result['supplied_inputs']['unresolved'][0]['reason'])
            self.assertFalse(result['mock_used'])

    def test_numbered_upload_document_is_used_and_multiple_versions_need_selection(self):
        source='from pathlib import Path\ndef read_uploaded(path="data/book.txt"):\n    p=Path(path)\n    return p.read_text()\nassert read_uploaded()=="ACTUAL VERSION"'
        (self.student/'main.py').write_text(source)
        (self.student/'book-1.txt').write_text('ACTUAL VERSION')
        result=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertFalse(result['mock_used'])
        self.assertEqual(result['supplied_inputs']['files'][0]['original'],'book-1.txt')
        self.assertIn('numbered-upload',result['supplied_inputs']['files'][0]['match'])
        self.assertEqual((self.student/'main.py').read_text(),source)
        (self.student/'book-2.txt').write_text('ANOTHER VERSION')
        result=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertNotEqual(result['returncode'],0)
        self.assertFalse(result['mock_used'])

    def test_local_writer_is_not_mistaken_for_an_input_reader(self):
        (self.student/'main.py').write_text('from pathlib import Path\ndef save(path):\n    with Path(path).open("w") as f:\n        f.write("created")\nsave("output.txt")')
        self.assertEqual(audit_prerequisites(self.student)['inputs'],[])

    def test_named_constant_guard_uses_unique_numbered_document_without_source_repair(self):
        source='''from pathlib import Path
def choose_book():
    LOCAL_FILE='Assignment8/book.txt'
    candidates=[Path(LOCAL_FILE)]
    local=next((path for path in candidates if path.exists()),None)
    return local.read_text() if local else UNDEFINED_FALLBACK
assert choose_book()=='ACTUAL SUPPLIED BOOK'
'''
        (self.student/'main.py').write_text(source)
        (self.student/'book-1.txt').write_text('ACTUAL SUPPLIED BOOK')
        result=run_entrypoint(self.student,'main.py',10,mock=False)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertFalse(result['mock_used'])
        self.assertEqual((self.student/'main.py').read_text(),source)
        self.assertFalse((self.student/'Assignment8').exists())
        matched=next(item for item in result['supplied_inputs']['files'] if item['requested_path']=='Assignment8/book.txt')
        self.assertEqual(matched['original'],'book-1.txt')
        self.assertIn('numbered-upload',matched['match'])

    def test_guard_constants_respect_cell_scope_parameters_and_conflicting_values(self):
        import ast
        from prerequisites import guarded_path_literals
        source='''from pathlib import Path
BOOK='global.txt'
def first():
    LOCAL_FILE='one/book.txt'
    return Path(LOCAL_FILE)
def second():
    LOCAL_FILE='two/book.txt'
    return Path(LOCAL_FILE)
def dynamic(BOOK):
    return Path(BOOK)
def reassigned(flag):
    if flag:
        LOCAL_FILE='one.txt'
    else:
        LOCAL_FILE='two.txt'
    return Path(LOCAL_FILE)
def global_reference():
    return Path(BOOK)
'''
        self.assertEqual([value for _,value in guarded_path_literals(ast.parse(source))],['one/book.txt','two/book.txt','global.txt'])


if __name__=='__main__': unittest.main()
