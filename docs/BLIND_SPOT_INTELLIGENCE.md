# Blind Spot Intelligence Engine

TestForge's mutation loop tells the Writer *which* mutants survived. The Blind
Spot Intelligence Engine explains **why** each one survived, finds an input
that exposes it, writes a corrective test and **verifies** that test before it
counts. Every statement it makes comes from running code; nothing is invented.

```
Source → Analyzer → Planner → Writer → Executor ⇄ Fixer → Mutation Critic
       → Blind Spot Intelligence Agent → targeted refinement (Writer) → Executor → mutation verification
```

Code: `testforge/blindspot/` (engine), `testforge/agents/team.py` (`BlindSpotAgent`),
`testforge/orchestrator.py` (loop), `testforge/web/blindspot_api.py` (API),
`frontend/src/pages/BlindSpot.tsx` + `frontend/src/components/blindspot/` (workspace).

## 1. Diagnosis: the RIPR model

A test kills a mutant only if it **R**eaches the mutated code, the mutation
**I**nfects the program state there, the difference **P**ropagates to something
observable, and an assertion **R**eveals it. The first condition that fails is
the blind spot's primary category:

| Category | Measured by | Corrective strategy |
|---|---|---|
| Reachability failure | coverage.py (per-test contexts): no test executes the mutated statement | inputs that execute the missing branch/statement |
| Infection failure | infection probe: the statement runs, but the original and mutated expression give the same value on every evaluation | inputs that make them differ, especially boundary values |
| Propagation failure | the expression's value differs at least once, yet every test-level call returns/raises/leaves state exactly as on the original | paths and inputs on which the difference reaches an output |
| Revealability failure | some test-level call already behaves differently on the mutant, yet the test passes | stronger assertion on the output, exception or state |
| Inconclusive | coverage unavailable, or infection not measurable and no output differed | none: more evidence needed |

**Secondary labels** complement the category: missing boundary condition, weak
assertion, missing exception test, insufficient input diversity, redundant test,
missing state verification, potential equivalent mutant.

**Finding status.** *Confirmed* when the category is measured directly and the
fault is shown to be detectable (a distinguishing input was found, or a test
already observed a different output). *Hypothesis* when the category is measured
but no distinguishing input was found (the mutant may be equivalent).
*Inconclusive* when the measurements do not separate the cases. Evidence
strength (strong / moderate / weak) rates how complete the measurements are; it
is **not** a probability.

## 2. Evidence collection (all in the sandbox)

Per round, one baseline run of the suite on the original module:
coverage.py with `dynamic_context = test_function` (which tests execute each
line) and a pytest plugin (`harness/tf_trace_conftest.py`) that records every
*test-level* call into the module: arguments, return value or exception, and the
object's state after a method call.

Per investigated mutant, two more runs:

* **B: original module, instrumented at the mutation point** (`instrument.py`).
  The mutated expression is evaluated both as written and as the mutant computes
  it; the probe records whether they differ and returns the original value, so
  behaviour is unchanged. Expressions containing calls, `await`, `yield` or `:=`
  are not instrumented (evaluating them twice could change behaviour) and are
  reported as unsupported. Run B also yields a second trace of the original:
  calls that differ between two runs of the *original* are non-deterministic
  and are ignored.
* **C: the mutant**, traced the same way. Calls whose outcome differs from the
  baseline are the propagation evidence.

The test file itself is parsed for assertion quality (asserts per test, weak
forms such as `assert x is not None`, bare truthiness, `isinstance`).

## 3. Counterexample discovery

`harness/tf_counterexample.py` imports the original and the mutant side by side
and searches for an input on which they differ (return value, exception type or
object state), cheapest and most explainable first:

1. inputs the existing tests used;
2. one-argument neighbours: ±1, the mutated code's constants and the values the
   object is constructed with (c−1, c, c+1), 0, −1, empty/shortened strings and
   lists, flipped booleans;
3. **Hypothesis** property-based search over the argument types, which shrinks a
   found input to a minimal one.

Only Python literals are used, so every counterexample can be written into a
test. Each call has a watchdog timeout.

*Symbolic reasoning (Z3) is not implemented*; the brief listed it as optional
and the differential search plus Hypothesis covered the benchmark cases.

## 4. Corrective tests and verification

From a counterexample the engine writes a test that asserts the original
program's observed behaviour (`assert m.is_adult(18) == True`,
`pytest.raises(...)`, or `vars(obj) == {...}` for state). Where no literal oracle
exists and an AI backend is configured, the LLM writes the test from the
structured evidence (`CorrectiveTestSet` schema). In the orchestrator loop the
remaining unresolved mutants go to the Writer's targeted refinement together
with the diagnoses.

