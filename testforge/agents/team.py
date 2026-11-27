"""The agent team.

    Analyzer        static analysis of the code under test        (deterministic)
    Planner         designs test cases                            (LLM)
    Writer          writes the pytest file                        (LLM)
    Executor        runs tests, measures coverage                 (deterministic)
    Fixer           repairs failing tests                         (LLM)
    MutationCritic  mutation analysis, finds weak spots           (deterministic)
    Refiner         writes tests that kill surviving mutants      (LLM, Writer role)

Deterministic agents are tools with a clear contract; LLM agents receive
validated structured output so the orchestrator never parses free text.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.analysis import analyze_file, analyze_source
from ..core.mutation import run_mutation_analysis
from ..core.runner import run_tests
from ..core.budget import estimate_tokens
from ..core.suite import (extract_tests, is_valid_python, replace_tests, strip_code_fences,
                          list_test_names)
from ..models import ExecResult, GeneratedTests, ModuleInfo, MutationResult, RefinedTests, TestPlan
from . import prompts
from .base import Agent


class AnalyzerAgent(Agent):
    name, emoji = "Analyzer", "🔍"

    def run(self, path: str | None = None, source: str | None = None,
            module_name: str | None = None) -> ModuleInfo:
        info = analyze_file(path) if path else analyze_source(source or "", module_name or "module")
        s = info.summary()
        self.log(f"Found {s['functions']} functions/methods in {s['classes']} classes, "
                 f"{s['loc']} lines, total complexity {s['total_complexity']}", summary=s)
        return info


class PlannerAgent(Agent):
    name, emoji = "Planner", "📋"

    def run(self, info: ModuleInfo, focus: set[str] | None = None) -> TestPlan:
        plan = self.llm.complete("planner", prompts.PLANNER_SYSTEM,
                                 prompts.planner_prompt(info, focus), TestPlan,
                                 {"module": info, "focus": focus})
        cats: dict[str, int] = {}
        for c in plan.cases:
            cats[c.category] = cats.get(c.category, 0) + 1
        self.log(f"Planned {len(plan.cases)} test cases " +
                 ", ".join(f"{v} {k}" for k, v in sorted(cats.items())), cases=len(plan.cases))
        return plan


class WriterAgent(Agent):
    name, emoji = "Writer", "✍️"

    def run(self, info: ModuleInfo, plan: TestPlan | None, focus: set[str] | None = None) -> str:
        out = self.llm.complete("writer", prompts.WRITER_SYSTEM,
                                prompts.writer_prompt(info, plan, focus), GeneratedTests,
                                {"module": info, "plan": plan, "focus": focus})
        code = strip_code_fences(out.code)
        self.log(f"Wrote test file ({len(code.splitlines())} lines)", notes=out.notes)
        return code

    def refine(self, info: ModuleInfo, test_code: str, survivors, diagnoses: str | None = None) -> RefinedTests:
        prompt = prompts.refiner_prompt(info, test_code, survivors, diagnoses=diagnoses)
        budget = self.config.max_prompt_tokens
        if budget and estimate_tokens(prompts.REFINER_SYSTEM + prompt) > budget:
            # focus on the functions the survivors live in; list test names instead of code
            survivors = list(survivors)
            while True:
                funcs = {m.function for m in survivors}
                focus = None if None in funcs else funcs
                prompt = prompts.refiner_prompt(info, None, survivors, focus, list_test_names(test_code),
                                                diagnoses=diagnoses[:1500] if diagnoses else None)
                if estimate_tokens(prompts.REFINER_SYSTEM + prompt) <= budget or len(survivors) <= 3:
                    break
                survivors = survivors[: max(3, len(survivors) // 2)]
            self.log(f"Prompt over budget: focusing on {len(survivors)} mutants in "
                     f"{'the module' if focus is None else ', '.join(sorted(focus))}")
        out = self.llm.complete("refiner", prompts.REFINER_SYSTEM, prompt, RefinedTests,
                                {"module": info, "test_code": test_code, "survivors": survivors})
        out.code = strip_code_fences(out.code)
        self.log(f"Wrote new tests targeting {len(survivors)} surviving mutants"
                 + (f"; flagged {len(out.equivalent_mutant_ids)} as equivalent"
                    if out.equivalent_mutant_ids else ""), notes=out.notes)
        return out


class BlindSpotAgent(Agent):
    """Blind Spot Intelligence: explains WHY tests miss each surviving mutant
    (RIPR evidence), finds distinguishing inputs and verifies corrective tests.
    Thin agent wrapper around testforge.blindspot.engine.BlindSpotEngine."""
    name, emoji = "Blind Spot Intelligence", "🧠"

    def __init__(self, llm, config, emit=None, *, info: ModuleInfo, extra_paths=None, cancel=None,
                 session_id: str = "run") -> None:
        super().__init__(llm, config, emit)
        from ..blindspot.engine import BlindSpotEngine
        self.engine = BlindSpotEngine(info, llm, config, self._emit, session_id=session_id,
                                      extra_paths=extra_paths, cancel=cancel)

    def run(self, test_code: str, mut: MutationResult, round_no: int, survivors) -> tuple[str, list]:
        """Diagnose, propose and verify. Returns (suite with verified corrective tests, diagnoses)."""
        ds = self.engine.investigate(test_code, mut, round_no, survivors=survivors)
        by = {}
        for d in ds:
            by[d.primary_category] = by.get(d.primary_category, 0) + 1
        self.log(f"Diagnosed {len(ds)} blind spot(s): " + (", ".join(f"{v} {k}" for k, v in by.items()) or "none"),
                 blind_spots=len(ds))
        self.engine.propose_llm(ds)
        suite = self.engine.verify(test_code, ds)
        resolved = sum(1 for d in ds if d.resolution_status == "resolved")
        if any(d.proposed_test for d in ds):
            self.log(f"Verified {resolved} corrective test(s): each passes on the original, fails on its mutant, "
                     "and the suite stays green", resolved=resolved)
        return suite, ds

    def brief(self, diagnoses) -> str:
        """Evidence summary for the Writer's targeted refinement prompt."""
        lines = []
        for d in diagnoses:
            if d.resolution_status == "resolved" or d.primary_category == "inconclusive":
                continue
            line = (f"- mutant #{d.mutant_id}: {d.primary_category} failure. {d.corrective_strategy}")
            if d.counterexample:
                line += (f" Distinguishing input: {d.counterexample.call} (original {d.counterexample.original['repr']},"
                         f" mutant {d.counterexample.mutant['repr']}).")
            elif d.evidence.reached is False:
                line += " No test executes this line."
            lines.append(line)
        return "\n".join(lines[:12])


