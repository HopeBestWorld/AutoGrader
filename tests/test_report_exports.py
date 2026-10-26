import csv
import io
import json
import unittest
import zipfile

from bs4 import BeautifulSoup

from grader_core import serializable_report
from report_exports import (checklist_rows, csv_bytes, handoff_files, handoff_zip,
                            html_report, overview_rows, summarize_parts,compact_rows,prepare_report)


def fixture():
    parts = []
    for index, (decision, evidence, missing) in enumerate([
        ('Complete', True, []), ('Incomplete', True, []), ('Needs review', False, ['code', 'prose']),
        ('Needs review', True, ['prose']), ('Needs review', True, []), ('Not applicable', False, []),
    ]):
        parts.append({'id': f'part-{index}', 'title': f'Part {index}: Analyze', 'description': 'Analyze and explain.',
                      'criteria': ['Code is supplied.', 'Explain the findings.'], 'review': decision,
                      'review_note': 'Missing explanation' if decision == 'Incomplete' else '',
                      'missing_evidence_types': missing,
                      'evidence': [{'file': 'answer.py', 'location': 'line', 'line': 12, 'kind': 'code',
                                    'text': 'analyze(book)'}] if evidence else []})
    row = {'student': 'student_123', 'requirements': parts, 'execution': [
        {'file': 'answer.py', 'status': 'MOCK BROWSER RUN SUCCEEDED — ORIGINAL INPUTS UNVERIFIED',
         'mock_used': True, 'log': 'Done', 'returncode': 0}], 'unmatched': False}
    profile = {'title': 'Example assignment', 'requirements': [{k: p[k] for k in ('id', 'title', 'description', 'criteria')} for p in parts]}
    return {'assignment': profile, 'students': [row], 'archive_sha256': 'archive-hash', 'assignment_digest': 'checklist-hash'}


