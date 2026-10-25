import unittest
from execution_summary import summarize_attempt,summarize_student,summarize_batch

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
