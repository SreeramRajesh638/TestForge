"""Interactive Blind Spot sessions for the dashboard workspace.

A session holds one module, its current test suite and a BlindSpotEngine. Each
user action (analyse, investigate, generate / execute / verify a corrective
test, resolve all, cancel) runs as a background job and reports progress as
events; the suite only changes when a corrective test passes all three
verification checks. Sessions are saved to the run-history directory so the
results survive a restart (as read-only snapshots).
"""

from __future__ import annotations

import ast
import csv
import html
import io
import json
import queue
import threading
import time
import uuid
from pathlib import Path

from ..config import Config
from ..core.analysis import analyze_source
from ..core.mutation import run_mutation_analysis
from ..core.suite import count_tests
from ..llm import make_llm
from ..models import MutationResult
from . import evidence as ev_mod
from .engine import BlindSpotEngine, Cancelled
from .schema import PRIMARY_LABELS

DEMO_DIR = Path(__file__).resolve().parents[2] / "examples" / "blindspot_demo"
MAX_SESSIONS = 30


class Busy(Exception):
    pass


class Session:
    def __init__(self, *, module_name: str, source: str, tests: str, llm: str = "mock", model: str | None = None,
                 origin: str = "custom", from_run: str | None = None, extra_paths: list[str] | None = None) -> None:
        self.id = uuid.uuid4().hex[:10]
        self.created = time.time()
        self.module_name, self.source = module_name, source
        self.original_tests = tests
        self.tests = tests
        self.origin, self.from_run = origin, from_run
        self.llm_kind, self.model = llm, model
        self.config = Config(llm=llm, model=model, mutation_workers=4, blind_spot_max_investigations=20)
        self.info = analyze_source(source, module_name)
        self.extra_paths = extra_paths or []
        llm_client = None
        if llm != "mock":
            llm_client = make_llm(llm, self.config.model, self.config.effort, self.extra_paths)
        self.engine = BlindSpotEngine(self.info, llm_client, self.config, self._engine_emit, session_id=self.id,
                                      extra_paths=self.extra_paths, max_investigations=20)
        self.mut: MutationResult | None = None
        self.versions = [{"label": "original suite", "tests": tests, "test_count": count_tests(tests),
                          "score": None, "t": 0.0}]
        self.events: list[dict] = []
        self.subscribers: list[queue.Queue] = []
        self.status = "idle"
        self.action: str | None = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self.round = 0

    # ---------------------------------------------------------------- events
    def _engine_emit(self, kind: str, data: dict) -> None:
        self.emit(kind, data)

    def emit(self, kind: str, data: dict) -> None:
        item = {"kind": kind, "data": data, "t": round(time.time() - self.created, 3), "seq": len(self.events)}
        self.events.append(item)
        for q in list(self.subscribers):
            q.put(item)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    # ---------------------------------------------------------------- jobs
    def run_job(self, name: str, fn, on_done=None) -> None:
        with self._lock:
            if self.status == "running":
                raise Busy(f"'{self.action}' is still running")
            self.status, self.action, self.error = "running", name, None
            self.engine.cancel.clear()
        self.emit("action", {"name": name, "state": "started"})

        def worker() -> None:
            try:
                fn()
                self.status = "idle"
            except Cancelled:
                self.status = "cancelled"
            except Exception as e:  # surface every failure to the UI
                self.status, self.error = "failed", f"{type(e).__name__}: {e}"
                self.engine.event("analysis_failed", f"{name} failed: {self.error}"[:400])
            finally:
                self.emit("action", {"name": name, "state": self.status, "error": self.error})
                self.action = None
                if on_done:
                    on_done(self)

        threading.Thread(target=worker, daemon=True).start()

    def cancel(self) -> bool:
        if self.status != "running":
            return False
        self.engine.cancel.set()
        return True

    # ---------------------------------------------------------------- actions
    def analyze(self) -> None:
        """Mutation analysis of the current suite, then diagnose every survivor."""
        self.round += 1
        self.engine.current_round = self.round
        self.engine.event("mutation_started", "Running the suite against every mutant")
        res, err = ev_mod.junit_run(self.info, self.source, self.tests, extra_paths=self.extra_paths)
        if err or not res:
            raise RuntimeError(f"the test suite does not run on the original code: {err or 'no tests collected'}")
        failing = [n for n, ok in res.items() if not ok]
        if failing:
            raise RuntimeError("these tests fail on the original code, so mutation results would be meaningless: "
                               + ", ".join(failing[:6]))
        self.mut = run_mutation_analysis(self.info, self.tests, workers=self.config.mutation_workers,
                                         limit=self.config.max_mutants, extra_paths=self.extra_paths)
        self.engine.event("survivors_detected", f"Mutation score {self.mut.score:.0%}: {len(self.mut.survivors)} of "
                          f"{len(self.mut.mutants)} mutants survived", score=round(self.mut.score, 4),
                          survivors=[m.id for m in self.mut.survivors],
                          mutants=[{**m.__dict__} for m in self.mut.mutants])
        self.engine.reconcile(self.mut)
        if self.versions[-1]["score"] is None:
            self.versions[-1]["score"] = round(self.mut.score, 4)
        if self.engine.cancel.is_set():
            raise Cancelled()
        self.engine.investigate(self.tests, self.mut, self.round)

    def investigate(self, mutant_id: int) -> None:
        if self.mut is None:
            raise RuntimeError("run the analysis first")
        if mutant_id not in self.engine.mutants:
            raise RuntimeError(f"unknown mutant #{mutant_id}")
        self.engine.investigate(self.tests, self.mut, self.round, only_ids=[mutant_id], limit=1)

    def diagnosis(self, bs_id: str):
        d = next((d for d in self.engine.diagnoses.values() if d.blind_spot_id == bs_id), None)
        if d is None:
            raise KeyError(bs_id)
        return d

    def generate_corrective(self, bs_id: str) -> None:
        d = self.diagnosis(bs_id)
        if d.proposed_test:
            self.engine.event("corrective_test_generated", f"{bs_id}: {d.proposed_test_name} (already generated)",
                              blind_spot_id=bs_id, mutant_id=d.mutant_id, test_name=d.proposed_test_name,
                              origin=d.test_origin, code=d.proposed_test)
            return
        if d.resolution_status == "invalid_test":
            d.resolution_status = "unresolved"
        made = self.engine.propose_llm([d])
        if not made:
            reason = ("no distinguishing input was found and the session uses the offline mock backend, which "
                      "cannot write tests from a diagnosis; choose an AI backend to generate one") \
                if self.llm_kind == "mock" else "the LLM did not return a usable test"
            self.engine.event("blind_spot_unresolved", f"{bs_id}: no corrective test: {reason}",
                              blind_spot_id=bs_id, mutant_id=d.mutant_id, reason=reason)

    def execute_corrective(self, bs_id: str) -> None:
        d = self.diagnosis(bs_id)
        if not d.proposed_test:
            raise RuntimeError("generate a corrective test first")
        res, err = ev_mod.junit_run(self.info, self.source, d.proposed_test, extra_paths=self.extra_paths)
        ok = bool(res) and all(res.values()) and not err
        d.verification.passes_on_original = ok
        self.engine.event("corrective_tests_executed",
                          f"{bs_id}: {d.proposed_test_name} {'passes' if ok else 'FAILS'} on the original program"
                          + (f" ({err[:160]})" if err else ""), blind_spot_id=bs_id, passed=int(ok), total=1,
                          outcomes=res)

    def verify(self, bs_id: str) -> None:
        d = self.diagnosis(bs_id)
        if not d.proposed_test:
            raise RuntimeError("generate a corrective test first")
        self._apply(self.engine.verify(self.tests, [d]), f"verified {d.proposed_test_name}")

    def resolve_all(self) -> None:
        ds = [d for d in self.engine.diagnoses.values()
              if d.resolution_status in ("unresolved", "invalid_test") and d.primary_category != "inconclusive"]
        if not ds:
            self.engine.event("analysis_completed", "No eligible blind spots to resolve", **self.engine.summary())
            return
        for d in ds:
            if d.resolution_status == "invalid_test":
                d.resolution_status = "unresolved"
        self.engine.propose_llm(ds)
        if self.engine.cancel.is_set():
            raise Cancelled()
        before = self.tests
        self._apply(self.engine.verify(self.tests, ds), "resolve all eligible")
        if self.tests != before:
            self.analyze()  # recalculate the mutation score and re-diagnose what is left

    def _apply(self, suite: str, label: str) -> None:
        if suite == self.tests:
            return
        self.tests = suite
        self.versions.append({"label": label, "tests": suite, "test_count": count_tests(suite), "score": None,
                              "t": round(time.time() - self.created, 2)})
        self.engine.event("suite_updated", f"Suite now has {count_tests(suite)} tests ({label})",
                          test_count=count_tests(suite))

    def try_inputs(self, bs_id: str, args: list[str] | None) -> dict:
        """Run inputs (default: the suggested counterexample) on the original and the mutant."""
        d = self.diagnosis(bs_id)
        cx = d.counterexample
        fn = cx.function if cx else d.function_name
        if not fn:
            raise RuntimeError("this blind spot is at module level; there is no function to call")
        args = args if args is not None else (cx.args if cx else None)
        if args is None:
            raise RuntimeError("enter the arguments to try")
        for a in args:
            ast.literal_eval(a)  # only Python literals are accepted
        init = cx.init if cx else []
        if "." in fn and not init:
            cls = next((c for c in self.info.classes if c.name == fn.split(".")[0]), None)
            if cls and any(p.default is None and not p.name.startswith("*") for p in cls.init_params):
                raise RuntimeError("this method needs constructor arguments; use the suggested counterexample")
        _, src = self.engine.mutants[d.mutant_id]
        out = ev_mod.search_counterexample(self.info, src, [{"fn": fn, "seeds": [args], "init": init, "kinds": []}],
                                           [], extra_paths=self.extra_paths, timeout_s=30, only_seeds=True)
        entry = (out.get("entries") or [{}])[0]
        ev = (entry.get("evaluated") or [None])[0]
        if ev is None:
            raise RuntimeError("the inputs could not be run: " + str(out.get("error") or "timed out")[:300])
        self.engine.event("counterexample_executed", f"{bs_id}: {self.engine._call_text(fn, args, init)} → original "
                          f"{ev['original']['repr']}, mutant {ev['mutant']['repr']}"
                          + (" (differs)" if ev["differs"] else " (same)"), blind_spot_id=bs_id,
                          mutant_id=d.mutant_id, differs=ev["differs"])
        return {"function": fn, "call": self.engine._call_text(fn, args, init), **ev}

    # ---------------------------------------------------------------- views
    def mutant_view(self, mutant_id: int) -> dict:
        if mutant_id not in self.engine.mutants:
            raise KeyError(mutant_id)
        m, src = self.engine.mutants[mutant_id]
        return {"mutant": {**m.__dict__, "status": self.engine.statuses.get(m.id, m.status)},
                "original_source": self.source, "mutated_source": src}

    def state(self) -> dict:
        s = self.engine.state()
        s.update(id=self.id, created=self.created, module_name=self.module_name, source=self.source,
                 tests=self.tests, original_tests=self.original_tests, versions=self.versions,
                 status=self.status, action=self.action, error=self.error, origin=self.origin,
                 from_run=self.from_run, llm=self.llm_kind, model=self.config.model, live=True,
                 mutants=[{**m.__dict__, "status": self.engine.statuses.get(m.id, "pending")}
                          for m, _ in self.engine.pairs],
                 events_count=len(self.events))
        return s

    def row(self) -> dict:
        sm = self.engine.summary()
        return {"id": self.id, "created": self.created, "module": self.module_name, "origin": self.origin,
                "status": self.status, "live": True, "discovered": sm["discovered"], "resolved": sm["resolved"],
                "unresolved": sm["unresolved"], "score_before": sm["score_before"], "score_after": sm["score_after"]}


