import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from grader_core import (completion_status, group_submissions, inspect_folder, load_profile,
                         profile_from_text, review_folder, run_entrypoint, safe_extract,
                         serializable_report, validate_profile)

ROOT = Path(__file__).resolve().parents[1]


def archive(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    buf.seek(0)
    return buf


class GraderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = load_profile((ROOT/'assignments/assignment-7.json').read_bytes(), 'a.json')

    def write(self, name, text):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_canvas_groups_late_and_nested_helpers(self):
        z = archive({'smith_LATE_12_34_notebook.zip': archive({'submission/app.py': 'print(42)', 'submission/data/book.txt': 'hello'}).getvalue(), 'smith_12_35_note.md': 'discussion', 'jones_13_36_main.py': 'print(1)'})
        groups = group_submissions(z, self.root)
        self.assertEqual(set(groups), {'smith_12', 'jones_13'})
        audit = inspect_folder(groups['smith_12'])
        self.assertEqual(len(audit['scripts']), 1)
        self.assertIn('note.md', audit['attachments'])

    def test_zip_traversal_and_symlink_rejected(self):
        for name in ['../escape', '/absolute', 'a/../../escape', 'C:\\escape']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_extract(archive({name: 'bad'}), self.root/'out')
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            info = zipfile.ZipInfo('link'); info.external_attr = 0o120777 << 16
            z.writestr(info, '/etc/passwd')
        b.seek(0)
        with self.assertRaises(ValueError): safe_extract(b, self.root/'links')

    def test_unknown_files_not_assigned_to_student(self):
        groups = group_submissions(archive({'random.py': 'print(1)'}), self.root)
        row = review_folder(groups['UNMATCHED_REVIEW'], self.profile, 'UNMATCHED_REVIEW')
        self.assertTrue(row['unmatched'])

    def test_pdf_style_sections_and_arbitrary_checklist(self):
        profile = profile_from_text('Assignment 99\nPart 1: Setup\nCreate a script.\nPart 2: Explain\nDiscuss your choices.')
        self.assertEqual(len(profile['requirements']), 2)
        self.assertIn('Discuss', profile['requirements'][1]['description'])
        self.assertEqual(len(profile_from_text('- Load data\n- Reflect')['requirements']), 2)

    def test_profile_validation(self):
        with self.assertRaises(ValueError): validate_profile({'title': 'a', 'requirements': []})
        with self.assertRaises(ValueError): validate_profile({'title': 'a', 'requirements': [{'id':'x','title':'one'},{'id':'x','title':'two'}]})

    def test_static_review_does_not_execute_and_headings_not_answers(self):
        self.write('main.py', 'raise RuntimeError("MUST NOT RUN")\nimport marimo\napp = marimo.App()\n@app.cell\ndef _(mo):\n    mo.md("## Part 3: Predictions")\n    return\n')
        row = review_folder(self.root, self.profile)
        self.assertEqual(row['execution'], [])
        self.assertFalse(next(r for r in row['requirements'] if r['id']=='part-3')['evidence'])
        self.assertEqual(completion_status(row), 'NEEDS REVIEW')

    def test_prose_evidence_and_review_decisions(self):
        self.write('main.py', 'import marimo as m\napp=m.App()\n@app.cell\ndef _(mo):\n    mo.md("I predict that positive and negative sentiment will reveal different emotional passages in this novel, but I expect irony to be difficult.")\n    return\n')
        row = review_folder(self.root, self.profile)
        part = next(r for r in row['requirements'] if r['id']=='part-3')
        self.assertEqual(part['evidence'][0]['kind'], 'prose')
        self.assertEqual(completion_status(row), 'NEEDS REVIEW')
        for r in row['requirements']: r['review'] = 'Complete'
        self.assertEqual(completion_status(row), 'COMPLETE (reviewed)')
        row['requirements'][0]['review'] = 'Incomplete'
        self.assertEqual(completion_status(row), 'INCOMPLETE (reviewed)')

    def test_plain_script_and_stderr_failure(self):
        self.write('main.py', 'print("REAL_EXECUTION_OK")')
        ok = run_entrypoint(self.root, 'main.py', 10)
        self.assertEqual(ok['returncode'], 0)
        self.assertIn('REAL_EXECUTION_OK', ok['log'])
        self.write('main.py', 'raise ValueError("real_failure")')
        bad = run_entrypoint(self.root, 'main.py', 10)
        self.assertNotEqual(bad['returncode'], 0)
        self.assertTrue(bad['status'].startswith('EXECUTION FAILED'))

    def test_missing_data_and_module_are_blockers(self):
        self.write('main.py', 'open("absent.txt").read()')
        self.assertIn('MISSING FILE', run_entrypoint(self.root, 'main.py', 10)['status'])
        self.write('main.py', 'import nonexistent_course_helper_123')
        self.assertIn('DEPENDENCY', run_entrypoint(self.root, 'main.py', 10)['status'])

    def test_shared_files_and_exact_mapping_preserve_original(self):
        shared = self.root/'instructor'; shared.mkdir()
        (shared/'helper.py').write_text('VALUE=42')
        folder = self.root/'student'; folder.mkdir()
        original = 'from helper import VALUE\nassert VALUE == 42\nassert open("/student/path/book.txt").read() == "actual data"\n'
        (folder/'main.py').write_text(original)
        (folder/'book.txt').write_text('actual data')
        result = run_entrypoint(folder, 'main.py', 10, shared, {'/student/path/book.txt':'book.txt'})
        self.assertEqual(result['returncode'], 0, result['log'])
        self.assertEqual((folder/'main.py').read_text(), original)
        self.assertEqual(len(result['path_changes']), 1)
        (folder/'helper.py').write_text('VALUE=99')
        result = run_entrypoint(folder, 'main.py', 10, shared, {'/student/path/book.txt':'book.txt'})
        self.assertNotEqual(result['returncode'], 0)
        self.assertIn('kept student', result['staged_files'][0]['action'])

    def test_mapping_and_entrypoint_cannot_escape(self):
        self.write('main.py', 'print(1)')
        with self.assertRaises(ValueError): run_entrypoint(self.root, '../outside.py')
        with self.assertRaises(ValueError): run_entrypoint(self.root, 'main.py', mapping={'x':'/etc/passwd'})

    def test_timeout_and_no_credential_inheritance(self):
        self.write('main.py', 'import time\ntime.sleep(30)')
        self.assertIn('TIMEOUT', run_entrypoint(self.root, 'main.py', 1)['status'])
        import os
        os.environ['GRADER_TEST_SECRET'] = 'not-for-students'
        self.addCleanup(os.environ.pop, 'GRADER_TEST_SECRET', None)
        self.write('main.py', 'import os\nassert "GRADER_TEST_SECRET" not in os.environ')
        self.assertEqual(run_entrypoint(self.root, 'main.py', 10)['returncode'], 0)

    def test_marimo_native_execution_and_failure(self):
        self.write('notebook.py', 'import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    import marimo as mo\n    mo.md("MARIMO_OUTPUT_OK")\n    return\nif __name__ == "__main__":\n    app.run()\n')
        result = run_entrypoint(self.root, 'notebook.py', 30)
        self.assertEqual(result['returncode'], 0, result['log'])
        self.assertIn(b'MARIMO_OUTPUT_OK', result['artifact'])
        self.write('notebook.py', 'import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    raise RuntimeError("CELL_ERROR_MUST_FAIL")\n    return\nif __name__ == "__main__":\n    app.run()\n')
        result = run_entrypoint(self.root, 'notebook.py', 30)
        self.assertNotEqual(result['returncode'], 0, result['log'])

    def test_jupyter_uses_prepared_interpreter_and_fails_on_cell_error(self):
        import nbformat
        nb = nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell('import pypdf\nprint("JUPYTER_OUTPUT_OK")')])
        self.write('work.ipynb', nbformat.writes(nb))
        result = run_entrypoint(self.root, 'work.ipynb', 30)
        self.assertEqual(result['returncode'], 0, result['log'])
        self.assertIn(b'JUPYTER_OUTPUT_OK', result['artifact'])
        nb.cells.append(nbformat.v4.new_code_cell('raise ValueError("NOTEBOOK_ERROR")'))
        self.write('work.ipynb', nbformat.writes(nb))
        result = run_entrypoint(self.root, 'work.ipynb', 30)
        self.assertNotEqual(result['returncode'], 0)

    def test_json_report_has_decisions_no_binary(self):
        self.write('main.py', 'print(1)')
        row = review_folder(self.root, self.profile)
        row['execution'] = [{'file':'main.py','status':'RAN', 'artifact':b'html'}]
        report = serializable_report(self.profile, [row])
        encoded = json.dumps(report)
        self.assertNotIn('artifact', encoded)
        self.assertEqual(report['students'][0]['completion'], 'NEEDS REVIEW')


if __name__ == '__main__': unittest.main()
