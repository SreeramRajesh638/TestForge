"""Data structures shared between agents.

Two kinds of models live here:
* dataclasses for internal state and reports;
* pydantic models (``extra="forbid"``) that double as JSON schemas for the
  LLM's structured outputs, so every agent reply is validated.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------
# Code analysis (Analyzer agent output)
# --------------------------------------------------------------------------
@dataclass
class ParamInfo:
    name: str
    annotation: str | None = None
    default: str | None = None


@dataclass
class FunctionInfo:
    name: str
    qualname: str                    # e.g. "BankAccount.deposit"
    class_name: str | None
    params: list[ParamInfo]
    returns: str | None
    docstring: str | None
    source: str
    lineno: int
    end_lineno: int
    raises: list[str] = field(default_factory=list)
    branches: int = 0                # if/for/while/try/boolop/ternary count
    constants: list = field(default_factory=list)  # numeric literals (boundary hints)

    @property
    def complexity(self) -> int:
        """Approximate cyclomatic complexity."""
        return self.branches + 1


@dataclass
class ClassInfo:
    name: str
    init_params: list[ParamInfo]
    methods: list[str]
    docstring: str | None


@dataclass
class ModuleInfo:
    module_name: str
    path: str
    source: str
    functions: list[FunctionInfo]
    classes: list[ClassInfo]
    imports: list[str]

    def function(self, qualname: str) -> FunctionInfo | None:
        return next((f for f in self.functions if f.qualname == qualname), None)

    def summary(self) -> dict:
        return {
            "module": self.module_name,
            "functions": len(self.functions),
            "classes": len(self.classes),
            "loc": len(self.source.splitlines()),
            "total_complexity": sum(f.complexity for f in self.functions),
        }


# --------------------------------------------------------------------------
# Structured LLM outputs
# --------------------------------------------------------------------------
class PlannedCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(description="Qualified name of the function/method under test")
    category: str = Field(description="normal | boundary | error | state")
    description: str = Field(description="What behaviour this case checks")
    inputs: str = Field(description="Concrete inputs / setup, as Python-like text")
    expected: str = Field(description="Expected result or exception")


class TestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    __test__ = False  # stop pytest collecting this class
    cases: list[PlannedCase]


class GeneratedTests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    __test__ = False
    code: str = Field(description="Complete pytest file contents")
    notes: str = Field(description="Short explanation of what was written or changed")


class RefinedTests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(description="pytest code containing only the NEW tests (with imports)")
    equivalent_mutant_ids: list[int] = Field(
        description="IDs of mutants judged semantically equivalent to the original"
    )
    notes: str


# --------------------------------------------------------------------------
# Execution and mutation results
# --------------------------------------------------------------------------
@dataclass
class TestOutcome:
    name: str
    passed: bool
    message: str = ""


@dataclass
class ExecResult:
    total: int
    passed: int
    failed: int
    collection_error: str | None
    outcomes: list[TestOutcome]
    line_coverage: float
    branch_coverage: float
    duration_s: float
    raw_output: str = ""

    @property
    def failing(self) -> list[TestOutcome]:
        return [o for o in self.outcomes if not o.passed]

    @property
    def ok(self) -> bool:
        return self.collection_error is None and self.failed == 0 and self.total > 0


@dataclass
class Mutant:
    id: int
    operator: str          # AOR, ROR, LCR, UOI, CRP, RET, ASR
    lineno: int
    original: str
    mutated: str
    function: str | None = None
    status: str = "pending"   # killed | survived | timeout | equivalent | error

    def describe(self) -> str:
        where = f" in {self.function}" if self.function else ""
        return f"#{self.id} [{self.operator}] line {self.lineno}{where}: `{self.original}` -> `{self.mutated}`"


@dataclass
class MutationResult:
    mutants: list[Mutant]
    duration_s: float

    def count(self, status: str) -> int:
        return sum(1 for m in self.mutants if m.status == status)

    @property
    def killed(self) -> int:
        return self.count("killed") + self.count("timeout")

    @property
    def scored_total(self) -> int:
        """Mutants that count toward the score (equivalent ones are excluded)."""
        return len(self.mutants) - self.count("equivalent") - self.count("error")

    @property
    def score(self) -> float:
        return self.killed / self.scored_total if self.scored_total else 0.0

    @property
    def survivors(self) -> list[Mutant]:
        return [m for m in self.mutants if m.status == "survived"]


# --------------------------------------------------------------------------
# Run report (one per module)
# --------------------------------------------------------------------------
@dataclass
class IterationRecord:
    iteration: int
    phase: str                 # "initial" or "refine"
    tests_total: int
    tests_passing: int
    line_coverage: float
    branch_coverage: float
    mutation_score: float | None
    mutants_total: int
    mutants_killed: int
    mutants_survived: int
    mutants_equivalent: int
    fix_rounds: int
    pruned_tests: int
    input_tokens: int
    output_tokens: int
    elapsed_s: float


@dataclass
class RunReport:
    module: dict
    config: dict
    iterations: list[IterationRecord]
    final_tests: str
    stop_reason: str
    total_input_tokens: int
    total_output_tokens: int
    cost_usd: float
    elapsed_s: float
    survivors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def final(self) -> IterationRecord | None:
        return self.iterations[-1] if self.iterations else None
