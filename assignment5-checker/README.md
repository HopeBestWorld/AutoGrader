# Assignment 5: Topic Modeling Grading Assistant

This update replaces the Assignment 4 checker with an Assignment 5 review tool. It keeps the Canvas ZIP workflow and nested archive support, adds Assignment 5 evidence cues, and runs notebooks through native marimo HTML export. No numeric grading rubric was supplied, so it does not assign grades.

## Start

Use Python 3.11 or later. Place `app.py`, `pyproject.toml`, and this `README.md` together, then run:

```bash
uv run streamlit run app.py
```

`uv` resolves the revised dependencies and creates or updates `uv.lock`. The uploaded lockfile predates the new `pyrmallet` and `duckdb` dependencies; do not use `--locked` or `--frozen` until it has been regenerated. Dependency installation and full notebook execution were not verified in the preparation environment, which lacks marimo, Streamlit, and pyrmallet. Existing optional course dependencies remain in the project; an import error is an environment issue requiring review.

## Workflow

1. Upload the Canvas submissions ZIP and run a static review first. No student code runs in this mode.
2. Inspect code, prose, evidence line numbers, attachments, and data path literals. The JSON download preserves this review.
3. To execute, supply a local instructor data folder. Enable exact filename matching if desired, or provide explicit mappings. Download the chosen newspapers from the course Box folder yourself; the checker does not download the 19 TB image collection.
4. Select execution and optionally specify student IDs copied from the table. Execution may be slow: the default timeout is 1,000 seconds per notebook and can be increased.
5. Read the execution log and exported HTML, including HTML produced during failed runs. Review separate submitted HTML/PDF discussion attachments in the original ZIP.

The corpus files must be the actual selected newspapers. Missing originals are expected because students were told not to submit them. Do not deduct points solely because a dataset is absent from the ZIP.

### Path mappings

Mapping keys are exact string literals from a notebook, and values are existing local files on the computer running Streamlit:

```json
{
  "/Users/student/Downloads/Daily_Atlas__sn84022188.jsonl.gz": "/home/grader/newspapers/Daily_Atlas__sn84022188.jsonl.gz"
}
```

Only explicitly mapped literals or opted-in unique, case-insensitive exact basenames are replaced. The actual file is copied into a disposable execution directory; every replacement is logged. Submitted originals remain unchanged. No fuzzy matching, compression conversion, or substitute newspaper selection is performed. Composed paths, f-strings, directory globs, and renamed/compressed variants may require a manually prepared execution copy. Filenames detected statically may refer to unused code or outputs, so they are not proof of a missing input.

## Interpreting results

- `NOT RUN`: static review only; valid Python syntax does not establish marimo graph validity.
- `EXECUTED — REVIEW OUTPUTS`: native export returned zero, not an assignment grade. Check disabled cells, caught errors, empty outputs, and the actual analysis.
- `BLOCKED — DATA OR PATH MISSING`: inspect the traceback and locate the intended input. This may be an environment/path issue.
- `ENVIRONMENT / DEPENDENCY ERROR`: a package or import is unavailable or incompatible.
- `TIMEOUT — INCONCLUSIVE`: training exceeded the configured limit. This is not automatic evidence of incorrect work.
- `EXECUTION ERROR — REVIEW TRACEBACK`: a runtime or notebook-structure error needs review.
- `SYNTAX ERROR`, `NO PYTHON FILE`, or `CHECKER ERROR`: inspect the indicated source, attachments, or checker configuration.

Classification summarizes a log and is not a root-cause diagnosis. A run with multiple errors may need several fixes. Logs retain the last 2 MB and disclose truncation. HTML outputs remain downloadable during the app session; download them before restarting.

## Assignment-specific manual review

- Part 1: a marimo notebook; use of `pyrmallet.LatentDirichletAllocation`.
- Part 2: newspaper selection, location and date range, suitable data loading and article handling.
- Predictions: expected themes, article varieties and vocabulary, with evidence they preceded inspection. Source order alone cannot prove chronology.
- Training: appropriate input and real topic-model training.
- Analysis: topic keywords and highly weighted documents, with interpretation. SQL is suggested, not required.
- Iteration: different topic counts and added stopwords, with discussion of the effects. Multiple constructors or parameter expressions alone do not establish completed experiments.
- Discussion: actual findings versus predictions, surprises, and how text-mining techniques helped or hindered understanding.

Static evidence cues include source lines, constructor parameters, fitting calls, topic-related API attributes, stoplist definitions and prose. They can miss alternative implementations and can match unused code or assignment prompt text. They never label a submission complete or award credit. The original exported outputs and separate attachments need human review.

## Changes from the previous app

Removed Assignment 4 clustering checks, model prewarming, mock data and model stubs, error-suppressing pandas/scikit-learn patches, automatic indentation repairs, and fallback cell flattening. Native marimo export respects notebook dependencies and exposes notebook errors. The Canvas/Drive attachment downloader is not included; link-only submissions are flagged for attachment review rather than fetched with account credentials.

ZIP handling rejects traversal and symlink entries and imposes 2 GiB / 10,000-entry limits and a nested depth limit. Execution uses temporary folders, not a security sandbox: student code runs with your account's permissions. Use a disposable grading environment without sensitive credentials. On POSIX, timeouts kill the process group; on Windows, only the direct process is terminated.

## Verification and supplied review

`static-review.json` was generated from the supplied archive without running student code. Tests cover Canvas grouping, all supplied Python syntax, archive traversal rejection, exact data-path staging, alias detection, syntax/runtime status distinctions and subprocess timeout behavior. No student scores were assigned.

Reference: [marimo static HTML export](https://docs.marimo.io/guides/exporting/static_html/) executes notebooks and returns a nonzero status when cells error. [pyrmallet package](https://pypi.org/project/pyrmallet/).


## Fixes after reviewing the October 1 execution logs

Replace app.py and pyproject.toml, stop the existing Streamlit process, and run `uv sync` followed by `uv run streamlit run app.py`. The revised manifest includes sqlglot and pyarrow. Execution preflight checks core packages before starting a batch. Native HTML export explicitly uses `--no-sandbox` to use the prepared grading environment, and stdin is closed to prevent an interactive prompt from consuming the timeout. A notebook's inline dependency versions are not installed automatically in this mode; version-specific failures still need environment review.

The report now retains multiple diagnostics, including SQL dependencies, data paths, duplicate definitions, and the observed undefined variables. These diagnostics do not change submitted code. Zero return status remains an export result, not proof of completed training: notebook stop conditions, disabled cells and output content must still be checked.

The supplied `assignment5-diagnostics.html` analyzes the user's previous execution logs, not a rerun. It includes one section per student with distinct execution issues, evidence links to source lines, the extracted notebook prose, and a manual checklist. The checklist starts unassessed; checking a box records the reviewer's assessment only and is not an automatically generated grade. Its browser checkmarks are temporary; print/save the reviewed page to retain them.
