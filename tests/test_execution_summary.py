import unittest
from execution_summary import summarize_attempt,summarize_student,summarize_batch,retryable_browser_result

class ExecutionSummaryTests(unittest.TestCase):
    def test_mock_success_is_not_original_input_verification(self):
        r=summarize_attempt({'status':'MOCK BROWSER RUN SUCCEEDED — ORIGINAL INPUTS UNVERIFIED'})
        self.assertEqual(r['answer'],'Yes — with mocks');self.assertEqual(r['category'],'ran')
        self.assertFalse(r['original_inputs_verified'])
    def test_missing_resource_is_unknown_not_no(self):
        r=summarize_attempt({'status':'MOCK BLOCKED / UNSUPPORTED IN BROWSER','log':"ModuleNotFoundError: No module named 'course_helper'"})
        self.assertEqual(r['category'],'unknown');self.assertIn('course_helper',r['reason'])
    def test_book_marker_mismatch_is_unknown_when_text_was_synthesized(self):
        r=summarize_attempt({'status':'MOCK BROWSER EXECUTION ERROR','log':'ValueError: substring not found','mocks':{'manifest':[{'format':'synthetic prose'}]}})
        self.assertEqual(r['answer'],'Unknown — mock text incompatible')
        self.assertIn('original text',r['next_step'])
    def test_real_runtime_error_is_visible(self):
        r=summarize_attempt({'status':'BROWSER EXECUTION ERROR','log':'NameError: missing_variable is not defined'})
        self.assertEqual(r['category'],'test_error');self.assertIn('missing_variable',r['reason'])
    def test_partial_success_does_not_hide_other_failures(self):
        row={'execution':[{'status':'BROWSER RUN SUCCEEDED'},{'status':'BROWSER TIMEOUT — INCONCLUSIVE'}]}
        r=summarize_student(row);self.assertEqual(r['category'],'unknown');self.assertIn('1/2',r['reason'])
    def test_counts_preserve_all_students(self):
        rows=[{'execution':[]},{'execution':[{'status':'BROWSER RUN SUCCEEDED'}]}, {'execution':[{'status':'BROWSER EXECUTION ERROR'}]}, {'execution':[{'status':'BLOCKED / UNSUPPORTED IN BROWSER'}]}]
        self.assertEqual(summarize_batch(rows),dict(ran=1,test_error=1,unknown=1,not_run=1))
    def test_supplied_document_and_mock_helper_are_reported_separately(self):
        result={'status':'MOCK BROWSER RUN SUCCEEDED','mock_used':True,
                'supplied_inputs':{'files':[{'file':'data/book.txt','original':'book.txt'}]},
                'mocks':{'manifest':[{'file':'sentiment_toolkit.py','origin':'BUNDLED MOCK HELPER'}]}}
        summary=summarize_attempt(result)
        self.assertEqual(summary['inputs'],'Supplied files + mocks')
        self.assertEqual(summary['supplied_files'],['book.txt'])
        self.assertIn('sentiment_toolkit.py',summary['mock_files'][0])
        self.assertTrue(summary['uses_mocks'])
        aggregate=summarize_student({'execution':[result,{'status':'BROWSER RUN SUCCEEDED'}]})
        self.assertEqual(aggregate['answer'],'Yes — includes mock runs')
        self.assertEqual(aggregate['supplied_files'],['book.txt'])
    def test_provided_html_alias_names_actual_attachment_and_keeps_assumption(self):
        result={'status':'MOCK BROWSER RUN SUCCEEDED','mocks':{'manifest':[
            {'file':'book.html','original':'Provided novel.html','origin':'MOCK INPUT ALIAS'}]}}
        summary=summarize_attempt(result)
        self.assertEqual(summary['supplied_files'],['Provided novel.html'])
        self.assertEqual(summary['mock_files'],['book.html (MOCK INPUT ALIAS)'])
        self.assertTrue(summary['uses_mocks'])

    def test_loading_timeout_and_execution_timeout_are_not_student_failures(self):
        for kind,phrase in [('browser_startup_timeout','loading time limit'),('browser_execution_timeout','code time limit')]:
            result={'status':'BROWSER TIMEOUT — INCONCLUSIVE','failure_kind':kind,'retryable':True}
            summary=summarize_attempt(result)
            self.assertEqual(summary['category'],'unknown')
            self.assertIn(phrase,summary['answer'])
            self.assertTrue(retryable_browser_result(result))

    def test_legacy_generic_errors_and_known_code_errors_have_distinct_recovery(self):
        result={'status':'BROWSER RUNTIME BLOCKED','log':'Error'}
        self.assertIn('without detailed diagnostics',summarize_attempt(result)['reason'])
        self.assertTrue(retryable_browser_result(result))
        self.assertFalse(retryable_browser_result({'status':'BROWSER RUNTIME BLOCKED','retryable':False}))
        self.assertFalse(retryable_browser_result({'status':'MOCK BROWSER EXECUTION ERROR','exception_type':'NameError'}))
