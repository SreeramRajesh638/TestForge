"""Experiment runner for the evaluation chapter.

Runs every module in a benchmark folder under several configurations
(baseline, full system, ablations), repeats each run, and writes:

    results/runs.csv        one row per (module, configuration, repetition)
    results/iterations.csv  one row per refinement round (for the curves)
    results/summary.md      mean ± std per configuration, ready for the thesis

Usage:
    python experiments/run.py                          # mock baseline only
    python experiments/run.py --configs all --reps 3   # needs ANTHROPIC_API_KEY
    python experiments/run.py --modules examples --configs claude_full,claude_no_feedback
    python experiments/run.py --configs ollama --ollama-model qwen2.5-coder:3b   # free, local

Configurations that need the Claude API are skipped (with a warning) when no
API key is set; results are never simulated.
"""

from __future__ import annotations

import argparse
import csv
import os
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from testforge.config import Config  # noqa: E402
from testforge.orchestrator import Orchestrator  # noqa: E402

BASE = Config(max_refine_iterations=3, target_mutation_score=0.95)

CONFIGS: dict[str, Config] = {
    # Non-LLM baseline: characterization tests + mutation-guided boundary inputs
    "mock_baseline": replace(BASE, llm="mock"),
    # Single-shot LLM: no planning, no repair, no feedback ("plain LLM")
    "claude_single_shot": replace(BASE, llm="claude", use_planner=False, use_fixer=False,
                                  use_mutation_feedback=False),
    # Full multi-agent system (the proposed approach)
    "claude_full": replace(BASE, llm="claude"),
    # Ablations: full system minus one agent
    "claude_no_planner": replace(BASE, llm="claude", use_planner=False),
    "claude_no_fixer": replace(BASE, llm="claude", use_fixer=False),
    "claude_no_feedback": replace(BASE, llm="claude", use_mutation_feedback=False),
    # The same design on a free cloud model (Groq free tier; GROQ_API_KEY)
    "groq_single_shot": replace(BASE, llm="groq", effort="medium", use_planner=False,
                                use_fixer=False, use_mutation_feedback=False),
    "groq_full": replace(BASE, llm="groq", effort="medium"),
    "groq_no_planner": replace(BASE, llm="groq", effort="medium", use_planner=False),
    "groq_no_fixer": replace(BASE, llm="groq", effort="medium", use_fixer=False),
    "groq_no_feedback": replace(BASE, llm="groq", effort="medium", use_mutation_feedback=False),
    # The same design on a free local model (Ollama)
    "ollama_single_shot": replace(BASE, llm="ollama", use_planner=False, use_fixer=False,
                                  use_mutation_feedback=False),
    "ollama_full": replace(BASE, llm="ollama"),
    "ollama_no_planner": replace(BASE, llm="ollama", use_planner=False),
    "ollama_no_fixer": replace(BASE, llm="ollama", use_fixer=False),
    "ollama_no_feedback": replace(BASE, llm="ollama", use_mutation_feedback=False),
}

# BASE was built with the mock backend, so replace() would carry its model
# name ("mock") into every config. Re-resolve each backend's default model.
CONFIGS = {name: replace(cfg, model=None) for name, cfg in CONFIGS.items()}

RUN_FIELDS = ["module", "config", "rep", "status", "stop_reason", "tests", "line_coverage",
              "branch_coverage", "mutation_score_initial", "mutation_score_final",
              "mutants_total", "mutants_equivalent", "refine_rounds", "input_tokens",
              "output_tokens", "cost_usd", "elapsed_s", "error"]
ITER_FIELDS = ["module", "config", "rep", "iteration", "tests_total", "line_coverage",
               "branch_coverage", "mutation_score", "mutants_survived", "input_tokens",
               "output_tokens", "elapsed_s"]


