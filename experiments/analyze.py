"""Statistical analysis and figures for the evaluation chapter.

Reads runs.csv / iterations.csv written by experiments/run.py and produces:

    <dir>/analysis.md                 tables + statistical tests (paste into the thesis)
    <dir>/figures/mutation_by_config.png
    <dir>/figures/refinement_curve.png
    <dir>/figures/feedback_gain_<config>.png
    <dir>/figures/ablation.png        (when ablation configurations were run)

Statistics: configurations are compared per module (each module's mean over
repetitions), with a two-sided Wilcoxon signed-rank test (exact) and the
Vargha-Delaney A12 effect size.

    python experiments/analyze.py results/benchmark_mock
"""

from __future__ import annotations

import argparse
import csv
import itertools
import statistics
from collections import defaultdict
from pathlib import Path

# Reference categorical palette (fixed order, validated for colour-vision deficiency).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK2, GRID, SURFACE, MUTED = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb", "#b9b8b2"
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]

ORDER = ["mock_baseline", "claude_single_shot", "claude_full", "claude_no_planner", "claude_no_fixer",
         "claude_no_feedback", "ollama_single_shot", "ollama_full", "ollama_no_planner",
         "ollama_no_fixer", "ollama_no_feedback", "groq_single_shot", "groq_full",
         "groq_no_planner", "groq_no_fixer", "groq_no_feedback",
         "mock_blindspot", "claude_blindspot", "ollama_blindspot", "groq_blindspot"]
# Baseline of each "TestForge + Blind Spot Intelligence" configuration
BLINDSPOT_PAIRS = [("mock_baseline", "mock_blindspot"), ("claude_full", "claude_blindspot"),
                   ("ollama_full", "ollama_blindspot"), ("groq_full", "groq_blindspot")]
LABELS = {
    "mock_baseline": "Non-LLM baseline", "claude_single_shot": "Claude, single prompt",
    "claude_full": "Claude, TestForge", "claude_no_planner": "Claude, no Planner",
    "claude_no_fixer": "Claude, no Fixer", "claude_no_feedback": "Claude, no feedback",
    "ollama_single_shot": "Local model, single prompt", "ollama_full": "Local model, TestForge",
    "ollama_no_planner": "Local model, no Planner", "ollama_no_fixer": "Local model, no Fixer",
    "ollama_no_feedback": "Local model, no feedback",
    "groq_single_shot": "gpt-oss-120b, single prompt", "groq_full": "gpt-oss-120b, TestForge",
    "groq_no_planner": "gpt-oss-120b, no Planner", "groq_no_fixer": "gpt-oss-120b, no Fixer",
    "groq_no_feedback": "gpt-oss-120b, no feedback",
    "mock_blindspot": "Non-LLM + Blind Spot Intelligence", "claude_blindspot": "Claude, TestForge + Blind Spot",
    "ollama_blindspot": "Local model, TestForge + Blind Spot", "groq_blindspot": "gpt-oss-120b, TestForge + Blind Spot",
}


def label(c: str) -> str:
    return LABELS.get(c, c)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(directory: Path):
    runs = [r for r in csv.DictReader(open(directory / "runs.csv", encoding="utf-8")) if r["status"] == "ok"]
    for r in runs:
        for k, v in r.items():
            if k not in ("module", "config", "status", "stop_reason", "error"):
                r[k] = _num(v)
    iters = []
    if (directory / "iterations.csv").exists():
        iters = list(csv.DictReader(open(directory / "iterations.csv", encoding="utf-8")))
        for r in iters:
            for k in ("iteration", "mutation_score", "line_coverage", "branch_coverage", "tests_total"):
                r[k] = _num(r[k])
    configs = sorted({r["config"] for r in runs}, key=lambda c: ORDER.index(c) if c in ORDER else 99)
    return runs, iters, configs


def per_module(runs, config, metric="mutation_score_final") -> dict[str, float]:
    by = defaultdict(list)
    for r in runs:
        if r["config"] == config and r[metric] is not None:
            by[r["module"]].append(r[metric])
    return {m: statistics.mean(v) for m, v in by.items()}


