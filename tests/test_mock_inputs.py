import json
from pathlib import Path
import tempfile
import unittest

from grader_core import load_profile, review_folder, run_entrypoint, serializable_report
from mock_inputs import prepare_mock_inputs


class MockInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.student = self.root/'student'; self.student.mkdir()

    def script(self, source):
        (self.student/'main.py').write_text(source)

    def test_missing_text_runs_only_in_mock_mode_and_original_is_preserved(self):
        source = 'from pathlib import Path\ntext=Path("missing.txt").read_text()\nassert len(text.split()) >= 15000\nprint("SYNTHETIC_SMOKE_OK")'
        self.script(source)
        original = run_entrypoint(self.student,'main.py',10)
        self.assertIn('BLOCKED',original['status'])
        mock = run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(mock['returncode'],0,mock['log'])
        self.assertEqual(mock['test_scope'],'MOCK SMOKE TEST')
        self.assertIn('ORIGINAL INPUTS UNVERIFIED',mock['status'])
        self.assertIn('SYNTHETIC_SMOKE_OK',mock['log'])
        self.assertEqual(mock['mocks']['manifest'][0]['file'],'missing.txt')
        self.assertFalse((self.student/'missing.txt').exists())
        self.assertEqual((self.student/'main.py').read_text(),source)

    def test_generated_csv_schema_and_json_lines(self):
        self.script('import pandas as pd\nimport json\ndf=pd.read_csv("table.csv")\nassert len(df["rating"]) == 24\nassert "text" in df\nassert len([json.loads(line) for line in open("records.jsonl")]) == 24')
        result=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertEqual(len(result['mocks']['manifest']),2)
        self.assertIn('rating',next(m for m in result['mocks']['manifest'] if m['file']=='table.csv')['schema'])

    def test_explicit_mock_helper_and_mock_data_run_together(self):
        self.script('from unique_mock_helper_123 import summarize\nassert summarize("book.txt") > 10')
        fixtures=self.root/'fixtures';fixtures.mkdir()
        (fixtures/'unique_mock_helper_123.py').write_text('def summarize(path):\n    return len(open(path).read())')
        (fixtures/'book.txt').write_text('This is explicitly synthetic fixture data.')
        result=run_entrypoint(self.student,'main.py',10,mock=True,mock_fixtures=fixtures)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertTrue(result['mock_used'])
        self.assertTrue(any(m.get('kind')=='helper test double' for m in result['mocks']['manifest']))
        self.assertTrue(all(m['origin']=='UPLOADED MOCK FIXTURE' for m in result['mocks']['manifest']))
        with self.assertRaises(ValueError):
            run_entrypoint(self.student,'main.py',10,mock_fixtures=fixtures)

    def test_unknown_helper_not_replaced_with_permissive_stub(self):
        self.script('from unknown_helper_api_xyz import run_everything\nrun_everything()')
        result=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertNotEqual(result['returncode'],0)
        self.assertFalse(result['mock_used'])
        self.assertIn('BLOCKED',result['status'])
        self.assertTrue(any('helpers are not invented' in u['reason'] for u in result['mocks']['unresolved']))

    def test_assertions_and_runtime_errors_are_not_suppressed(self):
        self.script('open("book.txt").read()\nassert False, "REAL_ASSERTION"')
        result=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertNotEqual(result['returncode'],0)
        self.assertTrue(result['status'].startswith('MOCK SMOKE TEST FAILED'))
        self.assertIn('REAL_ASSERTION',result['log'])

    def test_real_data_and_installed_modules_take_precedence(self):
        self.script('assert open("book.txt").read()=="real data"')
        (self.student/'book.txt').write_text('real data')
        fixtures=self.root/'fixtures';fixtures.mkdir()
        (fixtures/'book.txt').write_text('fake data')
        (fixtures/'json.py').write_text('raise RuntimeError("must not replace json")')
        result=run_entrypoint(self.student,'main.py',10,mock=True,mock_fixtures=fixtures)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertFalse(result['mock_used'])
        self.assertEqual(result['test_scope'],'PROVIDED INPUTS')
        self.assertEqual(len(result['mocks']['skipped']),2)

    def test_absolute_missing_literal_is_remapped_only_in_copy(self):
        source='assert len(open("/unavailable/student/book.txt").read()) > 100'
        self.script(source)
        result=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertEqual(len(result['mocks']['path_changes']),1)
        self.assertEqual((self.student/'main.py').read_text(),source)
        self.assertFalse(Path('/unavailable/student/book.txt').exists())

    def test_mock_text_is_deterministic(self):
        self.script('open("book.txt").read()')
        one=run_entrypoint(self.student,'main.py',10,mock=True)
        two=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(one['mocks']['manifest'][0]['sha256'],two['mocks']['manifest'][0]['sha256'])

    def test_unsupported_binary_format_and_remote_inputs_remain_unverified(self):
        self.script('open("secret.pkl","rb").read()\nimport pandas as pd\npd.read_csv("https://example.com/data.csv")')
        report=prepare_mock_inputs(self.student)
        self.assertEqual(report['manifest'],[])
        self.assertEqual(len(report['unresolved']),2)

    def test_mocked_notebook_artifacts_are_prominently_labeled(self):
        import nbformat
        source=nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('assert len(open("/missing/data.txt").read()) > 100\nprint("NOTEBOOK_MOCK_OK")')])
        (self.student/'main.ipynb').write_text(nbformat.writes(source))
        result=run_entrypoint(self.student,'main.ipynb',30,mock=True)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertEqual(result['artifact_name'],'mock-executed.ipynb')
        artifact=nbformat.reads(result['artifact'].decode(),as_version=4)
        self.assertEqual(artifact.metadata['grader_test_scope'],'MOCK SMOKE TEST')
        self.assertIn('NOT verified',artifact.cells[0].source)
        self.assertEqual((self.student/'main.ipynb').read_text(),nbformat.writes(source))

    def test_native_marimo_output_labeled_and_completion_unchanged(self):
        self.script('import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    import marimo as mo\n    mo.md(open("missing.txt").read()[:150])\n    return\nif __name__=="__main__":\n    app.run()')
        result=run_entrypoint(self.student,'main.py',30,mock=True)
        self.assertEqual(result['returncode'],0,result['log'])
        self.assertEqual(result['artifact_name'],'mock-executed.html')
        self.assertIn(b'MOCK SMOKE TEST OUTPUT',result['artifact'])
        profile=load_profile((Path(__file__).parents[1]/'assignments/assignment-7.json').read_bytes(),'a.json')
        row=review_folder(self.student,profile)
        row['execution']=[result]
        report=serializable_report(profile,[row])
        self.assertEqual(report['students'][0]['completion'],'NEEDS REVIEW')
        self.assertTrue(report['students'][0]['execution'][0]['mock_used'])
        self.assertNotIn('artifact',report['students'][0]['execution'][0])


    def test_missing_text_fixture_preserves_declared_boundary_markers(self):
        source='text=open("novel.txt").read()\nstart_marker="Once the remarkable story begins"\nend_marker="THE END OF THIS STORY"\nstart=text.index(start_marker)\nend=text.index(end_marker)\nassert end>start\nassert 2+2==4'
        self.script(source)
        r=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(r['returncode'],0,r['log'])
        self.assertIn('Once the remarkable story begins',r['mocks']['text_hints']['starts'])
        self.assertEqual((self.student/'main.py').read_text(),source)
    def test_unique_real_file_is_reused_without_faking_its_contents(self):
        self.script('assert open("data/book.txt").read()=="genuine supplied text"')
        (self.student/'book.txt').write_text('genuine supplied text')
        r=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertEqual(r['returncode'],0,r['log'])
        self.assertFalse(r['mock_used']);self.assertTrue(r['supplied_inputs']['files'])
        self.assertFalse((self.student/'data/book.txt').exists())

    def test_expected_output_assertion_is_not_satisfied_by_a_fabricated_input(self):
        self.script('from pathlib import Path\nassert Path("never_produced.txt").is_file(), "output was not produced"')
        r=run_entrypoint(self.student,'main.py',10,mock=True)
        self.assertNotEqual(r['returncode'],0)
        self.assertIn('output was not produced',r['log'])
        self.assertFalse(r['mock_used'])

if __name__=='__main__': unittest.main()
