"""Manipulating pytest files: merging new tests in, pruning failing tests,
and extracting code from LLM replies."""

from __future__ import annotations

import ast
import re


def strip_code_fences(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def _split(code: str) -> tuple[list[ast.stmt], list[ast.stmt], list[ast.stmt]]:
    """Return (imports, helpers, tests) top-level statements."""
    tree = ast.parse(code)
    imports, helpers, tests = [], [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            tests.append(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            tests.append(node)
        elif isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue  # module docstring
        else:
            helpers.append(node)
    return imports, helpers, tests


def merge_suites(existing: str, new: str) -> str:
    """Add tests from ``new`` to ``existing``; dedupe imports, rename clashes."""
    if not existing.strip():
        return new
    e_imp, e_help, e_tests = _split(existing)
    n_imp, n_help, n_tests = _split(new)

    seen_imports = {ast.unparse(i) for i in e_imp}
    imports = list(e_imp) + [i for i in n_imp if ast.unparse(i) not in seen_imports]

    helper_names = {getattr(h, "name", None) for h in e_help}
    helpers = list(e_help) + [h for h in n_help
                              if getattr(h, "name", None) not in helper_names
                              or getattr(h, "name", None) is None
                              and ast.unparse(h) not in {ast.unparse(x) for x in e_help}]

    names = {t.name for t in e_tests}
    tests = list(e_tests)
    for t in n_tests:
        base, k = t.name, 2
        while t.name in names:
            t.name = f"{base}_{k}"
            k += 1
        names.add(t.name)
        tests.append(t)

    module = ast.Module(body=imports + helpers + tests, type_ignores=[])
    return ast.unparse(ast.fix_missing_locations(module)) + "\n"


def prune_tests(code: str, failing: set[str]) -> tuple[str, int]:
    """Remove failing test functions (by name; pytest param ids stripped).
    Test classes lose only their failing methods."""
    failing_base = {f.split("[")[0] for f in failing}
    tree = ast.parse(code)
    removed = 0
    new_body = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in failing_base:
            removed += 1
            continue
        if isinstance(node, ast.ClassDef):
            keep = [n for n in node.body if not (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                                  and n.name in failing_base)]
            removed += len(node.body) - len(keep)
            if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in keep):
                continue
            node.body = keep
        new_body.append(node)
    tree.body = new_body
    return ast.unparse(tree) + "\n", removed


def count_tests(code: str) -> int:
    try:
        _, _, tests = _split(code)
    except SyntaxError:
        return 0
    n = 0
    for t in tests:
        if isinstance(t, ast.ClassDef):
            n += sum(1 for x in t.body if isinstance(x, ast.FunctionDef) and x.name.startswith("test"))
        else:
            n += 1
    return n


def is_valid_python(code: str) -> str | None:
    try:
        ast.parse(code)
        return None
    except SyntaxError as e:
        return f"SyntaxError line {e.lineno}: {e.msg}"
