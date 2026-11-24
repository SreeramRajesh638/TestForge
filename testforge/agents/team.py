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

from pathlib import Path

from ..core.analysis import analyze_file, analyze_source
from ..core.mutation import run_mutation_analysis
from ..core.runner import run_tests
from ..core.suite import is_valid_python, strip_code_fences
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

    def run(self, info: ModuleInfo) -> TestPlan:
        plan = self.llm.complete("planner", prompts.PLANNER_SYSTEM, prompts.planner_prompt(info),
                                 TestPlan, {"module": info})
        cats: dict[str, int] = {}
        for c in plan.cases:
            cats[c.category] = cats.get(c.category, 0) + 1
        self.log(f"Planned {len(plan.cases)} test cases " +
                 ", ".join(f"{v} {k}" for k, v in sorted(cats.items())), cases=len(plan.cases))
        return plan


class WriterAgent(Agent):
    name, emoji = "Writer", "✍️"

    def run(self, info: ModuleInfo, plan: TestPlan | None) -> str:
        out = self.llm.complete("writer", prompts.WRITER_SYSTEM, prompts.writer_prompt(info, plan),
                                GeneratedTests, {"module": info, "plan": plan})
        code = strip_code_fences(out.code)
        self.log(f"Wrote test file ({len(code.splitlines())} lines)", notes=out.notes)
        return code

    def refine(self, info: ModuleInfo, test_code: str, survivors) -> RefinedTests:
        out = self.llm.complete("refiner", prompts.REFINER_SYSTEM,
                                prompts.refiner_prompt(info, test_code, survivors), RefinedTests,
                                {"module": info, "test_code": test_code, "survivors": survivors})
        out.code = strip_code_fences(out.code)
        self.log(f"Wrote new tests targeting {len(survivors)} surviving mutants"
                 + (f"; flagged {len(out.equivalent_mutant_ids)} as equivalent"
                    if out.equivalent_mutant_ids else ""), notes=out.notes)
        return out


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
        out = self.llm.complete("fixer", prompts.FIXER_SYSTEM,
                                prompts.fixer_prompt(info, test_code, failures), GeneratedTests,
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
