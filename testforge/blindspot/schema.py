"""Validated data model of the Blind Spot Intelligence Engine (pydantic).

A *blind spot* is a surviving mutant together with the evidence that explains
why the test suite misses it. Every field that states a fact about execution is
filled from a measurement (coverage, instrumentation, differential runs); the
explanation text is generated from those measurements, never invented.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PrimaryCategory = Literal["reachability", "infection", "propagation", "revealability", "inconclusive"]
FindingStatus = Literal["confirmed", "hypothesis", "inconclusive"]
Strength = Literal["strong", "moderate", "weak"]
Resolution = Literal["unresolved", "investigating", "resolved", "invalid_test", "not_attempted"]
VerificationStatus = Literal["not_started", "running", "verified", "failed"]

PRIMARY_LABELS = {
    "reachability": "Reachability failure",
    "infection": "Infection failure",
    "propagation": "Propagation failure",
    "revealability": "Revealability failure",
    "inconclusive": "Inconclusive",
}

STRATEGIES = {
    "reachability": "Generate inputs that execute the missing branch or statement.",
    "infection": "Use inputs that make the original and the mutated expression differ, "
                 "especially values at and next to the decision boundary.",
    "propagation": "Find inputs and paths on which the internal difference reaches a return value, "
                   "an exception or the object's state.",
    "revealability": "Strengthen the assertion: check the output, exception or state that already differs.",
    "inconclusive": "Collect more evidence; the available measurements do not justify a corrective strategy.",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservedCall(_Strict):
    """One test-level call whose observable outcome differs between the
    original program and the mutant."""
    test: str | None
    call: str
    original: str
    mutant: str
    original_state: str | None = None
    mutant_state: str | None = None


class Evidence(_Strict):
    reached: bool | None = Field(description="Did the existing tests execute the mutated statement (coverage)?")
    reaching_tests: list[str] = []
    executions: int = Field(0, description="How often the mutated expression was evaluated by the tests")
    infection_supported: bool = False
    infection_point: str = ""
    infection_note: str = ""
    infected_evaluations: int = 0
    infection_examples: list[dict] = []
    propagated_calls: list[ObservedCall] = []
    noisy_calls: int = Field(0, description="Calls that differ between two runs of the ORIGINAL program; ignored")
    diverged_tests: list[str] = []
    assertions: dict[str, dict] = {}
    unsupported: list[str] = []


class Counterexample(_Strict):
    function: str
    call: str
    args: list[str]
    init: list[str] = []
    original: dict
    mutant: dict
    source: str
    tried: int
    alternatives: int = 0


class Verification(_Strict):
    passes_on_original: bool | None = None
    kills_mutant: bool | None = None
    suite_regression_free: bool | None = None
    killed_by: str | None = None
    detail: str = ""


class Diagnosis(_Strict):
    blind_spot_id: str
    session_id: str
    mutant_id: int
    mutant_key: str
    round: int
    function_name: str | None
    source_line: int
    mutation_operator: str
    original_code: str
    mutated_code: str
    behaviour_category: str
    primary_category: PrimaryCategory
    secondary_labels: list[str] = []
    diagnosis: str
    evidence: Evidence
    evidence_strength: Strength
    evidence_notes: list[str] = []
    finding_status: FindingStatus
    suggested_input: str | None = None
    observed_original_output: str | None = None
    observed_mutant_output: str | None = None
    counterexample: Counterexample | None = None
    counterexample_searched: bool = False
    corrective_strategy: str
    proposed_test: str | None = None
    proposed_test_name: str | None = None
    test_origin: Literal["counterexample", "llm", "writer"] | None = None
    llm_explanation: str | None = None
    verification: Verification = Verification()
    verification_status: VerificationStatus = "not_started"
    resolution_status: Resolution = "unresolved"
    priority: float = 0.0
    priority_factors: dict[str, float] = {}
    attempts: int = 0
    duration_s: float = 0.0


# ---- LLM output for corrective tests -------------------------------------
class CorrectiveTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blind_spot_id: str = Field(description="Id of the blind spot this test targets, e.g. BS-3")
    test_code: str = Field(description="One pytest test function (with the imports it needs)")
    explanation: str = Field(description="One or two sentences: why the original suite missed this fault "
                                         "and what the new assertion checks")


class CorrectiveTestSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tests: list[CorrectiveTest]