def run_one(module: Path, name: str, cfg: Config, rep: int) -> tuple[dict, list[dict]]:
    row = {"module": module.stem, "config": name, "rep": rep}
    try:
        report = Orchestrator(replace(cfg, seed=cfg.seed + rep)).run(str(module))
    except Exception as e:  # keep the sweep going; record the failure
        return {**row, "status": "error", "error": f"{type(e).__name__}: {e}"}, []
    if not report.iterations:
        return {**row, "status": "no_tests", "stop_reason": report.stop_reason,
                "elapsed_s": report.elapsed_s}, []
    first, last = report.iterations[0], report.iterations[-1]
    row.update(status="ok", stop_reason=report.stop_reason, tests=last.tests_total,
               line_coverage=last.line_coverage, branch_coverage=last.branch_coverage,
               mutation_score_initial=first.mutation_score,
               mutation_score_final=last.mutation_score, mutants_total=last.mutants_total,
               mutants_equivalent=last.mutants_equivalent, refine_rounds=len(report.iterations) - 1,
               input_tokens=report.total_input_tokens, output_tokens=report.total_output_tokens,
               cost_usd=report.cost_usd, elapsed_s=report.elapsed_s, error="")
    iters = [{"module": module.stem, "config": name, "rep": rep, "iteration": it.iteration,
              "tests_total": it.tests_total, "line_coverage": it.line_coverage,
              "branch_coverage": it.branch_coverage, "mutation_score": it.mutation_score,
              "mutants_survived": it.mutants_survived, "input_tokens": it.input_tokens,
              "output_tokens": it.output_tokens, "elapsed_s": it.elapsed_s}
             for it in report.iterations]
    return row, iters


def save(out: Path, rows: list[dict], iters: list[dict]) -> None:
    with open(out / "runs.csv", "w", newline="") as f:
        w = csv.DictWriter(f, RUN_FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in RUN_FIELDS} for r in rows)
    with open(out / "iterations.csv", "w", newline="") as f:
        w = csv.DictWriter(f, ITER_FIELDS)
        w.writeheader()
        w.writerows(iters)


def _fmt(values: list[float], pct: bool = True) -> str:
    if not values:
        return "–"
    m = statistics.mean(values)
    s = statistics.stdev(values) if len(values) > 1 else 0.0
    return f"{m:.1%} ± {s:.1%}" if pct else f"{m:.2f} ± {s:.2f}"


