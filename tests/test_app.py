from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest

UI = '''
import io, zipfile
import streamlit as st
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w') as z:
    z.writestr('demo_123_456_main.py', 'print("UI_EXECUTION_OK")')
class Upload(io.BytesIO):
    name='submissions.zip'
def upload(label, **kwargs):
    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None
st.file_uploader=upload
import app
app.main()
'''


class AppTests(unittest.TestCase):
    def test_render_review_decisions_and_execution(self):
        at = AppTest.from_string(UI).run(timeout=30)
        self.assertFalse(at.exception)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        self.assertFalse(at.exception)
        self.assertEqual(len(at.session_state['report']['rows']), 1)
        decisions = [s for s in at.selectbox if s.label == 'Completion decision']
        self.assertEqual(len(decisions), 6)
        decisions[0].select('Complete').run()
        self.assertEqual(at.session_state['report']['rows'][0]['requirements'][0]['review'], 'Complete')
        next(c for c in at.checkbox if c.label.startswith('Execute student code')).check().run()
        next(b for b in at.button if b.label == 'Run selected entrypoints').click().run(timeout=30)
        self.assertFalse(at.exception)
        result = at.session_state['report']['rows'][0]['execution'][0]
        self.assertEqual(result['returncode'], 0, result['log'])
        self.assertIn('UI_EXECUTION_OK', result['log'])

    def test_preflight_then_supply_personal_data_and_retry(self):
        script = UI.replace('print("UI_EXECUTION_OK")', 'assert open("book.txt").read() == "real data"')
        script = script.replace("    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None", "    if label == 'Student-specific files ZIP (optional)' and st.session_state.get('provide_resources'):\n        data=io.BytesIO()\n        with zipfile.ZipFile(data,'w') as z:\n            z.writestr('demo_123/book.txt','real data')\n        return Upload(data.getvalue())\n    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None")
        at = AppTest.from_string(script).run(timeout=30)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        next(b for b in at.button if b.label.startswith('Check missing prerequisites')).click().run(timeout=30)
        row = at.session_state['report']['rows'][0]
        self.assertEqual(row['execution'], [])
        self.assertIn('MISSING', row['prerequisites']['inputs'][0]['status'])
        at.session_state['provide_resources'] = True
        at.run()
        next(b for b in at.button if b.label.startswith('Check missing prerequisites')).click().run(timeout=30)
        self.assertEqual(at.session_state['report']['rows'][0]['prerequisites']['inputs'][0]['status'], 'AVAILABLE')
        next(c for c in at.checkbox if c.label.startswith('Execute student code')).check().run()
        next(b for b in at.button if b.label == 'Run selected entrypoints').click().run(timeout=30)
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['report']['rows'][0]['execution'][0]['returncode'], 0)

    def test_changed_assignment_requires_new_review(self):
        at = AppTest.from_string(UI).run(timeout=30)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        at.selectbox[0].select('Assignment 8: Reading Difficulty').run()
        self.assertFalse(at.exception)
        self.assertFalse(any(s.label == 'Completion decision' for s in at.selectbox))
        self.assertTrue(any('new review' in i.value for i in at.info))


if __name__ == '__main__': unittest.main()
