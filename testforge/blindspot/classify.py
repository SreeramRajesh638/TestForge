"""RIPR-based blind spot classification (pure function, no I/O).

The RIPR model (Reachability, Infection, Propagation, Revealability) says a
test kills a mutant only if it (1) reaches the mutated location, (2) the
mutated code produces a different internal state there, (3) that difference
propagates to something observable, and (4) an assertion checks it. The
first condition that fails is the blind spot's *primary category*:

=============  ==============================================================
Category       Measured by
=============  ==============================================================
reachability   coverage.py: no test executes the mutated statement
infection      instrumentation: the statement runs, but on every evaluation
               the original and mutated expression give the same value
propagation    the expression's value differs at least once, yet every
               test-level call returns/raises/leaves state exactly as the
               original does
revealability  some test-level call observably differs (return value,
               exception or object state) but the test still passes: the
               assertions do not check what changed
inconclusive   the evidence cannot separate the cases (coverage failed, or
               infection could not be measured and no output differed)
=============  ==============================================================

A finding is **confirmed** when its category is measured directly *and* the
fault is shown to be detectable (a counterexample was found, or a test already
observed a different output). Without a counterexample it stays a
**hypothesis** (the mutant might be equivalent). Evidence strength is a
qualitative rating of how complete the measurements are; it is not a
calibrated probability.
"""

from __future__ import annotations

from .schema import Evidence, STRATEGIES


def classify(ev: Evidence, *, boundary: bool, behaviour: str, is_method: bool,
             counterexample_found: bool, search_ran: bool, redundant_tests: bool = False) -> dict:
    notes: list[str] = []
    labels: list[str] = []

    if ev.reached is None and ev.executions == 0:
        primary = "inconclusive"
        notes.append("coverage data was not available, so reachability could not be decided")
    elif ev.reached is False and ev.executions == 0:
        primary = "reachability"
        notes.append("coverage.py: no test executed the mutated statement")
    elif ev.propagated_calls:
        primary = "revealability"
        notes.append(f"{len(ev.propagated_calls)} test-level call(s) returned a different result on the mutant, "
                     "yet every test passed")
    elif ev.infection_supported and ev.executions > 0 and ev.infected_evaluations == 0:
        primary = "infection"
        notes.append(f"the mutated expression was evaluated {ev.executions} time(s) and never changed value")
    elif ev.infection_supported and ev.infected_evaluations > 0:
        primary = "propagation"
        notes.append(f"the mutated expression changed value in {ev.infected_evaluations} of {ev.executions} "
                     "evaluation(s), but no test-level output or state differed")
    else:
        primary = "inconclusive"
        if not ev.infection_supported:
            notes.append(f"infection could not be measured ({ev.infection_note or 'unsupported expression'}) "
                         "and no observable output differed")
        else:
            notes.append("the statement is covered but the expression was never evaluated by the probe")

    # secondary labels (complement, never replace, the primary category)
    if primary == "infection":
        labels.append("Missing boundary condition" if boundary else "Insufficient input diversity")
    if primary == "reachability":
        labels.append("Insufficient input diversity")
    if primary == "revealability":
        labels.append("Weak assertion")
        weak = {t: a for t, a in ev.assertions.items() if a.get("weak") or a.get("asserts", 0) == 0}
        reaching_weak = [t for t in ev.reaching_tests if t in weak]
        if reaching_weak:
            notes.append("tests with weak or no assertions reach this code: " + ", ".join(reaching_weak[:4]))
    if behaviour in ("exception_handling", "input_validation") and primary in ("reachability", "infection", "revealability"):
        labels.append("Missing exception test")
    if is_method and (primary == "propagation" or any(c.original_state != c.mutant_state
                                                      for c in ev.propagated_calls)):
        labels.append("Missing state verification")
    if redundant_tests:
        labels.append("Redundant test")
    if search_ran and not counterexample_found and primary in ("infection", "propagation", "inconclusive"):
        labels.append("Potential equivalent mutant")
        notes.append("no input that distinguishes the mutant was found; it may be equivalent "
                     "(reported separately, not removed from the score)")

    if primary == "inconclusive":
        status = "inconclusive"
    elif primary in ("reachability", "revealability"):
        status = "confirmed"
    else:
        status = "confirmed" if counterexample_found else "hypothesis"

    measured = primary != "inconclusive"
    if measured and (counterexample_found or primary == "revealability"):
        strength = "strong"
    elif measured:
        strength = "moderate"
    else:
        strength = "weak"

    return {"primary": primary, "secondary": list(dict.fromkeys(labels)), "status": status,
            "strength": strength, "notes": notes, "strategy": STRATEGIES[primary]}