# ----------------------------------------------------------------- statistics
def wilcoxon_exact(x: list[float], y: list[float]) -> tuple[float | None, int]:
    """Two-sided exact Wilcoxon signed-rank p-value (zero differences dropped).
    Returns (p, n_used); p is None when fewer than 2 non-zero pairs."""
    d = [a - b for a, b in zip(x, y) if abs(a - b) > 1e-12]
    n = len(d)
    if n < 2:
        return None, n
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:  # average ranks for ties
        j = i
        while j + 1 < n and abs(abs(d[order[j + 1]]) - abs(d[order[i]])) < 1e-12:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    w_plus = sum(r for r, v in zip(ranks, d) if v > 0)
    total = sum(ranks)
    observed = min(w_plus, total - w_plus)
    if n > 20:  # normal approximation keeps runtime bounded
        mu, sigma = total / 2, (sum(r * r for r in ranks) / 4) ** 0.5
        z = (abs(w_plus - mu) - 0.5) / sigma
        return min(1.0, 2 * (1 - statistics.NormalDist().cdf(z))), n
    count = 0
    for signs in itertools.product((0, 1), repeat=n):
        s = sum(r for r, take in zip(ranks, signs) if take)
        if min(s, total - s) <= observed + 1e-9:
            count += 1
    return min(1.0, count / 2 ** n), n


def a12(x: list[float], y: list[float]) -> float:
    """Vargha-Delaney A12: probability that a value from x beats one from y."""
    wins = sum((a > b) + 0.5 * (a == b) for a in x for b in y)
    return wins / (len(x) * len(y))


def a12_label(a: float) -> str:
    d = abs(a - 0.5)
    return "negligible" if d < 0.06 else "small" if d < 0.14 else "medium" if d < 0.21 else "large"


# ----------------------------------------------------------------- figures
def _style(plt):
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "text.color": INK, "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
        "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    })


def fig_by_config(plt, runs, configs, out: Path):
    rows = []
    for c in configs:
        vals = list(per_module(runs, c).values())
        if vals:
            rows.append((c, statistics.mean(vals), statistics.stdev(vals) if len(vals) > 1 else 0, len(vals)))
    rows.sort(key=lambda r: r[1])
    fig, ax = plt.subplots(figsize=(7.5, 0.55 * len(rows) + 1.4))
    y = range(len(rows))
    ax.barh(list(y), [r[1] * 100 for r in rows], height=0.5, color=SERIES[0], zorder=2)
    ax.errorbar([r[1] * 100 for r in rows], list(y), xerr=[r[2] * 100 for r in rows], fmt="none",
                ecolor=INK2, elinewidth=1.2, capsize=3, zorder=3)
    for i, r in enumerate(rows):
        ax.text(min(r[1] * 100 + r[2] * 100 + 1.5, 101), i, f"{r[1]:.0%}", va="center", color=INK, fontsize=9)
    ax.set_yticks(list(y), [label(r[0]) for r in rows])
    ax.set_xlim(0, 110)
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Final mutation score (mean over modules; bar = ±1 SD)")
    best = rows[-1]
    if len(rows) == 1:
        ax.set_title(f"{label(best[0])}: mean mutation score {best[1]:.0%} over {best[3]} modules")
    else:
        ax.set_title(f"{label(best[0])} reaches the highest mutation score ({best[1]:.0%})")
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)


