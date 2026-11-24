# TestForge

**An Autonomous Multi-Agent Framework for Unit Test Generation with Mutation-Guided Refinement**

TestForge takes a Python module and automatically writes a pytest suite for it.
A team of specialised agents plans, writes, runs and repairs the tests, then a
mutation-testing critic plants small bugs in the code and tells the writer which
ones the tests missed. The loop repeats until the suite is strong enough.

![Dashboard](docs/dashboard.png)

## How it works

```
                 🎯 Orchestrator
                       │
 🔍 Analyzer → 📋 Planner → ✍️ Writer → ▶️ Executor ⇄ 🔧 Fixer
                              ▲              │
                              │              ▼
                   surviving mutants   🧬 Mutation Critic
                              └──────── score < target?
```

| Agent | Type | Responsibility |
|---|---|---|
| Analyzer | deterministic | AST analysis: functions, parameters, exceptions, branch complexity, boundary constants |
| Planner | LLM | Designs normal / boundary / error / state test cases |
| Writer | LLM | Writes the pytest file; in refinement rounds writes tests that target surviving mutants and flags equivalent mutants |
| Executor | deterministic | Runs pytest in an isolated workspace with line + branch coverage |
| Fixer | LLM | Repairs failing tests (the code under test is the oracle); tests that still fail are pruned |
| Mutation Critic | deterministic | Own AST mutation engine (AOR, ROR, LCR, UOI, CRP, RET, ASR), runs mutants in parallel |
| Orchestrator | control loop | Coordinates agents, stops on target reached / plateau / iteration limit, records metrics |

LLM agents use **structured outputs** (pydantic schemas), so the orchestrator never parses free text.

## Install

**Windows (easiest):**
1. Install Python 3.12+ from https://www.python.org/downloads/ and tick **"Add Python to PATH"**.
2. Download this project (GitHub: **Code → Download ZIP**) and unzip it.
3. Double-click `setup_windows.bat` (one time only).
4. Double-click `run_dashboard.bat`; the dashboard opens in your browser.

**macOS / Linux:** `bash setup_unix.sh`

**Manual:**

```bash
pip install -e .
```

## Usage

```bash
# Offline mock backend (no API key needed)
testforge generate examples/calculator.py

# With Claude (needs ANTHROPIC_API_KEY)
export ANTHROPIC_API_KEY=...
testforge generate examples/bank_account.py --llm claude

# Ablation study: switch agents off
testforge generate examples/calculator.py --llm claude --no-planner
testforge generate examples/calculator.py --llm claude --no-mutation-feedback

# Mutation-score any existing test suite (e.g. human-written tests, a Pynguin baseline)
testforge mutate examples/calculator.py path/to/test_calculator.py

# Web dashboard at http://127.0.0.1:8000
testforge serve
```

Each `generate` run writes `generated_tests/test_<module>.py` and a JSON report
with per-iteration metrics (tests, coverage, mutation score, tokens, cost, time)
for the evaluation chapter.

## Experiments

```bash
python experiments/run.py                          # offline baseline only
python experiments/run.py --configs all --reps 3   # all configurations (needs ANTHROPIC_API_KEY)
```

| Configuration | Purpose |
|---|---|
| `mock_baseline` | Non-LLM baseline |
| `claude_single_shot` | Plain LLM: one prompt, no planning, repair or feedback |
| `claude_full` | The proposed multi-agent system |
| `claude_no_planner` / `claude_no_fixer` / `claude_no_feedback` | Ablations: full system minus one agent |

Results go to `results/`: `runs.csv`, `iterations.csv` (per-round curves) and `summary.md` (mean ± std tables).

## Backends

* `claude`: Anthropic Messages API, default model `claude-opus-5-5`, configurable effort.
* `mock`: offline and deterministic. It calls the code in a sandboxed subprocess and records the results as
  characterization tests. It lets the whole pipeline run without a key and serves as a **non-LLM baseline**.

## Project layout

```
testforge/
  agents/team.py      the seven agents
  agents/prompts.py   system prompts (part of the method)
  orchestrator.py     feedback loop and stopping rules
  core/analysis.py    static analysis
  core/mutation.py    mutation engine
  core/runner.py      isolated pytest + coverage execution
  core/suite.py       merging / pruning test files
  llm.py              Claude client and mock backend
  web/                FastAPI dashboard (live via Server-Sent Events)
examples/             modules under test
tests/                tests for TestForge itself
```

## Running the framework's own tests

```bash
pytest
```

## Security note

Generated tests execute the code under test. Only run TestForge on code you trust,
or inside a container/VM.
