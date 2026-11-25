"""Read experiment results (results/<name>/runs.csv, iterations.csv) for the
dashboard's Experiments page. Only real files written by experiments/run.py
are shown; nothing is computed that the CSVs do not contain."""

from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from pathlib import Path

from .history import data_dir

NUMERIC = {"tests", "line_coverage", "branch_coverage", "mutation_score_initial", "mutation_score_final",
           "mutants_total", "mutants_equivalent", "refine_rounds", "input_tokens", "output_tokens",
           "cost_usd", "elapsed_s", "iteration", "tests_total", "mutation_score", "mutants_survived", "rep"}


def results_root() -> Path:
    return data_dir().parent


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in list(r.items()):
            if k in NUMERIC:
                try:
                    r[k] = float(v) if v not in ("", None) else None
                except ValueError:
                    r[k] = None
    return rows


def list_experiments() -> list[dict]:
    out = []
    root = results_root()
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "runs.csv").exists()):
        rows = _read(d / "runs.csv")
        ok = [r for r in rows if r.get("status") == "ok"]
        out.append({"name": d.name, "runs": len(rows), "ok": len(ok),
                    "modules": len({r["module"] for r in rows}),
                    "configs": sorted({r["config"] for r in rows}),
                    "updated": (d / "runs.csv").stat().st_mtime})
    return out


def _mean(values):
    vals = [v for v in values if v is not None]
    return statistics.mean(vals) if vals else None


def load_experiment(name: str) -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
        return None
    d = results_root() / name
    if not (d / "runs.csv").exists():
        return None
    runs = _read(d / "runs.csv")
    iters = _read(d / "iterations.csv")
    ok = [r for r in runs if r.get("status") == "ok"]

    configs = sorted({r["config"] for r in runs})
    summary = []
    for c in configs:
        rows = [r for r in ok if r["config"] == c]
        summary.append({"config": c, "runs": len(rows),
                        "failed_runs": sum(1 for r in runs if r["config"] == c and r.get("status") != "ok"),
                        **{k: _mean(r[k] for r in rows) for k in (
                            "mutation_score_final", "mutation_score_initial", "line_coverage",
                            "branch_coverage", "tests", "elapsed_s", "cost_usd")}})

    per_module = defaultdict(dict)
    for r in runs:
        cell = per_module[r["module"]].setdefault(r["config"], {"final": [], "initial": [], "errors": 0})
        if r.get("status") == "ok":
            cell["final"].append(r["mutation_score_final"])
            cell["initial"].append(r["mutation_score_initial"])
        else:
            cell["errors"] += 1
    modules = [{"module": m, "configs": {c: {"final": _mean(v["final"]), "initial": _mean(v["initial"]),
                                             "errors": v["errors"]} for c, v in cfgs.items()}}
               for m, cfgs in sorted(per_module.items())]

    curves = defaultdict(lambda: defaultdict(list))
    for r in iters:
        if r.get("mutation_score") is not None and r.get("iteration") is not None:
            curves[r["config"]][int(r["iteration"])].append(r["mutation_score"])
    curve = {c: [{"iteration": i, "mutation_score": _mean(v), "n": len(v)} for i, v in sorted(points.items())]
             for c, points in curves.items()}
    return {"name": name, "configs": configs, "summary": summary, "modules": modules, "curves": curve,
            "errors": [{"module": r["module"], "config": r["config"], "error": (r.get("error") or "")[:240]}
                       for r in runs if r.get("status") != "ok"]}