def summarize(rows: list[dict], out: Path, reps: int) -> str:
    configs = [c for c in CONFIGS if any(r["config"] == c for r in rows)]
    modules = sorted({r["module"] for r in rows})
    lines = ["# Experiment summary", "",
             f"Modules: {len(modules)} · repetitions per configuration: {reps} · "
             f"generated {time.strftime('%Y-%m-%d %H:%M')}", "",
             "## Overall (mean ± std over all modules and repetitions)", "",
             "| Configuration | Mutation score | Line cov. | Branch cov. | Tests | Cost (USD) | Time (s) | Failed runs |",
             "|---|---|---|---|---|---|---|---|"]
    for c in configs:
        ok = [r for r in rows if r["config"] == c and r["status"] == "ok"]
        bad = sum(1 for r in rows if r["config"] == c and r["status"] != "ok")
        lines.append(
            f"| {c} | {_fmt([r['mutation_score_final'] for r in ok])} "
            f"| {_fmt([r['line_coverage'] for r in ok])} | {_fmt([r['branch_coverage'] for r in ok])} "
            f"| {_fmt([r['tests'] for r in ok], pct=False)} | {_fmt([r['cost_usd'] for r in ok], pct=False)} "
            f"| {_fmt([r['elapsed_s'] for r in ok], pct=False)} | {bad} |")
    lines += ["", "## Mutation score per module (final / initial)", "",
              "| Module | " + " | ".join(configs) + " |", "|---" * (len(configs) + 1) + "|"]
    for m in modules:
        cells = []
        for c in configs:
            ok = [r for r in rows if r["config"] == c and r["module"] == m and r["status"] == "ok"]
            if ok:
                fin = statistics.mean(r["mutation_score_final"] for r in ok)
                ini = statistics.mean(r["mutation_score_initial"] for r in ok)
                cells.append(f"{fin:.0%} ({ini:.0%})")
            else:
                cells.append("–")
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    text = "\n".join(lines) + "\n"
    (out / "summary.md").write_text(text)
    return text


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--modules", default=str(ROOT / "examples"), help="folder of .py modules")
    p.add_argument("--configs", default="mock_baseline",
                   help="comma-separated names, 'all', 'claude' or 'ollama' (every config of that "
                        "backend plus the mock baseline). Available: " + ", ".join(CONFIGS))
    p.add_argument("--ollama-model", default=None, help="model for ollama_* configs")
    p.add_argument("--claude-model", default=None, help="model for claude_* configs")
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--out", default=str(ROOT / "results"))
    p.add_argument("--resume", action="store_true",
                   help="keep successful runs already in --out and only run the rest "
                        "(for spreading a sweep over several days of a free tier)")
    a = p.parse_args()

    if a.configs == "all":
        names = list(CONFIGS)
    elif a.configs in ("claude", "ollama", "groq"):
        names = ["mock_baseline"] + [n for n in CONFIGS if CONFIGS[n].llm == a.configs]
    else:
        names = [c.strip() for c in a.configs.split(",")]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        p.error(f"unknown configuration(s): {unknown}")
    if not os.environ.get("GROQ_API_KEY"):
        skipped = [n for n in names if CONFIGS[n].llm == "groq"]
        if skipped:
            print(f"! GROQ_API_KEY not set - skipping {', '.join(skipped)}", file=sys.stderr)
        names = [n for n in names if CONFIGS[n].llm != "groq"]
    if not os.environ.get("ANTHROPIC_API_KEY"):
        skipped = [n for n in names if CONFIGS[n].llm == "claude"]
        if skipped:
            print(f"! ANTHROPIC_API_KEY not set - skipping {', '.join(skipped)}", file=sys.stderr)
        names = [n for n in names if CONFIGS[n].llm != "claude"]
    if not names:
        print("Nothing to run.", file=sys.stderr)
        return 1

    for n in names:
        if CONFIGS[n].llm == "ollama" and a.ollama_model:
            CONFIGS[n] = replace(CONFIGS[n], model=a.ollama_model)
        if CONFIGS[n].llm == "claude" and a.claude_model:
            CONFIGS[n] = replace(CONFIGS[n], model=a.claude_model)

    modules = sorted(m for m in Path(a.modules).glob("*.py") if not m.name.startswith(("_", "test_")))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, iters = [], []
    if a.resume and (out / "runs.csv").exists():
        rows = [r for r in csv.DictReader(open(out / "runs.csv")) if r["status"] == "ok"]
        done = {(r["module"], r["config"], str(r["rep"])) for r in rows}
        if (out / "iterations.csv").exists():
            iters = [r for r in csv.DictReader(open(out / "iterations.csv"))
                     if (r["module"], r["config"], str(r["rep"])) in done]
        for r in rows:  # CSV gives strings; summarize() needs numbers
            for k in RUN_FIELDS:
                if k not in ("module", "config", "status", "stop_reason", "error") and r.get(k) not in ("", None):
                    r[k] = float(r[k]) if k != "rep" else int(r[k])
        print(f"Resuming: {len(rows)} successful runs kept", flush=True)
    else:
        done = set()
    total = len(modules) * len(names) * a.reps
    n = 0
    for module in modules:
        for name in names:
            for rep in range(a.reps):
                n += 1
                if (module.stem, name, str(rep)) in done:
                    print(f"[{n}/{total}] {module.stem} · {name} · rep {rep} · already done", flush=True)
                    continue
                print(f"[{n}/{total}] {module.stem} · {name} · rep {rep}", flush=True)
                row, it = run_one(module, name, CONFIGS[name], rep)
                rows.append(row)
                iters.extend(it)
                score = row.get("mutation_score_final")
                print(f"        {row['status']}" + (f" · mutation score {score:.0%}" if score is not None else "")
                      + (f" · {row['error']}" if row.get("error") else ""), flush=True)
                save(out, rows, iters)  # keep finished runs even if the sweep is interrupted

    save(out, rows, iters)
    print("\n" + summarize(rows, out, a.reps))
    print(f"Results written to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
