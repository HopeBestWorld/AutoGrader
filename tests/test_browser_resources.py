import io
from pathlib import Path
import tempfile
import unittest
import zipfile
from browser_resources import resource_hashes,stage_browser_resources


def archive(files):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,text in files.items():z.writestr(name,text)
    return out.getvalue()


class BrowserResourceTests(unittest.TestCase):
    def test_original_personal_and_shared_precedence_and_student_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);groups={name:root/name for name in ('one_123','two_124')}
            for folder in groups.values():folder.mkdir()
            (groups['one_123']/'original.txt').write_text('ORIGINAL')
            common=archive({'helper.py':'VALUE=42','book.txt':'SHARED','original.txt':'SHARED REPLACEMENT'})
            personal=archive({'one_123/book.txt':'PERSONAL','one_123/private.txt':'PRIVATE'})
            result=stage_browser_resources(groups,root,common,personal)
            self.assertEqual((groups['one_123']/'original.txt').read_text(),'ORIGINAL')
            self.assertEqual((groups['one_123']/'book.txt').read_text(),'PERSONAL')
            self.assertEqual((groups['two_124']/'book.txt').read_text(),'SHARED')
            self.assertFalse((groups['two_124']/'private.txt').exists())
            self.assertEqual(result['configuration'],resource_hashes(common,personal))

    def test_unknown_student_and_unsafe_paths_fail_before_staging(self):
        for files in ({'wrong_999/file.txt':'WRONG'},{'../outside.txt':'BAD'}):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);folder=root/'student';folder.mkdir()
                with self.assertRaises(ValueError):stage_browser_resources({'one_123':folder},root,archive({'helper.py':'VALUE=42'}),archive(files))
                self.assertEqual(list(folder.iterdir()),[])
