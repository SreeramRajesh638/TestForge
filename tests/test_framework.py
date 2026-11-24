"""Tests for TestForge itself (run with: pytest)."""

from pathlib import Path

import pytest

from testforge.config import Config
from testforge.core.analysis import analyze_file, analyze_source
from testforge.core.mutation import generate_mutants, run_mutation_analysis
from testforge.core.runner import run_tests
from testforge.core.suite import count_tests, merge_suites, prune_tests
from testforge.orchestrator import Orchestrator

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

SRC = '''
def sign(x: int) -> int:
    """Return the sign of x."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0
'''


# ---------------------------------------------------------------- analysis
def test_analyzer_finds_functions_and_methods():
    info = analyze_file(EXAMPLES / "bank_account.py")
    names = {f.qualname for f in info.functions}
    assert {"BankAccount.deposit", "BankAccount.withdraw", "BankAccount.transfer"} <= names
    withdraw = info.function("BankAccount.withdraw")
    assert "InsufficientFundsError" in withdraw.raises
    assert [p.name for p in withdraw.params] == ["amount"]  # self removed


def test_analyzer_counts_branches_and_constants():
    info = analyze_source(SRC, "sign")
    fn = info.function("sign")
    assert fn.complexity == 3
    assert 0 in fn.constants and 1 in fn.constants


# ---------------------------------------------------------------- mutation
def test_mutants_are_valid_and_described():
    info = analyze_source(SRC, "sign")
    mutants = generate_mutants(info)
    ops = {m.operator for m, _ in mutants}
    assert {"ROR", "CRP", "UOI", "RET"} <= ops
    for m, src in mutants:
        compile(src, "<m>", "exec")
        assert m.function == "sign"
        assert m.original != m.mutated


def test_docstrings_are_not_mutated():
    info = analyze_source(SRC, "sign")
    assert all("Return the sign" not in m.original for m, _ in generate_mutants(info))


def test_strong_suite_kills_all_mutants():
    info = analyze_source(SRC, "sign")
    tests = ("from sign import sign\n"
             "def test_pos(): assert sign(5) == 1\n"
             "def test_one(): assert sign(1) == 1\n"
             "def test_neg(): assert sign(-3) == -1\n"
             "def test_minus_one(): assert sign(-1) == -1\n"
             "def test_zero(): assert sign(0) == 0\n")
    res = run_mutation_analysis(info, tests, workers=2)
    assert res.score == 1.0


def test_weak_suite_leaves_survivors():
    info = analyze_source(SRC, "sign")
    tests = "from sign import sign\ndef test_runs(): sign(5)\n"
    res = run_mutation_analysis(info, tests, workers=2)
    assert res.score < 0.5 and res.survivors


# ---------------------------------------------------------------- runner
def test_runner_reports_pass_fail_and_coverage():
    tests = ("from sign import sign\n"
             "def test_ok(): assert sign(2) == 1\n"
             "def test_bad(): assert sign(2) == 5\n")
    res = run_tests("sign", SRC, tests)
    assert (res.total, res.passed, res.failed) == (2, 1, 1)
    assert res.failing[0].name == "test_bad"
    assert 0 < res.line_coverage < 1


def test_runner_reports_collection_errors():
    res = run_tests("sign", SRC, "import does_not_exist\ndef test_x(): pass\n")
    assert res.collection_error is not None


# ---------------------------------------------------------------- suite
def test_merge_dedupes_imports_and_renames_clashes():
    a = "import pytest\nfrom m import f\n\ndef test_a():\n    assert f() == 1\n"
    b = "import pytest\nfrom m import g\n\ndef test_a():\n    assert g() == 2\n"
    merged = merge_suites(a, b)
    assert merged.count("import pytest") == 1
    assert "def test_a_2" in merged and count_tests(merged) == 2


def test_prune_removes_only_failing_tests():
    code = "def test_a(): pass\ndef test_b(): pass\nclass TestC:\n    def test_c(self): pass\n"
    pruned, n = prune_tests(code, {"test_b", "test_c"})
    assert n == 2 and count_tests(pruned) == 1 and "TestC" not in pruned


# ---------------------------------------------------------------- end to end
@pytest.mark.parametrize("module", ["calculator.py", "text_utils.py"])
def test_pipeline_with_mock_improves_mutation_score(module):
    report = Orchestrator(Config(llm="mock", max_refine_iterations=2)).run(str(EXAMPLES / module))
    first, last = report.iterations[0], report.iterations[-1]
    assert last.tests_passing == last.tests_total > 0
    assert last.mutation_score >= first.mutation_score
    assert last.line_coverage > 0.8


def test_ablation_without_feedback_runs_single_iteration():
    cfg = Config(llm="mock", use_mutation_feedback=False, use_planner=False)
    report = Orchestrator(cfg).run(str(EXAMPLES / "calculator.py"))
    assert len(report.iterations) == 1 and report.stop_reason == "feedback_disabled"


def test_experiment_configs_use_their_backends_default_model():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_experiments", Path(__file__).resolve().parent.parent / "experiments" / "run.py")
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    from testforge.config import DEFAULT_MODELS
    for name, cfg in run.CONFIGS.items():
        assert cfg.model == DEFAULT_MODELS[cfg.llm], name
