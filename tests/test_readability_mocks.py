import sys
import tempfile
from pathlib import Path
import types
import unittest
from browser_execution import run_browser_entry

class ReadabilityMockTests(unittest.TestCase):
    def run_code(self, source, model_apis=False, files=None, mock=True):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'main.py').write_text(source)
            for name,content in (files or {}).items():(root/name).write_text(content)
            result=run_browser_entry(root,'main.py',mock=mock,model_apis=model_apis)
            self.assertEqual((root/'main.py').read_text(),source)
            for name,content in (files or {}).items():self.assertEqual((root/name).read_text(),content)
            return result

    def test_supplied_text_calculated_base_features_and_custom_measure(self):
        result=self.run_code('''import readability_tools as rt
text=open('book.txt').read()
assert text=='The traveler crossed a quiet river. The remarkable adventure continued.'
nlp=rt.load_parser()
docs=rt.parse_paragraphs(nlp,rt.clean_paragraphs(text),batch_size=2)
chunks=rt.chunk_paragraphs(docs,words_per_chunk=8)
measure=rt.Measure('length','Token length',lambda d:{t.i:len(t.text) for t in d if rt.is_word(t)})
table=rt.feature_table(chunks,[measure])
assert len(table)>0 and {'fk_grade','mean_depth','mean_dep_distance','length'} <= set(table)
assert table['length'].gt(0).all()
assert all(t.head.i<=t.i for d in docs for t in d)
assert 'clauses' in rt.sentence_table(rt.join_docs(docs))
assert 'MOCK' in rt.DEPTH.view_html(chunks[0])
''',files={'book.txt':'The traveler crossed a quiet river. The remarkable adventure continued.'})
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertEqual(result['supplied_inputs']['files'][0]['original'],'book.txt')
        self.assertTrue(any(m['file']=='readability_tools.py' for m in result['mocks']['manifest']))

    def test_bad_callback_and_unsupported_model_remain_errors(self):
        result=self.run_code('''import readability_tools as rt
def invalid(doc):raise RuntimeError('CALLER_ERROR')
rt.feature_table([rt.load_parser()('A sentence.')],[rt.Measure('depth','Custom depth',invalid)])''')
        self.assertIn('EXECUTION ERROR',result['status']);self.assertIn('CALLER_ERROR',result['log'])
        result=self.run_code("import readability_tools as rt\nrt.load_parser('unknown_model')")
        self.assertIn('No reviewed fixture',result['log'])
        self.assertEqual(result['failure_kind'],'unsupported_mock_api')
        result=self.run_code("import readability_tools as rt\ndef todo(doc):raise NotImplementedError('USER_TODO')\nrt.feature_table([rt.load_parser()('Some text.')],[rt.Measure('todo','Todo',todo)])")
        self.assertEqual(result['failure_kind'],'runtime_error')
        self.assertIn('USER_TODO',result['log'])

    def test_browser_memory_limit_is_inconclusive_and_retryable(self):
        result=self.run_code("raise MemoryError('runtime heap exhausted')")
        self.assertEqual(result['failure_kind'],'browser_resource_limit')
        self.assertTrue(result['retryable'])
        self.assertIn('BLOCKED',result['status'])

    def test_reference_and_model_fixtures_are_explicit_and_scoped(self):
        result=self.run_code('''import readability_tools as rt
reference=rt.load_clear()
assert len(reference)>=400 and {'excerpt','difficulty','category','author'} <= set(reference)
features=rt.feature_table(rt.load_parser().pipe(reference.excerpt))
predict=rt.difficulty_model(features,reference.difficulty)
assert len(predict(features))==len(reference)
raw=reference[['excerpt','difficulty']]
assert len(rt.difficulty_model(raw,raw.difficulty)(raw))==len(raw)
extra=features.head(1).assign(predicted_difficulty=0)
assert 'predicted_difficulty' not in rt.percentile_table(extra,features,rt.BUILTIN_MEASURES)
rt.trajectory_chart(features.head(3),'fk_grade','Grade',reference=features.fk_grade).to_dict()
tokenizer,model=rt.load_language_model()
doc=rt.load_parser()('This is synthetic model input.')
measure=rt.make_surprisal_measure(tokenizer,model,model_name='GPT-2')
assert rt.document_features(doc,[measure])['mean_surprisal']>0
assert measure.doc_value(doc)>0
''',model_apis=True)
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertTrue(any(m['origin']=='BUNDLED MOCK MODEL API' for m in result['mocks']['manifest']))
        self.assertTrue(any(m.get('format')=='synthetic CLEAR reference' for m in result['mocks']['manifest']))
        result=self.run_code('import readability_tools as rt\nrt.load_language_model()',model_apis=False)
        self.assertIn('Model-API fixtures were not enabled',result['log'])

    def test_provided_helper_wins_and_provided_mode_does_not_inject_helpers(self):
        result=self.run_code('import readability_tools as rt\nassert rt.value==123',files={'readability_tools.py':'value=123'})
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertFalse(any(m['file']=='readability_tools.py' for m in result['mocks']['manifest']))
        result=self.run_code('import readability_tools',mock=False)
        self.assertIn('BLOCKED',result['status'])
        self.assertFalse(result['mock_used'])

    def test_spacy_fixture_restores_host_module(self):
        previous=sys.modules.get('spacy');sentinel=types.ModuleType('spacy');sys.modules['spacy']=sentinel
        try:
            result=self.run_code("import spacy\nassert spacy.__version__=='0.0-mock'\nassert spacy.load('en_core_web_sm')('Some text.').text=='Some text.'")
            self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
            self.assertIs(sys.modules['spacy'],sentinel)
        finally:
            if previous is None:sys.modules.pop('spacy',None)
            else:sys.modules['spacy']=previous

    def test_cell_future_annotations_are_scoped_and_late_imports_still_fail(self):
        source='''import marimo
app=marimo.App()
@app.cell
def _():
    from __future__ import annotations
    def f(x:UnknownType):return x
    assert f.__annotations__['x']=='UnknownType'
    return (f,)
'''
        result=self.run_code(source)
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])
        self.assertTrue(result['adapter_assumptions'])
        ordinary=source+'''\n@app.cell
def _(f):
    def g(x:OtherUnknownType):return x
    return
'''
        self.assertIn('NameError',self.run_code(ordinary)['log'])
        late=source.replace('    from __future__ import annotations','    value=1\n    from __future__ import annotations')
        self.assertIn('SyntaxError',self.run_code(late)['log'])

    def test_app_functions_observe_exported_globals(self):
        result=self.run_code('''import marimo
app=marimo.App()
@app.function
def helper():return base+1
@app.cell
def _():
    base=41
    return (base,)
@app.cell
def _(helper,base):
    assert helper()==42
    return
''')
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])

    def test_guarded_path_uses_supplied_file_without_fabricating_outputs(self):
        result=self.run_code('''from pathlib import Path
candidates=[Path('data/book.txt')]
selected=next((p for p in candidates if p.exists()),None)
assert selected.read_text()=='REAL BOOK'
assert not Path('never-produced.txt').exists()
''',files={'book.txt':'REAL BOOK'})
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])
        self.assertFalse(result['mock_used'])

    def test_html_boundaries_word_limit_and_whole_paragraph_token_alignment(self):
        result=self.run_code('''import readability_tools as rt
assert rt.text_to_paragraphs('Wrapped line\\nwith words.')[0]=='Wrapped line with words.'
text='<html><p>Start <i>of</i> actual fiction has a quiet river.</p><p>The next real paragraph has more words.</p></html>'
paragraphs=rt.clean_paragraphs(text,start_at='Start of actual',min_words=1,max_words=10)
assert sum(len(p.split()) for p in paragraphs)==10
docs=rt.parse_paragraphs(rt.load_parser(),paragraphs)
chunks=rt.chunk_paragraphs(docs,words_per_chunk=3)
assert sum(len(d) for d in docs)==sum(len(d) for d in chunks)
import pandas as pd
reference=pd.read_csv('_mock_readability_clear.csv',index_col='id')
assert len(reference)==500
''')
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])

    def test_helper_url_can_use_one_supplied_book_with_recorded_identity_assumption(self):
        book='This is supplied prose about an actual fictional journey. '*300
        result=self.run_code('''import readability_tools as rt
text=rt.fetch_url('https://example.invalid/book')
assert text.startswith('This is supplied prose')
''',files={'actual-book.txt':book})
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        aliases=[m for m in result['mocks']['manifest'] if m['origin']=='MOCK INPUT ALIAS']
        self.assertEqual(aliases[0]['original'],'actual-book.txt')
        self.assertIn('unverified',aliases[0]['reason'])
        result=self.run_code("import readability_tools as rt\nassert 'SYNTHETIC SMOKE TEST' in rt.fetch_url('https://example.invalid/book')")
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertTrue(any(m.get('requested_path')=='https://example.invalid/book' and m['origin']=='AUTOMATIC SYNTHETIC DATA' for m in result['mocks']['manifest']))

    def test_missing_generated_cache_is_computed_by_student_code(self):
        result=self.run_code('''import pandas as pd
from pathlib import Path
def make_cache(path):
    if Path(path).exists():return pd.read_csv(path)
    frame=pd.DataFrame({'computed':[42]})
    frame.to_csv(path,index=False)
    return frame
result=make_cache('clear_reference.csv')
assert result['computed'].tolist()==[42]
assert pd.read_csv('clear_reference.csv')['computed'].tolist()==[42]
''')
        self.assertEqual(result['status'],'BROWSER RUN SUCCEEDED',result['log'])
        self.assertFalse(result['mock_used'])
        self.assertTrue(any(item['file']=='clear_reference.csv' for item in result['mocks']['skipped']))

if __name__=='__main__':unittest.main()
