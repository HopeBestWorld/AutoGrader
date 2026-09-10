import streamlit as st
import zipfile
import tempfile
import os
import re
import sys
import subprocess
import shutil
from pathlib import Path
import pandas as pd

st.set_page_config(page_title="Canvas Assignment Runner", layout="wide")
st.title("Canvas Assignment Execution Checker")

TIMEOUT_SECONDS = 40  # Max run time per submission

def parse_canvas_name(filename: str):
    """
    Parses 'lastnamefirstname_12345_67890_filename.py'
    Returns (student_identifier, original_filename)
    """
    pattern = r"^([a-zA-Z0-9]+)_[0-9]+_[0-9]+_(.+)$"
    match = re.match(pattern, filename)
    if match:
        return match.group(1), match.group(2)
    return filename.split("_")[0], filename

uploaded_zip = st.file_uploader("Upload Canvas Submissions ZIP", type=["zip"])

if uploaded_zip:
    if st.button("Run Autograder"):
        with tempfile.TemporaryDirectory() as extract_dir:
            # 1. Unpack Canvas ZIP
            zip_path = Path(extract_dir) / "submissions.zip"
            zip_path.write_bytes(uploaded_zip.read())
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)

            # 2. Group files by student identifier
            student_files = {}
            for item in Path(extract_dir).iterdir():
                if item.name == "submissions.zip" or item.name.startswith("."):
                    continue
                ident, orig_name = parse_canvas_name(item.name)
                if ident not in student_files:
                    student_files[ident] = []
                student_files[ident].append((item, orig_name))

            results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            total_students = len(student_files)

            # 3. Process each student in an isolated directory
            for idx, (student, files) in enumerate(student_files.items()):
                status_text.text(f"Grading {student} ({idx + 1}/{total_students})...")
                
                with tempfile.TemporaryDirectory() as student_workdir:
                    py_scripts = []
                    # Copy all student files into their workspace using original basenames
                    for src_path, orig_name in files:
                        dest_path = Path(student_workdir) / orig_name
                        shutil.copy2(src_path, dest_path)
                        if orig_name.endswith(".py"):
                            py_scripts.append(dest_path)

                    if not py_scripts:
                        results.append({
                            "Student": student,
                            "Status": "No Python File Found",
                            "Script": "N/A",
                            "Error / Output": "No .py file submitted in the package."
                        })
                        continue

                    # Execute each python script found for that student
                    for script in py_scripts:
                        try:
                            # Run with current Python executable (inside uv virtualenv)
                            proc = subprocess.run(
                                [sys.executable, str(script.name)],
                                cwd=student_workdir,
                                capture_output=True,
                                text=True,
                                timeout=TIMEOUT_SECONDS
                            )
                            if proc.returncode == 0:
                                results.append({
                                    "Student": student,
                                    "Status": "PASSED",
                                    "Script": script.name,
                                    "Error / Output": proc.stdout[-500:] if proc.stdout else "Executed with 0 errors."
                                })
                            else:
                                results.append({
                                    "Student": student,
                                    "Status": "FAILED",
                                    "Script": script.name,
                                    "Error / Output": proc.stderr[-1200:] if proc.stderr else proc.stdout[-1200:]
                                })
                        except subprocess.TimeoutExpired:
                            results.append({
                                "Student": student,
                                "Status": "TIMEOUT",
                                "Script": script.name,
                                "Error / Output": f"Execution timed out after {TIMEOUT_SECONDS}s."
                            })
                        except Exception as ex:
                            results.append({
                                "Student": student,
                                "Status": "ERROR",
                                "Script": script.name,
                                "Error / Output": str(ex)
                            })

                progress_bar.progress((idx + 1) / total_students)

            status_text.text("Finished execution checks!")

            # 4. Display Results
            df = pd.DataFrame(results)
            
            # Metric counters
            col1, col2, col3 = st.columns(3)
            passed = len(df[df["Status"] == "PASSED"])
            failed = len(df[df["Status"] != "PASSED"])
            col1.metric("Total Tested", len(df))
            col2.metric("Passed", passed)
            col3.metric("Failed / Errors", failed)

            # Highlight Table
            def color_status(val):
                color = '#d4edda' if val == 'PASSED' else '#f8d7da'
                return f'background-color: {color}'

            st.dataframe(df[["Student", "Script", "Status"]].style.applymap(color_status, subset=['Status']), use_container_width=True)

            # Details / Traceback Expander
            st.subheader("Tracebacks & Logs for Non-Passing Files")
            non_passing = df[df["Status"] != "PASSED"]
            if non_passing.empty:
                st.success("All scripts executed successfully!")
            else:
                for _, row in non_passing.iterrows():
                    with st.expander(f"{row['Student']} — {row['Script']} ({row['Status']})"):
                        st.code(row["Error / Output"])
