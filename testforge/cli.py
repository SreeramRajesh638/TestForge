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
    shown = mut.mutants if a.all else mut.survivors
    if a.operator:
        shown = [m for m in shown if m.operator in a.operator.upper().split(",")]
    for m in shown:
        print(f"  {m.status:9s}", m.describe())
    if mut.survivors and not a.all:
        print("\nSee any mutant's change with:  testforge mutants", a.module, "--show <id>")
    return 0


def cmd_mutants(a: argparse.Namespace) -> int:
    """List the mutants of a module, show one as a diff, or write it to a file."""
    import difflib
    from .core.mutation import generate_mutants
    info = analyze_file(a.module)
    pairs = generate_mutants(info)
    if a.operator:
        wanted = a.operator.upper().split(",")
        pairs = [(m, src) for m, src in pairs if m.operator in wanted]
    if a.show is None and a.write is None:
        counts: dict[str, int] = {}
        for m, _ in pairs:
            counts[m.operator] = counts.get(m.operator, 0) + 1
        print(f"{len(pairs)} mutants in {a.module}: " +
              ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
        for m, _ in pairs:
            print("  " + m.describe())
        print(f"\nShow one:   testforge mutants {a.module} --show <id>"
              f"\nWrite one:  testforge mutants {a.module} --write <id>   (creates <module>_mutant<id>.py)")
        return 0
    wanted_id = a.show if a.show is not None else a.write
    match = next(((m, src) for m, src in pairs if m.id == wanted_id), None)
    if match is None:
        print(f"No mutant with id {wanted_id}", file=sys.stderr)
        return 1
    m, src = match
    if a.write is not None:
        out = Path(a.module).with_name(f"{Path(a.module).stem}_mutant{m.id}.py")
        out.write_text(src)
        print(f"Wrote {out} ({m.describe()})")
        print("Note: the mutant is written in normalised form (comments removed).")
        return 0
    import ast
    original = ast.unparse(ast.parse(info.source)).splitlines()
    print(m.describe())
    print("\n".join(difflib.unified_diff(original, src.splitlines(), "original", f"mutant {m.id}",
                                         lineterm="", n=2)))
    return 0


def _serve_in_background(port: int) -> None:
    """Start the dashboard (which hosts the ShopLite demo) in a thread."""
    import os
    import threading
    import time
    import urllib.request
    import uvicorn
    os.environ["PORT"] = str(port)
    server = uvicorn.Server(uvicorn.Config("testforge.web.app:app", host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("demo server did not start")


def cmd_ui_test(a: argparse.Namespace) -> int:
    from .uiauto.engine import bug_detection_matrix, filter_scenarios, load_scenarios, run_scenarios
    base = a.url
    if not base:
        _serve_in_background(a.port)
        base = f"http://127.0.0.1:{a.port}"
    scenarios = filter_scenarios(load_scenarios(a.scenarios), query=a.grep or "")
    if not scenarios:
        print("No scenarios match.", file=sys.stderr)
        return 1
    bugs = [b for b in (a.bugs or "").split(",") if b]
    if a.bug_hunt:
        all_bugs = bugs or ["search", "clear", "cart", "total", "remove", "email", "password", "phone", "terms"]
        m = bug_detection_matrix(base, scenarios, all_bugs,
                                 on_event=lambda k, d: print(f"  injecting {d['bug']} ...") if k == "bug_start" else None)
        for bug, by in m["bugs"].items():
            print(f"{'CAUGHT' if by else 'MISSED':7s} {bug:10s} {'; '.join(by)}")
        print(f"Bug detection rate: {m['detected']}/{m['total']}")
        return 0 if not m["clean_failures"] else 1

    def show(kind, d):
        if kind == "scenario_done":
            print(f"{'PASS' if d['status'] == 'passed' else 'FAIL'}  {d['name']}  ({d['duration_ms']} ms)")
            if d["message"]:
                print("      " + d["message"].replace("\n", "\n      "))
    summary = run_scenarios(base, scenarios, bugs=bugs, headless=not a.headed, on_event=show)
    print(f"\n{summary.passed} passed, {summary.failed} failed in {summary.duration_ms / 1000:.1f}s")
    return 0 if summary.failed == 0 else 1


def cmd_serve(a: argparse.Namespace) -> int:
    import os
    import uvicorn
    os.environ["PORT"] = str(a.port)  # the UI automation engine browses this server
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
    m.add_argument("--all", action="store_true", help="list every mutant with its status, not only survivors")
    m.add_argument("--operator", help="only these operators, e.g. ROR,AOR")
    m.set_defaults(func=cmd_mutate)

    mm = sub.add_parser("mutants", help="list the mutants of a module, show or write one")
    mm.add_argument("module")
    mm.add_argument("--show", type=int, metavar="ID", help="print mutant ID as a diff")
    mm.add_argument("--write", type=int, metavar="ID", help="write mutant ID to <module>_mutant<ID>.py")
    mm.add_argument("--operator", help="only these operators: AOR, ROR, LCR, UOI, CRP, RET, ASR")
    mm.set_defaults(func=cmd_mutants)

    u = sub.add_parser("ui-test", help="run the UI automation scenarios (Playwright)")
    u.add_argument("--url", help="app base URL; default: start the built-in ShopLite demo")
    u.add_argument("--scenarios", help="scenario JSON file; default: ShopLite scenarios")
    u.add_argument("--grep", help="only scenarios whose name or tags contain this text")
    u.add_argument("--bugs", help="comma-separated bugs to inject, e.g. search,total")
    u.add_argument("--bug-hunt", action="store_true", help="inject each bug in turn; report which are caught")
    u.add_argument("--headed", action="store_true", help="show the browser window")
    u.add_argument("--port", type=int, default=8766)
    u.set_defaults(func=cmd_ui_test)

    s = sub.add_parser("serve", help="start the web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