def fig_curve(plt, iters, configs, out: Path):
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    max_it = 0
    for idx, c in enumerate(configs):
        by_it = defaultdict(list)
        modules = {r["module"] for r in iters if r["config"] == c}
        last: dict[tuple, float] = {}
        for r in iters:
            if r["config"] == c and r["mutation_score"] is not None:
                by_it[int(r["iteration"])].append(r["mutation_score"])
                key = (r["module"], r["rep"])
                last[key] = max(last.get(key, -1), int(r["iteration"]))
        if not by_it:
            continue
        # runs that stopped early keep their last score in later rounds
        top = max(by_it)
        series = []
        for it in range(top + 1):
            vals = []
            for (m, rep), stop in last.items():
                rows = [r for r in iters if r["config"] == c and r["module"] == m and r["rep"] == rep
                        and int(r["iteration"]) <= it]
                if rows:
                    vals.append(max(rows, key=lambda r: r["iteration"])["mutation_score"])
            series.append(statistics.mean(vals) * 100 if vals else None)
        max_it = max(max_it, top)
        color = SERIES[idx % len(SERIES)]
        xs = list(range(len(series)))
        ax.plot(xs, series, color=color, linewidth=2, marker=MARKERS[idx % len(MARKERS)], markersize=6,
                label=f"{label(c)} (n={len(modules)})", zorder=3)
        ax.annotate(f"{series[-1]:.0f}%", (xs[-1], series[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", color=INK, fontsize=9)
    ax.set_xticks(range(max_it + 1), ["initial"] + [f"round {i}" for i in range(1, max_it + 1)])
    ax.set_ylim(0, 105)
    ax.set_ylabel("Mutation score (mean over modules, %)")
    ax.set_title("Mutation score after each refinement round")
    ax.legend(frameon=False, loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)


def fig_gain(plt, runs, config, out: Path):
    ini = per_module(runs, config, "mutation_score_initial")
    fin = per_module(runs, config, "mutation_score_final")
    mods = sorted(fin, key=lambda m: fin[m])
    fig, ax = plt.subplots(figsize=(7.5, 0.38 * len(mods) + 1.5))
    for i, m in enumerate(mods):
        ax.plot([ini[m] * 100, fin[m] * 100], [i, i], color=MUTED, linewidth=2, zorder=2)
    ax.scatter([ini[m] * 100 for m in mods], range(len(mods)), color=MUTED, s=40, zorder=3,
               label="Before feedback", edgecolors=SURFACE, linewidths=1.5)
    ax.scatter([fin[m] * 100 for m in mods], range(len(mods)), color=SERIES[0], s=48, zorder=4,
               label="After feedback", edgecolors=SURFACE, linewidths=1.5)
    for i, m in enumerate(mods):
        ax.text(fin[m] * 100 + 2, i, f"{fin[m]:.0%}", va="center", fontsize=8.5, color=INK)
    ax.set_yticks(range(len(mods)), mods)
    ax.set_xlim(0, 110)
    ax.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    ax.grid(axis="y", visible=False)
    gain = statistics.mean(fin[m] - ini[m] for m in mods)
    ax.set_title(f"{label(config)}: feedback adds {gain * 100:.1f} points on average")
    ax.set_xlabel("Mutation score")
    ax.legend(frameon=False, loc="lower right", fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)


def fig_ablation(plt, runs, backend, out: Path) -> bool:
    full = per_module(runs, f"{backend}_full")
    parts = [(c, per_module(runs, c)) for c in (f"{backend}_no_planner", f"{backend}_no_fixer",
                                                f"{backend}_no_feedback")]
    parts = [(c, v) for c, v in parts if v]
    if not full or not parts:
        return False
    rows = []
    for c, v in parts:
        common = [m for m in full if m in v]
        rows.append((c, statistics.mean(v[m] - full[m] for m in common) * 100))
    fig, ax = plt.subplots(figsize=(7.5, 0.6 * len(rows) + 1.4))
    ax.barh(range(len(rows)), [r[1] for r in rows], height=0.5, color=SERIES[1], zorder=2)
    ax.axvline(0, color=INK2, linewidth=1)
    for i, r in enumerate(rows):
        ax.text(r[1] + (0.4 if r[1] >= 0 else -0.4), i, f"{r[1]:+.1f}", va="center",
                ha="left" if r[1] >= 0 else "right", fontsize=9, color=INK)
    ax.set_yticks(range(len(rows)), [label(r[0]) for r in rows])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Change in mutation score vs. full TestForge (percentage points)")
    worst = min(rows, key=lambda r: r[1])
    ax.set_title(f"Removing {label(worst[0]).split(', no ')[-1]} costs the most ({worst[1]:+.1f} points)")
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    plt.close(fig)
    return True


# ----------------------------------------------------------------- report
def report(runs, iters, configs, directory: Path, figures: list[str]) -> str:
    L = ["# Results analysis", "", f"Source: `{directory}` · {len(runs)} successful runs · "
         f"{len({r['module'] for r in runs})} modules · {len(configs)} configurations", ""]
    L += ["## Summary per configuration", "",
          "Means over modules (each module first averaged over its repetitions).", "",
          "| Configuration | Modules | Mutation score | Initial score | Line cov. | Branch cov. | Tests | Cost (USD) |",
          "|---|---|---|---|---|---|---|---|"]
    for c in configs:
        ms = per_module(runs, c)
        def mean_of(metric, pct=True):
            v = list(per_module(runs, c, metric).values())
            if not v:
                return "–"
            m, s = statistics.mean(v), (statistics.stdev(v) if len(v) > 1 else 0)
            return f"{m:.1%} ± {s:.1%}" if pct else f"{m:.2f}"
        L.append(f"| {label(c)} | {len(ms)} | {mean_of('mutation_score_final')} | "
                 f"{mean_of('mutation_score_initial')} | {mean_of('line_coverage')} | "
                 f"{mean_of('branch_coverage')} | {mean_of('tests', False)} | {mean_of('cost_usd', False)} |")

    pairs = []
    for backend in ("claude", "ollama", "groq"):
        full = f"{backend}_full"
        if full in configs:
            pairs += [(full, c) for c in configs if c != full and (c.startswith(backend) or c == "mock_baseline")]
    if pairs:
        L += ["", "## Statistical comparison", "",
              "Paired by module. Wilcoxon signed-rank test (two-sided, exact); A12 = probability that "
              "the first configuration scores higher on a module (0.5 = no difference).", "",
              "| Comparison | Mean difference | p-value | A12 | Effect |", "|---|---|---|---|---|"]
        for a, b in pairs:
            xa, xb = per_module(runs, a), per_module(runs, b)
            common = sorted(set(xa) & set(xb))
            if not common:
                continue
            x, y = [xa[m] for m in common], [xb[m] for m in common]
            p, n = wilcoxon_exact(x, y)
            eff = a12(x, y)
            diff = statistics.mean(i - j for i, j in zip(x, y)) * 100
            L.append(f"| {label(a)} vs {label(b)} | {diff:+.1f} pts | "
                     f"{'n/a' if p is None else f'{p:.4f}'} | {eff:.2f} | {a12_label(eff)} |")
        L += ["", "With 15 modules the smallest possible two-sided p-value is about 0.00006; "
              "report p < 0.05 as significant and always give the effect size alongside it."]

    for c in configs:
        ini, fin = per_module(runs, c, "mutation_score_initial"), per_module(runs, c, "mutation_score_final")
        common = sorted(set(ini) & set(fin))
        if len(common) < 2 or all(abs(fin[m] - ini[m]) < 1e-12 for m in common):
            continue
        p, n = wilcoxon_exact([fin[m] for m in common], [ini[m] for m in common])
        L += ["", f"**Feedback effect, {label(c)}:** mean gain "
              f"{statistics.mean(fin[m] - ini[m] for m in common) * 100:+.1f} points over {len(common)} modules "
              f"(Wilcoxon p = {'n/a' if p is None else f'{p:.4f}'}, A12 = "
              f"{a12([fin[m] for m in common], [ini[m] for m in common]):.2f})."]

    L += blindspot_section(runs, configs, directory)

    L += ["", "## Per-module mutation score (final, with initial in brackets)", "",
          "| Module | " + " | ".join(label(c) for c in configs) + " |", "|---" * (len(configs) + 1) + "|"]
    for m in sorted({r["module"] for r in runs}):
        cells = []
        for c in configs:
            f, i = per_module(runs, c).get(m), per_module(runs, c, "mutation_score_initial").get(m)
            cells.append("–" if f is None else f"{f:.0%} ({i:.0%})")
        L.append(f"| {m} | " + " | ".join(cells) + " |")

    L += ["", "## Figures", ""] + [f"![{f}](figures/{f})" for f in figures]
    text = "\n".join(L) + "\n"
    (directory / "analysis.md").write_text(text, encoding="utf-8")
    return text


def _mean(values):
    v = [x for x in values if x is not None]
    return statistics.mean(v) if v else None


def blindspot_section(runs, configs, directory: Path) -> list[str]:
    """Original pipeline vs TestForge + Blind Spot Intelligence, from recorded runs only."""
    L: list[str] = []
    pairs = [(a, b) for a, b in BLINDSPOT_PAIRS if a in configs and b in configs]
    if not pairs:
        return L
    L += ["", "## Blind Spot Intelligence vs the original pipeline", "",
          "Same modules, mutation operators, round limit and target; the Blind Spot configuration adds the "
          "diagnosis / counterexample / verified-corrective-test agent. *Raw* scores count every mutant the "
          "Writer flagged as equivalent as a survivor (LLM judgement is not trusted to shrink the denominator). "
          "*Held-out* scores use mutants hidden from every agent (when the sweep ran with `--holdout`)."]
    metrics = [("Initial mutation score", "mutation_score_initial", True), ("Final mutation score", "mutation_score_final", True),
               ("Final raw score", "mutation_score_raw_final", True), ("Held-out score (final)", "holdout_score_final", True),
               ("Held-out score (initial)", "holdout_score_initial", True), ("Tests", "tests", False),
               ("Refinement rounds", "refine_rounds", False), ("Input + output tokens", None, False),
               ("Time (s)", "elapsed_s", False), ("Cost (USD)", "cost_usd", False)]
    for a, b in pairs:
        L += ["", f"### {label(b)} vs {label(a)}", "", f"| Metric | {label(a)} | {label(b)} |", "|---|---|---|"]
        for name, key, pct in metrics:
            vals = []
            for c in (a, b):
                rows = [r for r in runs if r["config"] == c]
                if key is None:
                    v = _mean((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in rows)
                else:
                    v = _mean(r.get(key) for r in rows)
                vals.append("–" if v is None else (f"{v:.1%}" if pct else f"{v:,.2f}"))
            L.append(f"| {name} | {vals[0]} | {vals[1]} |")
        rb = [r for r in runs if r["config"] == b]
        prop = int(sum(r.get("corrective_tests_proposed") or 0 for r in rb))
        valid = int(sum(r.get("corrective_tests_valid") or 0 for r in rb))
        dfile = directory / "diagnoses.csv"
        drows = [d for d in csv.DictReader(open(dfile, encoding="utf-8")) if d["config"] == b] if dfile.exists() else []
        if drows:  # one row per diagnosis: the consistent source for these counts
            found = len(drows)
            res = sum(1 for d in drows if d["resolution_status"] == "resolved")
            conf = sum(1 for d in drows if d["finding_status"] == "confirmed")
            eqv = sum(1 for d in drows if "Potential equivalent mutant" in d["secondary_labels"])
            cats = {}
            for d in drows:
                cats[d["primary_category"]] = cats.get(d["primary_category"], 0) + 1
        else:
            found = int(sum(r.get("blind_spots") or 0 for r in rb))
            res = int(sum(r.get("blind_spots_resolved") or 0 for r in rb))
            conf = int(sum(r.get("confirmed_diagnoses") or 0 for r in rb))
            eqv = int(sum(r.get("potential_equivalent") or 0 for r in rb))
            cats = {}
        L += ["", f"- Blind spots diagnosed: **{found}** over {len(rb)} modules; verified resolutions: **{res}** "
                  f"({res / found:.0%} of diagnosed)." if found else f"- No blind spots were diagnosed over {len(rb)} modules.",
              f"- Confirmed diagnoses (measured category + distinguishing input or observed difference): {conf}"
              + (f" of {found}." if found else "."),
              *([f"- Primary categories: " + ", ".join(f"{k} {v}" for k, v in sorted(cats.items(), key=lambda x: -x[1])) + "."] if cats else []),
              f"- Corrective test validity: {valid} of {prop} proposed corrective tests passed on the original program"
              + (f" ({valid / prop:.0%})." if prop else "."),
              f"- Suspected equivalent mutants (no distinguishing input found; reported, not removed): {eqv}."]
        for c in (a, b):
            rows = [r for r in runs if r["config"] == c]
            gained = int(sum((r.get("mutants_killed_final") or 0) - (r.get("mutants_killed_initial") or 0) for r in rows))
            secs = sum(r.get("elapsed_s") or 0 for r in rows)
            cost = sum(r.get("cost_usd") or 0 for r in rows)
            toks = sum((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0) for r in rows)
            if gained > 0:
                L.append(f"- {label(c)}: {gained} additional mutants killed by refinement; "
                         f"{secs / gained:.1f} s, {toks / gained:,.0f} tokens and ${cost / gained:.4f} per additional killed mutant.")
            elif any(r.get("mutants_killed_final") is not None for r in rows):
                L.append(f"- {label(c)}: no additional mutants killed by refinement.")
        L += ["", "| Comparison (paired by module) | Mean difference | p-value | A12 |", "|---|---|---|---|"]
        for name, key in (("Final score", "mutation_score_final"), ("Raw score", "mutation_score_raw_final"),
                          ("Held-out score", "holdout_score_final")):
            xa, xb = per_module(runs, b, key), per_module(runs, a, key)
            common = sorted(set(xa) & set(xb))
            if len(common) < 2:
                continue
            x, y = [xa[m] for m in common], [xb[m] for m in common]
            p, _ = wilcoxon_exact(x, y)
            L.append(f"| {name}: {label(b)} vs {label(a)} | {statistics.mean(i - j for i, j in zip(x, y)) * 100:+.1f} pts | "
                     f"{'n/a' if p is None else f'{p:.4f}'} | {a12(x, y):.2f} |")

    diag = directory / "diagnoses.csv"
    if diag.exists():
        rows = list(csv.DictReader(open(diag, encoding="utf-8")))
        labelled = [r for r in rows if (r.get("manual_label") or "").strip()]
        L += ["", "### Diagnosis accuracy", ""]
        if not labelled:
            L.append(f"`diagnoses.csv` lists {len(rows)} diagnoses. No manual labels yet: fill the `manual_label` "
                     "column (reachability / infection / propagation / revealability / inconclusive) for a sample "
                     "and re-run this script to measure agreement.")
        else:
            agree = sum(1 for r in labelled if r["manual_label"].strip().lower() == r["primary_category"])
            L.append(f"Agreement with manual labels: **{agree}/{len(labelled)} ({agree / len(labelled):.0%})**.")
            cats = ["reachability", "infection", "propagation", "revealability", "inconclusive"]
            L += ["", "| Manual \\ Engine | " + " | ".join(cats) + " |", "|---" * (len(cats) + 1) + "|"]
            for mc in cats:
                L.append(f"| {mc} | " + " | ".join(str(sum(1 for r in labelled if r['manual_label'].strip().lower() == mc
                                                            and r['primary_category'] == ec)) for ec in cats) + " |")
    return L


def fig_blindspot(plt, runs, a: str, b: str, out: Path) -> bool:
    xa, xb = per_module(runs, a), per_module(runs, b)
    ha, hb = per_module(runs, a, "holdout_score_final"), per_module(runs, b, "holdout_score_final")
    mods = sorted(set(xa) & set(xb))
    if not mods:
        return False
    fig, ax = plt.subplots(figsize=(max(7, len(mods) * 0.55), 4.2))
    idx = range(len(mods))
    w = 0.38
    ax.bar([i - w / 2 for i in idx], [xa[m] * 100 for m in mods], w, label=label(a), color="#7c8db5")
    ax.bar([i + w / 2 for i in idx], [xb[m] * 100 for m in mods], w, label=label(b), color="#a855f7")
    if ha and hb:
        ax.scatter([i - w / 2 for i in idx if mods[i] in ha], [ha[mods[i]] * 100 for i in idx if mods[i] in ha],
                   marker="_", s=160, color="#1f2937", label="held-out score", zorder=3)
        ax.scatter([i + w / 2 for i in idx if mods[i] in hb], [hb[mods[i]] * 100 for i in idx if mods[i] in hb],
                   marker="_", s=160, color="#1f2937", zorder=3)
    ax.set_xticks(list(idx))
    ax.set_xticklabels(mods, rotation=40, ha="right")
    ax.set_ylabel("Final mutation score (%)")
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=160)
    plt.close(fig)
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("directory", nargs="?", default="results")
    a = p.parse_args()
    directory = Path(a.directory)
    runs, iters, configs = load(directory)
    if not runs:
        print("No successful runs found.")
        return 1

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)
    figdir = directory / "figures"
    figdir.mkdir(exist_ok=True)
    figures = ["mutation_by_config.png"]
    fig_by_config(plt, runs, configs, figdir / figures[0])
    if iters:
        figures.append("refinement_curve.png")
        fig_curve(plt, iters, configs, figdir / "refinement_curve.png")
    for c in configs:
        if any(r["config"] == c and r["refine_rounds"] for r in runs):
            name = f"feedback_gain_{c}.png"
            fig_gain(plt, runs, c, figdir / name)
            figures.append(name)
    for backend in ("claude", "ollama", "groq"):
        if fig_ablation(plt, runs, backend, figdir / f"ablation_{backend}.png"):
            figures.append(f"ablation_{backend}.png")
    for a, b in BLINDSPOT_PAIRS:
        if a in configs and b in configs and fig_blindspot(plt, runs, a, b, figdir / f"blindspot_{b}.png"):
            figures.append(f"blindspot_{b}.png")
    print(report(runs, iters, configs, directory, figures))
    print(f"Figures in {figdir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
