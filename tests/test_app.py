from pathlib import Path
import csv
import io
import json
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from streamlit.testing.v1 import AppTest

UI = '''
import io, zipfile
import streamlit as st
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w') as z:
    z.writestr(zipfile.ZipInfo('demo_123_456_main.py', date_time=(2020, 1, 1, 0, 0, 0)), 'print("UI_EXECUTION_OK")')
class Upload(io.BytesIO):
    name='submissions.zip'
def upload(label, **kwargs):
    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None
st.file_uploader=upload
import app
app.main()
'''


class AppTests(unittest.TestCase):
    def test_native_import_keeps_decisions_history_and_survives_browser_rerenders(self):
        script=UI.replace("    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None",'''    import hashlib,json
    from grader_core import load_profile,profile_digest
    from pathlib import Path
    profile=load_profile((Path(app.__file__).parent/'assignments/assignment-7.json').read_bytes(),'a.json')
    browser={'file':'main.py','status':'BROWSER RUNTIME BLOCKED','log':'Error'}
    if label=='Browser execution results (automatic)' and st.session_state.get('browser'):
        if st.session_state.get('new_browser'):browser={'file':'main.py','status':'BROWSER EXECUTION ERROR','log':'ValueError: NEW_BROWSER_ERROR'}
        payload={'archive_sha256':hashlib.sha256(buf.getvalue()).hexdigest(),'assignment_digest':profile_digest(profile),'students':[{'student':'demo_123','execution':[browser]}]}
        return Upload(json.dumps(payload).encode())
    if label=='Import native follow-up results (optional)' and st.session_state.get('native'):
        payload={'archive_sha256':hashlib.sha256(buf.getvalue()).hexdigest(),'assignment_digest':profile_digest(profile),
                 'native_followup':{'students':['demo_123']},'students':[{'student':'demo_123','execution':[{'file':'main.py','status':'RAN SUCCESSFULLY','runtime':'native Python','log':'NATIVE_OK'}],'execution_history':[browser]}]}
        return Upload(json.dumps(payload).encode())
    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None''')
        script=script.replace('app.main()',"from unittest.mock import patch\nwith patch('app.native_execution_available',return_value=False):\n    app.main()")
        at=AppTest.from_string(script).run(timeout=30)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        at.session_state['browser']=True;at.run(timeout=30)
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['report']['rows'][0]['execution'][0]['status'],'BROWSER RUNTIME BLOCKED')
        next(s for s in at.selectbox if s.label=='Completion decision').select('Complete').run()
        at.session_state['native']=True;at.run(timeout=30)
        next(b for b in at.button if b.label=='Apply native follow-up results').click().run(timeout=30)
        at.run(timeout=30)
        self.assertFalse(at.exception)
        row=at.session_state['report']['rows'][0]
        self.assertEqual(row['requirements'][0]['review'],'Complete')
        self.assertEqual(row['execution'][0]['log'],'NATIVE_OK')
        self.assertEqual(row['execution_history'][0]['status'],'BROWSER RUNTIME BLOCKED')
        at.session_state['new_browser']=True;at.run(timeout=30)
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['report']['rows'][0]['execution'][0]['log'],'ValueError: NEW_BROWSER_ERROR')
        self.assertNotIn('native_followup',at.session_state['report'])

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

    def test_mock_mode_keeps_original_failure_separate(self):
        script = UI.replace('print("UI_EXECUTION_OK")', 'print(open("missing.txt").read()[:30])')
        at = AppTest.from_string(script).run(timeout=30)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        next(c for c in at.checkbox if c.label.startswith('Execute student code')).check().run()
        next(b for b in at.button if b.label == 'Run selected entrypoints').click().run(timeout=30)
        before = at.session_state['report']['rows'][0]['execution'][0]
        self.assertIn('BLOCKED', before['status'])
        next(s for s in at.selectbox if s.label == 'Execution mode').select('Mock inputs — smoke test only').run()
        next(b for b in at.button if b.label == 'Run selected entrypoints').click().run(timeout=30)
        self.assertFalse(at.exception)
        row = at.session_state['report']['rows'][0]
        self.assertEqual(len(row['execution']), 2)
        original = next(e for e in row['execution'] if not e['mock_requested'])
        mocked = next(e for e in row['execution'] if e['mock_requested'])
        self.assertNotEqual(original['returncode'], 0)
        self.assertEqual(mocked['returncode'], 0, mocked['log'])
        self.assertTrue(mocked['status'].startswith('MOCK SMOKE TEST PASSED'))
        self.assertTrue(all(r['review'] == 'Needs review' for r in row['requirements']))

    def test_changed_assignment_requires_new_review(self):
        at = AppTest.from_string(UI).run(timeout=30)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        at.selectbox[0].select('Assignment 8: Reading Difficulty').run()
        self.assertFalse(at.exception)
        self.assertFalse(any(s.label == 'Completion decision' for s in at.selectbox))
        self.assertTrue(any('new review' in i.value for i in at.info))

    def test_ta_exports_reflect_latest_decisions_and_include_filtered_out_students(self):
        script = UI.replace("class Upload(io.BytesIO):", "    z.writestr('other_124_457_main.py', 'print(2)')\nclass Upload(io.BytesIO):")
        script = script.replace('import app\napp.main()', '''
original_download = st.download_button
st.session_state['downloads'] = {}
def capture_download(label, data, *args, **kwargs):
    st.session_state['downloads'][label] = data
    return original_download(label, data, *args, **kwargs)
import app
from unittest.mock import patch
with patch('streamlit.download_button', side_effect=capture_download):
    app.main()
''')
        at = AppTest.from_string(script).run(timeout=30)
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        self.assertFalse(at.exception)
        decisions = [s for s in at.selectbox if s.label == 'Completion decision']
        decisions[0].select('Complete').run()
        [s for s in at.selectbox if s.label == 'Completion decision'][1].select('Incomplete').run()
        [s for s in at.text_area if s.label == 'Reviewer note'][1].set_value('Please add the book title.').run()
        next(s for s in at.selectbox if s.label == 'Show run outcomes').select('Ran successfully').run()
        self.assertFalse(at.exception)
        with zipfile.ZipFile(io.BytesIO(at.session_state['downloads']['Download TA handoff ZIP'])) as archive:
            report = json.loads(archive.read('submission-report.json'))
            self.assertEqual(len(report['students']), 2)
            summary = report['students'][0]['completion_summary']
            self.assertEqual(summary['parts'][0]['status'], 'Complete (reviewed)')
            self.assertEqual(summary['parts'][1]['status'], 'Incomplete (reviewed)')
            self.assertEqual(summary['parts'][1]['reviewer_note'], 'Please add the book title.')
            checklist = list(csv.DictReader(io.StringIO(archive.read('part-checklist.csv').decode('utf-8-sig'))))
            self.assertEqual(len(checklist), 12)
            self.assertIn('Please add the book title.', archive.read('TA-report.html').decode())
        html=at.session_state['downloads']['Download shareable TA report (HTML)']
        self.assertIn(b'overview-body',html)
        self.assertIn(b'Please add the book title.',html)
        combined=list(csv.DictReader(io.StringIO(at.session_state['downloads']['Download combined results CSV'].decode('utf-8-sig'))))
        self.assertEqual(len(combined),2)
        self.assertEqual(combined[0]['1. '+report['assignment']['requirements'][0]['title']],'✅')
        table = next(frame.value for frame in at.dataframe if 'Assignment part' in frame.value.columns)
        self.assertEqual(table.iloc[0]['Status'], 'Complete (reviewed)')
        self.assertEqual(table.iloc[1]['Reviewer note'], 'Please add the book title.')


if __name__ == '__main__': unittest.main()