A blind spot is marked **resolved only when all three checks pass**:

1. the corrective test passes on the original program;
2. it fails on the targeted mutant;
3. the whole suite with the test added still passes on the original program.

Tests from the Writer's refinement count as resolving a blind spot when the
next full mutation run shows the mutant killed (`Mutant.killed_by` records the
killing test) by the stabilised, all-passing suite. The engine never edits the
module under test and never changes a measured score; the mutation score only
moves through real mutation runs.

## 5. Prioritisation (rule-based, explainable)

```
priority = 3.0·unreached + 2.0·boundary + 1.5·min(1, other survivors in the function / 5)
         + 1.0·min(1, complexity / 10) + 1.0·missing branch coverage + 1.0·weak assertions
         − 2.0·failed earlier attempts − 0.5·min(1, suite runtime / 30 s)
```

Each factor's contribution is stored with the diagnosis and shown in the UI.
The engine investigates the highest-priority survivors first
(`Config.blind_spot_max_investigations`, default 12 per round).

## 6. Stopping conditions

Mutation-score target, maximum rounds, no measurable improvement (plateau / no
new tests), and the new `Config.max_total_tokens`, `Config.max_seconds` and
user cancellation (`POST /api/runs/{id}/cancel`, or *Cancel analysis* in the
workspace). The loop is always bounded by the round limit.

## 7. Research protocol

* `use_blind_spot=False` keeps the original pipeline as the baseline.
  Configurations `mock_blindspot`, `groq_blindspot`, `claude_blindspot` and
  `ollama_blindspot` add the agent to the corresponding `*_full` / baseline.
* `--holdout 0.3` holds out 30% of mutants (deterministic hash, identical in
  every configuration). They are never shown to the Writer or the Blind Spot
  agent; their kill rate is reported as the held-out score.
* `mutation_score_raw` counts mutants the Writer's LLM flagged as equivalent as
  survivors, so the comparison does not rely on LLM judgement to shrink the
  denominator. Suspected equivalent mutants from the Blind Spot engine are
  reported separately and never removed.
* `diagnoses.csv` lists every diagnosis with empty `manual_label` /
  `manual_notes` columns. Label a sample by hand and re-run
  `python experiments/analyze.py <dir>` for the agreement figure and confusion
  table.

```bash
python experiments/run.py --modules benchmark/modules --configs mock_baseline,mock_blindspot --holdout 0.3 \
    --out results/benchmark_blindspot_mock
python experiments/analyze.py results/benchmark_blindspot_mock
```

## 8. API and events

REST endpoints are listed in `testforge/web/blindspot_api.py`. Live updates use
Server-Sent Events (`/api/blindspot/sessions/{id}/events?after=N` resumes after
event N on reconnect). Event types: `analysis_started`, `prioritized`,
`mutant_under_investigation`, `evidence_collected`, `counterexample_found`,
`blind_spot_discovered`, `diagnosis_completed`, `corrective_test_generated`,
`corrective_tests_executed`, `verification_started`, `blind_spot_resolved`,
`blind_spot_unresolved`, `suite_updated`, `analysis_completed`,
`analysis_cancelled`, `analysis_failed` (plus `initial_generation`,
`survivors_detected` and `mutation_started` for the timeline). Generate runs
carry the same events with kind `blindspot`.

## 9. Demonstration

`examples/blindspot_demo/`: `is_adult(age)` returns `age >= 18`; the starting
tests use ages 15 and 25 (100% line coverage). Open **Blind Spot Intelligence →
Demonstration**. The engine measures that line 11 runs but `age >= 18` and
`age > 18` agree for both inputs (infection failure, missing boundary
condition), finds `is_adult(18)`, writes `assert m.is_adult(18) == True`, runs
it on the original (passes) and on the mutant (fails), checks the suite, and the
mutation score moves from 33% to 100% in the following mutation run. All of it
is real execution; `tests/test_blindspot.py` and `tests/test_web_api.py` check it.

## 10. Limitations

* Infection is not measured for expressions that call functions; such cases can
  only be inconclusive or revealability failures.
* Counterexample search handles literal arguments (numbers, strings, booleans,
  lists, tuples, None); objects that need complex setup are skipped.
* The LLM explanation field is filled only when an AI backend writes the
  corrective test; with the offline backend explanations are generated from the
  measurements (rule-based text).
* The network block in the sandbox is a guard against accidental use, not a
  security boundary; run untrusted code in a container.
* Workspace sessions live in server memory; after a restart they are saved
  read-only snapshots (start a new session to run actions).
* Z3 symbolic reasoning is not implemented.
