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


DEFAULT_MODELS = {"claude": "claude-opus-5-5", "ollama": "qwen2.5-coder:3b", "mock": "mock",
                  "groq": "openai/gpt-oss-120b", "openrouter": "openai/gpt-oss-120b:free"}


# Default prompt budgets for free tiers (leaves room for the reply in the
# per-minute token limit). Override with Config(max_prompt_tokens=...).
FREE_TIER_PROMPT_BUDGET = {"groq": 4500}


@dataclass
class Config:
    # LLM backend: "claude" (Anthropic API), "ollama" (free local models),
    # "groq" / "openrouter" (OpenAI-compatible services with free tiers)
    # or "mock" (offline, no AI). model=None picks the backend's default.
    llm: str = "mock"
    model: str | None = None
    effort: str = "high"  # low | medium | high | xhigh | max (Claude only)
    ollama_host: str | None = None     # default http://127.0.0.1:11434
    num_ctx: int = 8192                # Ollama context window (tokens)
    # Largest prompt to send in one request (estimated tokens). None = no limit.
    # Set for services with per-request caps (Groq free tier: 8,000 tokens/minute).
    max_prompt_tokens: int | None = None

    # Feedback loop limits.
    max_fix_attempts: int = 2          # Fixer rounds per execution
    max_refine_iterations: int = 3     # mutation-guided refinement rounds
    target_mutation_score: float = 0.90

    # Ablation switches (turn an agent off to measure its contribution).
    use_planner: bool = True
    use_fixer: bool = True
    use_mutation_feedback: bool = True
    # Blind Spot Intelligence: diagnose surviving mutants with execution
    # evidence and add verified corrective tests before targeted refinement.
    # Off by default so the original pipeline stays the baseline configuration.
    use_blind_spot: bool = False
    blind_spot_max_investigations: int = 12   # per round, highest priority first

    # Research protocol: this fraction of mutants is held out (deterministically,
    # by hash) and never shown to the Writer or the Blind Spot agent; their kill
    # rate is reported separately as the held-out mutation score.
    holdout_fraction: float = 0.0

    # Extra stopping conditions (None = no limit).
    max_total_tokens: int | None = None
    max_seconds: float | None = None

    # Execution limits.
    test_timeout_s: int = 60           # one pytest run of the whole suite
    mutant_timeout_s: int = 10         # one pytest run against one mutant
    mutation_workers: int = 4
    max_mutants: int | None = None     # cap for very large modules

    seed: int = 42
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.model is None:
            self.model = DEFAULT_MODELS.get(self.llm, "mock")
        if self.max_prompt_tokens is None and self.llm in FREE_TIER_PROMPT_BUDGET:
            self.max_prompt_tokens = FREE_TIER_PROMPT_BUDGET[self.llm]

    def to_dict(self) -> dict:
        return asdict(self)

    def price(self) -> tuple[float, float]:
        if self.llm != "claude":
            return (0.0, 0.0)  # mock, local Ollama and free-tier services
        return MODEL_PRICES.get(self.model, (0.0, 0.0))
