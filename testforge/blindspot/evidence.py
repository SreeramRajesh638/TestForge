"""Runtime evidence for blind spot diagnosis. Every function here runs real
code in the sandbox (see sandbox.py) and returns what it measured.

Per round:
    baseline()          original program + coverage.py with per-test contexts +
                        the trace plugin (test-level calls and their outcomes)
Per investigated mutant:
    mutant_runs()       (B) original program instrumented at the mutation point
                            -> infection data, and a second original trace
                            (used to discard non-deterministic calls)
                        (C) the mutant -> its trace, compared with the baseline
    search_counterexample()  differential search original vs mutant
Verification:
    junit_run()         per-test pass/fail of a test file against any source
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.runner import _parse_junit
from ..models import ModuleInfo, Mutant
from . import sandbox
from .harness import text as harness_text
from .instrument import Infection

TEST_FILE = "test_generated.py"


@dataclass
class Baseline:
    ok: bool
    line_tests: dict[int, list[str]] = field(default_factory=dict)
    executed: set[int] = field(default_factory=set)
    missing_branch_lines: set[int] = field(default_factory=set)
    trace: dict = field(default_factory=dict)
    duration_s: float = 0.0
    error: str = ""
    line_coverage: float | None = None

    def tests_on_line(self, line: int) -> list[str]:
        return self.line_tests.get(line, [])


def _trace_cfg(info: ModuleInfo, out: str) -> str:
    return json.dumps({"module": info.module_name, "functions": [f.qualname for f in info.functions], "out": out})


def _context_test(ctx: str) -> str | None:
    if not ctx:
        return None
    name = ctx.split("|")[0]
    return name.rsplit(".", 1)[-1] if name else None


def baseline(info: ModuleInfo, test_code: str, *, extra_paths=None, timeout_s: float = 90) -> Baseline:
    start = time.time()
    mod = f"{info.module_name}.py"
    with sandbox.Workspace("tf_bs_base_") as ws:
        ws.write(mod, info.source)
        ws.write(TEST_FILE, test_code)
        ws.write("conftest.py", harness_text("tf_trace_conftest.py"))
        ws.write(".coveragerc", f"[run]\nbranch = True\ndynamic_context = test_function\ninclude = {mod}\n")
        env = {"TF_TRACE_CFG": _trace_cfg(info, str(ws.path / "trace.json"))}
        res = sandbox.run(ws, ["-m", "coverage", "run", "--rcfile=.coveragerc", "-m", "pytest", TEST_FILE, "-q",
                               "-p", "no:cacheprovider", "--tb=no"], timeout_s=timeout_s,
                          extra_paths=extra_paths, env_extra=env)
        if res.timed_out:
            return Baseline(False, error=f"test suite timed out after {timeout_s:.0f}s", duration_s=time.time() - start)
        sandbox.run(ws, ["-m", "coverage", "json", "--rcfile=.coveragerc", "--show-contexts", "-q", "-o", "cov.json"],
                    timeout_s=60, extra_paths=extra_paths)
        base = Baseline(True, duration_s=time.time() - start)
        try:
            data = json.loads((ws.path / "cov.json").read_text(encoding="utf-8"))
            f = next(iter(v for k, v in data.get("files", {}).items() if k.endswith(mod)))
            base.executed = set(f.get("executed_lines", []))
            for line, ctxs in (f.get("contexts") or {}).items():
                names = sorted({t for t in (_context_test(c) for c in ctxs) if t})
                if names:
                    base.line_tests[int(line)] = names
            for src, _dst in f.get("missing_branches", []):
                base.missing_branch_lines.add(int(src))
            s = f.get("summary", {})
            if s.get("num_statements"):
                base.line_coverage = s.get("covered_lines", 0) / s["num_statements"]
        except (OSError, ValueError, StopIteration):
            base.ok = False
            base.error = "coverage data could not be read"
        try:
            base.trace = json.loads((ws.path / "trace.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            base.trace = {}
        return base


def _run_traced(info: ModuleInfo, module_source: str, test_code: str, *, probe: bool,
                extra_paths=None, timeout_s: float = 60) -> tuple[dict, bool]:
    with sandbox.Workspace("tf_bs_run_") as ws:
        ws.write(f"{info.module_name}.py", module_source)
        ws.write(TEST_FILE, test_code)
        ws.write("conftest.py", harness_text("tf_trace_conftest.py"))
        if probe:
            ws.write("_tf_probe.py", harness_text("tf_probe.py"))
        env = {"TF_TRACE_CFG": _trace_cfg(info, str(ws.path / "trace.json"))}
        res = sandbox.run(ws, ["-m", "pytest", TEST_FILE, "-q", "-p", "no:cacheprovider", "--tb=no"],
                          timeout_s=timeout_s, extra_paths=extra_paths, env_extra=env)
        try:
            return json.loads((ws.path / "trace.json").read_text(encoding="utf-8")), not res.timed_out
        except (OSError, ValueError):
            return {}, not res.timed_out


def _by_test(trace: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in trace.get("calls", []):
        out.setdefault(c.get("test") or "?", []).append(c)
    return out


def _sig(c: dict) -> tuple:
    return (c.get("fn"), tuple(c.get("args", [])), tuple(sorted((c.get("kwargs") or {}).items())))


def _outcome(c: dict) -> tuple:
    return (c.get("exc"), c.get("result"), c.get("state"))


def call_text(c: dict) -> str:
    args = list(c.get("args", [])) + [f"{k}={v}" for k, v in (c.get("kwargs") or {}).items()]
    return f"{c.get('fn')}({', '.join(args)})"


def outcome_text(c: dict) -> str:
    return f"raises {c['exc']}" if c.get("exc") else str(c.get("result"))


def compare(base: dict, other: dict, noise: set[tuple] | None = None) -> tuple[list[dict], list[str]]:
    """Calls whose outcome differs (aligned per test, in call order), and
    tests whose sequence of calls diverged."""
    diffs, diverged = [], []
    a, b = _by_test(base), _by_test(other)
    for test, calls in a.items():
        other_calls = b.get(test, [])
        if [_sig(c) for c in calls] != [_sig(c) for c in other_calls]:
            diverged.append(test)
        for i, (x, y) in enumerate(zip(calls, other_calls)):
            if _sig(x) != _sig(y):
                break
            if _outcome(x) != _outcome(y) and (noise is None or (test, i) not in noise):
                diffs.append({"test": test, "index": i, "original": x, "mutant": y})
    return diffs, diverged


@dataclass
class MutantRuns:
    infection: dict | None           # probe records (None: not instrumented)
    infection_ok: bool
    trace_original: dict
    trace_mutant: dict
    differing: list[dict]
    diverged: list[str]
    noisy: int


def mutant_runs(info: ModuleInfo, test_code: str, base: Baseline, mutated_source: str, inf: Infection, *,
                extra_paths=None, timeout_s: float = 60) -> MutantRuns:
    src_b = inf.source if inf.supported and inf.source else info.source
    trace_b, ok_b = _run_traced(info, src_b, test_code, probe=inf.supported, extra_paths=extra_paths,
                                timeout_s=timeout_s)
    trace_c, _ = _run_traced(info, mutated_source, test_code, probe=False, extra_paths=extra_paths,
                             timeout_s=timeout_s)
    noise_diffs, _ = compare(base.trace, trace_b)
    noise = {(d["test"], d["index"]) for d in noise_diffs}
    differing, diverged = compare(base.trace, trace_c, noise)
    probe = trace_b.get("probe") if inf.supported else None
    return MutantRuns(probe, ok_b and probe is not None, trace_b, trace_c, differing, diverged, len(noise))


# ------------------------------------------------------------ counterexamples
def search_counterexample(info: ModuleInfo, mutated_source: str, entries: list[dict], boundaries: list, *,
                          extra_paths=None, timeout_s: float = 45, call_timeout_s: float = 2.0,
                          max_candidates: int = 400, hypothesis_examples: int = 300,
                          only_seeds: bool = False) -> dict:
    with sandbox.Workspace("tf_bs_cx_") as ws:
        ws.write(f"orig/{info.module_name}.py", info.source)
        ws.write(f"mut/{info.module_name}.py", mutated_source)
        ws.write("tf_counterexample.py", harness_text("tf_counterexample.py"))
        req = {"module": info.module_name, "entries": entries, "boundaries": boundaries,
               "max_candidates": max_candidates, "hypothesis_examples": hypothesis_examples,
               "call_timeout_s": call_timeout_s, "only_seeds": only_seeds}
        res = sandbox.run(ws, ["tf_counterexample.py"], stdin=json.dumps(req), timeout_s=timeout_s,
                          extra_paths=extra_paths + [str(ws.path / "orig")] if extra_paths else [str(ws.path / "orig")])
        for line in reversed(res.stdout.splitlines()):
            try:
                return json.loads(line)
            except ValueError:
                continue
        return {"entries": [], "error": (res.stderr or "no output")[-600:], "timed_out": res.timed_out}


# ------------------------------------------------------------ verification
def junit_run(info: ModuleInfo, module_source: str, test_code: str, *, extra_paths=None,
              timeout_s: float = 60) -> tuple[dict[str, bool], str | None]:
    """Run a test file against ``module_source``; per-test pass/fail."""
    with sandbox.Workspace("tf_bs_verify_") as ws:
        ws.write(f"{info.module_name}.py", module_source)
        ws.write(TEST_FILE, test_code)
        res = sandbox.run(ws, ["-m", "pytest", TEST_FILE, "-q", "--tb=line", "-p", "no:cacheprovider",
                               "--junitxml=junit.xml"], timeout_s=timeout_s, extra_paths=extra_paths)
        if res.timed_out:
            return {}, f"timed out after {timeout_s:.0f}s"
        outcomes, err = _parse_junit(Path(ws.path / "junit.xml"), res.stdout + res.stderr)
        return {o.name: o.passed for o in outcomes}, err


def mutant_by_id(pairs: list[tuple[Mutant, str]]) -> dict[int, tuple[Mutant, str]]:
    return {m.id: (m, src) for m, src in pairs}
