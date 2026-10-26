import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from grader_core import run_entrypoint, safe_extract
from streamlit.testing.v1 import AppTest
from test_app import UI

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('build_pages',ROOT/'scripts/build_pages.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class PagesTests(unittest.TestCase):
    def test_bundle_contains_only_application_sources_and_checklists(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=module.build(Path(tmp)/'site')
            bundle=next(output.glob('app-bundle-*.json'))
            app=json.loads(bundle.read_text())
            self.assertEqual(set(app['files']),set(module.APP_FILES))
            from native_followup import FOLLOWUP_FILES
            self.assertTrue(set(FOLLOWUP_FILES)<=set(app['files']))
            self.assertEqual(app['entrypoint'],'app.py')
            self.assertTrue((output/'.nojekyll').exists())
            html=(output/'index.html').read_text()
            self.assertIn(bundle.name,html)
            self.assertNotIn('__APP_BUNDLE__',html)
            self.assertIn('@stlite/browser@0.89.1',html)
            self.assertTrue((output/'worker-client.mjs').exists())
            self.assertFalse(any('submission' in p for p in app['files']))
            before=bundle.read_bytes()
            module.build(output)
            self.assertEqual(before,bundle.read_bytes())

    def test_streamlit_browser_button_and_result_import(self):
        script=UI.replace("    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None", "    if label == 'Browser execution results (automatic)' and st.session_state.get('inject_result'):\n        import json,hashlib\n        from grader_core import load_profile,profile_digest\n        from pathlib import Path\n        profile=load_profile((Path(app.__file__).parent/'assignments/assignment-7.json').read_bytes(),'a.json')\n        payload={'archive_sha256':hashlib.sha256(buf.getvalue()).hexdigest(),'assignment_digest':profile_digest(profile),'students':[{'student':'demo_123','execution':[{'status':'BROWSER TIMEOUT — INCONCLUSIVE','log':'worker timeout'}]}]}\n        return Upload(json.dumps(payload).encode())\n    return Upload(buf.getvalue()) if label == 'Canvas submissions ZIP' else None")
        script=script.replace('app.main()',"from unittest.mock import patch\nwith patch('app.native_execution_available', return_value=False):\n    app.main()")
        at=AppTest.from_string(script).run(timeout=30)
        next(b for b in at.button if b.label=='Run submissions').click().run(timeout=30)
        self.assertFalse(at.exception)
        at.session_state['inject_result']=True
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['report']['rows'][0]['execution'][0]['status'],'BROWSER TIMEOUT — INCONCLUSIVE')
        self.assertEqual(at.session_state['report']['rows'][0]['execution'][0]['file'],'')
        self.assertFalse(any(b.label=='Retry interrupted runs' for b in at.button))

    def test_native_runner_explicitly_refuses_browser_execution(self):
        with patch('grader_core.native_execution_available',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'EXECUTION UNAVAILABLE IN BROWSER'):
                run_entrypoint('/nonexistent','main.py')

    def test_browser_review_remains_available_but_run_button_disabled(self):
        script=UI.replace('app.main()',"from unittest.mock import patch\nwith patch('app.native_execution_available', return_value=False):\n    app.main()")
        at=AppTest.from_string(script).run(timeout=30)
        self.assertFalse(at.exception)
        self.assertTrue(any('GitHub Pages edition' in item.value for item in at.info))
        next(b for b in at.button if b.label.startswith('Review submissions')).click().run(timeout=30)
        self.assertFalse(at.exception)
        self.assertEqual(len(at.session_state['report']['rows']),1)
        self.assertTrue(next(b for b in at.button if b.label=='Run selected entrypoints').disabled)
        self.assertFalse(any(s.label=='Execution mode' for s in at.selectbox))
        self.assertFalse(any(c.label.startswith('Execute student code') for c in at.checkbox))
        self.assertEqual(len([s for s in at.selectbox if s.label=='Completion decision']),6)


if __name__=='__main__':unittest.main()
