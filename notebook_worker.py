"""Child-process Jupyter runner. Execute original cells without suppressing errors."""
import sys
import os
import json
from pathlib import Path
import nbformat
from nbclient import NotebookClient

if __name__ == '__main__':
    source, output, timeout = sys.argv[1:]
    kernel_root = Path(output).parent / 'jupyter'
    kernel = kernel_root / 'kernels' / 'grader-python'
    kernel.mkdir(parents=True)
    (kernel / 'kernel.json').write_text(json.dumps({'argv': [sys.executable, '-m', 'ipykernel_launcher', '-f', '{connection_file}'], 'display_name': 'Grader Python', 'language': 'python'}))
    os.environ['JUPYTER_PATH'] = str(kernel_root)
    notebook = nbformat.read(source, as_version=4)
    try:
        NotebookClient(notebook, timeout=int(timeout), kernel_name='grader-python', allow_errors=False,
                       resources={'metadata': {'path': str(Path(source).parent)}}).execute()
    finally:
        nbformat.write(notebook, output)