# ---------------------------------------------------------------- exports
def export_csv(state: dict) -> str:
    buf = io.StringIO()
    fields = ["blind_spot_id", "mutant_id", "function_name", "source_line", "mutation_operator", "original_code",
              "mutated_code", "behaviour_category", "primary_category", "secondary_labels", "finding_status",
              "evidence_strength", "suggested_input", "observed_original_output", "observed_mutant_output",
              "resolution_status", "test_origin", "proposed_test_name", "killed_by", "priority", "diagnosis"]
    w = csv.DictWriter(buf, fields)
    w.writeheader()
    for d in state.get("diagnoses", []):
        row = {k: d.get(k) for k in fields if k in d}
        row["secondary_labels"] = "; ".join(d.get("secondary_labels", []))
        row["killed_by"] = (d.get("verification") or {}).get("killed_by")
        w.writerow(row)
    return buf.getvalue()


def export_html(state: dict) -> str:
    """Printable report (use the browser's Print → Save as PDF)."""
    e = html.escape
    sm = state.get("summary", {})
    pct = lambda v: "–" if v is None else f"{v * 100:.0f}%"  # noqa: E731
    rows = "".join(
        f"<tr><td>{e(d['blind_spot_id'])}</td><td>{e(str(d.get('function_name') or '-'))}:{d['source_line']}</td>"
        f"<td><code>{e(d['original_code'])}</code> → <code>{e(d['mutated_code'])}</code></td>"
        f"<td>{e(PRIMARY_LABELS[d['primary_category']])}<br><small>{e(', '.join(d['secondary_labels']))}</small></td>"
        f"<td>{e(d['finding_status'])} / {e(d['evidence_strength'])}</td><td>{e(d['resolution_status'])}</td>"
        f"<td><small>{e(d['diagnosis'])}</small></td></tr>"
        for d in state.get("diagnoses", []))
    tests = "".join(f"<h3>{e(d['blind_spot_id'])} · {e(d.get('proposed_test_name') or '')} "
                    f"({e(d['resolution_status'])})</h3><pre>{e(d['proposed_test'])}</pre>"
                    for d in state.get("diagnoses", []) if d.get("proposed_test"))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Blind Spot report · {e(state.get('module_name', ''))}</title>
