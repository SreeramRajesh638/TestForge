"""Blind Spot Intelligence: classification and resolution logic.

The engine tests use small modules whose correct diagnosis is known by
construction (one per RIPR category), so they check the measurements end to
end rather than a mocked classifier.
"""

import textwrap

import pytest

from testforge.blindspot.classify import classify
from testforge.blindspot.engine import BlindSpotEngine
from testforge.blindspot.instrument import analyze_assertions, behaviour_category, infection_source, locate
from testforge.blindspot.priority import priority
from testforge.blindspot.schema import Evidence, ObservedCall
from testforge.config import Config
from testforge.core.analysis import analyze_source
from testforge.core.mutation import run_mutation_analysis


# ---------------------------------------------------------------- pure classifier
def ev(**kw):
    base = dict(reached=True, reaching_tests=["t"], executions=3, infection_supported=True, infected_evaluations=0)
    base.update(kw)
    return Evidence(**base)


def test_classify_reachability_when_never_executed():
    r = classify(ev(reached=False, executions=0, reaching_tests=[]), boundary=False, behaviour="arithmetic",
                 is_method=False, counterexample_found=False, search_ran=True)
    assert r["primary"] == "reachability" and r["status"] == "confirmed"


def test_classify_infection_with_boundary_label():
    r = classify(ev(), boundary=True, behaviour="boundary_conditions", is_method=False,
                 counterexample_found=True, search_ran=True)
    assert r["primary"] == "infection"
    assert "Missing boundary condition" in r["secondary"]
    assert r["status"] == "confirmed" and r["strength"] == "strong"


def test_classify_infection_without_counterexample_is_only_a_hypothesis():
    r = classify(ev(), boundary=False, behaviour="arithmetic", is_method=False,
                 counterexample_found=False, search_ran=True)
    assert r["primary"] == "infection" and r["status"] == "hypothesis"
    assert "Potential equivalent mutant" in r["secondary"]


def test_classify_propagation():
    r = classify(ev(infected_evaluations=2), boundary=False, behaviour="arithmetic", is_method=True,
                 counterexample_found=False, search_ran=True)
    assert r["primary"] == "propagation"
    assert "Missing state verification" in r["secondary"]


def test_classify_revealability_beats_other_signals():
    call = ObservedCall(test="t", call="f(1)", original="1", mutant="2")
    r = classify(ev(infected_evaluations=1, propagated_calls=[call]), boundary=True, behaviour="arithmetic",
                 is_method=False, counterexample_found=True, search_ran=False)
    assert r["primary"] == "revealability" and "Weak assertion" in r["secondary"]


def test_classify_never_forces_a_category_without_evidence():
    r = classify(ev(infection_supported=False, infection_note="calls a function"), boundary=False,
                 behaviour="arithmetic", is_method=False, counterexample_found=False, search_ran=True)
    assert r["primary"] == "inconclusive" and r["status"] == "inconclusive"
    r = classify(ev(reached=None, executions=0), boundary=False, behaviour="arithmetic", is_method=False,
                 counterexample_found=False, search_ran=False)
    assert r["primary"] == "inconclusive"


def test_priority_is_explainable_and_ordered():
    hi, factors = priority(unreached=True, boundary=True, cluster=4, complexity=8, missing_branch=True,
                           weak_assertions=True, failed_attempts=0, suite_seconds=1)
    lo, _ = priority(unreached=False, boundary=False, cluster=1, complexity=1, missing_branch=False,
                     weak_assertions=False, failed_attempts=2, suite_seconds=1)
    assert hi > lo
    assert set(factors) >= {"unreached", "boundary", "cluster", "failed_attempts"}


def test_weak_assertions_are_detected():
    info = analyze_assertions(textwrap.dedent("""
        def test_a():
            assert f(1) is not None
        def test_b():
            f(2)
        def test_c():
            assert f(3) == 4
    """))
    assert info["test_a"]["weak"] and info["test_b"]["asserts"] == 0 and not info["test_c"]["weak"]


# ---------------------------------------------------------------- engine, end to end
MODULE = textwrap.dedent('''
    def is_adult(age: int) -> bool:
        return age >= 18


    def sign_label(n: int) -> str:
        doubled = n * 2
        return "positive" if doubled > 0 else "other"


    def describe(n: int) -> str:
        return "big" if n > 100 else "small"


    def unused(x: int) -> int:
        return x + 1
''')

TESTS = textwrap.dedent('''
    import demo as m


    def test_minor():
        assert m.is_adult(15) is False


    def test_adult():
        assert m.is_adult(25) is True


    def test_label():
        assert m.sign_label(3) == "positive"


    def test_describe_runs():
        m.describe(500)
''')


@pytest.fixture(scope="module")
def diagnosed():
    info = analyze_source(MODULE, "demo")
    mut = run_mutation_analysis(info, TESTS, workers=2)
    events = []
    engine = BlindSpotEngine(info, None, Config(mutation_workers=2), lambda k, d: events.append(d),
                             session_id="test", max_investigations=40)
    ds = engine.investigate(TESTS, mut, 1)
    return info, mut, engine, ds, events


def _find(ds, original, mutated):
    return next(d for d in ds if d.original_code == original and d.mutated_code == mutated)


