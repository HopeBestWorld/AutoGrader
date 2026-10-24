# Assignment Submission Checker

Review whether Python, marimo, or Jupyter submissions **run** and whether **every assignment part is answered**, without scoring the accuracy of the work. The main app is assignment independent. Assignment 7 (Sentiment and Plot) and Assignment 8 (Reading Difficulty) have supplied checklists based on the assignment PDFs.

## Start

Use Python 3.11+ and `uv`:

```bash
cd /workspace/AutoGrader
export UV_CACHE_DIR=/workspace/.cache/uv
export XDG_CACHE_HOME=/workspace/.cache
export MPLCONFIGDIR=/workspace/.cache/matplotlib
uv sync --frozen
uv run --frozen streamlit run app.py --server.headless=true --browser.gatherUsageStats=false
```

For checkouts outside this cloud environment, change the working directory and cache paths as needed. Dependencies include PDF extraction, Jupyter execution, `pronouncing`, the English spaCy model, and optional readability libraries used by the supplied submissions. Instructor course files are separate from Python dependencies. The older Assignment 5 tool remains in `assignment5-checker/` with its own environment.

## Use the same app for a new assignment

1. Select an existing checklist, or upload the assignment PDF, a text checklist, or a previously saved JSON checklist. Numbered Parts/Sections/Tasks/Questions are extracted from PDFs. Scanned PDFs need an OCR/text checklist. Review the extracted checklist: compound requirements, quantitative thresholds, conditional requirements, and optional work must be represented accurately. You can split a part into multiple requirements in the JSON editor. No app changes are needed.
2. Upload the Canvas submissions ZIP. Canvas IDs group multiple files, including late submissions and nested ZIPs. Files without Canvas identifiers go into `UNMATCHED_REVIEW` and are excluded from batch execution.
3. Click **Review submissions (no code execution)**. Then use **Check missing prerequisites (no execution)** to inventory potential missing helpers, packages, models, literal input paths and dynamic inputs without importing student code. Inspect candidate code/prose evidence, line or cell locations, syntax, imports, input path literals, attachments and raw text word counts. Open/download originals in the student inspector. A raw attachment word count is not proof of the length of the text actually analyzed.
4. Upload a **shared course-files ZIP** when needed. Place helper modules at its root and retain expected subdirectories. The shared ZIP is used only for execution and does not count as student answer evidence. Student files take precedence; conflicts are logged. Use a separate shared ZIP for each assignment.
5. Optionally upload a **student-specific files ZIP** with one folder per exact Canvas student key (for example `smith_12345/book.txt`). This supplies different data/helpers to each student without sharing them with classmates. Precedence is original student files, then student-specific resources, then shared instructor files. Recheck prerequisites after supplying files. To execute, enable execution, choose students, inspect/select their entrypoints, and click **Run selected entrypoints**. Marimo/Jupyter notebooks are selected by default. A single plain Python script is selected automatically; multiple plain scripts require choosing the main file. Each entrypoint gets a fresh temporary copy, the instructor files, and a timeout. The runner does not modify originals, install packages requested by students, repair code, flatten notebooks, inject mock data/models, suppress exceptions or guess dataset substitutions. Optional exact path mappings are applied only to literal strings in disposable Python copies, with a change log.
6. Review every criterion, evidence and actual outputs. Mark each part **Complete**, **Incomplete**, **Not applicable**, or **Needs review**, and add notes. Download the JSON report to retain the checklist, evidence, reviewer decisions, logs, input archive hash and execution configuration. Download executed HTML/Jupyter artifacts separately. Session state is in memory, so download before leaving/restarting; downloaded reports are records, not currently importable sessions.

### Shared course files for these assignments

Assignment 7 requires the actual instructor toolkit (with the module name used by students, often `sentiment_toolkit.py`) and lexicon data, for example:

```text
sentiment_toolkit.py
 data/bingliu.csv
 data/syuzhet.csv
 # plus other real course data referenced by submissions
```

Assignment 8 requires the real instructor-provided `readability_tools.py` at the ZIP root. The app never invents or substitutes this library. The supplied student ZIPs do not include these shared course resources. This is reported as a blocked execution check rather than an accuracy or completeness failure.

