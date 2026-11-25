"""Runs pytest (with coverage) in an isolated temporary workspace."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..models import ExecResult, TestOutcome

TEST_FILE = "test_generated.py"


def _env(work: Path, extra_paths: list[str] | None) -> dict:
    env = os.environ.copy()
    paths = [str(work)] + list(extra_paths or [])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"  # Windows: non-ASCII source/output (e.g. ₹) must not break runs
    env.pop("PYTEST_ADDOPTS", None)
    return env


def run_tests(module_name: str, module_source: str, test_code: str, *,
              timeout_s: int = 60, extra_paths: list[str] | None = None) -> ExecResult:
    """Execute ``test_code`` against ``module_source`` and measure coverage."""
    work = Path(tempfile.mkdtemp(prefix="tf_run_"))
    start = time.time()
    try:
        (work / f"{module_name}.py").write_text(module_source, encoding="utf-8")
        (work / TEST_FILE).write_text(test_code, encoding="utf-8")
        cmd = [sys.executable, "-m", "coverage", "run", "--branch",
               f"--include={module_name}.py", "-m", "pytest", TEST_FILE,
               "-q", "--tb=short", "-p", "no:cacheprovider", "--junitxml=junit.xml"]
        try:
            proc = subprocess.run(cmd, cwd=work, env=_env(work, extra_paths), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or b"")
            output = out.decode() if isinstance(out, bytes) else out
            return ExecResult(0, 0, 0, f"Test run timed out after {timeout_s}s", [], 0.0, 0.0,
                              time.time() - start, output[-4000:])

        outcomes, collection_error = _parse_junit(work / "junit.xml", output)
        line_cov, branch_cov = _coverage(work, module_name)
        passed = sum(o.passed for o in outcomes)
        return ExecResult(
            total=len(outcomes), passed=passed, failed=len(outcomes) - passed,
            collection_error=collection_error, outcomes=outcomes,
            line_coverage=line_cov, branch_coverage=branch_cov,
            duration_s=time.time() - start, raw_output=output[-6000:],
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _parse_junit(path: Path, output: str) -> tuple[list[TestOutcome], str | None]:
    if not path.exists():
        return [], "pytest produced no report:\n" + output[-3000:]
    root = ET.parse(path).getroot()
    outcomes: list[TestOutcome] = []
    collection_error = None
    for case in root.iter("testcase"):
        name = case.get("name", "?")
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if case.find("skipped") is not None:
            continue
        if problem is not None and not case.get("classname"):
            # error while collecting the file (syntax error, bad import ...)
            collection_error = (problem.get("message", "") + "\n" + (problem.text or ""))[-3000:]
            continue
        msg = ""
        if problem is not None:
            msg = (problem.get("message", "") + "\n" + (problem.text or ""))[-1500:]
        outcomes.append(TestOutcome(name=name, passed=problem is None, message=msg))
    if not outcomes and collection_error is None and "error" in output.lower():
        collection_error = output[-3000:]
    return outcomes, collection_error


def _coverage(work: Path, module_name: str) -> tuple[float, float]:
    try:
        subprocess.run([sys.executable, "-m", "coverage", "json", "-q", "-o", "cov.json"],
                       cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        data = json.loads((work / "cov.json").read_text(encoding="utf-8"))
    except Exception:
        return 0.0, 0.0
    totals = data.get("totals", {})
    statements = totals.get("num_statements", 0)
    covered = totals.get("covered_lines", 0)
    branches = totals.get("num_branches", 0)
    covered_br = totals.get("covered_branches", 0)
    line = covered / statements if statements else 0.0
    branch = covered_br / branches if branches else 1.0
    return round(line, 4), round(branch, 4)


def run_pytest_quick(work: Path, test_file: str, timeout_s: int,
                     extra_paths: list[str] | None = None) -> str:
    """Fast pass/fail run used for mutants. Returns 'pass' | 'fail' | 'timeout' | 'error'."""
    cmd = [sys.executable, "-m", "pytest", test_file, "-x", "-q", "--tb=no",
           "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(cmd, cwd=work, env=_env(work, extra_paths), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return "timeout"
    # pytest exit codes: 0 ok, 1 tests failed, 2 interrupted (often import error
    # caused by the mutant -> counts as killed), 5 no tests collected
    return {0: "pass", 1: "fail", 2: "fail"}.get(proc.returncode, "error")
