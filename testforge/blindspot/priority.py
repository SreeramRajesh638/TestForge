"""Explainable, rule-based investigation priority.

Investigating a surviving mutant costs several sandboxed test runs, so the
engine investigates the most promising ones first. The policy is a weighted sum
of observable factors (no trained model):

    priority =  3.0 * unreached            (coverage: no test executes the line)
              + 2.0 * boundary             (mutation moves a decision boundary)
              + 1.5 * min(1, cluster / 5)  (other survivors in the same function)
              + 1.0 * min(1, complexity / 10)   (cyclomatic-style complexity)
              + 1.0 * missing_branch       (the function has untaken branches)
              + 1.0 * weak_assertions      (tests reaching the function have weak/no asserts)
              - 2.0 * failed_attempts      (earlier rounds already failed to resolve it)
              - 0.5 * cost                 (suite runtime, normalised to 30 s)

Each factor is returned with the score so the UI can show why a blind spot
was ranked where it is.
"""

from __future__ import annotations

WEIGHTS = {"unreached": 3.0, "boundary": 2.0, "cluster": 1.5, "complexity": 1.0,
           "missing_branch": 1.0, "weak_assertions": 1.0, "failed_attempts": -2.0, "cost": -0.5}


def priority(*, unreached: bool, boundary: bool, cluster: int, complexity: int, missing_branch: bool,
             weak_assertions: bool, failed_attempts: int, suite_seconds: float) -> tuple[float, dict[str, float]]:
    factors = {
        "unreached": 1.0 if unreached else 0.0,
        "boundary": 1.0 if boundary else 0.0,
        "cluster": min(1.0, max(0, cluster - 1) / 5),
        "complexity": min(1.0, complexity / 10),
        "missing_branch": 1.0 if missing_branch else 0.0,
        "weak_assertions": 1.0 if weak_assertions else 0.0,
        "failed_attempts": float(failed_attempts),
        "cost": min(1.0, suite_seconds / 30),
    }
    contrib = {k: round(WEIGHTS[k] * v, 3) for k, v in factors.items()}
    return round(sum(contrib.values()), 3), contrib
