"""Saved dashboard runs, for the Runs page (history, comparison, replay).

Each finished run is written as one JSON file with its request, final report
and the full timestamped event stream, so it can be replayed exactly as it
happened. Files live in <data dir>/dashboard_runs (git-ignored).
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_lock = threading.Lock()


def data_dir() -> Path:
    base = os.environ.get("TESTFORGE_DATA_DIR")
    root = Path(base) if base else Path(__file__).resolve().parents[2] / "results"
    d = root / "dashboard_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(run_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{6,32}", run_id):
        raise ValueError("bad run id")
    return data_dir() / f"{run_id}.json"


def _slim_events(events: list[dict]) -> list[dict]:
    """Screenshots are large: keep a marker instead of the image data."""
    out = []
    for ev in events:
        data = ev.get("data")
        if isinstance(data, dict) and data.get("screenshot_png_b64"):
            data = {**data, "screenshot_png_b64": None, "had_screenshot": True}
        out.append({**ev, "data": data})
    return out


def summarize_generate(req: dict, report: dict | None, status: str) -> dict:
    final = (report or {}).get("iterations") or []
    last = final[-1] if final else {}
    first = final[0] if final else {}
    return {"module": req.get("module_name"), "backend": req.get("llm"),
            "model": (report or {}).get("config", {}).get("model"),
            "score": last.get("mutation_score"), "initial_score": first.get("mutation_score"),
            "tests": last.get("tests_total"), "line": last.get("line_coverage"),
            "branch": last.get("branch_coverage"), "mutants": last.get("mutants_total"),
            "rounds": max(0, len(final) - 1), "elapsed_s": (report or {}).get("elapsed_s"),
            "stop": (report or {}).get("stop_reason"), "status": status,
            "tokens_in": (report or {}).get("total_input_tokens"),
            "tokens_out": (report or {}).get("total_output_tokens")}


def summarize_ui(req: dict, report: dict | None, status: str) -> dict:
    report = report or {}
    if req.get("mode") == "bughunt":
        return {"module": "ShopLite bug hunt", "backend": "playwright", "status": status,
                "score": report.get("detection_rate"), "detected": report.get("detected"),
                "total": report.get("total"), "bugs": list(report.get("bugs", {}).keys())}
    total = (report.get("passed") or 0) + (report.get("failed") or 0)
    return {"module": "ShopLite scenarios", "backend": "playwright", "status": status,
            "score": (report.get("passed") / total) if total else None, "passed": report.get("passed"),
            "failed": report.get("failed"), "bugs": req.get("bugs", []),
            "elapsed_s": round((report.get("duration_ms") or 0) / 1000, 2)}


def save(record: dict) -> None:
    record = {**record, "events": _slim_events(record.get("events", []))}
    with _lock:
        _path(record["id"]).write_text(json.dumps(record, default=str), encoding="utf-8")


def list_runs(limit: int = 100) -> list[dict]:
    rows = []
    for p in sorted(data_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({k: r.get(k) for k in ("id", "kind", "started", "finished", "summary")})
    return rows


def get(run_id: str) -> dict | None:
    p = _path(run_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete(run_id: str) -> bool:
    p = _path(run_id)
    if p.exists():
        p.unlink()
        return True
    return False
