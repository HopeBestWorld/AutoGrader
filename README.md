# Assignment Submission Checker

Review whether Python, marimo, or Jupyter submissions **run** and whether **every assignment part is answered**, without scoring the accuracy of the work. The main app is assignment independent. Assignment 7 (Sentiment and Plot) and Assignment 8 (Reading Difficulty) have supplied checklists based on the assignment PDFs.

## Free GitHub Pages deployment

This repository includes a **Stlite browser edition**, using the same approach as OpenFLASH. GitHub Pages hosts static files, while Python/Streamlit run in the visitor's browser. No account, organization, paid backend, or local Python installation is required to use the published site.

1. In this GitHub repository, open **Settings → Pages** and choose **GitHub Actions** as the build source. If you do not see those settings, a repository administrator must enable them. GitHub Pages is free for public repositories on GitHub Free; private repository Pages availability depends on the account plan. The site itself should not be treated as private storage.
2. The **Deploy browser app to GitHub Pages** workflow runs on pushes to `main`. If enabling Pages after the push, open **Actions**, select that workflow, and click **Run workflow**. It builds from an explicit allowlist of app sources and assignment checklists; it never bundles uploaded submissions, reports, repository history, secrets or the Python environment.
3. Open the deployment URL shown by the successful workflow/Pages settings. Subsequent pushes to `main` update the site automatically. For this repository, the standard project-site URL is `https://hopebestworld.github.io/AutoGrader/` (it is live only after a successful Pages deployment).

**Browser capabilities:** assignment PDF/text/JSON checklists, Canvas ZIP grouping, syntax/code/prose inspection, evidence and completion decisions, provided-file prerequisite inventory, original-file inspection/downloads, and exported JSON reports. ZIP files are handled in the browser's virtual filesystem; the app does not upload them to an autograding backend. The site downloads Stlite/Python/packages from external CDNs. Prerequisite package availability describes the browser runtime, not a separate server.

**Native execution limitation:** the Streamlit review panel does not provide native Python, marimo/Jupyter or native mock execution. The separate browser execution panel now runs a supported Python subset in isolated browser workers. Those rely on native processes and packages that browser Python cannot supply. Native execution controls are disabled and reports record the browser's capabilities; unavailable execution is not recorded as a student failure. Use the native edition below for actual runs. A backend would be needed to provide the full native execution workflow through a shared hosted site.

### Free browser execution and no-AI assignment checks

Uploads made in the Streamlit review panel are also copied to the execution panel with the current checklist, avoiding duplicate uploads. The review-only table now says NOT RUN rather than execution unavailable; it is not an execution report. Synthetic input generation is selected by default in the execution panel and can be unchecked. Actual missing helper modules/files are listed in each blocked execution result.

The Streamlit interface is the primary Pages interface. After uploading the ZIP and instructions, click **Run browser checks (with mocks)**. It starts the browser worker and automatically imports results back into the Streamlit table and JSON export, verifying the archive/checklist hashes. The collapsible browser diagnostics panel shows progress and retains advanced controls. Both interfaces use the same browser runner; native Python execution remains separate.

The diagnostics panel accepts a submissions ZIP and assignment PDF/text/JSON. Click **1. Extract parts and review ZIP** to extract numbered requirements and collect candidate code/prose evidence. Expand student results to select students and entrypoints, then click **2. Run detected entrypoints**. Download the combined report with execution logs and per-part evidence. The fallback Assignment 7/8 checklists work when no instructions are uploaded. For scanned/unstructured instructions, supply a text or JSON checklist; inspect extracted parts before relying on them. No AI service, API key, organization, or paid backend is used.

Each execution starts in a fresh Pyodide Web Worker, which is terminated on timeout. The timeout includes runtime/package loading. Ordinary Python runs as submitted with real assertions/errors. Supported Pyodide packages are loaded from their published distribution. A limited marimo adapter executes submitted cell function bodies in returned-variable dependency order and supports basic markdown/stack display primitives. It **does not provide native marimo graph validation, reactive behavior, arbitrary UI APIs or native notebook exports**. Missing helpers/data, unavailable packages, interactive APIs, disabled cells and unsupported notebook structures remain blocked. Jupyter kernels are not supported. Optional synthetic data tests are marked as mocks, with manifests, and never verify original-input behavior. Missing helper APIs are not invented.

