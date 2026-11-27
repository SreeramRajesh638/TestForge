"""Blind Spot Intelligence Engine.

For each surviving mutant the engine answers, from measurements:

* Was the mutated code executed?                      (coverage.py contexts)
* Did the tests' inputs make the mutated expression
  compute something different?                        (infection probe)
* Did that difference reach anything a test can see?  (test-level call traces)
* Did an assertion check it?                          (the mutant survived, so no)
* Which input exposes the fault?                      (differential search + Hypothesis)
* Which test would kill it, and does it really?       (corrective test, verified
                                                       on the original, on the
                                                       mutant, and in the full suite)

The engine never edits the module under test, never changes a measured score,
and marks a blind spot *resolved* only after all three verification checks pass
(or after a full mutation run shows the mutant killed by a test of the
stabilised suite). Every step is published as a ``blindspot`` event.
"""

from __future__ import annotations

import ast
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import telemetry
from ..core.budget import estimate_tokens
from ..core.mutation import generate_mutants
from ..core.suite import is_valid_python, merge_suites, strip_code_fences
from ..llm import LLM, LLMError, MockLLM, _exc_ref, _kind
from ..models import ModuleInfo, Mutant, MutationResult
from . import evidence as ev_mod
from .classify import classify
from .instrument import (BEHAVIOUR_CATEGORIES, analyze_assertions, behaviour_category, infection_source,
                         is_boundary_operator, locate)
from .priority import priority as priority_score
from .schema import (Counterexample, CorrectiveTestSet, Diagnosis, Evidence, ObservedCall, PRIMARY_LABELS,
                     Verification)

BLINDSPOT_SYSTEM = """You write pytest unit tests that kill specific surviving mutants.
For each blind spot you receive execution evidence measured by the testing framework:
whether the mutated line ran, whether the tests' inputs activated the fault, which outputs
differed, and sometimes a distinguishing input. Use that evidence; do not guess behaviour.
Write ONE focused test per blind spot. It must pass on the original code shown and fail on
the mutant. Assert exact values or exceptions observed on the original code.
Return tests only for the blind spot ids given."""


class Cancelled(Exception):
    pass


def mutant_key(m: Mutant) -> str:
    return f"{m.lineno}|{m.original}|{m.mutated}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "fn"


def _numbers(text: str) -> list:
    out = []
    for tok in re.findall(r"-?\d+(?:\.\d+)?", text):
        out.append(float(tok) if "." in tok else int(tok))
    return out


