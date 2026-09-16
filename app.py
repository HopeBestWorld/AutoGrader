import streamlit as st
import zipfile
import tempfile
import os
import re
import sys
import subprocess
import shutil
import time
import ast
from pathlib import Path
import pandas as pd
import requests

st.set_page_config(page_title="Canvas Runner", layout="wide")
st.title("Execution & Deliverable Checker")

TIMEOUT_SECONDS = 180

# --- Canvas HTML Downloader Config ---
st.sidebar.header("Canvas Attachment Resolver")
canvas_base_url = st.sidebar.text_input("Canvas Base URL", value="https://canvas.cornell.edu")
canvas_cookie = st.sidebar.text_input(
    "Canvas Session Cookie / Bearer Token (Optional)",
    type="password",
    help="Paste your Canvas session cookie ('canvas_session=...') or API Bearer token to download linked student files."
)

@st.cache_resource
def prewarm_models():
    """Pre-caches sentence-transformers if available."""
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    except Exception:
        pass

prewarm_models()

def parse_canvas_name(filename: str):
    pattern = r"^([a-zA-Z0-9]+)(?:_LATE)?_[0-9]+_[0-9]+_(.+)$"
    match = re.match(pattern, filename)
    if match:
        return match.group(1), match.group(2)
    return filename.split("_")[0], filename

def normalize_canvas_filename(name: str) -> str:
    if "_." in name:
        return re.sub(r'_\.([a-zA-Z0-9]+)$', r'.\1', name)
    return name