Assignment coverage is deterministic evidence retrieval, not an AI or semantic completeness verdict. Each part reports candidate, partial or missing evidence; code can use different wording, copied prompts can match, and required method counts or substantive written answers need inspection. No student is automatically marked complete. The Streamlit panel remains available below for manual completion decisions. Browser success only establishes the tested browser-compatible code path; this cannot guarantee every Python package, assignment or submission will run. Student code can access the network; only run submissions you are authorized to test. Browser workers have no grader backend credentials.

Use a current desktop browser and allow time for the initial runtime download. Expanded archives are limited to 128 MiB in the browser edition. Reports and uploaded files are session memory; download reports before closing/reloading. The free Pages deployment has no persistent student-data storage, login system or paid services.

To reproduce the static build:

```bash
python scripts/build_pages.py
# Generated files are in dist/. Publish that directory with GitHub Pages.
```

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
5. Optionally upload a **student-specific files ZIP** with one folder per exact Canvas student key (for example `smith_12345/book.txt`). This supplies different data/helpers to each student without sharing them with classmates. Precedence is original student files, then student-specific resources, then shared instructor files. Recheck prerequisites after supplying files. To execute, enable execution, choose students, inspect/select their entrypoints, and click **Run selected entrypoints**. Marimo/Jupyter notebooks are selected by default. A single plain Python script is selected automatically; multiple plain scripts require choosing the main file. Each entrypoint gets a fresh temporary copy, the instructor files, and a timeout. The default provided-input mode does not modify originals, install packages requested by students, repair code, flatten notebooks, inject mock data/models, suppress exceptions or guess dataset substitutions. The separately selected mock mode below can create explicitly labeled synthetic inputs. Optional exact path mappings are applied only to literal strings in disposable Python copies, with a change log.
6. Review every criterion, evidence and actual outputs. Mark each part **Complete**, **Incomplete**, **Not applicable**, or **Needs review**, and add notes. Download the JSON report to retain the checklist, evidence, reviewer decisions, logs, input archive hash and execution configuration. Download executed HTML/Jupyter artifacts separately. Session state is in memory, so download before leaving/restarting; downloaded reports are records, not currently importable sessions.

### Shared course files for these assignments

Assignment 7 requires the actual instructor toolkit (with the module name used by students, often `sentiment_toolkit.py`) and lexicon data, for example:

```text
sentiment_toolkit.py
 data/bingliu.csv
 data/syuzhet.csv
 # plus other real course data referenced by submissions
```

Assignment 8 requires the real instructor-provided `readability_tools.py` at the ZIP root. Provided-input mode never invents or substitutes this library. In mock mode, an explicitly uploaded helper test double can stand in for it, and the result is labeled as a mock test. The supplied student ZIPs do not include these shared course resources. This is reported as a blocked execution check rather than an accuracy or completeness failure.

If the grader does not possess those originals, **the real run cannot yet be verified**. Ask the student/instructor to provide the named resources, or obtain the exact public dataset from its authoritative source yourself and upload it. The preflight report is downloadable as a resource request. Actual execution failures also extract missing module/file names and recovery steps. Arbitrary import names are never automatically installed from PyPI (a course helper can share a package name), and no synthetic data or replacement helper code is generated in the default provided-input mode. Conditional imports and generated inputs are marked as potential requirements; preflight does not refuse execution based on a static guess. A successful provided-input check is established only by an actual rerun with real resources; mock smoke tests are a separate, limited result. When contacting students for files, request their input data, local helper modules, dependency versions and expected entrypoint; do not request credentials.

### Mock smoke tests when original inputs are unavailable

Select **Execution mode → Mock inputs — smoke test only**, enable execution, and run selected entrypoints. This runs the actual submitted program or native notebook, with assertions and errors still active. It is useful for checking whether a code path can execute with substitute inputs; it cannot prove the original data/helper code would work or that all required analysis ran.

