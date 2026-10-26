import unittest
from execution_diagnostics import inspect_source,recovery_rows


class DiagnosticTests(unittest.TestCase):
    def test_missing_exports_have_cell_locations_without_executing(self):
        source='''import marimo
app=marimo.App()
@app.cell
def _(CLEAR_URL, corpus):
    raise RuntimeError('MUST_NOT_RUN')
    return
'''
        result=inspect_source(source,'main.py')
        self.assertEqual([p['name'] for p in result['missing_notebook_variables']],['CLEAR_URL','corpus'])
        self.assertTrue(all(p['cell_line']==4 for p in result['missing_notebook_variables']))

    def test_real_exports_setup_and_nested_helpers_are_distinct(self):
        source='''import marimo
app=marimo.App()
with app.setup:
    GLOBAL=42
@app.cell
def _():
    from __future__ import annotations
    def helper():return missing
    corpus=[]
    return corpus, helper
@app.cell
def _(corpus, helper, GLOBAL, missing):
    return
'''
        self.assertEqual([p['name'] for p in inspect_source(source,'main.py')['missing_notebook_variables']],['missing'])

    def test_native_calls_are_detected_with_import_aliases(self):
        source='import subprocess as sp\nfrom truststore import inject_into_ssl as inject\nsp.check_call(["python","-V"])\ninject()'
        features=inspect_source(source,'main.py')['native_features']
        self.assertEqual([f['feature'] for f in features],['subprocess.check_call','truststore.inject_into_ssl'])

    def test_recovery_hides_source_hints_when_the_actual_run_succeeds(self):
        diagnostic=inspect_source('import subprocess\nsubprocess.run(["python","-V"])','main.py')
        report={'students':[{'student':'demo','execution':[{'file':'main.py','status':'BROWSER RUN SUCCEEDED'}],'source_diagnostics':[diagnostic]}]}
        self.assertEqual(recovery_rows(report),[])
        report['students'][0]['execution'][0]['status']='BLOCKED / UNSUPPORTED IN BROWSER'
        self.assertEqual(recovery_rows(report)[0]['Line'],2)
