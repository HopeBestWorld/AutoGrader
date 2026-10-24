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
            self.assertEqual(app['entrypoint'],'app.py')
            self.assertTrue((output/'.nojekyll').exists())
            html=(output/'index.html').read_text()
            self.assertIn(bundle.name,html)
            self.assertNotIn('__APP_BUNDLE__',html)
            self.assertIn('@stlite/browser@0.89.1',html)
            self.assertFalse(any('submission' in p for p in app['files']))
            before=bundle.read_bytes()
            module.build(output)
            self.assertEqual(before,bundle.read_bytes())

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
        self.assertTrue(next(c for c in at.checkbox if c.label.startswith('Execute student code')).disabled)
        self.assertEqual(len([s for s in at.selectbox if s.label=='Completion decision']),6)


if __name__=='__main__':unittest.main()