- Missing literal text, CSV/TSV, JSON/JSONL/NDJSON and corresponding gzip inputs can be generated automatically. Text defaults to an approximately 18,000-word deterministic synthetic corpus (configurable 100–200,000); tables use synthetic rows with default fields and field names inferred from code. `load_lexicon` / `load_emolex` calls can receive small synthetic sentiment lexicons. These are smoke-test guesses, not real schemas or real research data. Generation is bounded to 64 files / 16 MiB.
- If a particular schema or helper interface is required, upload a **mock-fixtures ZIP** with correctly shaped synthetic data or compatible Python test doubles at the paths expected by the submission. For example, a missing `course_helper.py` can be supplied as a deliberately simplified mock of its documented API. No arbitrary missing helper is automatically replaced with a permissive stub: unknown return types and no-op implementations could hide student errors. The test double's code and its assumptions should be reviewed.
- Original student files, provided student resources, shared real files and installed Python modules take precedence over mocks. Only disposable execution copies receive substitutions. Mock fixtures are never included in the student completion evidence.
- Every generated/copied mock is recorded with origin, path, content hash and generated format/schema. Exact external path literals can be redirected to generated files inside the disposable copy; those path changes are logged. Composed/dynamic paths, remote downloads, binary formats and unknown helper APIs may still require fixtures or explicit mappings. They remain unresolved rather than silently skipped.
- Results explicitly say **MOCK SMOKE TEST PASSED / FAILED / BLOCKED / TIMEOUT**. A pass says **ORIGINAL INPUTS UNVERIFIED**. A mock incompatibility or failure does not prove the code would fail with the real inputs. If no substitutions were needed, the report says **PROVIDED INPUTS** and records that mock mode was requested.
- The app retains the latest provided-input and mock attempts separately. JSON reports include the full substitution manifest; downloadable mock HTML/Jupyter outputs carry a visible warning and `mock-` filename. Neither a mock pass nor its generated outputs marks any assignment part complete.

A missing Python helper can still block a test until a compatible test double is supplied. Mocking data is a useful partial check, not a universal substitute for unavailable executable code.

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

Use `--execute --mock` for an explicitly labeled smoke test; optionally add `--mock-fixtures /path/to/mock-fixtures.zip --mock-words 18000`. Mock fixtures require `--mock`. The CLI also includes the non-executing prerequisite inventory in every report. Add `--resources /path/to/student-files.zip` for student-specific files. Add `--execute --shared /path/to/course-files.zip --timeout 120` to run code. Repeat `--student studentname_12345` to select students. With execution disabled, no student code or imports run. CLI reports exclude binary artifacts; use the app to download executed outputs. The CLI exits successfully when it creates a report; student failures are recorded in the report rather than presented as passing tests.

```bash
uv run --frozen python -m unittest discover -s tests -v
```

Tests exercise archive safety/grouping, generic requirement extraction, evidence/decision separation, Python and native marimo/Jupyter success and failure, shared files, path mappings, timeouts, credential inheritance, and the Streamlit review/execution workflow.

## Execution boundary

ZIP extraction rejects traversal and symlinks, limits nested depth to four, and applies a shared 2 GiB / 10,000-entry expansion budget. Child processes receive no account/API environment credentials, stdin is closed, and POSIX timeout cleanup kills the process group. Logs retain their last 2 MB; generated artifacts are retained only up to 30 MiB.

**Temporary directories are not a security sandbox.** Student programs run with the machine account's filesystem/network permissions. Use a disposable isolated worker without sensitive files for execution; the app explicitly requires opting in. This app does not supply a hardened multi-user execution service.

### Browser compatibility fixes and bundled helper mocks

The browser adapter supports `app.setup`, identity `app.function` decorators, and Path-valued `mo.notebook_dir()`. Return statements inside nested helper functions are not mistaken for cell exports. In mock mode, a missing `sentiment_toolkit` may receive the bundled simplified test double (text segmentation, lexicon scoring/ranking and chart display). It is explicitly manifested as a **BUNDLED MOCK HELPER**, never supplied in real-input mode, and never replaces an existing helper. Classifier/LLM functions are deliberately unsupported. Other unknown helper APIs still require their original implementation or a compatible fixture. A smoke-test pass does not validate the actual instructor library or original datasets.

### Interpreting unsuccessful browser checks

Each run keeps its traceback and now includes a failure category and suggested next action. Unknown browser APIs/dependencies are blocked, while an index/key failure with synthetic inputs is **mock input / code mismatch — inconclusive**, not proof of a failure with original data. Numeric sliders can be tested at their declared initial values; every tested default is recorded and reactive interaction is not tested. The mock corpus starts with a 30,000-word target in the browser controls and can grow based on explicit passage indices/segment sizes, bounded at 200,000 words and the existing byte budget. Those sizing assumptions are recorded. Original text is never overwritten and code assertions are never disabled. Imported legacy reports and timeouts lacking a file field are normalized so one incomplete result cannot crash the table/export.