def test_demo_boundary_is_an_infection_failure_with_counterexample_18(diagnosed):
    _, _, _, ds, _ = diagnosed
    d = _find(ds, "age >= 18", "age > 18")
    assert d.evidence.reached and d.evidence.executions == 2 and d.evidence.infected_evaluations == 0
    assert d.primary_category == "infection" and "Missing boundary condition" in d.secondary_labels
    assert d.counterexample is not None and d.counterexample.args == ["18"]
    assert d.counterexample.original["repr"] == "True" and d.counterexample.mutant["repr"] == "False"
    assert d.proposed_test and "is_adult(18) == True" in d.proposed_test


def test_unexecuted_function_is_a_reachability_failure(diagnosed):
    _, _, _, ds, _ = diagnosed
    d = _find(ds, "x + 1", "x - 1")
    assert d.evidence.reached is False and d.primary_category == "reachability"


def test_masked_difference_is_a_propagation_failure(diagnosed):
    _, _, _, ds, _ = diagnosed
    d = _find(ds, "n * 2", "n / 2")
    assert d.primary_category == "propagation" and d.evidence.infected_evaluations >= 1
    # doubling vs halving never changes the sign test: no counterexample exists,
    # so the finding must stay a hypothesis and be flagged as possibly equivalent
    assert d.counterexample is None and d.finding_status == "hypothesis"
    assert "Potential equivalent mutant" in d.secondary_labels


def test_unchecked_output_is_a_revealability_failure(diagnosed):
    _, _, _, ds, _ = diagnosed
    d = _find(ds, "'big'", "''")
    assert d.primary_category == "revealability" and "Weak assertion" in d.secondary_labels
    assert d.evidence.propagated_calls[0].call == "describe(500)"
    assert d.counterexample and d.counterexample.source.startswith("observed in existing test")


def test_resolution_requires_verification(diagnosed):
    info, _, engine, ds, events = diagnosed
    d = _find(ds, "age >= 18", "age > 18")
    assert d.resolution_status == "unresolved"  # nothing is resolved before verification
    suite = engine.verify(TESTS, [d])
    assert d.resolution_status == "resolved"
    v = d.verification
    assert v.passes_on_original and v.kills_mutant and v.suite_regression_free
    assert d.proposed_test_name in suite
    assert any(e["type"] == "blind_spot_resolved" for e in events)


def test_wrong_oracle_is_rejected(diagnosed):
    info, _, engine, ds, _ = diagnosed
    d = _find(ds, "x + 1", "x - 1").model_copy(deep=True)
    d.proposed_test = "import demo as m\n\ndef test_bad():\n    assert m.unused(1) == 99\n"
    d.proposed_test_name, d.resolution_status = "test_bad", "unresolved"
    suite = engine.verify(TESTS, [d])
    assert d.resolution_status == "invalid_test" and d.verification.passes_on_original is False
    assert suite == TESTS  # the suite is not changed by an unverified test


def test_events_follow_the_investigation_order(diagnosed):
    *_, events = diagnosed
    types = [e["type"] for e in events]
    assert types[0] == "analysis_started" and "analysis_completed" in types
    assert types.index("blind_spot_discovered") < types.index("analysis_completed")


def test_heatmap_marks_blind_spots_and_verified_cells(diagnosed):
    _, _, engine, _, _ = diagnosed
    hm = engine.heatmap()
    statuses = {(c["function"], c["category"]): c["status"] for c in hm["cells"]}
    assert statuses[("unused", "unexplored_paths")] == "blind_spot"
    assert {c["key"] for c in hm["columns"]} >= {"boundary_conditions", "assertion_quality"}


def test_infection_probe_skips_impure_expressions():
    info = analyze_source("def f(x):\n    return g(x) + 1\n\ndef g(x):\n    return x\n", "mod")
    from testforge.core.mutation import generate_mutants
    m = next(mu for mu, _ in generate_mutants(info) if mu.operator == "AOR")
    inf = infection_source(info.source, m)
    assert not inf.supported and "calls a function" in inf.reason
    assert behaviour_category(m, locate(info.source, m)) == "arithmetic"


def test_infection_probe_works_inside_methods():
    """Regression: the probe name must not be mangled inside class bodies."""
    src = textwrap.dedent('''
        class Account:
            def __init__(self, balance: int):
                self.balance = balance

            def can_withdraw(self, amount: int) -> bool:
                return self.balance - amount >= 0
    ''')
    tests = textwrap.dedent('''
        import acct as m

        def test_ok():
            assert m.Account(100).can_withdraw(10) is True

        def test_too_much():
            assert m.Account(100).can_withdraw(500) is False
    ''')
    info = analyze_source(src, "acct")
    mut = run_mutation_analysis(info, tests, workers=2)
    engine = BlindSpotEngine(info, None, Config(mutation_workers=2), None, session_id="t", max_investigations=20)
    ds = engine.investigate(tests, mut, 1)
    d = next(d for d in ds if d.mutated_code == "self.balance - amount > 0")
    assert d.evidence.infection_supported and d.evidence.executions == 2
    assert d.primary_category == "infection" and d.counterexample is not None
    assert d.counterexample.call == "Account(100).can_withdraw(100)"
