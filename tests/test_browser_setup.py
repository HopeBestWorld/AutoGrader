import ssl
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from browser_execution import run_browser_entry
from browser_setup import setup_requirements
from execution_summary import summarize_attempt


class BrowserSetupTests(unittest.TestCase):
    def run_code(self,source,files=None):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'main.py').write_text(source)
            for name,content in (files or {}).items():(root/name).write_text(content)
            result=run_browser_entry(root,'main.py',mock=True)
            self.assertEqual((root/'main.py').read_text(),source)
            for name,content in (files or {}).items():self.assertEqual((root/name).read_text(),content)
            return result

    def test_package_and_model_setup_are_checked_and_labeled(self):
        original=subprocess.check_call
        result=self.run_code('''import subprocess,sys
assert subprocess.check_call([sys.executable,'-m','pip','install','numpy','spacy'])==0
assert subprocess.check_call([sys.executable,'-m','spacy','download','en_core_web_sm'])==0
import en_core_web_sm
assert len(en_core_web_sm.load()('Application body runs.'))>0
''')
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertEqual(len(result['browser_adaptations']),2)
        self.assertFalse(result['original_native_setup_verified'])
        self.assertIs(subprocess.check_call,original)
        self.assertIn('adapted browser',summarize_attempt(result)['answer'])
        self.assertTrue(any(m['origin']=='BROWSER SETUP ADAPTATION' for m in result['mocks']['manifest']))

    def test_tls_adaptation_changes_no_ssl_state_and_restores_modules(self):
        original=ssl.SSLContext;prior=sys.modules.get('truststore')
        result=self.run_code('from truststore import inject_into_ssl\ninject_into_ssl()\nassert 2+2==4')
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertIs(ssl.SSLContext,original)
        self.assertIs(sys.modules.get('truststore'),prior)
        self.assertTrue(any(r['kind']=='system SSL setup' for r in result['browser_adaptations']))

    def test_unknown_processes_and_tls_apis_are_blocked(self):
        result=self.run_code('import subprocess,sys\nsubprocess.check_call([sys.executable,"-c","raise RuntimeError(\'MUST_NOT_EXECUTE\')"])')
        self.assertIn('BLOCKED',result['status']);self.assertIn('outside the reviewed',result['log'])
        result=self.run_code('import truststore\ntruststore.SSLContext(2)')
        self.assertIn('BLOCKED',result['status'])

    def test_application_failures_are_preserved_after_setup_adaptation(self):
        result=self.run_code('import truststore\ntruststore.inject_into_ssl()\nraise ValueError("ACTUAL_APPLICATION_ERROR")')
        self.assertIn('EXECUTION ERROR',result['status'])
        self.assertEqual(summarize_attempt(result)['category'],'test_error')
        self.assertIn('ACTUAL_APPLICATION_ERROR',result['log'])

    def test_provided_truststore_module_is_not_replaced(self):
        result=self.run_code('import truststore\nassert truststore.VALUE==42',{'truststore.py':'VALUE=42'})
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertEqual(result['browser_adaptations'],[])

    def test_source_setup_detection_does_not_execute_expressions(self):
        setup=setup_requirements('import subprocess as sp,sys\nsp.check_call([sys.executable,"-m","pip","install","spacy"])\nsp.check_call(dangerous_expression())')
        self.assertEqual(setup['packages'],['spacy'])

    def test_url_fallback_preserves_supplied_input_and_first_attempt(self):
        source='''import readability_tools as rt
text=rt.fetch_url('https://example.invalid/novel')
paragraphs=rt.clean_paragraphs(text,start_at='Here the real story begins',min_words=3)
end=paragraphs.index('Printed in Canada')
assert len(paragraphs[:end])>20
'''
        result=self.run_code(source,{'actual.txt':'Here the real story begins with the supplied book.\n\nIts printed tail differs.'})
        self.assertIn('RUN SUCCEEDED',result['status'],result['log'])
        self.assertTrue(result['synthetic_url_fallback'])
        self.assertEqual(result['original_input_attempt']['exception_type'],'ValueError')
        self.assertEqual(result['original_input_attempt']['failure_kind'],'mock_input_or_code_mismatch')
        self.assertIn('synthetic URL',summarize_attempt(result)['answer'])
        self.assertIn('Printed in Canada',result['mocks']['text_hints']['ends'])
        broken=self.run_code("import readability_tools as rt\nrt.fetch_url('https://example.invalid/novel')\nassert False, 'REAL_ASSERTION'",{'actual.txt':'Supplied book.'})
        self.assertNotIn('original_input_attempt',broken)
        self.assertIn('REAL_ASSERTION',broken['log'])

    def test_direct_chunk_index_increases_synthetic_capacity(self):
        from mock_inputs import infer_corpus_size
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'main.py').write_text('passages=chunk_paragraphs(docs,words_per_chunk=300)\nprint(passages[251])')
            sizing=infer_corpus_size(root,30000)
        self.assertEqual(sizing['largest_explicit_passage_index'],251)
        self.assertGreater(sizing['effective_words'],252*300)
        self.assertLessEqual(sizing['effective_words'],200000)