class ReportExportTests(unittest.TestCase):
    def test_parts_prioritize_reviewed_decisions_and_never_promote_evidence(self):
        report = fixture()
        summary = summarize_parts(report['students'][0])
        self.assertEqual([p['category'] for p in summary['parts']],
                         ['complete', 'incomplete', 'missing', 'partial', 'candidate', 'not_applicable'])
        self.assertEqual(summary['status'], 'Incomplete (reviewed)')
        self.assertEqual(len(summary['awaiting_review']), 3)
        self.assertEqual(summary['reviewed_complete'], ['Part 0: Analyze'])
        self.assertEqual([p['part_id'] for p in summary['attention']], ['part-1', 'part-2', 'part-3', 'part-4'])
        self.assertIn('prose', summary['parts'][3]['next_step'])
        self.assertIn('answer.py — line 12', summary['parts'][0]['evidence_locations'])
        self.assertEqual(report['students'][0]['requirements'][4]['review'], 'Needs review')

    def test_overview_and_detail_csv_include_every_part_notes_and_context(self):
        report = fixture()
        overview = overview_rows(report)
        self.assertEqual(overview[0]['Parts marked incomplete'], 'Part 1: Analyze')
        self.assertIn('with mocks', overview[0]['Runs?'])
        self.assertEqual(overview[0]['part-3: Part 3: Analyze'], '❌')
        rows = list(csv.DictReader(io.StringIO(csv_bytes(checklist_rows(report)).decode('utf-8-sig'))))
        self.assertEqual(len(rows), 6)
        self.assertEqual([row['Status'] for row in rows], ['✅', '❌', '❌', '❌', '✅', '➖'])
        self.assertEqual(rows[1]['Reviewer note'], 'Missing explanation')
        self.assertIn('Explain the findings.', rows[1]['Criteria to check'])
        self.assertEqual(rows[2]['Automatic evidence'], 'No evidence found')

    def test_csv_does_not_execute_formulas_in_student_content_or_headers(self):
        rows = [{'=header': '=HYPERLINK("https://example.com")', 'note': '  @SUM(A1)', 'student': '\t+1'},
                {'=header': '-1', 'note': '+cmd', 'student': 'normal_123'}]
        encoded = csv_bytes(rows)
        self.assertTrue(encoded.startswith(b'\xef\xbb\xbf'))
        parsed = list(csv.reader(io.StringIO(encoded.decode('utf-8-sig'))))
        self.assertEqual(parsed[0][0], "'=header")
        self.assertTrue(all(value.startswith("'") for value in parsed[1]))
        self.assertEqual(parsed[2][2], 'normal_123')

    def test_html_is_offline_escapes_student_content_and_links_to_unique_parts(self):
        report = fixture()
        attack = '</script><script>alert("student")</script><img src="https://example.com/leak">'
        report['validation_notes'] = [attack]
        row = report['students'][0]
        row['student'] = attack
        row['requirements'][0]['review_note'] = attack
        row['requirements'][0]['evidence'][0]['text'] = attack
        row['execution'][0]['log'] = attack
        row['requirements'][1]['title'] = row['requirements'][0]['title']
        html = html_report(report).decode()
        soup = BeautifulSoup(html, 'html.parser')
        self.assertEqual(len(soup.find_all('script')), 1)
        self.assertFalse(soup.select('[src]'))
        self.assertFalse(soup.select('img, iframe, object, form'))
        self.assertIn(attack, soup.get_text())
        self.assertIn('No evidence found does not prove', soup.get_text())
        self.assertIn('archive-hash', soup.get_text())
        self.assertIn('Validation scope', soup.get_text())
        ids = [tag['id'] for tag in soup.select('[id]')]
        self.assertEqual(len(ids), len(set(ids)))
        for link in soup.select('a[href^="#"]'):
            self.assertIn(link['href'][1:], ids)
        self.assertEqual(soup.select_one('.student ol a')['href'], '#student-0-part-1')

    def test_zip_is_self_contained_and_recomputes_stale_summaries(self):
        report = fixture()
        report['students'][0]['completion_summary'] = {'status': 'Complete (reviewed)'}
        report['students'][0]['completion'] = 'COMPLETE (reviewed)'
        report['students'][0]['run_summary'] = {'answer': 'Wrong stale result'}
        files = handoff_files(report)
        with zipfile.ZipFile(io.BytesIO(handoff_zip(files))) as archive:
            self.assertEqual(set(archive.namelist()), {'START-HERE.txt', 'TA-report.html', 'class-results.csv','student-overview.csv',
                                                      'part-checklist.csv', 'submission-report.json'})
            exported = json.loads(archive.read('submission-report.json'))
            self.assertEqual(exported['students'][0]['completion_summary']['status'], 'Incomplete (reviewed)')
            self.assertEqual(exported['students'][0]['completion'], 'INCOMPLETE (reviewed)')
            self.assertIn('with mocks', exported['students'][0]['run_summary']['answer'])
            self.assertIn('read-only', archive.read('START-HERE.txt').decode())
        self.assertEqual(report['students'][0]['completion_summary']['status'], 'Complete (reviewed)')

    def test_successful_mock_run_with_all_evidence_still_needs_review(self):
        report = fixture()
        for part in report['students'][0]['requirements']:
            part.update(review='Needs review', missing_evidence_types=[], evidence=[{'file': 'answer.py', 'line': 1}])
        summary = summarize_parts(report['students'][0])
        self.assertEqual(summary['status'], 'Needs review')
        self.assertEqual(summary['reviewed_complete'], [])
        self.assertEqual(summary['check_first'], [])
        self.assertEqual(len(summary['awaiting_review']), 6)
        combined = compact_rows(report)[0]
        self.assertEqual(combined['Parts marked ✅'], 6)
        self.assertEqual(combined['Parts marked ❌'], 0)
        self.assertEqual(combined['Check first'], 'None')
        self.assertNotIn('Needs review', combined.values())
        self.assertTrue(all(p['review'] == 'Needs review' for p in report['students'][0]['requirements']))

    def test_combined_csv_and_matrix_include_every_part_and_run_scope(self):
        report=fixture()
        rows=compact_rows(report)
        self.assertEqual(len(rows),1)
        self.assertIn('with mocks',rows[0]['Run result'])
        self.assertEqual(rows[0]['1. Part 0: Analyze'],'✅')
        self.assertEqual(rows[0]['2. Part 1: Analyze'],'❌')
        self.assertEqual(rows[0]['5. Part 4: Analyze'],'✅')
        self.assertEqual(rows[0]['Parts marked ✅'],2)
        self.assertEqual(rows[0]['Parts marked ❌'],3)
        self.assertEqual(rows[0]['Check first'],'Part 1: Analyze; Part 2: Analyze; Part 3: Analyze')
        soup=BeautifulSoup(html_report(report),'html.parser')
        self.assertEqual(len(soup.select('.matrix thead th')),9)
        cells=soup.select('.matrix .part-cell')
        self.assertEqual(len(cells),6)
        self.assertEqual([cell.select_one('a')['class'][1] for cell in cells],['complete','incomplete','missing','partial','candidate','not_applicable'])
        self.assertTrue(soup.select_one('#csv-download'))
        self.assertNotIn('Download original',soup.get_text())

    def test_omitted_assessment_cannot_make_partial_record_complete(self):
        report=fixture();original=report['students'][0]['requirements'][:1]
        report['students'][0]['requirements']=original
        prepared=prepare_report(report)
        row=prepared['students'][0]
        self.assertEqual(row['requirements'],original)
        self.assertEqual(len(row['completion_summary']['parts']),6)
        self.assertEqual(row['completion_summary']['status'],'Needs review')
        self.assertEqual(row['completion_summary']['counts']['unassessed'],5)
        self.assertEqual(compact_rows(report)[0]['2. Part 1: Analyze'],'❌')
        soup=BeautifulSoup(html_report(report),'html.parser')
        self.assertEqual(len(soup.select('.matrix .part-cell .unassessed')),5)
        self.assertEqual(len(report['students'][0]['requirements']),1)

    def test_empty_and_not_applicable_checklists_are_not_reported_as_complete(self):
        self.assertEqual(summarize_parts({'requirements': []})['status'], 'Needs review')
        report = fixture()
        for part in report['students'][0]['requirements']:
            part['review'] = 'Not applicable'
        serialized = serializable_report(report['assignment'], report['students'])
        row = serialized['students'][0]
        self.assertEqual(row['completion_summary']['status'], 'Not applicable (reviewed)')
        self.assertEqual(row['completion'], 'NOT APPLICABLE (reviewed)')
        self.assertFalse(row['completion_summary']['attention'])

    def test_handoff_distinguishes_real_documents_and_mock_helpers(self):
        report = fixture()
        result = report['students'][0]['execution'][0]
        result['supplied_inputs'] = {'files': [{'file': 'data/book.txt', 'original': 'book.txt'}]}
        result['mocks'] = {'manifest': [{'file': 'sentiment_toolkit.py', 'origin': 'BUNDLED MOCK HELPER'}]}
        files = handoff_files(report)
        overview = list(csv.DictReader(io.StringIO(files['student-overview.csv'].decode('utf-8-sig'))))[0]
        self.assertEqual(overview['Supplied files referenced'], 'book.txt')
        self.assertEqual(overview['Test inputs'], 'Supplied files + mocks')
        self.assertIn('sentiment_toolkit.py', overview['Mocks substituted'])
        soup = BeautifulSoup(files['TA-report.html'], 'html.parser')
        self.assertIn('Supplied files referenced: book.txt', soup.get_text())
        self.assertIn('sentiment_toolkit.py (BUNDLED MOCK HELPER)', soup.get_text())


if __name__ == '__main__':
    unittest.main()
