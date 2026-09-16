# Canvas Assignment Execution Checker & Autograder

A Streamlit-based testing harness designed to batch-evaluate Python and Marimo assignment submissions downloaded directly from Canvas.

The autograder handles:
- Automatic extraction and grouping of multi-file student submissions.
- Recursive nested zip unpacking and case-insensitive filename reconciliation.
- Path sanitization to resolve absolute local directories (e.g., macOS `/Users/...` or Windows paths).
- Dynamic runtime shimming to handle mock data fallbacks, headless Matplotlib rendering, missing text files, and tokenizer/model stubs without code modification.
- Real-time progress monitoring, live traceback inspection, and timeout enforcement.

---

## Prerequisites

- **Python**: 3.11 or higher
- Recommended package manager: [`uv`](https://docs.astral.sh/uv/) (faster) or standard `pip`

---

## Quick Start

### Option A: Using `uv` (Recommended)

1. Clone or download this repository:
   ```bash
   git clone <repo-url>
   cd <repo-folder>

```

2. Sync dependencies and run the application:
uv run streamlit run app.py

```



---

### Option B: Using Standard `pip` and Virtualenv

1. Clone or download this repository:
```bash
git clone <repo-url>
cd <repo-folder>

```


2. Create and activate a virtual environment:
```bash
python3 -m venv .venv
source .venv/bin/activate      # On Windows: .venv\Scripts\activate

```


3. Install the dependencies:
```bash
pip install -e .

```


*(Or install directly from `pyproject.toml` dependencies: `streamlit`, `marimo`, `scikit-learn`, `sentence-transformers`, `transformers==4.48.3`, `pandas`, `numpy`, `ipython`, `matplotlib`, `spacy`, `datasets`, `seaborn`)*
4. Launch the application:
```bash
streamlit run app.py

```



---

## How to Run

1. Open the local URL displayed in your terminal (typically `http://localhost:8501`).
2. Download the bulk submission `.zip` file directly from Canvas:
* Navigate to the assignment on Canvas.
* Click **Download Submissions** in the right-hand sidebar.


3. Drag and drop the downloaded `.zip` file into the upload box.
4. Click **Run Autograder**.

---

## Submission ZIP Structure

The autograder expects the standard naming convention produced by Canvas when exporting submissions in bulk:

```text
submissions.zip
├── netid1_12345_67890_assignment.py
├── netid1_12345_67891_dataset.csv
├── netid2_12346_67892_assignment.py
├── netid2_12346_67893_supplemental_files.zip
└── ...

```

Files are automatically grouped by student identifier, unzipped into isolated temporary sandboxes, and executed independently with a per-script timeout (default: 45 seconds).

---

## Status Indicators

| Status | Meaning |
| --- | --- |
| **PASSED** | The script finished with return code `0`. |
| **MISSING DATA FILE** | Execution failed due to a missing file/directory (`FileNotFoundError`). |
| **FAILED** | The script terminated with a runtime Python error/exception. |
| **TIMEOUT** | The script exceeded the execution time limit (default 45s). |
| **NO SCRIPT** | No `.py` file was found in the student's submission package. |

---

## Troubleshooting

* **NLP Model Downloads**: The autograder attempts to pre-download common spaCy models (`en_core_web_sm`, `zh_core_web_sm`, etc.) on initial startup. If your machine is offline, ensure the required models are installed in your environment beforehand:
```bash
python -m spacy download en_core_web_sm
python -m spacy download zh_core_web_sm

```


* **Execution Timeouts**: Very heavy operations (such as large loops or unbatched inference) may hit the 45-second cap. You can adjust `TIMEOUT_SECONDS = 45` near the top of `app.py` if longer execution windows are desired.
