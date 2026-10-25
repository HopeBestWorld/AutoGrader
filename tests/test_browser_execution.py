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
        result=self.run_code('import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    import marimo as mo\n    x=mo.ui.unknown_widget(0,10)\n    return (x,)')
        self.assertEqual(result['status'],'BLOCKED / UNSUPPORTED IN BROWSER')
    def test_missing_data_and_mock_scope(self):
        self.assertEqual(self.run_code('open("absent.txt").read()')['status'],'BLOCKED / UNSUPPORTED IN BROWSER')
        result=self.run_code('assert len(open("absent.txt").read())>100',True)
        self.assertIn('MOCK BROWSER RUN SUCCEEDED',result['status'])
        self.assertIn('ORIGINAL INPUTS UNVERIFIED',result['status'])
    def test_duplicate_exports_fail(self):
        source='import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    x=1\n    return (x,)\n@app.cell\ndef _():\n    x=2\n    return (x,)'
        self.assertEqual(self.run_code(source)['status'],'BROWSER EXECUTION ERROR')

    def test_setup_and_nested_function_returns_are_not_cell_exports(self):
        source='''import marimo
app=marimo.App()
with app.setup:
    base=40
@app.function
def add(x):
    return x+2
@app.cell
def _(base,add):
    def nested(x):
        return x+1
    assert nested(add(base))==43
    return
'''
        result=self.run_code(source)
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])

    def test_bundled_sentiment_helper_is_mocked_not_real(self):
        source='from sentiment_toolkit import load_segments,load_lexicon,score_lexicon\nsegments=load_segments("book.txt")\nlexicon=load_lexicon("data/lex.csv")\nassert len(segments)>0\nassert score_lexicon("happy",lexicon)>0'
        result=self.run_code(source,True)
        self.assertIn('MOCK BROWSER RUN SUCCEEDED',result['status'],result['log'])
        self.assertTrue(any(m['origin']=='BUNDLED MOCK HELPER' for m in result['mocks']['manifest']))

    def test_display_tabs_evaluates_actual_body_without_mocking_computation(self):
        source='import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    import marimo as mo\n    assert 2+2==4\n    tabs=mo.ui.tabs({"one":mo.md("body executed")})\n    return (tabs,)'
        result=self.run_code(source)
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])
        self.assertIn('body executed',result['log'])

    def test_slider_runs_declared_default_and_records_scope(self):
        result=self.run_code('import marimo\napp=marimo.App()\n@app.cell\ndef _():\n    import marimo as mo\n    window=mo.ui.slider(1,25,value=8,label="Window")\n    assert window.value==8\n    return (window,)')
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])
        self.assertEqual(result['widget_defaults_tested'][0]['value'],8)

    def test_index_error_on_synthetic_data_is_inconclusive(self):
        result=self.run_code('open("book.txt").read()\nraise KeyError(109)',True)
        self.assertEqual(result['failure_kind'],'mock_input_or_code_mismatch')
        self.assertIn('INCONCLUSIVE',result['status'])
        self.assertIn('corpus size',result['action'])

    def test_explicit_passage_index_sizes_mock_corpus_without_editing_code(self):
        result=self.run_code('from sentiment_toolkit import load_segments\nsegments=load_segments("book.txt",words_per_segment=200)\ndef passage_evidence(index):\n    return segments.loc[index,"text"]\nassert passage_evidence(336)',True)
        self.assertIn('MOCK BROWSER RUN SUCCEEDED',result['status'],result['log'])
        self.assertEqual(result['mocks']['sizing']['largest_explicit_passage_index'],336)
        self.assertLessEqual(result['mocks']['sizing']['effective_words'],200000)
