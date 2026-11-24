"""Tests for context-budget-aware prompting (focused views and chunking)."""

from pathlib import Path

from testforge.agents import prompts
from testforge.config import Config
from testforge.core.analysis import analyze_file, analyze_source
from testforge.core.budget import chunk_functions, estimate_tokens, focused_source
from testforge.core.suite import extract_tests, replace_tests, list_test_names
from testforge.orchestrator import Orchestrator

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

SRC = '''import math

LIMIT = 10


def first(x):
    """Doc."""
    y = x + 1
    return y * 2


def second(a, b):
    if a > b:
        return a
    return b


class Box:
    def __init__(self, v):
        self.v = v

    def grow(self, n):
        self.v += n
        return self.v
'''


def test_focused_view_keeps_line_numbers_and_hides_other_bodies():
    info = analyze_source(SRC, "mod")
    view = focused_source(info, {"second"})
    assert "  13 |     if a > b:" in view                 # focused function, original numbering
    assert "y = x + 1" not in view                         # other body hidden
    assert "   6 | def first(x):" in view                  # signature kept
    assert "body not shown (lines 7-9)" in view
    assert "   3 | LIMIT = 10" in view                     # module-level code kept
    assert "self.v += n" not in view and "  22 |     def grow(self, n):" in view


def test_no_focus_shows_everything():
    info = analyze_source(SRC, "mod")
    assert "body not shown" not in focused_source(info, None)


def test_chunks_cover_every_function_and_respect_room():
    info = analyze_file(EXAMPLES / "calculator.py")
    chunks = chunk_functions(info, budget_tokens=250, overhead_tokens=100)
    assert len(chunks) > 1
    assert set().union(*chunks) == {f.qualname for f in info.functions}


def test_focused_prompt_is_smaller():
    info = analyze_file(EXAMPLES / "calculator.py")
    full = estimate_tokens(prompts.writer_prompt(info, None))
    focused = estimate_tokens(prompts.writer_prompt(info, None, focus={"add"}))
    assert focused < full * 0.75


def test_extract_and_replace_tests():
    code = ("import pytest\nimport m\n\ndef test_a():\n    assert m.f() == 1\n\n"
            "def test_b():\n    assert m.f() == 2\n")
    assert list_test_names(code) == ["test_a", "test_b"]
    only_b = extract_tests(code, {"test_b"})
    assert "test_a" not in only_b and "import m" in only_b
    fixed = replace_tests(code, {"test_b"}, "import m\n\ndef test_b():\n    assert m.f() == 1\n")
    assert fixed.count("def test_b") == 1 and "== 2" not in fixed


def test_pipeline_runs_in_chunks_under_a_tight_budget():
    events = []
    cfg = Config(llm="mock", max_prompt_tokens=700, max_refine_iterations=1)
    report = Orchestrator(cfg, on_event=lambda k, d: events.append((k, d))).run(
        str(EXAMPLES / "calculator.py"))
    assert any("chunks of functions" in d.get("message", "") for k, d in events if k == "agent")
    assert report.final and report.final.tests_passing == report.final.tests_total > 0


def test_groq_gets_a_default_prompt_budget():
    assert Config(llm="groq").max_prompt_tokens == 4500
    assert Config(llm="claude").max_prompt_tokens is None
