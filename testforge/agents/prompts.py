"""System prompts and prompt builders for the LLM-backed agents.

Kept in one file so prompt changes are easy to review and version (the
prompts are part of the method described in the thesis).
"""

from __future__ import annotations

from ..models import ModuleInfo, Mutant, TestPlan

PLANNER_SYSTEM = """You are the test lead of an automated unit-testing team.
Given a Python module and a static analysis of it, design a test plan that a
test engineer can implement with pytest.

Cover, for every public function and method:
- normal behaviour with representative inputs,
- boundary values (zero, empty, one element, limits that appear as constants in the code,
  both sides of every comparison),
- error paths (every exception the code raises explicitly),
- state changes for classes (sequences of method calls).

Be concrete: give exact inputs and the exact expected result derived by reading the code.
Prefer many small focused cases over a few broad ones."""

WRITER_SYSTEM = """You are a senior test engineer writing pytest unit tests.

Rules for the test file you produce:
- Import the module under test by its module name exactly as given.
- Tests must be deterministic: no network, no real filesystem writes outside tmp_path,
  no sleeping, no randomness without a fixed seed.
- One behaviour per test function, named test_<function>_<behaviour>.
- Assert exact values. Use pytest.approx for floats and pytest.raises for exceptions
  (check the message with match= when the code sets one).
- Expected values must reflect what the code under test actually does; the code is the
  oracle. Work them out carefully by tracing the code.
- Strong assertions matter: the suite will be scored by mutation testing, so check
  return values, state after calls and exception types, not just that code runs.
- Output the complete test file."""

FIXER_SYSTEM = """You are a debugging engineer. Some generated unit tests fail.
The code under test is assumed to be correct: the tests are what is wrong
(wrong expected value, wrong setup, wrong import, wrong exception type).

Fix the failing tests so that they pass and still check meaningful behaviour.
Do not delete assertions just to make a test pass, and do not change passing tests.
Return the complete corrected test file."""

REFINER_SYSTEM = """You are a test engineer improving a test suite using mutation testing.

A mutant is a copy of the code with one small change. A *surviving* mutant means
every test still passes on the changed code, so the suite cannot detect that fault.

For each surviving mutant, write NEW focused tests that pass on the original code
but fail on the mutant: pick inputs that make the original and the mutated expression
produce different observable results, and assert on those results.

Some mutants are *equivalent*: no input can tell them apart from the original
(for example the change sits in dead code, or `x >= 0` vs `x > 0` where x can never be 0).
Do not write tests for those; list their ids in equivalent_mutant_ids instead.
Only call a mutant equivalent if you are confident.

Return only the new tests (with the imports they need), not the existing suite."""


def _module_block(info: ModuleInfo) -> str:
    numbered = "\n".join(f"{i:4d} | {line}" for i, line in enumerate(info.source.splitlines(), 1))
    return f"Module name: `{info.module_name}`\n\n```python\n{numbered}\n```"


def _analysis_block(info: ModuleInfo) -> str:
    rows = []
    for f in info.functions:
        params = ", ".join(p.name + (f": {p.annotation}" if p.annotation else "")
                           + (f" = {p.default}" if p.default else "") for p in f.params)
        extra = []
        if f.raises:
            extra.append("raises " + ", ".join(f.raises))
        if f.constants:
            extra.append("constants " + ", ".join(map(str, f.constants)))
        rows.append(f"- {f.qualname}({params})"
                    + (f" -> {f.returns}" if f.returns else "")
                    + f"  [complexity {f.complexity}" + ("; " + "; ".join(extra) if extra else "") + "]")
    for c in info.classes:
        init = ", ".join(p.name + (f" = {p.default}" if p.default else "") for p in c.init_params)
        rows.append(f"- class {c.name}({init}) methods: {', '.join(c.methods)}")
    return "Static analysis:\n" + "\n".join(rows)


def planner_prompt(info: ModuleInfo) -> str:
    return f"{_module_block(info)}\n\n{_analysis_block(info)}\n\nProduce the test plan."


def writer_prompt(info: ModuleInfo, plan: TestPlan | None) -> str:
    parts = [_module_block(info), _analysis_block(info)]
    if plan is not None:
        cases = "\n".join(f"- [{c.category}] {c.target}: {c.description} | inputs: {c.inputs} "
                          f"| expected: {c.expected}" for c in plan.cases)
        parts.append(f"Test plan to implement:\n{cases}")
    else:
        parts.append("No test plan is available: decide yourself what to test.")
    parts.append(f"Write the pytest file. Import the module as `import {info.module_name}` "
                 f"or `from {info.module_name} import ...`.")
    return "\n\n".join(parts)


def fixer_prompt(info: ModuleInfo, test_code: str, failures: str) -> str:
    return (f"{_module_block(info)}\n\nCurrent test file:\n```python\n{test_code}\n```\n\n"
            f"pytest failures:\n```\n{failures}\n```\n\nReturn the corrected complete test file.")


def refiner_prompt(info: ModuleInfo, test_code: str, survivors: list[Mutant]) -> str:
    listing = "\n".join(m.describe() for m in survivors)
    return (f"{_module_block(info)}\n\nExisting test suite:\n```python\n{test_code}\n```\n\n"
            f"Surviving mutants ({len(survivors)}):\n{listing}\n\n"
            "Write new tests that kill these mutants.")
