"""Reproducible batch review without Streamlit; no model/accuracy grading."""
from prerequisites import preflight_submission
import argparse
import json
from pathlib import Path
import tempfile

from grader_core import (group_submissions, load_profile, review_folder, run_entrypoint,
                         safe_extract, serializable_report, unpack_nested)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assignment', required=True, type=Path, help='PDF, text or JSON checklist')
    parser.add_argument('--submissions', required=True, type=Path)
    parser.add_argument('--shared', type=Path, help='Instructor course-files ZIP')
    parser.add_argument('--resources', type=Path, help='ZIP with one folder per Canvas student key')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--execute', action='store_true', help='Execute notebooks/single scripts in this disposable machine')
    parser.add_argument('--student', action='append', help='Exact Canvas student key; repeat to select students')
    parser.add_argument('--mock', action='store_true', help='Explicitly labeled smoke test with generated missing inputs')
    parser.add_argument('--mock-fixtures', type=Path, help='ZIP of mock data/helper test doubles (requires --mock)')
    parser.add_argument('--mock-words', type=int, default=18000)
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if args.mock_fixtures and not args.mock:
        parser.error('--mock-fixtures requires --mock')
    if not 100 <= args.mock_words <= 50000:
        parser.error('--mock-words must be between 100 and 50000')
    if args.timeout < 1:
        parser.error('--timeout must be positive')
    profile = load_profile(args.assignment.read_bytes(), args.assignment.name)
    with tempfile.TemporaryDirectory(prefix='grader-cli-') as tmp:
        groups = group_submissions(args.submissions, Path(tmp))
        unknown = set(args.student or []) - groups.keys()
        if unknown:
            parser.error('Unknown student keys: ' + ', '.join(sorted(unknown)))
        shared = None
        if args.shared:
            shared = Path(tmp) / 'shared'
            budget = safe_extract(args.shared, shared)
            unpack_nested(shared, budget)
        resource_root = None
        if args.resources:
            resource_root = Path(tmp) / 'resources'
            budget = safe_extract(args.resources, resource_root)
            unpack_nested(resource_root, budget)
            unknown_resources = [p.name for p in resource_root.iterdir() if not p.is_dir() or p.name not in groups]
            if unknown_resources:
                parser.error('Unknown student resource folders: ' + ', '.join(unknown_resources))
        mock_root = None
        if args.mock_fixtures:
            mock_root = Path(tmp) / 'mock-fixtures'
            budget = safe_extract(args.mock_fixtures, mock_root)
            unpack_nested(mock_root, budget)
        rows = []
        for student, folder in groups.items():
            row = review_folder(folder, profile, student)
            resources = resource_root / student if resource_root else None
            row['prerequisites'] = preflight_submission(folder, shared, resources)
            if args.execute and not row['unmatched'] and (not args.student or student in args.student):
                notebooks = [s['file'] for s in row['scripts'] if s['marimo'] or s['file'].endswith('.ipynb')]
                entries = notebooks or (row['entrypoints'] if len(row['entrypoints']) == 1 else [])
                if not entries:
                    row['execution'].append({'file': '', 'status':'NOT RUN — NO UNAMBIGUOUS ENTRYPOINT', 'returncode': None, 'log':'Select the main script in the Streamlit app.'})
                for entry in entries:
                    try:
                        row['execution'].append(run_entrypoint(folder, entry, args.timeout, shared, resources=resources, mock=args.mock, mock_fixtures=mock_root, mock_words=args.mock_words))
                    except Exception as exc:
                        row['execution'].append({'file': entry, 'status':'CHECKER / STAGING ERROR', 'returncode':None, 'log':f'{type(exc).__name__}: {exc}'})
            rows.append(row)
        report = serializable_report(profile, rows)
        import hashlib
        report['archive_sha256'] = hashlib.sha256(args.submissions.read_bytes()).hexdigest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps({'students': len(rows), 'source_files':sum(len(r['scripts']) for r in rows),
                          'executed':sum(len(r['execution']) for r in rows), 'output':str(args.output)}))


if __name__ == '__main__': main()