If the grader does not possess those originals, **the real run cannot yet be verified**. Ask the student/instructor to provide the named resources, or obtain the exact public dataset from its authoritative source yourself and upload it. The preflight report is downloadable as a resource request. Actual execution failures also extract missing module/file names and recovery steps. Arbitrary import names are never automatically installed from PyPI (a course helper can share a package name), and no fake data or replacement helper code is generated. Conditional imports and generated inputs are marked as potential requirements; preflight does not refuse execution based on a static guess. Successful execution is established only by an actual rerun with real resources. When contacting students for files, request their input data, local helper modules, dependency versions and expected entrypoint; do not request credentials.

### What the results mean

Execution and completion are independent:

- **RAN SUCCESSFULLY — REVIEW OUTPUTS**: the Python process or native notebook execution returned zero. Review disabled cells, stop calls, caught exceptions, empty results, and outputs; zero exit does not prove every required operation occurred.
- **BLOCKED — DEPENDENCY / ENVIRONMENT** or **MISSING FILE / PATH**: inspect the retained log and provide the real prerequisite before retrying. No negative completion judgment is inferred.
- **SYNTAX ERROR**, **EXECUTION FAILED**, **TIMEOUT — INCONCLUSIVE**, and **CHECKER / STAGING ERROR** remain distinct.
- **EVIDENCE FOUND**, **PARTIAL EVIDENCE**, and **NO MATCH FOUND** describe search results, not grades. Code and prose can be required separately. Comments and headings alone do not count as answers. Matches can still include copied prompts or unused code; alternative wording can be missed.
- **COMPLETE (reviewed)** appears only after all applicable parts are marked complete. Keyword matches and successful execution never automatically mark a submission complete. Semantic completeness (including whether prose answers the question, four distinct methods were actually displayed, or predictions preceded analysis) cannot be reliably proved by static matching. The tool surfaces evidence for those decisions and does not grade accuracy.

For other assignments, checklist files have this shape:

```json
{
  "title": "My assignment",
  "requirements": [
    {
      "id": "part-1",
      "title": "Part 1: Analyze and discuss",
      "description": "Implement the analysis and explain the results.",
      "keywords": ["analyze", "compare", "findings"],
      "evidence_types": ["code", "prose"],
      "criteria": ["Analysis is implemented.", "Written discussion is supplied."]
    }
  ]
}
```

`keywords`, `evidence_types`, and `criteria` are optional lists. With no keywords, search terms are derived from the requirement text. Empty `evidence_types` allows either kind. These are search hints, not mandatory APIs or automatic assertions.

## Batch CLI and validation

```bash
uv run --frozen python check_submissions.py \
  --assignment assignments/assignment-7.json \
  --submissions /path/to/submissions.zip \
  --output /path/to/report.json
```

The CLI also includes the non-executing prerequisite inventory in every report. Add `--resources /path/to/student-files.zip` for student-specific files. Add `--execute --shared /path/to/course-files.zip --timeout 120` to run code. Repeat `--student studentname_12345` to select students. With execution disabled, no student code or imports run. CLI reports exclude binary artifacts; use the app to download executed outputs. The CLI exits successfully when it creates a report; student failures are recorded in the report rather than presented as passing tests.

```bash
uv run --frozen python -m unittest discover -s tests -v
```

Tests exercise archive safety/grouping, generic requirement extraction, evidence/decision separation, Python and native marimo/Jupyter success and failure, shared files, path mappings, timeouts, credential inheritance, and the Streamlit review/execution workflow.

## Execution boundary

ZIP extraction rejects traversal and symlinks, limits nested depth to four, and applies a shared 2 GiB / 10,000-entry expansion budget. Child processes receive no account/API environment credentials, stdin is closed, and POSIX timeout cleanup kills the process group. Logs retain their last 2 MB; generated artifacts are retained only up to 30 MiB.

**Temporary directories are not a security sandbox.** Student programs run with the machine account's filesystem/network permissions. Use a disposable isolated worker without sensitive files for execution; the app explicitly requires opting in. This app does not supply a hardened multi-user execution service.
