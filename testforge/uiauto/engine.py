"""Keyword-driven UI automation engine (Playwright).

A scenario is a name, optional tags and a list of steps. Each step is one
keyword with its arguments, e.g.

    {"action": "fill",  "target": "search-input", "value": "lamp"}
    {"action": "click", "target": "search-button"}
    {"action": "expect_text", "target": "results-count", "equals": "Showing 1 of 8 products"}

``target`` is a data-testid (the recommended, stable way to locate elements);
``selector`` takes any CSS selector instead. Assertions wait for the page
(Playwright auto-waiting) up to ``timeout_ms``. On a failure the engine
records the failing step, the message and a screenshot.

Keywords
    goto            path (relative to the base URL), optional query
    fill            target/selector, value
    type            target/selector, value (key by key)
    click           target/selector
    press           target/selector, key (e.g. "Enter")
    check/uncheck   target/selector
    select          target/selector, value
    wait            ms
    expect_text     target/selector, equals | contains
    expect_value    target/selector, equals
    expect_count    selector, count
    expect_visible / expect_hidden / expect_enabled / expect_disabled   target/selector
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

SCENARIO_DIR = Path(__file__).parent / "scenarios"
EventFn = Callable[[str, dict], None]


class StepError(Exception):
    pass


@dataclass
class StepResult:
    index: int
    action: str
    description: str
    status: str            # passed | failed | skipped
    duration_ms: int
    message: str = ""


@dataclass
class ScenarioResult:
    name: str
    tags: list[str]
    status: str            # passed | failed | error
    duration_ms: int
    steps: list[StepResult] = field(default_factory=list)
    message: str = ""
    screenshot_png_b64: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RunSummary:
    base_url: str
    bugs: list[str]
    results: list[ScenarioResult]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(r.status == "passed" for r in self.results)

    @property
    def failed(self) -> int:
        return len(self.results) - self.passed

    def to_dict(self) -> dict:
        return {"base_url": self.base_url, "bugs": self.bugs, "duration_ms": self.duration_ms,
                "passed": self.passed, "failed": self.failed,
                "results": [r.to_dict() for r in self.results]}


# ------------------------------------------------------------------ scenarios
def load_scenarios(path: str | Path | None = None) -> list[dict]:
    """Scenarios from a JSON file (a list, or {"scenarios": [...]}); default: ShopLite."""
    p = Path(path) if path else SCENARIO_DIR / "shoplite.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    scenarios = data["scenarios"] if isinstance(data, dict) else data
    fragments = data.get("fragments", {}) if isinstance(data, dict) else {}
    for s in scenarios:
        if "name" not in s or not isinstance(s.get("steps"), list):
            raise ValueError(f"scenario without name or steps: {s!r}"[:200])
        s["steps"] = _expand(s["steps"], fragments)
    return scenarios


def _expand(steps: list[dict], fragments: dict, depth: int = 0) -> list[dict]:
    """Replace {"include": "name", "with": {...}} by the named fragment's
    steps; "with" overrides step fields by target, e.g. a different email."""
    if depth > 5:
        raise ValueError("fragments nested too deeply")
    out = []
    for step in steps:
        if "include" not in step:
            out.append(step)
            continue
        if step["include"] not in fragments:
            raise ValueError(f"unknown fragment '{step['include']}'")
        overrides = step.get("with", {})
        for inner in _expand(fragments[step["include"]], fragments, depth + 1):
            inner = dict(inner)
            if inner.get("target") in overrides:
                change = overrides[inner["target"]]
                if change is None:
                    continue  # override None = leave this step out
                inner.update(change if isinstance(change, dict) else {"value": change})
            out.append(inner)
    return out


def filter_scenarios(scenarios: list[dict], query: str = "", names: list[str] | None = None) -> list[dict]:
    q = query.strip().lower()
    out = []
    for s in scenarios:
        if names is not None and s["name"] not in names:
            continue
        haystack = " ".join([s["name"], s.get("description", ""), *s.get("tags", [])]).lower()
        if q and q not in haystack:
            continue
        out.append(s)
    return out


# ------------------------------------------------------------------ steps
def _locator(page, step: dict):
    if "target" in step:
        return page.get_by_test_id(step["target"])
    if "selector" in step:
        return page.locator(step["selector"])
    raise StepError(f"step '{step.get('action')}' needs 'target' or 'selector'")


def describe(step: dict) -> str:
    a = step.get("action", "?")
    where = step.get("target") or step.get("selector") or step.get("path") or ""
    extra = ""
    for k in ("value", "equals", "contains", "count", "key", "ms"):
        if k in step:
            extra = f" {k}={step[k]!r}"
            break
    return f"{a} {where}{extra}".strip()


def _run_step(page, step: dict, base_url: str, bugs: list[str], timeout_ms: int) -> None:
    from playwright.sync_api import expect

    action = step.get("action")
    if action == "goto":
        query = step.get("query", "")
        if bugs:
            query = (query + "&" if query else "") + "bugs=" + ",".join(bugs)
        url = base_url.rstrip("/") + "/" + step.get("path", "").lstrip("/") + (f"?{query}" if query else "")
        page.goto(url, wait_until="domcontentloaded")
        return
    if action == "wait":
        page.wait_for_timeout(int(step.get("ms", 100)))
        return

    loc = _locator(page, step)
    if action == "fill":
        loc.fill(str(step.get("value", "")), timeout=timeout_ms)
    elif action == "type":
        loc.press_sequentially(str(step.get("value", "")), timeout=timeout_ms)
    elif action == "click":
        loc.click(timeout=timeout_ms)
    elif action == "press":
        loc.press(step["key"], timeout=timeout_ms)
    elif action == "check":
        loc.check(timeout=timeout_ms)
    elif action == "uncheck":
        loc.uncheck(timeout=timeout_ms)
    elif action == "select":
        loc.select_option(str(step["value"]), timeout=timeout_ms)
    elif action == "expect_text":
        if "equals" in step:
            expect(loc).to_have_text(str(step["equals"]), timeout=timeout_ms)
        elif "contains" in step:
            expect(loc).to_contain_text(str(step["contains"]), timeout=timeout_ms)
        else:
            raise StepError("expect_text needs 'equals' or 'contains'")
    elif action == "expect_value":
        expect(loc).to_have_value(str(step["equals"]), timeout=timeout_ms)
    elif action == "expect_count":
        expect(loc).to_have_count(int(step["count"]), timeout=timeout_ms)
    elif action == "expect_visible":
        expect(loc).to_be_visible(timeout=timeout_ms)
    elif action == "expect_hidden":
        expect(loc).to_be_hidden(timeout=timeout_ms)
    elif action == "expect_enabled":
        expect(loc).to_be_enabled(timeout=timeout_ms)
    elif action == "expect_disabled":
        expect(loc).to_be_disabled(timeout=timeout_ms)
    else:
        raise StepError(f"unknown action '{action}'")


def _short(msg: str) -> str:
    """First meaningful lines of a Playwright assertion message."""
    lines = [ln.rstrip() for ln in str(msg).splitlines() if ln.strip()]
    keep = []
    for ln in lines:
        if ln.lstrip().startswith(("Call log", "Aria snapshot")):
            break  # the rest is Playwright's internal trace
        if not ln.lstrip().startswith(("-  ", "- ")):
            keep.append(ln)
    return "\n".join(keep[:4])[:600]


# ------------------------------------------------------------------ runner
def run_scenarios(base_url: str, scenarios: list[dict], *, bugs: list[str] | None = None,
                  headless: bool = True, timeout_ms: int = 3000,
                  on_event: EventFn | None = None, viewport=(1280, 900)) -> RunSummary:
    """Run each scenario in a fresh browser context (isolated cookies/state)."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # optional dependency (not installed on e.g. Vercel)
        raise RuntimeError("UI automation needs Playwright: pip install -e \".[ui]\" "
                           "&& python -m playwright install chromium") from e

    emit = on_event or (lambda kind, data: None)
    bugs = list(bugs or [])
    results: list[ScenarioResult] = []
    start_all = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        try:
            for n, sc in enumerate(scenarios, 1):
                emit("scenario_start", {"index": n, "total": len(scenarios), "name": sc["name"]})
                ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
                page = ctx.new_page()
                res = ScenarioResult(sc["name"], sc.get("tags", []), "passed", 0)
                t0 = time.monotonic()
                failed = False
                for i, step in enumerate(sc["steps"], 1):
                    if failed:
                        res.steps.append(StepResult(i, step.get("action", "?"), describe(step), "skipped", 0))
                        continue
                    s0 = time.monotonic()
                    try:
                        _run_step(page, step, base_url, bugs, timeout_ms)
                        res.steps.append(StepResult(i, step.get("action", "?"), describe(step), "passed",
                                                    int((time.monotonic() - s0) * 1000)))
                    except (AssertionError, PlaywrightError, StepError, KeyError, ValueError) as e:
                        failed = True
                        res.status = "error" if isinstance(e, (StepError, KeyError, ValueError)) else "failed"
                        res.message = f"Step {i} ({describe(step)}): {_short(e)}"
                        res.steps.append(StepResult(i, step.get("action", "?"), describe(step), "failed",
                                                    int((time.monotonic() - s0) * 1000), _short(e)))
                        try:
                            res.screenshot_png_b64 = base64.b64encode(page.screenshot(full_page=True)).decode()
                        except PlaywrightError:
                            pass
                res.duration_ms = int((time.monotonic() - t0) * 1000)
                ctx.close()
                results.append(res)
                emit("scenario_done", res.to_dict())
                from .. import telemetry
                telemetry.emit("ui_scenario", "orchestrator", "playwright", duration_ms=res.duration_ms,
                               ok=res.status == "passed", detail=f"{res.status}: {res.name}")
        finally:
            browser.close()
    summary = RunSummary(base_url, bugs, results, int((time.monotonic() - start_all) * 1000))
    emit("run_done", {k: v for k, v in summary.to_dict().items() if k != "results"})
    return summary


def bug_detection_matrix(base_url: str, scenarios: list[dict], bugs: list[str],
                         on_event: EventFn | None = None) -> dict:
    """Run the scenarios once per injected bug (the UI analogue of mutation
    testing). A bug is *detected* when at least one scenario fails with it
    while every scenario passes on the clean app."""
    emit = on_event or (lambda kind, data: None)
    emit("bug_start", {"bug": "(clean app, no bug)"})
    clean = run_scenarios(base_url, scenarios)
    rows = {}
    for bug in bugs:
        emit("bug_start", {"bug": bug})
        summary = run_scenarios(base_url, scenarios, bugs=[bug])
        caught_by = [r.name for r in summary.results if r.status != "passed"]
        rows[bug] = caught_by
        emit("bug_done", {"bug": bug, "caught_by": caught_by})
    detected = sum(1 for v in rows.values() if v)
    return {"clean_failures": [r.name for r in clean.results if r.status != "passed"],
            "bugs": rows, "detected": detected, "total": len(bugs),
            "detection_rate": detected / len(bugs) if bugs else 0.0}
