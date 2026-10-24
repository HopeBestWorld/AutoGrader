"""Build a static Stlite site from an explicit allowlist; never bundle submissions."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_FILES = ('app.py', 'browser_execution.py', 'grader_core.py', 'prerequisites.py', 'mock_inputs.py',
             'assignments/assignment-7.json', 'assignments/assignment-8.json')


def build(output):
    output = Path(output)
    if output.resolve() == ROOT or ROOT.is_relative_to(output.resolve()):
        raise ValueError('Choose a generated output directory, not the checkout or its parent')
    output.mkdir(parents=True, exist_ok=True)
    files = {name: (ROOT / name).read_text(encoding='utf-8') for name in APP_FILES}
    bundle = {'entrypoint': 'app.py', 'files': files, 'requirements': ['pypdf==6.21.2', 'beautifulsoup4==4.15.0'],
              'streamlitConfig': {'browser.gatherUsageStats': False}}
    content = json.dumps(bundle, ensure_ascii=False).encode('utf-8')
    digest = hashlib.sha256(content).hexdigest()[:16]
    bundle_name = f'app-bundle-{digest}.json'
    (output / bundle_name).write_bytes(content)
    html = (ROOT / 'web/index.html').read_text().replace('__APP_BUNDLE__', bundle_name)
    (output / 'index.html').write_text(html)
    for asset in ('runner.js','runner-worker.js'):
        (output/asset).write_text((ROOT/'web'/asset).read_text())
    (output / '.nojekyll').touch()
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT/'dist'))
    print(build(parser.parse_args().output))
