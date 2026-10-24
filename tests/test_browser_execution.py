import tempfile
from pathlib import Path
import unittest
from browser_execution import run_browser_entry

class BrowserExecutionTests(unittest.TestCase):
    def run_code(self,source,mock=False):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'main.py').write_text(source)
            return run_browser_entry(Path(tmp),'main.py',mock)
    def test_plain_python_and_failure(self):
        self.assertEqual(self.run_code('assert 2+2==4')['status'],'BROWSER RUN SUCCEEDED')
        self.assertEqual(self.run_code('raise ValueError("real error")')['status'],'BROWSER EXECUTION ERROR')
    def test_actual_cell_dependencies_and_function_bodies(self):
        source='''import marimo
app=marimo.App()
@app.cell
def _(x):
    assert x==42
    return
@app.cell
def _():
    x=42
    return (x,)
if __name__=="__main__":
    app.run()
'''
        result=self.run_code(source)
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])
        self.assertEqual(result['cells_executed'],2)
    def test_unknown_ui_not_silently_stubbed(self):
        result=self.run_code('import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    import marimo as mo\n    x=mo.ui.slider(0,10)\n    return (x,)')
        self.assertEqual(result['status'],'BLOCKED / UNSUPPORTED IN BROWSER')
    def test_missing_data_and_mock_scope(self):
        self.assertEqual(self.run_code('open("absent.txt").read()')['status'],'BLOCKED / UNSUPPORTED IN BROWSER')
        result=self.run_code('assert len(open("absent.txt").read())>100',True)
        self.assertIn('MOCK BROWSER RUN SUCCEEDED',result['status'])
        self.assertIn('ORIGINAL INPUTS UNVERIFIED',result['status'])
    def test_duplicate_exports_fail(self):
        source='import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    x=1\n    return (x,)\n@app.cell\ndef _():\n    x=2\n    return (x,)'
        self.assertEqual(self.run_code(source)['status'],'BROWSER EXECUTION ERROR')
