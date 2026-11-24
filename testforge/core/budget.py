"""Context-budget-aware prompting.

Some LLM services (e.g. free tiers) cap the size of a single request. When a
module does not fit, the agents work on *focused views* of it: the functions
in focus are shown in full, every other function only by its signature, and
the original line numbers are kept so mutant locations still match. Modules
are split into chunks of functions that each fit the budget.
"""

from __future__ import annotations

import ast
import math

from ..models import ModuleInfo

CHARS_PER_TOKEN = 3.2  # conservative for code; real tokenizers vary


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _function_spans(source: str) -> dict[str, tuple[int, int, int]]:
    """qualname -> (first line incl. decorators, first body line, last line)."""
    spans: dict[str, tuple[int, int, int]] = {}
    tree = ast.parse(source)

    def visit(body, prefix: str | None) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                name = f"{prefix}.{node.name}" if prefix else node.name
                spans[name] = (start, node.body[0].lineno, node.end_lineno or node.lineno)
            elif isinstance(node, ast.ClassDef) and prefix is None:
                visit(node.body, node.name)

    visit(tree.body, None)
    return spans


def focused_source(info: ModuleInfo, focus: set[str] | None) -> str:
    """Numbered source. Functions outside ``focus`` keep only their signature;
    their body is replaced by one marker line. Line numbers are the original
    ones. ``focus=None`` shows everything."""
    lines = info.source.splitlines()
    hidden: list[tuple[int, int]] = []  # (first body line, last line), 1-based
    if focus is not None:
        for qualname, (start, body, end) in _function_spans(info.source).items():
            # one-line functions (body on the def line) have nothing to hide
            if qualname not in focus and body != start:
                hidden.append((body, end))
    hidden.sort()
    out, i = [], 1
    while i <= len(lines):
        span = next((h for h in hidden if h[0] == i), None)
        if span:
            indent = len(lines[i - 1]) - len(lines[i - 1].lstrip())
            out.append(f"{'':4s} | {' ' * indent}...  # body not shown (lines {span[0]}-{span[1]})")
            i = span[1] + 1
            continue
        out.append(f"{i:4d} | {lines[i - 1]}")
        i += 1
    return "\n".join(out)


def function_cost(info: ModuleInfo, qualname: str) -> int:
    f = info.function(qualname)
    return estimate_tokens(f.source) + 40 if f else 0


def chunk_functions(info: ModuleInfo, budget_tokens: int, overhead_tokens: int) -> list[set[str]]:
    """Greedy split of the module's functions into groups whose focused view
    (skeleton + their full bodies) stays within ``budget_tokens``.
    ``overhead_tokens`` = system prompt + instructions + skeleton."""
    room = max(budget_tokens - overhead_tokens, 200)
    chunks: list[set[str]] = []
    current: set[str] = set()
    used = 0
    for f in info.functions:
        cost = function_cost(info, f.qualname)
        if current and used + cost > room:
            chunks.append(current)
            current, used = set(), 0
        current.add(f.qualname)  # a single function larger than room still gets its own chunk
        used += cost
    if current:
        chunks.append(current)
    return chunks or [set()]
