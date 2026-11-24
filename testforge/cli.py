"""Command-line interface.

    testforge generate examples/calculator.py            # offline mock backend
    testforge generate examples/calculator.py --llm ollama --model qwen2.5-coder:3b   # free, local
    testforge generate examples/calculator.py --llm groq --effort medium   # free tier, GROQ_API_KEY
    testforge generate examples/calculator.py --llm claude
    testforge generate examples/calculator.py --no-planner --no-mutation-feedback   # ablation
    testforge mutate examples/calculator.py tests/test_calculator.py               # score any suite
    testforge serve                                        # web dashboard
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .core.analysis import analyze_file
from .core.mutation import run_mutation_analysis
from .core.runner import run_tests
from .llm import LLMError
from .orchestrator import Orchestrator, save_report


def _print_event(kind: str, data: dict) -> None:
    if kind == "phase":
        detail = f" ({data['detail']})" if data.get("detail") else ""
        print(f"\n== {data['phase'].upper()}{detail}")
    elif kind == "agent":
        print(f"  {data.get('emoji', '')} {data['agent']}: {data['message']}")
    elif kind == "error":
        print(f"  !! {data['message']}", file=sys.stderr)


def cmd_generate(a: argparse.Namespace) -> int:
    cfg = Config(llm=a.llm, model=a.model, effort=a.effort, ollama_host=a.ollama_host,
                 num_ctx=a.num_ctx,
                 max_refine_iterations=a.max_iterations, target_mutation_score=a.target,
                 use_planner=not a.no_planner, use_fixer=not a.no_fixer,
                 use_mutation_feedback=not a.no_mutation_feedback,
                 mutation_workers=a.workers, max_mutants=a.max_mutants)
    try:
        report = Orchestrator(cfg, on_event=_print_event).run(a.module)
    except LLMError as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 2
    name = Path(a.module).stem
    test_path, report_path = save_report(report, a.out, name)
    print("\n" + "=" * 60)
    if report.final:
        f = report.final
        print(f"Tests: {f.tests_total}   line cov: {f.line_coverage:.0%}   branch cov: "
              f"{f.branch_coverage:.0%}   mutation score: {f.mutation_score:.0%}")
    print(f"Stop reason: {report.stop_reason}   time: {report.elapsed_s:.1f}s   "
          f"tokens: {report.total_input_tokens}/{report.total_output_tokens}   cost: ${report.cost_usd}")
    print(f"Tests  -> {test_path}\nReport -> {report_path}")
    return 0 if report.final else 1


def cmd_mutate(a: argparse.Namespace) -> int:
    info = analyze_file(a.module)
    code = Path(a.tests).read_text()
    extra = [str(Path(a.module).resolve().parent)]
    res = run_tests(info.module_name, info.source, code, extra_paths=extra)
    print(f"Tests {res.passed}/{res.total} passing, line {res.line_coverage:.0%}, "
          f"branch {res.branch_coverage:.0%}")
    if res.collection_error or res.failed:
        print("Suite must pass on the original code before mutation analysis.", file=sys.stderr)
        print(res.collection_error or res.raw_output[-2000:], file=sys.stderr)
        return 1
    mut = run_mutation_analysis(info, code, workers=a.workers, extra_paths=extra)
    print(f"Mutation score {mut.score:.1%} ({mut.killed}/{mut.scored_total})")
    for m in mut.survivors:
        print("  survived", m.describe())
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn
    uvicorn.run("testforge.web.app:app", host=a.host, port=a.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="testforge",
                                description="Multi-agent unit test generation with mutation feedback")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate tests for a Python module")
    g.add_argument("module")
    g.add_argument("--out", default="generated_tests")
    g.add_argument("--llm", choices=["mock", "ollama", "groq", "openrouter", "claude"], default="mock")
    g.add_argument("--model", default=None,
                   help="default: claude-opus-5-5 (claude), qwen2.5-coder:3b (ollama), "
                        "openai/gpt-oss-120b (groq)")
    g.add_argument("--ollama-host", default=None, help="default http://127.0.0.1:11434")
    g.add_argument("--num-ctx", type=int, default=8192, help="Ollama context window")
    g.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    g.add_argument("--max-iterations", type=int, default=3)
    g.add_argument("--target", type=float, default=0.9)
    g.add_argument("--no-planner", action="store_true")
    g.add_argument("--no-fixer", action="store_true")
    g.add_argument("--no-mutation-feedback", action="store_true")
    g.add_argument("--workers", type=int, default=4)
    g.add_argument("--max-mutants", type=int, default=None)
    g.set_defaults(func=cmd_generate)

    m = sub.add_parser("mutate", help="mutation-score an existing test suite")
    m.add_argument("module")
    m.add_argument("tests")
    m.add_argument("--workers", type=int, default=4)
    m.set_defaults(func=cmd_mutate)

    s = sub.add_parser("serve", help="start the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