class ExecutorAgent(Agent):
    name, emoji = "Executor", "▶️"

    def run(self, info: ModuleInfo, test_code: str, extra_paths=None) -> ExecResult:
        syntax = is_valid_python(test_code)
        if syntax:
            self.log(f"Test file is not valid Python: {syntax}")
            return ExecResult(0, 0, 0, syntax, [], 0.0, 0.0, 0.0)
        res = run_tests(info.module_name, info.source, test_code,
                        timeout_s=self.config.test_timeout_s, extra_paths=extra_paths)
        if res.collection_error:
            self.log("Tests could not be collected", error=res.collection_error[:500])
        else:
            self.log(f"{res.passed}/{res.total} passed · line coverage {res.line_coverage:.0%} · "
                     f"branch coverage {res.branch_coverage:.0%}",
                     passed=res.passed, total=res.total)
        return res


class FixerAgent(Agent):
    name, emoji = "Fixer", "🔧"

    def run(self, info: ModuleInfo, test_code: str, result: ExecResult) -> str:
        if result.collection_error:
            failures = result.collection_error
        else:
            failures = "\n\n".join(f"FAILED {o.name}\n{o.message}" for o in result.failing)[:12000]
        prompt = prompts.fixer_prompt(info, test_code, failures)
        budget = self.config.max_prompt_tokens
        if budget and not result.collection_error and \
                estimate_tokens(prompts.FIXER_SYSTEM + prompt) > budget:
            # send only the failing tests and the functions they call
            failing = {o.name.split("[")[0] for o in result.failing}
            subset = extract_tests(test_code, failing)
            focus = {f.qualname for f in info.functions
                     if re.search(rf"\b{re.escape(f.name)}\b", subset)} or None
            prompt = prompts.fixer_prompt(info, subset, failures[:4000], focus, partial=True)
            out = self.llm.complete("fixer", prompts.FIXER_SYSTEM, prompt, GeneratedTests,
                                    {"module": info, "test_code": test_code, "result": result})
            self.log(f"Attempted repair of {len(failing)} failing test(s) (focused)", notes=out.notes)
            try:
                return replace_tests(test_code, failing, strip_code_fences(out.code))
            except SyntaxError:
                return test_code
        out = self.llm.complete("fixer", prompts.FIXER_SYSTEM, prompt, GeneratedTests,
                                {"module": info, "test_code": test_code, "result": result})
        self.log(f"Attempted repair of {len(result.failing) or 'collection'} failing test(s)",
                 notes=out.notes)
        return strip_code_fences(out.code)


class MutationCriticAgent(Agent):
    name, emoji = "Mutation Critic", "🧬"

    def run(self, info: ModuleInfo, test_code: str, known_equivalent=None,
            extra_paths=None) -> MutationResult:
        res = run_mutation_analysis(info, test_code, timeout_s=self.config.mutant_timeout_s,
                                    workers=self.config.mutation_workers,
                                    limit=self.config.max_mutants,
                                    known_equivalent=known_equivalent, extra_paths=extra_paths)
        self.log(f"Mutation score {res.score:.0%}: killed {res.killed}/{res.scored_total}, "
                 f"{len(res.survivors)} survived ({res.duration_s:.1f}s)",
                 score=res.score, survivors=len(res.survivors))
        return res