def extract_error_reason(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    for line in reversed(lines):
        if any(err in line for err in ["Error", "Exception", "timed out", "No .py", "UnparsableError", "NoSuchResource", "critical["]):
            return line[:140]
    return lines[-1][:140] if lines else "Unknown Failure"

def is_missing_data_error(err_log: str) -> bool:
    patterns = ["FileNotFoundError", "No such file or directory", "FileExistsError", "IsADirectoryError"]
    return any(p in err_log for p in patterns)

def extract_zip_into(zip_path: Path, dest_dir: Path):
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for member in zf.namelist():
                parts = Path(member).parts
                if any(p == "__MACOSX" or p.startswith("._") for p in parts) or member.endswith("/"):
                    continue
                target = dest_dir / member
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except Exception:
        pass

def parse_html_canvas_links(html_content: str, base_url: str):
    links = []
    matches = re.findall(r'<a\s+[^>]*?href=["\']([^"\']+)["\'][^>]*?>([^<]*)</a>', html_content, flags=re.IGNORECASE)
    for href, title in matches:
        href_clean = href.replace("&amp;", "&")
        fname = title.strip()
        if not fname or fname.startswith("<"):
            t_match = re.search(r'title=["\']([^"\']+)["\']', href_clean)
            if t_match:
                fname = t_match.group(1)
            else:
                continue
        if href_clean.startswith("/"):
            href_clean = base_url.rstrip("/") + href_clean
        links.append((fname, href_clean))
    return links

def parse_google_drive_links(html_content: str):
    return list(set(re.findall(r'https?://(?:drive\.google\.com|docs\.google\.com)[^\s"\'<>]+', html_content)))

def parse_canvas_placeholder_attachments(html_content: str):
    placeholders = re.findall(r'data-placeholder-for=["\']([^"\']+)["\']', html_content, flags=re.IGNORECASE)
    import urllib.parse
    return [urllib.parse.unquote(p) for p in placeholders]

def download_canvas_attachment(url: str, dest_path: Path, token_or_cookie: str = "", base_url: str = ""):
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    cookies = {}
    if token_or_cookie:
        clean_auth = token_or_cookie.strip().strip("'\"")
        if clean_auth.startswith("Bearer "):
            headers["Authorization"] = clean_auth
        elif len(clean_auth) > 40 and "=" not in clean_auth:
            headers["Authorization"] = f"Bearer {clean_auth}"
        else:
            for part in clean_auth.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    cookies[k.strip()] = v.strip()

    download_url = re.sub(r'/files/(\d+)(?!/download)', r'/files/\1/download', url)
    if "download_frd=1" not in download_url:
        delim = "&" if "?" in download_url else "?"
        download_url = f"{download_url}{delim}download_frd=1"

    try:
        r = requests.get(download_url, headers=headers, cookies=cookies, timeout=25, allow_redirects=True)
        content = r.content
        if content.lstrip().startswith((b"<!DOCTYPE", b"<html")):
            html_text = content.decode("utf-8", errors="ignore")
            inner_matches = re.findall(r'<a\s+[^>]*?href=["\']([^"\']+/download\?[^"\']*)["\']', html_text, flags=re.IGNORECASE)
            if inner_matches:
                inner_url = inner_matches[0].replace("&amp;", "&")
                if inner_url.startswith("/"):
                    inner_url = (base_url or "https://canvas.cornell.edu").rstrip("/") + inner_url
                r2 = requests.get(inner_url, headers=headers, cookies=cookies, timeout=25, allow_redirects=True)
                if r2.status_code == 200 and not r2.content.lstrip().startswith((b"<!DOCTYPE", b"<html")):
                    content = r2.content

        if r.status_code == 200 and len(content) > 0 and not content.lstrip().startswith((b"<!DOCTYPE", b"<html")):
            dest_path.write_bytes(content)
            return True
    except Exception:
        pass
    return False

def sanitize_hardcoded_paths(script_path: Path, data_dir_name: str = "data_files"):
    code = script_path.read_text(encoding="utf-8", errors="ignore")
    file_pattern = r"(['\"])(?:/Users/[^'\"]*?|[a-zA-Z]:\\[^'\"]*?)/([^/\\'\"\n]+\.[a-zA-Z0-9]+)\1"
    code = re.sub(file_pattern, r"\1\2\1", code)
    dir_pattern = r"(['\"])(?:/Users/[^'\"]+|[a-zA-Z]:\\[^'\"]+)\1"
    code = re.sub(dir_pattern, f"'{data_dir_name}'", code)
    script_path.write_text(code, encoding="utf-8")

def repair_indentation(code_str: str) -> str:
    """Sanitizes Unicode spaces and resolves simple empty blocks without distorting control flow."""
    code_str = code_str.replace('\r\n', '\n').replace('\r', '\n')
    code_str = code_str.replace('\xa0', ' ').replace('\u200b', '').expandtabs(4)
    
    # 1. If code already parses validly as a whole, do not touch it
    try:
        ast.parse(code_str)
        return code_str
    except (SyntaxError, IndentationError):
        pass

    lines = [line.rstrip() for line in code_str.splitlines()]
    
    compound_keywords = (
        'def ', 'async def ', 'class ', 'if ', 'elif ', 'else:',
        'for ', 'async for ', 'while ', 'try:', 'except', 'finally:', 'with ', 'async with '
    )

    # 2. Insert 'pass' into legitimately empty compound statement blocks
    fixed_lines = []
    for i, line in enumerate(lines):
        fixed_lines.append(line)
        stripped = line.strip()

        if not stripped or stripped.startswith('#'):
            continue

        # Target compound statements ending in ':'
        if stripped.endswith(':') and any(stripped.startswith(kw) for kw in compound_keywords):
            curr_indent = len(line) - len(line.lstrip())
            has_body = False
            for nxt in lines[i + 1:]:
                nxt_stripped = nxt.strip()
                if not nxt_stripped or nxt_stripped.startswith('#'):
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                if nxt_indent > curr_indent:
                    has_body = True
                break

            if not has_body:
                fixed_lines.append(' ' * (curr_indent + 4) + 'pass')

    cleaned_code = "\n".join(fixed_lines)

    try:
        ast.parse(cleaned_code)
        return cleaned_code
    except (SyntaxError, IndentationError):
        pass

    # 3. Fallback iterative comment-out for orphan/dangling clauses
    pass_lines = cleaned_code.splitlines()
    for _ in range(15):
        try:
            ast.parse("\n".join(pass_lines))
            return "\n".join(pass_lines)
        except (IndentationError, SyntaxError) as e:
            if e.lineno is not None and 1 <= e.lineno <= len(pass_lines):
                idx = e.lineno - 1
                curr = pass_lines[idx]
                stripped = curr.strip()
                if stripped.startswith(('else:', 'elif ', 'except:', 'except ', 'finally:')):
                    pass_lines[idx] = f"# [autograder fixed] {curr}"
                else:
                    break
            else:
                break

    return "\n".join(pass_lines)

def linearize_marimo_code(code_str: str) -> str:
    """AST-based Marimo sequential linearizer that avoids function scope isolation."""
    try:
        tree = ast.parse(code_str)
    except Exception:
        return code_str

    linear_statements = []
    for node in tree.body:
        is_cell = False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_id = ""
                if isinstance(dec, ast.Attribute):
                    dec_id = dec.attr
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    dec_id = dec.func.attr
                if dec_id == "cell":
                    is_cell = True
                    break

        if is_cell and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if isinstance(stmt, ast.Return):
                    continue
                # If a statement assigns to a variable, ensure it's treated globally
                seg = ast.get_source_segment(code_str, stmt)
                if seg:
                    linear_statements.append(seg)
        else:
            src = ast.get_source_segment(code_str, node)
            if src and not any(x in src for x in ["app = marimo.App", "app.run()", "if __name__ == '__main__'"]):
                linear_statements.append(src)

    return "\n\n".join(linear_statements)

def color_status(val):
    if val == 'PASSED':
        return 'background-color: #d4edda; color: #155724;'
    elif val == 'MISSING DATA FILE':
        return 'background-color: #fff3cd; color: #856404;'
    elif val == 'TIMEOUT':
        return 'background-color: #e2e3e5; color: #383d41;'
    elif any(tag in val for tag in ['EXTERNAL', 'COMMENT', 'ATTACHMENT']):
        return 'background-color: #d1ecf1; color: #0c5460;'
    else:
        return 'background-color: #f8d7da; color: #721c24;'

if "grading_done" not in st.session_state:
    st.session_state.grading_done = False
if "results_df" not in st.session_state:
    st.session_state.results_df = None
if "audit_df" not in st.session_state:
    st.session_state.audit_df = None
if "student_code_store" not in st.session_state:
    st.session_state.student_code_store = {}

uploaded_zip = st.file_uploader("Upload Canvas Submissions ZIP", type=["zip"])

if uploaded_zip:
    if st.button("Run Autograder"):
        st.session_state.grading_done = False
        st.session_state.results_df = None
        st.session_state.audit_df = None
        st.session_state.student_code_store = {}

        with tempfile.TemporaryDirectory() as extract_dir:
            zip_path = Path(extract_dir) / "submissions.zip"
            zip_path.write_bytes(uploaded_zip.read())

            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            student_files = {}
            for item in Path(extract_dir).iterdir():
                if item.name == "submissions.zip" or item.name.startswith("."):
                    continue
                ident, orig_name = parse_canvas_name(item.name)
                if ident not in student_files:
                    student_files[ident] = []
                student_files[ident].append((item, orig_name))

            total_students = len(student_files)
            results = []

            progress_bar = st.progress(0)
            status_text = st.empty()

            metric_cols = st.columns(5)
            m_total = metric_cols[0].empty()
            m_passed = metric_cols[1].empty()
            m_missing = metric_cols[2].empty()
            m_failed = metric_cols[3].empty()
            m_remaining = metric_cols[4].empty()

            def update_scoreboard(total_run, passed, missing, failed, remaining):
                m_total.metric("Processed", f"{total_run} / {total_students}")
                m_passed.metric("Passed", passed)
                m_missing.metric("Missing Data", missing)
                m_failed.metric("Code Failed", failed)
                m_remaining.metric("Remaining", remaining)

            update_scoreboard(0, 0, 0, 0, total_students)

            st.subheader("Real-Time Results Feed")
            table_placeholder = st.empty()
            status_container = st.status(f"Testing {total_students} student packages...", expanded=True)
            live_errors_container = st.container()

            cached_code = {}

            for idx, (student, files) in enumerate(student_files.items()):
                start_time = time.time()
                status_text.markdown(f"**Currently Running:** `{student}` ({idx + 1}/{total_students})")
                status_container.write(f"⏳ **[{idx + 1}/{total_students}]** Preparing `{student}`...")

                cached_code[student] = []

                with tempfile.TemporaryDirectory() as student_workdir:
                    py_scripts = []
                    workdir_path = Path(student_workdir)
                    data_dir = workdir_path / "data_files"
                    data_dir.mkdir(parents=True, exist_ok=True)

                    data_files = []
                    html_wrappers = []
                    detected_drive_links = []
                    detected_placeholders = []
                    has_comment_notice = False

                    for src_path, orig_name in files:
                        dest_path = workdir_path / orig_name
                        shutil.copy2(src_path, dest_path)

                        if orig_name.endswith(".py"):
                            py_scripts.append(dest_path)
                            code_str = src_path.read_text(encoding="utf-8", errors="ignore")
                            cached_code[student].append((orig_name, code_str))
                        elif orig_name.lower().endswith((".html", ".htm")):
                            html_wrappers.append((src_path, orig_name))
                        else:
                            data_files.append((src_path, orig_name))
                            shutil.copy2(src_path, data_dir / orig_name)

                        clean_name = re.sub(r'[-_]\d+(\.[a-zA-Z0-9]+)$', r'\1', orig_name)
                        if clean_name != orig_name:
                            shutil.copy2(src_path, workdir_path / clean_name)
                            shutil.copy2(src_path, data_dir / clean_name)
                            data_files.append((src_path, clean_name))

                        if orig_name.lower().endswith(".zip"):
                            extract_zip_into(dest_path, workdir_path)
                            extract_zip_into(dest_path, data_dir)
                            for expy in workdir_path.rglob("*.py"):
                                if expy not in py_scripts:
                                    py_scripts.append(expy)
                                    c_str = expy.read_text(encoding="utf-8", errors="ignore")
                                    cached_code[student].append((expy.name, c_str))

                    for h_path, h_name in html_wrappers:
                        html_text = h_path.read_text(encoding="utf-8", errors="ignore")
                        drive_links = parse_google_drive_links(html_text)
                        if drive_links:
                            detected_drive_links.extend(drive_links)

                        placeholders = parse_canvas_placeholder_attachments(html_text)
                        if placeholders:
                            detected_placeholders.extend(placeholders)

                        if "refer to my comment section" in html_text.lower() or "comment section" in html_text.lower():
                            has_comment_notice = True

                        links = parse_html_canvas_links(html_text, canvas_base_url)
                        for remote_fname, remote_url in links:
                            target_dest = workdir_path / remote_fname
                            if not target_dest.exists():
                                downloaded = download_canvas_attachment(remote_url, target_dest, canvas_cookie, canvas_base_url)
                                if downloaded:
                                    if remote_fname.endswith(".py"):
                                        py_scripts.append(target_dest)
                                        code_str = target_dest.read_text(encoding="utf-8", errors="ignore")
                                        cached_code[student].append((remote_fname, code_str))
                                    elif remote_fname.lower().endswith(".zip"):
                                        extract_zip_into(target_dest, workdir_path)
                                        extract_zip_into(target_dest, data_dir)
                                        for extracted_py in workdir_path.rglob("*.py"):
                                            if extracted_py not in py_scripts:
                                                py_scripts.append(extracted_py)
                                                c_str = extracted_py.read_text(encoding="utf-8", errors="ignore")
                                                cached_code[student].append((extracted_py.name, c_str))
                                    else:
                                        data_files.append((target_dest, remote_fname))
                                        shutil.copy2(target_dest, data_dir / remote_fname)
                                        clean_remote = re.sub(r'[-_]\d+(\.[a-zA-Z0-9]+)$', r'\1', remote_fname)
                                        if clean_remote != remote_fname:
                                            shutil.copy2(target_dest, workdir_path / clean_remote)
                                            shutil.copy2(target_dest, data_dir / clean_remote)
                                            data_files.append((target_dest, clean_remote))

                    for item in workdir_path.rglob("*"):
                        if item.is_file() and not item.name.endswith(".py") and not item.name.endswith(".zip") and not item.name.endswith((".html", ".htm")):
                            data_files.append((item, item.name))

                    for sub in ["data", "docs", "documents", "Assignment 3", "assignment_3", "assignment-3"]:
                        sub_p = workdir_path / sub
                        sub_p.mkdir(parents=True, exist_ok=True)
                        for src_p, d_name in data_files:
                            try:
                                shutil.copy2(src_p, sub_p / d_name)
                            except Exception:
                                pass

                    # 1. Download from Google Drive if a link was detected
                    if detected_drive_links and not py_scripts:
                        status_container.write(f"📥 Downloading Google Drive submission for `{student}`...")
                        try:
                            import gdown
                            drive_url = detected_drive_links[0]
                            if "folder" in drive_url:
                                gdown.download_folder(url=drive_url, output=str(workdir_path), quiet=True)
                            else:
                                gdown.download(url=drive_url, output=str(workdir_path / f"{student}_submission.py"), quiet=True)

                            # Extract any .zip archives downloaded from Drive
                            for z in list(workdir_path.rglob("*.zip")):
                                extract_zip_into(z, workdir_path)
                                extract_zip_into(z, data_dir)

                            # Re-index downloaded python scripts
                            for expy in workdir_path.rglob("*.py"):
                                if not expy.name.startswith("run_") and expy not in py_scripts:
                                    py_scripts.append(expy)
                                    cached_code[student].append((expy.name, expy.read_text(encoding="utf-8", errors="ignore")))

                            # Re-index downloaded data files
                            for exdata in workdir_path.rglob("*"):
                                if exdata.is_file() and not exdata.name.endswith((".py", ".html", ".htm", ".zip")):
                                    data_files.append((exdata, exdata.name))
                                    shutil.copy2(exdata, data_dir / exdata.name)
                        except Exception as e:
                            status_container.write(f"⚠️ Drive download failed for `{student}`: {e}")

                    # 2. Check if a runnable script was found
                    if not py_scripts:
                        elapsed = round(time.time() - start_time, 2)
                        if detected_drive_links:
                            status_val = "EXTERNAL DRIVE LINK"
                            output_msg = f"Submitted via Google Drive (Download failed or link private): {detected_drive_links[0]}"
                        elif detected_placeholders:
                            status_val = "BROKEN ATTACHMENT"
                            output_msg = f"Attachment placeholder unparsed: {detected_placeholders[0]} (Check Canvas)"
                        elif has_comment_notice:
                            status_val = "COMMENT SUBMISSION"
                            output_msg = "Student noted submission is attached in the Canvas comments."
                        else:
                            status_val = "NO SCRIPT"
                            output_msg = "No .py file found in submission package."
                            if html_wrappers and not canvas_cookie:
                                output_msg += " (HTML wrapper found, Canvas Cookie/Token not provided)."

                        results.append({
                            "Student": student,
                            "Status": status_val,
                            "Script": "N/A",
                            "Error Reason": output_msg,
                            "Time (s)": elapsed,
                            "Output": output_msg
                        })
                        icon = "🔗" if status_val == "EXTERNAL DRIVE LINK" else "⚠️"
                        status_container.write(f"{icon} `{student}`: {output_msg}")
                    else:
                        shared_mpl_dir = Path(tempfile.gettempdir()) / "autograder_mpl_cache"
                        shared_mpl_dir.mkdir(parents=True, exist_ok=True)

                        for script in py_scripts:
                            raw_code = script.read_text(encoding="utf-8", errors="ignore")
                            code_content = repair_indentation(raw_code)
                            is_marimo = "import marimo" in code_content or "app = marimo.App" in code_content

                            if "app._unparsable_cell" in code_content:
                                code_content = re.sub(
                                    r'app\._unparsable_cell\s*\(\s*r?["\']{3}.*?["\']{3}\s*(?:,\s*name\s*=\s*["\'].*?["\'])?\s*\)',
                                    "# [autograder] stripped unparsable cell",
                                    code_content,
                                    flags=re.DOTALL
                                )
                            script.write_text(code_content, encoding="utf-8")

                            env = os.environ.copy()
                            env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
                            env["PYTHONUNBUFFERED"] = "1"
                            env["MPLBACKEND"] = "Agg"
                            env["MPLCONFIGDIR"] = str(shared_mpl_dir)
                            env["TRANSFORMERS_VERBOSITY"] = "error"

                            runnable_script = "run_" + re.sub(r'[^a-zA-Z0-9_\.]', '_', script.name)
                            target_runnable = workdir_path / runnable_script

                            synthetic_jsonl = "".join([
                                f'{{"id": {i}, "doc_id": "doc_{i}", "entity": "topic_{(i % 2) + 1}", "company": "Company_{(i % 2) + 1}", "source": "NewsSource", "title": "Document Title {i}", "TITLE_STEMMED": "Document Title {i}", "text": "This is document {i} exploring dimensionality reduction with PCA and UMAP visualization.", "review": "This is a movie review {i} discussing plot and themes.", "comment": "This is a discussion comment {i}.", "description": "This is a product description {i}.", "content": "Poem or book content stanza {i}.", "author": "Author_{(i % 3) + 1}", "poem name": "Poem_{i}", "book": "Book_{(i % 3) + 1}", "chapter": "Chapter_{(i % 5) + 1}", "chapter_i": "Chapter_{(i % 5) + 1}", "chapter_j": "Chapter_{(i % 5) + 2}"}}\n'
                                for i in range(120)
                            ])
                            synthetic_csv = (
                                "id,doc_id,entity,company,source,title,TITLE_STEMMED,text,review,comment,description,content,author,poem name,book,chapter,chapter_i,chapter_j\n" +
                                "".join([
                                    f'{i},"doc_{i}","topic_{(i % 2) + 1}","Company_{(i % 2) + 1}","NewsSource","Document Title {i}","Document Title {i}","This is document {i} exploring dimensionality reduction with PCA and UMAP visualization.","This is a movie review {i} discussing plot and themes.","This is a discussion comment {i}.","This is a product description {i}.","Poem or book content stanza {i}.","Author_{(i % 3) + 1}","Poem_{i}","Book_{(i % 3) + 1}","Chapter_{(i % 5) + 1}","Chapter_{(i % 5) + 1}","Chapter_{(i % 5) + 2}"\n'
                                    for i in range(120)
                                ])
                            )

                            default_corpus_candidates = ["corpus.jsonl", "corpus.csv", "documents.csv", "data.csv"]
                            for cand in default_corpus_candidates:
                                cf = workdir_path / cand
                                if not cf.exists() or cf.stat().st_size == 0:
                                    cf.write_text(synthetic_jsonl if cand.endswith(".jsonl") else synthetic_csv, encoding="utf-8")

                            mentioned_data = set(re.findall(r'["\']([\w\-\s\.]+\.(?:jsonl|csv|tsv|txt))["\']', code_content))
                            for df_name in mentioned_data:
                                target_f = workdir_path / df_name
                                if not target_f.exists() or target_f.stat().st_size == 0:
                                    if df_name.endswith(".jsonl"):
                                        target_f.write_text(synthetic_jsonl, encoding="utf-8")
                                    elif df_name.endswith((".csv", ".tsv")):
                                        target_f.write_text(synthetic_csv, encoding="utf-8")
                                    else:
                                        target_f.write_text("Default document content line.\n" * 120, encoding="utf-8")

                            shim = (
                                "import marimo as mo\n"
                                "import base64, builtins, sys, os, io, pathlib\n"
                                "import numpy as _np\n"
                                "import pandas as _pd\n"
                                "from types import ModuleType\n"
                                "\n"
                                "# Pre-initialize dummy Altair chart fallback\n"
                                "class _DummyAltairChart:\n"
                                "    def copy(self, *a, **k): return self\n"
                                "    def to_dict(self, *a, **k): return {}\n"
                                "    def interactive(self, *a, **k): return self\n"
                                "    def properties(self, *a, **k): return self\n"
                                "\n"
                                "cat1_opts = []\n"
                                "cat2_opts = []\n"
                                "scatter = _DummyAltairChart()\n"
                                "distance_chart = _DummyAltairChart()\n"
                                "\n"
                                "# 1. Tolerant Marimo UI Dropdown\n"
                                "try:\n"
                                "    _orig_dropdown = mo.ui.dropdown\n"
                                "    def _tolerant_dropdown(options=None, value=None, *args, **kwargs):\n"
                                "        if isinstance(options, dict) and value is not None and value not in options:\n"
                                "            for k, v in options.items():\n"
                                "                if v == value:\n"
                                "                    value = k\n"
                                "                    break\n"
                                "        elif isinstance(options, (list, tuple)) and value is not None and value not in options:\n"
                                "            if len(options) > 0:\n"
                                "                value = options[0]\n"
                                "        return _orig_dropdown(options=options, value=value, *args, **kwargs)\n"
                                "    mo.ui.dropdown = _tolerant_dropdown\n"
                                "except Exception:\n"
                                "    pass\n"
                                "\n"
                                "# 1b. Tolerant Marimo UI Altair Chart\n"
                                "try:\n"
                                "    _orig_altair_chart = mo.ui.altair_chart\n"
                                "    def _tolerant_altair_chart(chart=None, *a, **k):\n"
                                "        if chart is None or not hasattr(chart, 'copy'):\n"
                                "            chart = _DummyAltairChart()\n"
                                "        try:\n"
                                "            return _orig_altair_chart(chart, *a, **k)\n"
                                "        except Exception:\n"
                                "            return None\n"
                                "    mo.ui.altair_chart = _tolerant_altair_chart\n"
                                "except Exception:\n"
                                "    pass\n"
                                "\n"
                                "# 2. Disable Altair browser popups\n"
                                "try:\n"
                                "    import altair as alt\n"
                                "    alt.renderers.enable('mimetype')\n"
                                "except Exception:\n"
                                "    pass\n"
                                "\n"
                                "# 3. Resilient Pandas DataFrame Interceptors\n"
                                "try:\n"
                                "    _orig_sample = _pd.DataFrame.sample\n"
                                "    def _tolerant_sample(self, n=None, frac=None, replace=False, *a, **k):\n"
                                "        if n is not None and n > len(self) and not replace:\n"
                                "            n = len(self)\n"
                                "        return _orig_sample(self, n=n, frac=frac, replace=replace, *a, **k)\n"
                                "    _pd.DataFrame.sample = _tolerant_sample\n"
                                "\n"
                                "    _orig_read_csv = _pd.read_csv\n"
                                "    def _tolerant_read_csv(filepath_or_buffer, *a, **k):\n"
                                "        try:\n"
                                "            res = _orig_read_csv(filepath_or_buffer, *a, **k)\n"
                                "            if len(res.columns) == 0:\n"
                                "                raise _pd.errors.EmptyDataError()\n"
                                "            return res\n"
                                "        except Exception:\n"
                                "            return _pd.DataFrame({\n"
                                "                'id': range(120),\n"
                                "                'doc_id': [f'doc_{i}' for i in range(120)],\n"
                                "                'title': [f'Document Title {i}' for i in range(120)],\n"
                                "                'TITLE_STEMMED': [f'Document Title {i}' for i in range(120)],\n"
                                "                'text': ['Dimensionality reduction text with TF-IDF, embeddings, PCA, and UMAP visualization.' for _ in range(120)],\n"
                                "                'content': ['Sample stanza and poem text content.' for _ in range(120)],\n"
                                "                'review': ['Movie review content.' for _ in range(120)],\n"
                                "                'description': ['Product description.' for _ in range(120)],\n"
                                "                'author': [f'Author_{i % 4}' for i in range(120)],\n"
                                "                'category': [f'Topic_{i % 3}' for i in range(120)]\n"
                                "            })\n"
                                "    _pd.read_csv = _tolerant_read_csv\n"
                                "\n"
                                "    _orig_getitem = _pd.DataFrame.__getitem__\n"
                                "    def _tolerant_getitem(self, key):\n"
                                "        try:\n"
                                "            return _orig_getitem(self, key)\n"
                                "        except KeyError:\n"
                                "            if isinstance(key, str):\n"
                                "                self[key] = self.iloc[:, 0] if len(self.columns) > 0 else 'sample_text'\n"
                                "                return _orig_getitem(self, key)\n"
                                "            elif isinstance(key, (list, tuple)):\n"
                                "                for k in key:\n"
                                "                    if k not in self.columns:\n"
                                "                        self[k] = self.iloc[:, 0] if len(self.columns) > 0 else 'sample_text'\n"
                                "                return _orig_getitem(self, key)\n"
                                "            raise\n"
                                "    _pd.DataFrame.__getitem__ = _tolerant_getitem\n"
                                "\n"
                                "    _orig_sort_values = _pd.DataFrame.sort_values\n"
                                "    def _tolerant_sort_values(self, by, *a, **k):\n"
                                "        by_cols = [by] if isinstance(by, str) else list(by)\n"
                                "        for c in by_cols:\n"
                                "            if c not in self.columns:\n"
                                "                self[c] = 0\n"
                                "        return _orig_sort_values(self, by, *a, **k)\n"
                                "    _pd.DataFrame.sort_values = _tolerant_sort_values\n"
                                "except Exception:\n"
                                "    pass\n"
                                "\n"
                                "# Fallback Mock for Bio / Biopython\n"
                                "if 'Bio' not in sys.modules:\n"
                                "    try: import Bio\n"
                                "    except ImportError:\n"
                                "        _bio = ModuleType('Bio')\n"
                                "        _entrez = ModuleType('Bio.Entrez')\n"
                                "        _entrez.email = ''\n"
                                "        _entrez.esearch = lambda *a, **k: io.BytesIO(b'<xml></xml>')\n"
                                "        _entrez.efetch = lambda *a, **k: io.StringIO('')\n"
                                "        _entrez.read = lambda *a, **k: {'IdList': []}\n"
                                "        _medline = ModuleType('Bio.Medline')\n"
                                "        _medline.parse = lambda *a, **k: []\n"
                                "        _bio.Entrez = _entrez\n"
                                "        _bio.Medline = _medline\n"
                                "        sys.modules['Bio'] = _bio\n"
                                "        sys.modules['Bio.Entrez'] = _entrez\n"
                                "        sys.modules['Bio.Medline'] = _medline\n"
                                "\n"
                                "# Fallback Mock for bs4 (BeautifulSoup)\n"
                                "if 'bs4' not in sys.modules:\n"
                                "    try: import bs4\n"
                                "    except ImportError:\n"
                                "        _bs4 = ModuleType('bs4')\n"
                                "        class _BS:\n"
                                "            def __init__(self, t, *a, **k): self.text = str(t)\n"
                                "            def get_text(self, *a, **k): return self.text\n"
                                "        _bs4.BeautifulSoup = _BS\n"
                                "        sys.modules['bs4'] = _bs4\n"
                                "\n"
                                "# Fallback Mock for jieba\n"
                                "if 'jieba' not in sys.modules:\n"
                                "    try: import jieba\n"
                                "    except ImportError:\n"
                                "        _jb = ModuleType('jieba')\n"
                                "        _jb.cut = lambda s, *a, **k: list(s.split())\n"
                                "        _jb.lcut = lambda s, *a, **k: list(s.split())\n"
                                "        sys.modules['jieba'] = _jb\n"
                                "\n"
                                "# Fallback SentenceTransformer Mock\n"
                                "class _MockST:\n"
                                "    def __init__(self, *a, **k): pass\n"
                                "    def encode(self, sentences, *a, **k):\n"
                                "        n = len(sentences) if hasattr(sentences, '__len__') else 1\n"
                                "        v = _np.ones((n, 64), dtype=_np.float32)\n"
                                "        return v / _np.linalg.norm(v, axis=1, keepdims=True)\n"
                                "try:\n"
                                "    import sentence_transformers\n"
                                "    _orig_st = sentence_transformers.SentenceTransformer\n"
                                "    def _patched_st(m='all-MiniLM-L6-v2', *a, **k):\n"
                                "        try: return _orig_st(m, *a, **k)\n"
                                "        except Exception: return _MockST()\n"
                                "    sentence_transformers.SentenceTransformer = _patched_st\n"
                                "except Exception:\n"
                                "    pass\n"
                            )

                            if is_marimo:
                                export_res = subprocess.run(
                                    [sys.executable, "-m", "marimo", "export", "script", script.name, "-o", runnable_script],
                                    cwd=student_workdir,
                                    capture_output=True,
                                    text=True,
                                    env=env
                                )
                                if target_runnable.exists() and export_res.returncode == 0:
                                    flat_code = target_runnable.read_text(encoding="utf-8", errors="ignore")
                                    fixed_flat = repair_indentation(flat_code)
                                    target_runnable.write_text(shim + "\n" + fixed_flat, encoding="utf-8")
                                    sanitize_hardcoded_paths(target_runnable, data_dir_name="data_files")
                                    cmd = [sys.executable, runnable_script]
                                else:
                                    linear_script_body = linearize_marimo_code(code_content)
                                    fixed_linear = repair_indentation(linear_script_body)
                                    target_runnable.write_text(shim + "\n" + fixed_linear, encoding="utf-8")
                                    sanitize_hardcoded_paths(target_runnable, data_dir_name="data_files")
                                    cmd = [sys.executable, runnable_script]
                            else:
                                target_runnable.write_text(shim + "\n" + code_content, encoding="utf-8")
                                sanitize_hardcoded_paths(target_runnable, data_dir_name="data_files")
                                cmd = [sys.executable, runnable_script]

                            try:
                                proc = subprocess.run(cmd, cwd=student_workdir, capture_output=True, text=True, timeout=TIMEOUT_SECONDS, env=env)
                                elapsed = round(time.time() - start_time, 2)

                                if proc.returncode == 0:
                                    results.append({"Student": student, "Status": "PASSED", "Script": script.name, "Error Reason": "None", "Time (s)": elapsed, "Output": proc.stdout[-500:] if proc.stdout else "Success."})
                                    status_container.write(f"✅ `{student}` passed in {elapsed}s.")
                                else:
                                    err_log = proc.stderr if proc.stderr else proc.stdout
                                    reason = extract_error_reason(err_log)
                                    status = "MISSING DATA FILE" if is_missing_data_error(err_log) else "FAILED"
                                    results.append({"Student": student, "Status": status, "Script": script.name, "Error Reason": reason, "Time (s)": elapsed, "Output": err_log[-2000:]})
                                    icon = "📁" if status == "MISSING DATA FILE" else "❌"
                                    status_container.write(f"{icon} `{student}` {status.lower()} in {elapsed}s ({reason})")
                                    with live_errors_container.expander(f"{icon} {student} — {script.name} [{status}]"):
                                        st.code(err_log[-2000:], language="python")
                            except subprocess.TimeoutExpired:
                                elapsed = round(time.time() - start_time, 2)
                                err_msg = f"Timed out after {TIMEOUT_SECONDS}s."
                                results.append({"Student": student, "Status": "TIMEOUT", "Script": script.name, "Error Reason": err_msg, "Time (s)": elapsed, "Output": err_msg})
                                status_container.write(f"⏱️ `{student}` timed out.")

                curr_df = pd.DataFrame(results)
                passed_cnt = len(curr_df[curr_df["Status"] == "PASSED"])
                missing_cnt = len(curr_df[curr_df["Status"] == "MISSING DATA FILE"])
                failed_cnt = len(curr_df[curr_df["Status"].isin(["FAILED", "ERROR", "NO SCRIPT"])])
                remaining_cnt = total_students - (idx + 1)

                update_scoreboard(idx + 1, passed_cnt, missing_cnt, failed_cnt, remaining_cnt)
                progress_bar.progress((idx + 1) / total_students)
                table_placeholder.dataframe(curr_df[["Student", "Script", "Status", "Error Reason", "Time (s)"]].style.map(color_status, subset=['Status']), width="stretch")

            # Deliverables & Reflection Audit
            audit_rows = []
            for student, py_list in cached_code.items():
                for orig_name, text in py_list:
                    has_tfidf = any(tok in text for tok in ["TfidfVectorizer", "tfidf", "TfidfTransformer"])
                    has_embed = any(tok in text for tok in ["SentenceTransformer", "sentence_transformers"])
                    has_pca = any(tok in text for tok in ["PCA(", "sklearn.decomposition", "TruncatedSVD"])
                    has_umap = any(tok in text for tok in ["UMAP(", "umap.UMAP", "umap_learn"])
                    has_altair = any(tok in text for tok in ["altair", "alt.Chart", ".mark_circle", ".mark_point"])
                    has_dist_check = any(tok in text for tok in ["euclidean_distances", "pairwise_distances", "scipy.spatial.distance"])

                    md_blocks = re.findall(r'mo\.md\(\s*r?["\']{3}(.*?)["\']{3}\s*\)', text, flags=re.DOTALL)
                    md_word_count = sum(len(b.split()) for b in md_blocks)
                    deliverables_passed = sum([has_tfidf, has_embed, has_pca, has_umap, has_altair, has_dist_check])

                    if deliverables_passed < 4:
                        flag = "⚠️ INCOMPLETE / DRAFT"
                    elif md_word_count < 40:
                        flag = "💬 MISSING REFLECTION"
                    else:
                        flag = "✅ COMPLETE"

                    audit_rows.append({
                        "Student": student,
                        "File": orig_name,
                        "Status": flag,
                        "TF-IDF": "✅" if has_tfidf else "❌",
                        "Embeddings": "✅" if has_embed else "❌",
                        "PCA": "✅" if has_pca else "❌",
                        "UMAP": "✅" if has_umap else "❌",
                        "Altair": "✅" if has_altair else "❌",
                        "High/Low Dist": "✅" if has_dist_check else "❌",
                        "Discussion Words": md_word_count,
                    })

            st.session_state.grading_done = True
            st.session_state.results_df = curr_df
            st.session_state.audit_df = pd.DataFrame(audit_rows)
            st.session_state.student_code_store = cached_code
            status_container.update(label=f"Finished checking all {total_students} submissions!", state="complete", expanded=False)

if st.session_state.grading_done and st.session_state.results_df is not None:
    st.divider()
    st.subheader("Execution Overview")
    st.dataframe(st.session_state.results_df[["Student", "Script", "Status", "Error Reason", "Time (s)"]].style.map(color_status, subset=['Status']), width="stretch")

    st.subheader("Requirements & Deliverables Audit")
    if st.session_state.audit_df is not None:
        st.dataframe(st.session_state.audit_df.sort_values(by="Status", ascending=True), width="stretch")

    st.subheader("Submissions Quick Viewer")
    all_students = sorted(list(st.session_state.student_code_store.keys()))
    if all_students:
        selected_student = st.selectbox("Inspect Student Code", options=all_students)
        if selected_student:
            for orig_name, text in st.session_state.student_code_store[selected_student]:
                with st.expander(f"📄 {orig_name}", expanded=True):
                    st.code(text, language="python")