<style>body{{font:14px/1.5 system-ui,sans-serif;margin:32px;color:#111}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:6px;vertical-align:top;text-align:left}}pre{{background:#f5f5f5;padding:10px;white-space:pre-wrap}}
code{{background:#f2f2f2;padding:0 3px}}.k{{display:inline-block;margin:0 18px 8px 0}}</style></head><body>
<h1>Blind Spot Intelligence report: {e(state.get('module_name', ''))}</h1>
<p>Generated {time.strftime('%Y-%m-%d %H:%M')} · session {e(state.get('id', ''))} · backend {e(str(state.get('llm')))}</p>
<div><span class="k"><b>{sm.get('analyzed_mutants')}</b> mutants analysed</span>
<span class="k"><b>{sm.get('discovered')}</b> blind spots</span><span class="k"><b>{sm.get('resolved')}</b> resolved</span>
<span class="k"><b>{sm.get('unresolved')}</b> unresolved</span>
<span class="k">mutation score <b>{pct(sm.get('score_before'))} → {pct(sm.get('score_after'))}</b></span>
<span class="k"><b>{sm.get('confirmed')}</b> confirmed · {sm.get('hypotheses')} hypotheses · {sm.get('inconclusive')} inconclusive</span></div>
<h2>Diagnoses</h2><table><tr><th>ID</th><th>Location</th><th>Mutation</th><th>Category</th><th>Finding / evidence</th>
<th>Status</th><th>Diagnosis (from execution evidence)</th></tr>{rows}</table>
<h2>Corrective tests</h2>{tests or '<p>None.</p>'}
<p><small>Evidence strength is a qualitative rating of how complete the measurements are, not a probability.
Suspected equivalent mutants are reported, never removed from the score.</small></p></body></html>"""


# ---------------------------------------------------------------- registry
class Registry:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def add(self, s: Session) -> Session:
        self.sessions[s.id] = s
        while len(self.sessions) > MAX_SESSIONS:
            oldest = min(self.sessions.values(), key=lambda x: x.created)
            if oldest.status == "running":
                break
            self.sessions.pop(oldest.id)
        return s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)


def save_snapshot(directory: Path, s: Session) -> None:
    state = s.state()
    state["live"] = False
    state["events"] = s.events[-1500:]
    (directory / f"{s.id}.json").write_text(json.dumps(state, default=str), encoding="utf-8")


def demo_files() -> tuple[str, str]:
    return ((DEMO_DIR / "eligibility.py").read_text(encoding="utf-8"),
            (DEMO_DIR / "test_eligibility.py").read_text(encoding="utf-8"))
