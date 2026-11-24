"""Run configuration for TestForge.

Every experimental knob lives here so that a run is fully described by one
Config object (saved into the report for reproducibility). The ``use_*`` flags
switch individual agents off for the ablation study.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Price per million tokens (input, output) in USD, used for cost metrics.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "mock": (0.0, 0.0),
}


@dataclass
class Config:
    # LLM backend: "claude" (Anthropic API) or "mock" (offline, no API key).
    llm: str = "mock"
    model: str = "claude-opus-5-5"
    effort: str = "high"  # low | medium | high | xhigh | max

    # Feedback loop limits.
    max_fix_attempts: int = 2          # Fixer rounds per execution
    max_refine_iterations: int = 3     # mutation-guided refinement rounds
    target_mutation_score: float = 0.90

    # Ablation switches (turn an agent off to measure its contribution).
    use_planner: bool = True
    use_fixer: bool = True
    use_mutation_feedback: bool = True

    # Execution limits.
    test_timeout_s: int = 60           # one pytest run of the whole suite
    mutant_timeout_s: int = 10         # one pytest run against one mutant
    mutation_workers: int = 4
    max_mutants: int | None = None     # cap for very large modules

    seed: int = 42
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def price(self) -> tuple[float, float]:
        key = "mock" if self.llm == "mock" else self.model
        return MODEL_PRICES.get(key, (0.0, 0.0))
