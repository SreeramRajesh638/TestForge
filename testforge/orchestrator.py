"""The Orchestrator coordinates the agent team.

    Analyze -> Plan -> Write -> [Execute -> Fix]* -> prune -> Mutate
                                   ^                              |
                                   |    survivors & score<target  v
                                   |              Blind Spot Intelligence (optional):
                                   |              diagnose -> counterexample -> corrective
                                   |              test -> verify (original / mutant / suite)
                                   |                              |
                                   +------- Refine (Writer) <-----+

Every pass through the loop is recorded as an IterationRecord, which gives
the per-iteration curves used in the evaluation chapter.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from .agents.base import EventFn
from .agents.team import (AnalyzerAgent, BlindSpotAgent, ExecutorAgent, FixerAgent, MutationCriticAgent,
                          PlannerAgent, WriterAgent)
from . import telemetry
from .agents import prompts
from .config import Config
from .core.budget import chunk_functions, estimate_tokens
from .core.suite import count_tests, merge_suites, prune_tests
from .llm import LLM, DailyQuotaExceeded, LLMError, make_llm
from .models import ExecResult, IterationRecord, ModuleInfo, MutationResult, RunReport


class Orchestrator:
    def __init__(self, config: Config | None = None, llm: LLM | None = None,
                 on_event: EventFn | None = None, cancel: threading.Event | None = None,
                 session_id: str = "run") -> None:
        self.config = config or Config()
        self.on_event = on_event or (lambda kind, data: None)
        self._llm = llm
        self.cancel = cancel or threading.Event()
        self.session_id = session_id
        self.blind_spot: BlindSpotAgent | None = None

    # ------------------------------------------------------------------
    def _holdout(self, m) -> bool:
        """Deterministic held-out split (same mutants in every configuration)."""
        frac = self.config.holdout_fraction
        if not frac:
            return False
        h = int(hashlib.sha1(f"{m.lineno}|{m.original}|{m.mutated}".encode()).hexdigest(), 16)
        return (h % 1000) < frac * 1000

    def _feedback(self, mut: MutationResult) -> tuple[list, float]:
        """Survivors the agents may see, and the score over non-held-out mutants."""
        visible = [m for m in mut.mutants if not self._holdout(m)]
        scored = [m for m in visible if m.status not in ("equivalent", "error")]
        killed = sum(1 for m in scored if m.status in ("killed", "timeout"))
        score = killed / len(scored) if scored else 0.0
        return [m for m in visible if m.status == "survived"], score

    def _budget_stop(self, llm: LLM, start: float) -> str | None:
        cfg = self.config
        if self.cancel.is_set():
            return "cancelled"
        if cfg.max_total_tokens and llm.usage.input_tokens + llm.usage.output_tokens >= cfg.max_total_tokens:
            return "token_budget"
        if cfg.max_seconds and time.time() - start >= cfg.max_seconds:
            return "time_budget"
        return None

    # ------------------------------------------------------------------
    def emit(self, kind: str, data: dict) -> None:
        self.on_event(kind, data)

    def phase(self, name: str, detail: str = "") -> None:
        self.emit("phase", {"phase": name, "detail": detail})

    # ------------------------------------------------------------------
    def run(self, path: str) -> RunReport:
        cfg = self.config
        start = time.time()
        extra_paths = [str(Path(path).resolve().parent)]
        llm = self._llm or make_llm(cfg.llm, cfg.model, cfg.effort, extra_paths,
                                      ollama_host=cfg.ollama_host, num_ctx=cfg.num_ctx)
        telemetry.instrument_llm(llm)
        args = (llm, cfg, self.emit)
        analyzer, planner, writer = AnalyzerAgent(*args), PlannerAgent(*args), WriterAgent(*args)
        executor, fixer, critic = ExecutorAgent(*args), FixerAgent(*args), MutationCriticAgent(*args)
        self.extra_paths = extra_paths

        self.phase("analyze")
        info = analyzer.run(path)

        code = self._generate(info, planner, writer)
        if code is None:
            return self._report(info, [], "", "llm_error", llm, start, None)

        self.phase("execute")
        code, result, fix_rounds, pruned = self._stabilize(info, code, executor, fixer)
        if result.collection_error or result.total == 0:
            self.emit("error", {"message": "Could not produce a runnable test suite",
                                "detail": result.collection_error})
            return self._report(info, [], code, "no_runnable_tests", llm, start, None)

        self.phase("mutate")
        equivalent: set[tuple[int, str, str]] = set()
        mut = critic.run(info, code, equivalent, extra_paths)
        if cfg.use_blind_spot:
            self.blind_spot = BlindSpotAgent(llm, cfg, self.emit, info=info, extra_paths=extra_paths,
                                             cancel=self.cancel, session_id=self.session_id)
            eng = self.blind_spot.engine
            eng.event("initial_generation", f"Initial suite: {count_tests(code)} tests, "
                      f"mutation score {mut.score:.0%}", tests=count_tests(code), score=round(mut.score, 4))
            eng.record_mutation(mut, "initial suite")
            eng.event("survivors_detected", f"{len(mut.survivors)} mutant(s) survived the initial suite",
                      survivors=[m.id for m in mut.survivors])
        records = [self._record(0, "initial", code, result, mut, fix_rounds, pruned, llm, start)]
        survivors, fb_score = self._feedback(mut)

        stop = "target_reached" if fb_score >= cfg.target_mutation_score else "max_iterations"
        if not cfg.use_mutation_feedback:
            stop = "feedback_disabled"
        elif not survivors:
            stop = "no_survivors"

        iteration = 0
        while (cfg.use_mutation_feedback and survivors
               and fb_score < cfg.target_mutation_score
               and iteration < cfg.max_refine_iterations):
            budget = self._budget_stop(llm, start)
            if budget:
                stop = budget
                self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯", "message": f"Stopping: {budget.replace('_', ' ')}"})
                break
            iteration += 1
            before = count_tests(code)
            diagnoses_text = None
            targets = survivors
            if self.blind_spot is not None:
                from .blindspot.engine import Cancelled, mutant_key
                self.phase("blindspot", f"iteration {iteration}")
                try:
                    suite, ds = self.blind_spot.run(code, mut, iteration, survivors)
                except Cancelled:
                    stop = "cancelled"
                    break
                if suite != code:
                    self.phase("execute", f"iteration {iteration} · corrective tests")
                    new_code, new_result, f_rounds, p = self._stabilize(info, suite, executor, fixer)
                    if not new_result.collection_error and new_result.total:
                        code, result, fix_rounds, pruned = new_code, new_result, f_rounds, p
                diagnoses_text = self.blind_spot.brief(ds)
                resolved = {d.mutant_key for d in self.blind_spot.engine.diagnoses.values()
                            if d.resolution_status == "resolved"}
                targets = [m for m in survivors if mutant_key(m) not in resolved]

            self.phase("refine", f"iteration {iteration}")
            newly_equiv = []
            if targets:
                try:
                    refined = writer.refine(info, code, targets[:40], diagnoses=diagnoses_text)
                except LLMError as e:
                    self.emit("error", {"message": str(e)})
                    stop = "llm_error"
                    break
                by_id = {m.id: m for m in targets}
                newly_equiv = [by_id[i] for i in refined.equivalent_mutant_ids if i in by_id]
                for m in newly_equiv:
                    equivalent.add((m.lineno, m.original, m.mutated))
                try:
                    candidate = merge_suites(code, refined.code)
                except SyntaxError:
                    candidate = code
            else:
                candidate = code
            if count_tests(candidate) > count_tests(code):
                self.phase("execute", f"iteration {iteration}")
                new_code, new_result, f_rounds, p = self._stabilize(info, candidate, executor, fixer)
                if new_result.collection_error or new_result.total == 0:
                    self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯",
                                        "message": "New tests broke the suite; keeping previous suite"})
                else:
                    code, result, fix_rounds, pruned = new_code, new_result, f_rounds, p
            gained = count_tests(code) - before

            if gained <= 0 and not newly_equiv:
                stop = "no_progress"
                self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯",
                                    "message": "No new useful tests this round; stopping"})
                break

            self.phase("mutate", f"iteration {iteration}")
            previous_score = fb_score
            mut = critic.run(info, code, equivalent, extra_paths)
            if self.blind_spot is not None:
                self.blind_spot.engine.reconcile(mut)
            records.append(self._record(iteration, "refine", code, result, mut, fix_rounds,
                                        pruned, llm, start))
            survivors, fb_score = self._feedback(mut)
            if fb_score <= previous_score and not newly_equiv:
                stop = "plateau"
                self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯",
                                    "message": "Mutation score did not improve; stopping"})
                break
            if fb_score >= cfg.target_mutation_score:
                stop = "target_reached"
            elif not survivors:
                stop = "no_survivors"
            else:
                stop = "max_iterations"

        # Final diagnosis (no correction) of what still survives, for the report.
        if self.blind_spot is not None and survivors and stop not in ("cancelled", "llm_error"):
            from .blindspot.engine import Cancelled
            self.phase("blindspot", "final diagnosis")
            try:
                self.blind_spot.engine.investigate(code, mut, iteration + 1, survivors=survivors)
            except Cancelled:
                stop = "cancelled"

        self.phase("done", stop)
        return self._report(info, records, code, stop, llm, start, mut)

    # ------------------------------------------------------------------
    def _chunks(self, info: ModuleInfo) -> list[set[str] | None]:
        """[None] when the whole module fits the prompt budget, else groups of
        functions whose focused views fit."""
        budget = self.config.max_prompt_tokens
        if not budget:
            return [None]
        full = prompts.WRITER_SYSTEM + prompts.writer_prompt(info, None)
        if estimate_tokens(full) <= budget * 0.6:  # leave room for the plan in the writer prompt
            return [None]
        skeleton = prompts.WRITER_SYSTEM + prompts.writer_prompt(info, None, focus=set())
        chunks = chunk_functions(info, int(budget * 0.6), estimate_tokens(skeleton))
        self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯",
                            "message": f"Module exceeds the prompt budget ({budget} tokens): "
                                       f"working in {len(chunks)} chunks of functions"})
        return chunks

    def _plan_batches(self, info: ModuleInfo, plan, focus):
        """Split a test plan so that each Writer prompt fits the budget."""
        budget = self.config.max_prompt_tokens
        if plan is None or not budget:
            return [plan]
        size = lambda p: estimate_tokens(prompts.WRITER_SYSTEM + prompts.writer_prompt(info, p, focus))
        batches, todo = [], [plan]
        while todo:
            p = todo.pop(0)
            if size(p) <= budget or len(p.cases) <= 2:
                batches.append(p)
            else:
                half = len(p.cases) // 2
                todo[:0] = [p.model_copy(update={"cases": p.cases[:half]}),
                            p.model_copy(update={"cases": p.cases[half:]})]
        if len(batches) > 1:
            self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯",
                                "message": f"Test plan split into {len(batches)} writing batches "
                                           "to fit the prompt budget"})
        return batches

    def _generate(self, info: ModuleInfo, planner: PlannerAgent, writer: WriterAgent) -> str | None:
        """Plan and write tests, chunk by chunk when the module is too large.
        A chunk whose LLM call fails is skipped; None if every chunk failed."""
        code, failures = "", []
        chunks = self._chunks(info)
        for n, focus in enumerate(chunks, 1):
            label = "" if focus is None else f"chunk {n}/{len(chunks)}"
            try:
                plan = None
                if self.config.use_planner:
                    self.phase("plan", label)
                    plan = planner.run(info, focus)
                self.phase("write", label)
                part = ""
                for batch in self._plan_batches(info, plan, focus):
                    part = merge_suites(part, writer.run(info, batch, focus))
            except DailyQuotaExceeded:
                raise  # no point trying further chunks today
            except LLMError as e:
                failures.append(str(e))
                self.emit("error", {"message": f"{label or 'generation'} failed: {e}"})
                continue
            try:
                code = merge_suites(code, part)
            except SyntaxError:
                code = code or part  # unparsable part: the Executor/Fixer will deal with it
        if not code and failures:
            raise LLMError(failures[0])
        return code

    # ------------------------------------------------------------------
    def _stabilize(self, info: ModuleInfo, code: str, executor: ExecutorAgent,
                   fixer: FixerAgent) -> tuple[str, ExecResult, int, int]:
        """Execute; let the Fixer repair failures; prune whatever still fails."""
        cfg = self.config
        fix_rounds = 0
        result = executor.run(info, code, self.extra_paths)
        while not result.ok and cfg.use_fixer and fix_rounds < cfg.max_fix_attempts:
            if result.total == 0 and not result.collection_error:
                break
            fix_rounds += 1
            try:
                fixed = fixer.run(info, code, result)
            except LLMError as e:
                self.emit("error", {"message": str(e)})
                break
            if fixed.strip() == code.strip():
                break
            code = fixed
            result = executor.run(info, code, self.extra_paths)

        pruned = 0
        if not result.collection_error and result.failed:
            code, pruned = prune_tests(code, {o.name for o in result.failing})
            self.emit("agent", {"agent": "Orchestrator", "emoji": "🎯",
                                "message": f"Removed {pruned} test(s) that still fail"})
            result = executor.run(info, code, self.extra_paths)
        return code, result, fix_rounds, pruned

    def _record(self, it, phase, code, result: ExecResult, mut: MutationResult, fix_rounds,
                pruned, llm: LLM, start) -> IterationRecord:
        held = [m for m in mut.mutants if self._holdout(m) and m.status not in ("equivalent", "error")]
        raw_total = len(mut.mutants) - mut.count("error")
        bs = self.blind_spot.engine.diagnoses.values() if self.blind_spot else []
        rec = IterationRecord(
            iteration=it, phase=phase, tests_total=count_tests(code), tests_passing=result.passed,
            line_coverage=result.line_coverage, branch_coverage=result.branch_coverage,
            mutation_score=round(mut.score, 4), mutants_total=len(mut.mutants),
            mutants_killed=mut.killed, mutants_survived=len(mut.survivors),
            mutants_equivalent=mut.count("equivalent"), fix_rounds=fix_rounds, pruned_tests=pruned,
            input_tokens=llm.usage.input_tokens, output_tokens=llm.usage.output_tokens,
            elapsed_s=round(time.time() - start, 2),
            mutation_score_raw=round(mut.killed / raw_total, 4) if raw_total else None,
            holdout_score=round(sum(1 for m in held if m.status in ("killed", "timeout")) / len(held), 4)
            if held else None,
            blind_spots=len(bs), blind_spots_resolved=sum(1 for d in bs if d.resolution_status == "resolved"),
        )
        self.emit("iteration", rec.__dict__)
        return rec

    def _blind_spot_section(self, code: str) -> dict:
        eng = self.blind_spot.engine
        state = eng.state()
        state["corrective_tests"] = _corrective_tests(eng, code)
        return state

    def _report(self, info, records, code, stop, llm: LLM, start, mut) -> RunReport:
        pin, pout = self.config.price()
        u = llm.usage
        report = RunReport(
            module=info.summary(), config=self.config.to_dict(), iterations=records,
            final_tests=code, stop_reason=stop, total_input_tokens=u.input_tokens,
            total_output_tokens=u.output_tokens,
            cost_usd=round(u.input_tokens / 1e6 * pin + u.output_tokens / 1e6 * pout, 4),
            elapsed_s=round(time.time() - start, 2),
            survivors=[m.__dict__ for m in (mut.survivors if mut else [])],
            blind_spot=self._blind_spot_section(code) if self.blind_spot else None,
        )
        self.emit("done", {"stop_reason": stop, "final": records[-1].__dict__ if records else None,
                           "cost_usd": report.cost_usd, "by_role": u.by_role})
        return report


def _corrective_tests(engine, suite: str) -> list[dict]:
    """Corrective tests that made it into the final suite, with their blind spot."""
    out = []
    for d in engine.diagnoses.values():
        name = d.verification.killed_by if d.resolution_status == "resolved" else d.proposed_test_name
        if not name:
            continue
        out.append({"blind_spot_id": d.blind_spot_id, "mutant_id": d.mutant_id, "test_name": name,
                    "origin": d.test_origin, "resolved": d.resolution_status == "resolved",
                    "in_suite": f"def {name}(" in suite, "code": d.proposed_test,
                    "primary_category": d.primary_category, "round": d.round})
    return out


def save_report(report: RunReport, out_dir: str | Path, module_name: str) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    test_path = out / f"test_{module_name}.py"
    test_path.write_text(report.final_tests, encoding="utf-8")
    report_path = out / f"report_{module_name}.json"
    report_path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
    return test_path, report_path