class BlindSpotEngine:
    def __init__(self, info: ModuleInfo, llm: LLM | None, config, emit, *, session_id: str,
                 extra_paths: list[str] | None = None, cancel: threading.Event | None = None,
                 max_investigations: int | None = None, workers: int | None = None) -> None:
        self.info, self.llm, self.config = info, llm, config
        self._emit = emit or (lambda kind, data: None)
        self.session_id = session_id
        self.extra_paths = extra_paths or []
        self.cancel = cancel or threading.Event()
        self.max_investigations = max_investigations or getattr(config, "blind_spot_max_investigations", 12)
        self.workers = workers or max(1, min(4, getattr(config, "mutation_workers", 4)))
        self.pairs = generate_mutants(info, getattr(config, "max_mutants", None))
        self.mutants = {m.id: (m, src) for m, src in self.pairs}
        self.statuses: dict[int, str] = {}
        self.killed_by: dict[int, str | None] = {}
        self.diagnoses: dict[str, Diagnosis] = {}
        self.timeline: list[dict] = []
        self.scores: list[dict] = []
        self.status = "idle"
        self.current_round = 0
        self.investigating: set[int] = set()
        self._lock = threading.RLock()
        self._next_id = 1
        self._base_cache: tuple[str, ev_mod.Baseline] | None = None
        self._behaviour_cache: dict[int, str] = {}
        self._t0 = time.time()

    # ------------------------------------------------------------ events
    def event(self, type_: str, text: str, **data) -> None:
        item = {"type": type_, "text": text, "round": self.current_round, **data}
        with self._lock:
            self.timeline.append({"t": round(time.time() - self._t0, 2), **item})
        self._emit("blindspot", item)

    def _check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    # ------------------------------------------------------------ helpers
    def _fn_info(self, qualname: str | None):
        return next((f for f in self.info.functions if f.qualname == qualname), None)

    def behaviour(self, m: Mutant) -> str:
        if m.id not in self._behaviour_cache:
            self._behaviour_cache[m.id] = behaviour_category(m, locate(self.info.source, m))
        return self._behaviour_cache[m.id]

    def record_mutation(self, mut: MutationResult, label: str = "") -> None:
        if getattr(self, "_last_mut", None) is mut:
            return
        self._last_mut = mut
        with self._lock:
            for m in mut.mutants:
                self.statuses[m.id] = m.status
                self.killed_by[m.id] = m.killed_by
            self.scores.append({"round": self.current_round, "score": round(mut.score, 4),
                                "killed": mut.killed, "total": mut.scored_total, "label": label})

    def baseline(self, test_code: str) -> ev_mod.Baseline:
        if self._base_cache and self._base_cache[0] == test_code:
            return self._base_cache[1]
        with telemetry.span("blindspot", "orchestrator", "pytest", detail="coverage + call trace of the suite"):
            base = ev_mod.baseline(self.info, test_code, extra_paths=self.extra_paths,
                                   timeout_s=max(60, getattr(self.config, "test_timeout_s", 60)))
        self._base_cache = (test_code, base)
        return base

    # ------------------------------------------------------------ investigate
    def investigate(self, test_code: str, mut: MutationResult, round_no: int,
                    survivors: list[Mutant] | None = None, only_ids: list[int] | None = None,
                    limit: int | None = None) -> list[Diagnosis]:
        """Diagnose surviving mutants (highest priority first)."""
        self.current_round = round_no
        self.status = "analyzing"
        self.record_mutation(mut, "mutation analysis")
        pool = survivors if survivors is not None else mut.survivors
        if only_ids is not None:
            pool = [m for m in pool if m.id in set(only_ids)] or \
                   [self.mutants[i][0] for i in only_ids if i in self.mutants]
        self.event("analysis_started", f"Investigating {min(len(pool), limit or self.max_investigations)} of "
                   f"{len(pool)} surviving mutant(s)", survivors=len(pool), total_mutants=len(mut.mutants),
                   score=round(mut.score, 4))
        if not pool:
            self.status = "idle"
            self.event("analysis_completed", "No surviving mutants to investigate", **self.summary())
            return []
        base = self.baseline(test_code)
        self.event("evidence_collected", "Coverage and call traces of the current suite recorded"
                   + (f" ({base.line_coverage:.0%} line coverage)" if base.line_coverage is not None else "")
                   + ("" if base.ok else f"; {base.error}"), scope="suite", ok=base.ok,
                   executed_lines=sorted(base.executed), duration_s=round(base.duration_s, 2))
        asserts = analyze_assertions(test_code)

        ranked = []
        for m in pool:
            if m.id not in self.mutants:
                continue
            ranked.append((self._priority(m, base, asserts, pool), m))
        ranked.sort(key=lambda x: -x[0][0])
        chosen = ranked[: (limit or self.max_investigations)]
        self.event("prioritized", "Investigation order: " + ", ".join(f"#{m.id}" for _, m in chosen),
                   order=[{"mutant_id": m.id, "priority": p[0], "factors": p[1]} for p, m in chosen])

        results: list[Diagnosis] = []
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futures = [ex.submit(self._investigate_one, m, test_code, base, asserts, round_no, p)
                           for p, m in chosen]
                for f in futures:
                    d = f.result()
                    if d is not None:
                        results.append(d)
        except Cancelled:
            self.status = "cancelled"
            self.event("analysis_cancelled", "Analysis cancelled by the user")
            raise
        self.status = "idle"
        self.event("analysis_completed", f"Diagnosed {len(results)} blind spot(s)", **self.summary())
        return results

    def _priority(self, m: Mutant, base, asserts, pool) -> tuple[float, dict]:
        loc = locate(self.info.source, m)
        line = loc.stmt_line if loc else m.lineno
        fn = self._fn_info(m.function)
        rng = range(fn.lineno, fn.end_lineno + 1) if fn else range(0)
        reaching = set(base.tests_on_line(line))
        weak = any(asserts.get(t, {}).get("weak") or asserts.get(t, {}).get("asserts", 1) == 0 for t in reaching)
        prev = self.diagnoses.get(mutant_key(m))
        return priority_score(
            unreached=base.ok and line not in base.executed,
            boundary=is_boundary_operator(m, loc),
            cluster=sum(1 for x in pool if x.function == m.function),
            complexity=fn.complexity if fn else 1,
            missing_branch=any(l in rng for l in base.missing_branch_lines),
            weak_assertions=weak,
            failed_attempts=prev.attempts if prev else 0,
            suite_seconds=base.duration_s)

    def _investigate_one(self, m: Mutant, test_code: str, base, asserts, round_no: int, prio) -> Diagnosis | None:
        self._check_cancel()
        t0 = time.time()
        _, mutated_src = self.mutants[m.id]
        with self._lock:
            self.investigating.add(m.id)
        self.event("mutant_under_investigation", f"Investigating mutant #{m.id} at line {m.lineno}",
                   mutant_id=m.id, line=m.lineno, function=m.function, operator=m.operator,
                   original=m.original, mutated=m.mutated)
        try:
            with telemetry.span("blindspot", "orchestrator", "pytest", detail=f"evidence for mutant #{m.id}"):
                return self._diagnose(m, mutated_src, test_code, base, asserts, round_no, prio, t0)
        finally:
            with self._lock:
                self.investigating.discard(m.id)

    def _diagnose(self, m, mutated_src, test_code, base, asserts, round_no, prio, t0) -> Diagnosis:
        loc = locate(self.info.source, m)
        behaviour = behaviour_category(m, loc)
        boundary = is_boundary_operator(m, loc)
        stmt_line = loc.stmt_line if loc else m.lineno
        reaching = base.tests_on_line(stmt_line)
        reached = (stmt_line in base.executed) if base.ok else None

        inf = infection_source(self.info.source, m)
        runs = ev_mod.mutant_runs(self.info, test_code, base, mutated_src, inf, extra_paths=self.extra_paths,
                                  timeout_s=max(30, getattr(self.config, "test_timeout_s", 60)))
        self._check_cancel()
        probe = runs.infection or {}
        propagated = []
        for diff in runs.differing[:10]:
            o, u = diff["original"], diff["mutant"]
            propagated.append(ObservedCall(test=diff["test"], call=ev_mod.call_text(o),
                                           original=ev_mod.outcome_text(o), mutant=ev_mod.outcome_text(u),
                                           original_state=o.get("state"), mutant_state=u.get("state")))
        unsupported = []
        if not inf.supported:
            unsupported.append(f"infection: {inf.reason}")
        if not base.ok:
            unsupported.append(f"coverage: {base.error}")
        evidence = Evidence(
            reached=reached if probe.get("evaluations", 0) == 0 else True,
            reaching_tests=reaching, executions=probe.get("evaluations", 0),
            infection_supported=inf.supported and runs.infection_ok, infection_point=inf.point,
            infection_note=inf.reason, infected_evaluations=probe.get("infected", 0),
            infection_examples=[{k: e[k] for k in ("original", "mutant", "infected")}
                                for e in probe.get("examples", [])][:6],
            propagated_calls=propagated, noisy_calls=runs.noisy, diverged_tests=runs.diverged[:10],
            assertions={t: asserts[t] for t in reaching if t in asserts}, unsupported=unsupported)
        self.event("evidence_collected",
                   f"#{m.id}: " + ("not reached by any test" if evidence.reached is False else
                                   f"reached by {len(reaching)} test(s), {evidence.executions} evaluation(s), "
                                   f"{evidence.infected_evaluations} infected, {len(propagated)} output difference(s)"),
                   mutant_id=m.id, line=stmt_line, reached=evidence.reached, executions=evidence.executions,
                   infected=evidence.infected_evaluations, propagated=len(propagated), reaching_tests=reaching[:8])

        # redundant tests: two reaching tests making exactly the same calls
        by_test: dict[str, tuple] = {}
        for c in base.trace.get("calls", []):
            if c.get("test") in reaching:
                by_test.setdefault(c["test"], ())
                by_test[c["test"]] += (ev_mod.call_text(c),)
        sigs = [s for s in by_test.values() if s]
        redundant = len(sigs) != len(set(sigs))

        cx, searched = self._counterexample(m, mutated_src, base, runs, propagated)
        self._check_cancel()
        if cx is not None:
            self.event("counterexample_found", f"#{m.id}: {cx.call} → original {cx.original['repr']}, "
                       f"mutant {cx.mutant['repr']}", mutant_id=m.id, call=cx.call,
                       original=cx.original, mutant=cx.mutant, source=cx.source)

        result = classify(evidence, boundary=boundary, behaviour=behaviour, is_method="." in (m.function or ""),
                          counterexample_found=cx is not None, search_ran=searched, redundant_tests=redundant)
        primary = result["primary"]
        heat_cat = {"reachability": "unexplored_paths", "revealability": "assertion_quality"}.get(primary, behaviour)
        key = mutant_key(m)
        with self._lock:
            prev = self.diagnoses.get(key)
            if prev:
                bs_id = prev.blind_spot_id
            else:
                bs_id = f"BS-{self._next_id}"
                self._next_id += 1
        d = Diagnosis(
            blind_spot_id=bs_id, session_id=self.session_id, mutant_id=m.id, mutant_key=key, round=round_no,
            function_name=m.function, source_line=m.lineno, mutation_operator=m.operator,
            original_code=m.original, mutated_code=m.mutated, behaviour_category=heat_cat,
            primary_category=primary, secondary_labels=result["secondary"],
            diagnosis=self._explain(m, primary, evidence, cx, searched, stmt_line),
            evidence=evidence, evidence_strength=result["strength"], evidence_notes=result["notes"],
            finding_status=result["status"],
            suggested_input=cx.call if cx else None,
            observed_original_output=cx.original["repr"] if cx else None,
            observed_mutant_output=cx.mutant["repr"] if cx else None,
            counterexample=cx, counterexample_searched=searched, corrective_strategy=result["strategy"],
            priority=prio[0], priority_factors=prio[1], attempts=prev.attempts if prev else 0,
            duration_s=round(time.time() - t0, 2))
        test = self._test_from_counterexample(d)
        if test:
            d.proposed_test_name, d.proposed_test, d.test_origin = test[0], test[1], "counterexample"
        with self._lock:
            self.diagnoses[key] = d
        if not prev:
            self.event("blind_spot_discovered", f"{bs_id}: {PRIMARY_LABELS[primary]} at line {m.lineno} "
                       f"({m.function or 'module level'})", blind_spot_id=bs_id, mutant_id=m.id, line=m.lineno,
                       function=m.function, category=heat_cat, primary=primary)
        self.event("diagnosis_completed", f"{bs_id}: {PRIMARY_LABELS[primary]} · {result['status']} · "
                   f"evidence {result['strength']}", blind_spot_id=bs_id, mutant_id=m.id, diagnosis=d.model_dump())
        if d.proposed_test:
            self.event("corrective_test_generated", f"{bs_id}: {d.proposed_test_name} from the counterexample",
                       blind_spot_id=bs_id, mutant_id=m.id, test_name=d.proposed_test_name, origin="counterexample",
                       code=d.proposed_test)
        return d

    # ------------------------------------------------------------ counterexamples
    def _counterexample(self, m, mutated_src, base, runs, propagated) -> tuple[Counterexample | None, bool]:
        # Revealability: a test already observed the difference; that call IS the counterexample.
        for diff in runs.differing:
            o, u = diff["original"], diff["mutant"]
            if all(a is not None for a in o.get("args_literal", [])) and not o.get("kwargs"):
                init = []
                if o.get("obj") is not None:
                    rec = base.trace.get("inits", {}).get(str(o["obj"]))
                    if not rec or None in rec.get("args_literal", []) or rec.get("kwargs"):
                        continue
                    init = rec["args_literal"]
                return Counterexample(
                    function=o["fn"], call=self._call_text(o["fn"], o["args_literal"], init),
                    args=o["args_literal"], init=init,
                    original={"kind": "exception" if o.get("exc") else "value",
                              "repr": o.get("exc") or o.get("result"), "literal": o.get("result_literal"),
                              "state": o.get("state"), "state_literal": o.get("state_literal")},
                    mutant={"kind": "exception" if u.get("exc") else "value",
                            "repr": u.get("exc") or u.get("result"), "state": u.get("state")},
                    source=f"observed in existing test {diff['test']}", tried=0), False

        entries = self._entries(m, base, runs)
        if not entries:
            return None, False
        fn = self._fn_info(m.function)
        boundaries = list(fn.constants if fn else []) + _numbers(m.original) + _numbers(m.mutated)
        # values the object is constructed with and the tested inputs are also
        # candidate boundaries (e.g. `balance - amount >= 0` flips at amount == balance)
        for e in entries:
            for text in list(e.get("init") or []) + [a for s in e.get("seeds", []) for a in s]:
                boundaries += _numbers(text)
        boundaries = list(dict.fromkeys(boundaries))
        res = ev_mod.search_counterexample(self.info, mutated_src, entries, boundaries[:16],
                                           extra_paths=self.extra_paths)
        tried = sum(e.get("tried", 0) for e in res.get("entries", []))
        best = None
        for e in res.get("entries", []):
            for f in e.get("found", []):
                if best is None or f.get("size", 0) < best[1].get("size", 0):
                    best = (e, f)
        if best is None:
            return None, True
        e, f = best
        alternatives = sum(len(x.get("found", [])) for x in res.get("entries", [])) - 1
        return Counterexample(function=e["fn"], call=self._call_text(e["fn"], f["args"], e.get("init") or []),
                              args=f["args"], init=e.get("init") or [], original=f["original"],
                              mutant=f["mutant"], source=f["source"], tried=tried, alternatives=alternatives), True

    @staticmethod
    def _call_text(fn: str, args: list[str], init: list[str]) -> str:
        if "." in fn:
            cls, meth = fn.split(".", 1)
            return f"{cls}({', '.join(init)}).{meth}({', '.join(args)})"
        return f"{fn}({', '.join(args)})"

    def _entries(self, m: Mutant, base, runs) -> list[dict]:
        """Which functions to call, with which seed inputs, to search for a counterexample."""
        calls = base.trace.get("calls", [])
        reach_fns: list[str] = []
        probe = runs.infection or {}
        traced_b = runs.trace_original.get("calls", [])
        for idx in probe.get("entries", []):
            if 0 <= idx < len(traced_b):
                fn = traced_b[idx].get("fn")
                if fn and fn not in reach_fns:
                    reach_fns.append(fn)
        if m.function and m.function not in reach_fns and not m.function.split(".")[-1].startswith("_"):
            reach_fns.append(m.function)
        if not reach_fns:
            reach_fns = list(dict.fromkeys(c["fn"] for c in calls))[:3]
        entries = []
        inits = base.trace.get("inits", {})
        for fn in reach_fns[:3]:
            info = self._fn_info(fn)
            if info is None or "<locals>" in fn:
                continue
            seeds, init = [], None
            for c in calls:
                if c.get("fn") != fn or c.get("kwargs") or None in c.get("args_literal", []):
                    continue
                if "." in fn:
                    rec = inits.get(str(c.get("obj")))
                    if not rec or None in rec.get("args_literal", []) or rec.get("kwargs"):
                        continue
                    if init is None:
                        init = rec["args_literal"]
                    elif rec["args_literal"] != init:
                        continue
                if c["args_literal"] not in seeds:
                    seeds.append(c["args_literal"])
                if len(seeds) >= 8:
                    break
            params = [p for p in info.params if p.default is None and not p.name.startswith("*")]
            kinds = [_kind(p.annotation, p.name, p.default) for p in params]
            if not seeds and not kinds and params:
                continue
            if "." in fn and init is None:
                # No test constructed this class: try a few constructor argument sets
                # (guessed types, then all strings, then all integers); a wrong guess
                # makes both versions raise the same exception, which proves nothing.
                cls = next((c for c in self.info.classes if c.name == fn.split(".")[0]), None)
                if cls is None:
                    continue
                from ..llm import _VALUES
                req = [p for p in cls.init_params if p.default is None and not p.name.startswith("*")]
                variants = [[repr(_VALUES[_kind(p.annotation, p.name)][1]) for p in req],
                            [repr(_VALUES["str"][2]) for _ in req], [repr(_VALUES["int"][2]) for _ in req]]
                for v in list(dict.fromkeys(tuple(x) for x in variants)):
                    entries.append({"fn": fn, "seeds": seeds, "init": list(v), "kinds": kinds})
                continue
            entries.append({"fn": fn, "seeds": seeds, "init": init, "kinds": kinds})
        return entries[:6]

    # ------------------------------------------------------------ explanations
    def _explain(self, m: Mutant, primary: str, e: Evidence, cx: Counterexample | None, searched: bool,
                 line: int) -> str:
        where = f"line {m.lineno}" + (f" in {m.function}" if m.function else "")
        change = f"`{m.original}` → `{m.mutated}`"
        tests = ", ".join(e.reaching_tests[:3]) + (" …" if len(e.reaching_tests) > 3 else "")
        if primary == "reachability":
            text = (f"No test executes {where}: coverage.py recorded the statement on line {line} as never run, "
                    f"so the mutation {change} cannot be observed.")
        elif primary == "infection":
            same = [x for x in e.infection_examples if not x["infected"]][:2]
            seen = "; ".join(f"both gave {x['original']}" for x in same)
            text = (f"{where.capitalize()} ran in {len(e.reaching_tests)} test(s) ({tests}) and `{e.infection_point}` "
                    f"was evaluated {e.executions} time(s), but the original and the mutant computed the same value "
                    f"every time{f' ({seen})' if seen else ''}. The inputs used never activate the fault ({change}).")
        elif primary == "propagation":
            diff = next((x for x in e.infection_examples if x["infected"]), None)
            text = (f"`{e.infection_point}` changed value in {e.infected_evaluations} of {e.executions} evaluation(s)"
                    + (f" (original {diff['original']}, mutant {diff['mutant']})" if diff else "")
                    + ", but every test-level call still returned the same result and left the same state, so the "
                      "difference was masked before it reached anything the tests can observe.")
        elif primary == "revealability":
            c = e.propagated_calls[0]
            text = (f"{len(e.propagated_calls)} test-level call(s) behave differently on the mutant: `{c.call}` gives "
                    f"{c.original} on the original and {c.mutant} on the mutant"
                    + (f" (in {c.test})" if c.test else "") + ". The test still passes, so its assertions do not "
                    "check this output.")
            weak = [a for t, a in e.assertions.items() if a.get("weak")]
            if weak:
                text += " Weak assertions found: " + "; ".join(weak[0]["weak"][:2]) + "."
        else:
            text = "The evidence is not sufficient to explain why this mutant survives: " + \
                   "; ".join(e.unsupported or ["no output differed and infection was not measurable"]) + "."
        if cx is not None and primary != "revealability":
            text += (f" Input `{cx.call}` distinguishes them: the original gives {cx.original['repr']}, the mutant "
                     f"gives {cx.mutant['repr']} ({cx.source.lower()}" + (f", {cx.tried} candidates tried)" if cx.tried else ")") + ".")
        elif searched:
            text += " No distinguishing input was found by the differential search."
        return text

    # ------------------------------------------------------------ corrective tests
    def _test_from_counterexample(self, d: Diagnosis) -> tuple[str, str] | None:
        cx = d.counterexample
        if cx is None:
            return None
        o, u = cx.original, cx.mutant
        args = ", ".join(cx.args)
        name = f"test_blind_spot_{d.mutant_id}_{_slug(cx.function)}"
        doc = (f'    """Blind Spot {d.blind_spot_id} ({PRIMARY_LABELS[d.primary_category]}): kills mutant '
               f'#{d.mutant_id}, line {d.source_line}: {d.original_code} -> {d.mutated_code}."""\n')
        if "." in cx.function:
            cls, meth = cx.function.split(".", 1)
            setup = f"    obj = m.{cls}({', '.join(cx.init)})\n"
            call = f"obj.{meth}({args})"
        else:
            setup, call = "", f"m.{cx.function}({args})"
        same_result = (o.get("kind"), o.get("repr")) == (u.get("kind"), u.get("repr"))
        if o.get("kind") == "exception" and not same_result:
            body = f"    with pytest.raises({_exc_ref(o['repr'])}):\n        {call}\n"
        elif o.get("kind") == "value" and o.get("literal") is not None and not same_result:
            value = o["literal"]
            try:
                if isinstance(ast.literal_eval(value), float):
                    value = f"pytest.approx({value})"
            except (ValueError, SyntaxError):
                return None
            body = f"    assert {call} == {value}\n"
        elif "." in cx.function and o.get("state_literal") and o.get("state") != u.get("state"):
            ctx = f"    with pytest.raises({_exc_ref(o['repr'])}):\n        {call}\n" if o.get("kind") == "exception" \
                else f"    {call}\n"
            body = ctx + f"    assert vars(obj) == {o['state_literal']}\n"
        else:
            return None
        code = f"import pytest\nimport {self.info.module_name} as m\n\n\ndef {name}():\n{doc}{setup}{body}"
        return (name, code) if not is_valid_python(code) else None

    def propose_llm(self, diagnoses: list[Diagnosis]) -> int:
        """Ask the configured LLM for corrective tests where no counterexample-based test exists."""
        todo = [d for d in diagnoses if not d.proposed_test and d.resolution_status == "unresolved"
                and d.primary_category != "inconclusive"]
        if not todo or self.llm is None or isinstance(self.llm, MockLLM):
            return 0
        from ..agents.prompts import _module_block
        focus = {d.function_name for d in todo if d.function_name} or None
        blocks = []
        for d in todo[:8]:
            e = d.evidence
            lines = [f"[{d.blind_spot_id}] mutant at line {d.source_line} in {d.function_name or 'module'}: "
                     f"`{d.original_code}` -> `{d.mutated_code}` ({d.mutation_operator})",
                     f"  diagnosis: {PRIMARY_LABELS[d.primary_category]} - {d.diagnosis}",
                     f"  strategy: {d.corrective_strategy}"]
            if e.infection_examples:
                lines.append("  measured values: " + "; ".join(
                    f"original {x['original']} / mutant {x['mutant']}" for x in e.infection_examples[:3]))
            if e.propagated_calls:
                c = e.propagated_calls[0]
                lines.append(f"  observed: {c.call} -> original {c.original}, mutant {c.mutant}")
            blocks.append("\n".join(lines))
        prompt = (f"{_module_block(self.info, focus)}\n\nBlind spots (one test each):\n" + "\n\n".join(blocks)
                  + f"\n\nImport the module as `import {self.info.module_name} as m`.")
        budget = getattr(self.config, "max_prompt_tokens", None)
        if budget and estimate_tokens(BLINDSPOT_SYSTEM + prompt) > budget * 1.5:
            prompt = prompt[: int(budget * 3 * 1.5)]
        try:
            out = self.llm.complete("blindspot", BLINDSPOT_SYSTEM, prompt, CorrectiveTestSet, {"module": self.info})
        except LLMError as e:
            self.event("analysis_failed", f"LLM could not generate corrective tests: {e}"[:300], stage="corrective")
            return 0
        by_id = {d.blind_spot_id: d for d in todo}
        made = 0
        for k, t in enumerate(out.tests):
            d = by_id.get(t.blind_spot_id)
            if d is None or d.proposed_test:
                continue
            code = strip_code_fences(t.test_code)
            renamed = self._rename_tests(code, f"test_blind_spot_{d.mutant_id}_llm")
            if renamed is None:
                continue
            d.proposed_test_name, d.proposed_test = renamed
            d.test_origin, d.llm_explanation = "llm", t.explanation[:600]
            made += 1
            self.event("corrective_test_generated", f"{d.blind_spot_id}: {d.proposed_test_name} written by the LLM",
                       blind_spot_id=d.blind_spot_id, mutant_id=d.mutant_id, test_name=d.proposed_test_name,
                       origin="llm", code=d.proposed_test)
        return made

    def _rename_tests(self, code: str, base: str) -> tuple[str, str] | None:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        names = []
        for i, node in enumerate(n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                                 and n.name.startswith("test")):
            node.name = base if i == 0 else f"{base}_{i + 1}"
            names.append(node.name)
        if not names:
            return None
        if not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body):
            tree.body.insert(0, ast.parse(f"import pytest\nimport {self.info.module_name} as m").body[1])
            tree.body.insert(0, ast.parse("import pytest").body[0])
        return names[0], ast.unparse(tree)

    # ------------------------------------------------------------ verification
    def verify(self, test_code: str, diagnoses: list[Diagnosis]) -> str:
        """Verify proposed corrective tests: pass on the original, fail on the
        targeted mutant, no failures in the merged suite. Returns the suite with
        the verified tests added (unchanged when none verify)."""
        todo = [d for d in diagnoses if d.proposed_test and d.resolution_status in ("unresolved", "invalid_test")]
        if not todo:
            return test_code
        self.status = "verifying"
        self.event("verification_started", f"Verifying {len(todo)} corrective test(s)",
                   blind_spot_ids=[d.blind_spot_id for d in todo])
        for d in todo:
            d.verification_status = "running"
        names = {d.blind_spot_id: self._test_names(d.proposed_test) for d in todo}

        # 1. every candidate against the ORIGINAL program
        combined = ""
        for d in todo:
            combined = merge_suites(combined, d.proposed_test) if combined else d.proposed_test
        outcomes, err = ev_mod.junit_run(self.info, self.info.source, combined, extra_paths=self.extra_paths)
        self.event("corrective_tests_executed", f"Corrective tests run on the original program: "
                   f"{sum(outcomes.values())}/{len(outcomes)} pass" + (f" ({err[:120]})" if err else ""),
                   passed=sum(outcomes.values()), total=len(outcomes))
        valid = []
        for d in todo:
            ok = bool(names[d.blind_spot_id]) and all(outcomes.get(n) for n in names[d.blind_spot_id])
            d.verification = Verification(passes_on_original=ok, detail="" if ok else
                                          "the test fails on the original program (wrong oracle); not used")
            if ok:
                valid.append(d)
            else:
                d.resolution_status, d.verification_status = "invalid_test", "failed"
                d.attempts += 1
                self.event("blind_spot_unresolved", f"{d.blind_spot_id}: corrective test fails on the original "
                           "program", blind_spot_id=d.blind_spot_id, mutant_id=d.mutant_id, reason="invalid_test")

        # 2. each valid test against ITS mutant
        def against_mutant(d: Diagnosis):
            _, src = self.mutants[d.mutant_id]
            res, _ = ev_mod.junit_run(self.info, src, d.proposed_test, extra_paths=self.extra_paths)
            failing = [n for n in names[d.blind_spot_id] if res.get(n) is False or n not in res]
            return d, failing

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            killers = list(ex.map(against_mutant, valid))
        killing = []
        for d, failing in killers:
            d.verification.kills_mutant = bool(failing)
            d.verification.killed_by = failing[0] if failing else None
            if failing:
                killing.append(d)
            else:
                d.verification.detail = "the test passes on the mutant too, so it does not detect the fault"

        # 3. regression check: merged suite on the original program
        suite = test_code
        if killing:
            candidate = test_code
            for d in killing:
                candidate = merge_suites(candidate, d.proposed_test)
            res, err = ev_mod.junit_run(self.info, self.info.source, candidate, extra_paths=self.extra_paths)
            failed = {n for n, ok in res.items() if not ok}
            new_names = {n for d in killing for n in names[d.blind_spot_id]}
            regression_free = not err and not (failed - new_names)
            if regression_free and not failed:
                suite = candidate
                for d in killing:
                    d.verification.suite_regression_free = True
            else:
                for d in killing:
                    d.verification.suite_regression_free = False
                    d.verification.detail = "adding the tests made the suite fail on the original program" \
                        + (f": {', '.join(sorted(failed))[:200]}" if failed else f" ({err[:200]})")
        for d in todo:
            v = d.verification
            if v.passes_on_original and v.kills_mutant and v.suite_regression_free:
                d.resolution_status, d.verification_status = "resolved", "verified"
                self.statuses[d.mutant_id] = "killed"
                self.killed_by[d.mutant_id] = v.killed_by
                self.event("blind_spot_resolved", f"{d.blind_spot_id} resolved: {v.killed_by} passes on the "
                           "original, fails on the mutant, suite still green", blind_spot_id=d.blind_spot_id,
                           mutant_id=d.mutant_id, test_name=v.killed_by, via="corrective_test")
            elif d.resolution_status != "invalid_test":
                d.resolution_status, d.verification_status = "unresolved", "failed"
                d.attempts += 1
                self.event("blind_spot_unresolved", f"{d.blind_spot_id}: {v.detail}", blind_spot_id=d.blind_spot_id,
                           mutant_id=d.mutant_id, reason=v.detail)
        self.status = "idle"
        return suite

    @staticmethod
    def _test_names(code: str | None) -> list[str]:
        if not code:
            return []
        try:
            return [n.name for n in ast.parse(code).body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
        except SyntaxError:
            return []

    def reconcile(self, mut: MutationResult, new_tests: set[str] | None = None) -> int:
        """After a full mutation run: diagnosed mutants that are now killed by a
        test of the (stabilised, all-passing) suite are resolved; others count a
        failed attempt."""
        self.record_mutation(mut, "mutation verification")
        by_key = {mutant_key(m): m for m in mut.mutants}
        resolved = 0
        for d in list(self.diagnoses.values()):
            if d.resolution_status == "resolved":
                continue
            m = by_key.get(d.mutant_key)
            if m is None:
                continue
            if m.status in ("killed", "timeout"):
                d.resolution_status, d.verification_status = "resolved", "verified"
                if d.test_origin is None:
                    d.test_origin = "writer"
                d.verification = Verification(passes_on_original=True, kills_mutant=True, suite_regression_free=True,
                                              killed_by=m.killed_by,
                                              detail="killed in the full mutation run of the stabilised suite")
                resolved += 1
                self.event("blind_spot_resolved", f"{d.blind_spot_id} resolved by "
                           f"{m.killed_by or 'the refined suite'} (full mutation run)", blind_spot_id=d.blind_spot_id,
                           mutant_id=d.mutant_id, test_name=m.killed_by, via="mutation_run")
            elif m.status == "survived" and d.round == self.current_round:
                d.attempts += 1
        return resolved

    # ------------------------------------------------------------ views
    def summary(self) -> dict:
        ds = list(self.diagnoses.values())
        resolved = [d for d in ds if d.resolution_status == "resolved"]
        unresolved = [d for d in ds if d.resolution_status != "resolved"]
        first = self.scores[0]["score"] if self.scores else None
        last = self.scores[-1]["score"] if self.scores else None
        confirmed = sum(1 for d in ds if d.finding_status == "confirmed")
        return {
            "analyzed_mutants": len(self.mutants),
            "surviving_mutants": sum(1 for s in self.statuses.values() if s == "survived"),
            "discovered": len(ds), "resolved": len(resolved), "unresolved": len(unresolved),
            "high_priority": sum(1 for d in unresolved if d.priority >= 4.0),
            "verified_corrective_tests": sum(1 for d in resolved if d.verification.killed_by),
            "score_before": first, "score_after": last,
            "resolution_rate": round(len(resolved) / len(ds), 4) if ds else None,
            "confirmed": confirmed, "hypotheses": sum(1 for d in ds if d.finding_status == "hypothesis"),
            "inconclusive": sum(1 for d in ds if d.finding_status == "inconclusive"),
            "by_primary": {k: sum(1 for d in ds if d.primary_category == k) for k in PRIMARY_LABELS},
            "potential_equivalent": sum(1 for d in ds if "Potential equivalent mutant" in d.secondary_labels),
            "status": self.status,
        }

    def heatmap(self) -> dict:
        rows = [f.qualname for f in self.info.functions]
        by_mutant = {d.mutant_id: d for d in self.diagnoses.values()}
        cells: dict[tuple[str, str], dict] = {}
        for mid, (m, _) in self.mutants.items():
            fn = m.function or "(module level)"
            if fn not in rows:
                rows.append(fn)
            d = by_mutant.get(mid)
            cat = d.behaviour_category if d else self.behaviour(m)
            cell = cells.setdefault((fn, cat), {"function": fn, "category": cat, "mutants": [], "killed": 0,
                                                "survived": 0, "blind_spots": [], "resolved": 0, "operators": [],
                                                "lines": []})
            cell["mutants"].append(mid)
            status = self.statuses.get(mid)
            if status in ("killed", "timeout"):
                cell["killed"] += 1
            elif status == "survived":
                cell["survived"] += 1
            if d:
                cell["blind_spots"].append(d.blind_spot_id)
                if d.resolution_status == "resolved":
                    cell["resolved"] += 1
            if m.operator not in cell["operators"]:
                cell["operators"].append(m.operator)
            if m.lineno not in cell["lines"]:
                cell["lines"].append(m.lineno)
        for cell in cells.values():
            ds = [by_mutant[i] for i in cell["mutants"] if i in by_mutant]
            open_ds = [d for d in ds if d.resolution_status != "resolved"]
            undiagnosed = [i for i in cell["mutants"] if self.statuses.get(i) == "survived" and i not in by_mutant]
            if any(i in self.investigating for i in cell["mutants"]):
                cell["status"] = "analyzing"
            elif any(d.finding_status in ("confirmed", "hypothesis") for d in open_ds):
                cell["status"] = "blind_spot"
            elif undiagnosed:
                cell["status"] = "investigating"
            elif open_ds:
                cell["status"] = "insufficient"
            elif cell["killed"] or cell["resolved"]:
                cell["status"] = "verified"
            else:
                cell["status"] = "not_evaluated"
        used_rows = [r for r in rows if any(c["function"] == r for c in cells.values())]
        return {"rows": used_rows, "columns": [{"key": k, "label": v} for k, v in BEHAVIOUR_CATEGORIES],
                "cells": list(cells.values())}

    def state(self) -> dict:
        with self._lock:
            return {"session_id": self.session_id, "module": self.info.module_name, "summary": self.summary(),
                    "heatmap": self.heatmap(), "scores": list(self.scores),
                    "diagnoses": [d.model_dump() for d in sorted(self.diagnoses.values(),
                                                                 key=lambda d: (-d.priority, d.mutant_id))],
                    "timeline": list(self.timeline), "investigating": sorted(self.investigating)}
